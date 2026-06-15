#!/usr/bin/env ruby
require 'yaml'
require 'json'

def load_dsl(path)
  if path && File.file?(path)
    return YAML.load_file(path, aliases: true)
  end
  fragments = Dir['NL2MGQL_CHATFLOW_DSL/NL2MGQL_CHATFLOW_[0-9][0-9].yml'].sort
  abort('no DSL fragments found') if fragments.empty?
  YAML.safe_load(fragments.map { |f| File.read(f) }.join, aliases: true, permitted_classes: [Date, Time])
end

path = ARGV[0]
d = load_dsl(path)
g = d.dig('workflow', 'graph') || {}
nodes = g['nodes'] || []
edges = g['edges'] || []
by_id = nodes.to_h { |n| [n['id'], n] }
by_title = nodes.to_h { |n| [n.dig('data', 'title'), n] }
errors = []
edge_errors = edges.select { |e| !by_id[e['source']] || !by_id[e['target']] }
errors += edge_errors.map { |e| "dangling edge #{e['id']}" }
in_deg = Hash.new(0); out_deg = Hash.new(0)
edges.each { |e| out_deg[e['source']] += 1; in_deg[e['target']] += 1 }
orphan = nodes.select { |n| n.dig('data', 'type') != 'start' && in_deg[n['id']] == 0 && out_deg[n['id']] == 0 }
errors += orphan.map { |n| "orphan node #{n['id']} #{n.dig('data', 'title')}" }

vars = Hash.new { |h, k| h[k] = {} }
vars['sys'] = Hash.new(true); vars['env'] = Hash.new(true); vars['conversation'] = Hash.new(true)
nodes.each do |n|
  t = n.dig('data', 'type'); id = n['id']
  case t
  when 'start'
    (n.dig('data', 'variables') || []).each { |v| vars[id][v['variable']] = true if v['variable'] }
  when 'llm'
    vars[id]['text'] = true; vars[id]['structured_output'] = true
  when 'http-request'
    # Dify HTTP Request node outputs are status_code/body/headers.
    %w[status_code body headers].each { |v| vars[id][v] = true }
  when 'code'
    outs = n.dig('data', 'outputs'); outs.keys.each { |v| vars[id][v] = true } if outs.is_a?(Hash)
  when 'knowledge-retrieval'
    vars[id]['result'] = true
  when 'iteration'
    vars[id]['item'] = true; vars[id]['output'] = true
  end
end

invalid = []; cond_invalid = []; http_invalid = []; answer_invalid = []; code_invalid = []
check_ref = lambda do |ctx, id, var|
  invalid << [ctx.dig('data', 'title'), id, var] unless vars[id] && vars[id][var]
end
scan = lambda do |obj, ctx|
  case obj
  when Hash
    %w[value_selector variable_selector selector].each do |key|
      if obj[key].is_a?(Array) && obj[key].size >= 2 && obj[key][0].is_a?(String)
        check_ref.call(ctx, obj[key][0], obj[key][1])
      end
    end
    obj.each_value { |v| scan.call(v, ctx) }
  when Array
    obj.each { |v| scan.call(v, ctx) }
  when String
    obj.scan(/\{\{#([^.#]+)\.([^#]+)#\}\}/).each { |id, var| check_ref.call(ctx, id, var) }
  end
end
nodes.each do |n|
  before = invalid.length
  scan.call(n['data'], n)
  added = invalid[before..] || []
  cond_invalid += added if n.dig('data', 'type') == 'if-else'
  http_invalid += added if n.dig('data', 'type') == 'http-request'
  answer_invalid += added if n.dig('data', 'type') == 'answer'
  code_invalid += added if n.dig('data', 'type') == 'code'
end

forbidden_patterns = [
  'global_' + 'use_' + 'full_' + 'llm',
  'effective_' + 'use_' + 'full_' + 'llm',
  'HTTP请求_' + '调用满血LLM',
  '代码执行_' + '检查满血LLM响应',
  '条件分支_' + '是否使用满血LLM'
]
forbidden_hits = []
nodes.each do |n|
  text = n.to_s
  forbidden_patterns.each do |pat|
    forbidden_hits << [n['id'], n.dig('data', 'title'), pat] if text.include?(pat)
  end
end
errors += forbidden_hits.map { |id, title, pat| "forbidden full-llm artifact #{id} #{title} #{pat}" }

summary = { orphan_nodes: orphan.length, dangling_edges: edge_errors.length, invalid_variables: invalid.length,
  answer_invalid_variables: answer_invalid.length, condition_invalid_variables: cond_invalid.length,
  http_invalid_variables: http_invalid.length, code_invalid_variables: code_invalid.length,
  llm_nodes: nodes.count { |n| n.dig('data', 'type') == 'llm' },
  forbidden_artifacts: forbidden_hits.length, errors: errors, invalid_details: invalid.first(20) }
puts JSON.pretty_generate(summary)
exit(errors.empty? && invalid.empty? ? 0 : 1)
