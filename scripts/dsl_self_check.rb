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

full_cond = nodes.select { |n| n.dig('data', 'title').to_s.start_with?('条件分支_是否使用满血LLM_') }
full_http = nodes.select { |n| n.dig('data', 'title').to_s.start_with?('HTTP请求_调用满血LLM_') }
full_check = nodes.select { |n| n.dig('data', 'title').to_s.start_with?('代码执行_检查满血LLM响应_') }
full_success = nodes.select { |n| n.dig('data', 'title').to_s.start_with?('条件分支_满血LLM是否成功_') }
full_unify = nodes.select { |n| n.dig('data', 'title').to_s.start_with?('代码执行_统一LLM输出_') }
full_cond.each do |n|
  sel = n.dig('data', 'cases', 0, 'conditions', 0, 'variable_selector')
  errors << "bad full condition #{n['id']}" unless sel && sel[0] == '1776100000000' && sel[1].to_s.start_with?('effective_use_full_llm_')
end
full_http.each do |n|
  data = n['data']; body = data.dig('body', 'data', 0, 'value') rescue nil
  json_ok = begin JSON.parse(body.to_s.gsub(/\{\{#[^#]+#\}\}/, 'PLACEHOLDER')); true rescue false end
  ok = data['method'].to_s.downcase == 'post' && data['url'].to_s.strip != '' &&
       data['headers'].to_s.include?('Content-Type') && data['headers'].to_s.match?(/Authorization|API-Key/i) &&
       body && json_ok && body.include?('"model"') && body.include?('"messages"') && body.include?('"stream": false')
  errors << "bad http #{n['id']} #{data['title']}" unless ok
end
full_check.each do |n|
  suffix = n.dig('data', 'title').sub('代码执行_检查满血LLM响应_', '')
  http = by_title["HTTP请求_调用满血LLM_#{suffix}"]
  unless http
    errors << "full check missing http #{n['id']} #{suffix}"
    next
  end
  selectors = (n.dig('data', 'variables') || []).map { |v| [v['variable'], v['value_selector']] }.to_h
  %w[status_code body].each do |name|
    sel = selectors[name]
    errors << "full check #{n['id']} missing #{name} from #{http['id']}" unless sel == [http['id'], name]
  end
  if selectors['headers'] && selectors['headers'] != [http['id'], 'headers']
    errors << "full check #{n['id']} bad headers selector"
  end
  selectors.each do |name, sel|
    next unless sel.is_a?(Array)
    errors << "full check #{n['id']} references non-http input #{name}" if %w[status_code body headers].include?(name) && sel[0] != http['id']
    errors << "full check #{n['id']} references invalid http output #{sel[1]}" if sel[0] == http['id'] && !%w[status_code body headers].include?(sel[1])
  end
end
full_success.each do |n|
  suffix = n.dig('data', 'title').sub('条件分支_满血LLM是否成功_', '')
  check = by_title["代码执行_检查满血LLM响应_#{suffix}"]
  sel = n.dig('data', 'cases', 0, 'conditions', 0, 'variable_selector')
  errors << "bad full success condition #{n['id']} #{suffix}" unless check && sel == [check['id'], 'success']
end

summary = { orphan_nodes: orphan.length, dangling_edges: edge_errors.length, invalid_variables: invalid.length,
  answer_invalid_variables: answer_invalid.length, condition_invalid_variables: cond_invalid.length,
  http_invalid_variables: http_invalid.length, code_invalid_variables: code_invalid.length,
  llm_nodes: nodes.count { |n| n.dig('data', 'type') == 'llm' }, full_conditions: full_cond.length,
  full_http: full_http.length, full_checks: full_check.length, full_success_conditions: full_success.length,
  full_unify_nodes: full_unify.length, errors: errors, invalid_details: invalid.first(20) }
puts JSON.pretty_generate(summary)
exit(errors.empty? && invalid.empty? ? 0 : 1)
