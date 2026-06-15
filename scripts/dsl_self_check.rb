#!/usr/bin/env ruby
require 'yaml'; require 'json'
path = ARGV[0] || 'NL2MGQL_CHATFLOW_DSL/NL2MGQL_CHATFLOW_FULL.yml'
d = YAML.load_file(path, aliases: true)
g = d.dig('workflow','graph') || {}
nodes = g['nodes'] || []
edges = g['edges'] || []
by_id = nodes.to_h { |n| [n['id'], n] }
errors = []
edge_errors = edges.select { |e| !by_id[e['source']] || !by_id[e['target']] }
errors += edge_errors.map { |e| "dangling edge #{e['id']}" }
in_deg = Hash.new(0); out_deg = Hash.new(0)
edges.each { |e| out_deg[e['source']] += 1; in_deg[e['target']] += 1 }
orphan = nodes.select { |n| n.dig('data','type') != 'start' && in_deg[n['id']] == 0 && out_deg[n['id']] == 0 }
errors += orphan.map { |n| "orphan node #{n['id']} #{n.dig('data','title')}" }
# approximate variable existence
vars = Hash.new { |h,k| h[k] = {} }
vars['sys'] = Hash.new(true); vars['env'] = Hash.new(true); vars['conversation'] = Hash.new(true)
nodes.each do |n|
  t=n.dig('data','type'); id=n['id']
  case t
  when 'llm'; vars[id]['text']=true; vars[id]['structured_output']=true
  when 'http-request'; %w[body status_code headers error].each { |v| vars[id][v]=true }
  when 'code'; outs=n.dig('data','outputs'); outs.keys.each { |v| vars[id][v]=true } if outs.is_a?(Hash)
  when 'knowledge-retrieval'; vars[id]['result']=true
  when 'iteration'; vars[id]['item']=true; vars[id]['output']=true
  end
end
invalid=[]; cond_invalid=[]; http_invalid=[]; answer_invalid=[]
scan = lambda do |obj, ctx|
  case obj
  when Hash
    if obj['value_selector'].is_a?(Array) && obj['value_selector'].size >= 2
      id,var=obj['value_selector'][0],obj['value_selector'][1]
      invalid << [ctx,id,var] unless vars[id] && vars[id][var]
    end
    if obj['variable_selector'].is_a?(Array) && obj['variable_selector'].size >= 2
      id,var=obj['variable_selector'][0],obj['variable_selector'][1]
      invalid << [ctx,id,var] unless vars[id] && vars[id][var]
    end
    obj.each_value { |v| scan.call(v,ctx) }
  when Array then obj.each { |v| scan.call(v,ctx) }
  when String
    obj.scan(/\{\{#([^.#]+)\.([^#]+)#\}\}/).each do |id,var|
      invalid << [ctx,id,var] unless vars[id] && vars[id][var]
    end
  end
end
nodes.each { |n| before=invalid.length; scan.call(n['data'], n); added=invalid[before..] || []; cond_invalid += added if n.dig('data','type')=='if-else'; http_invalid += added if n.dig('data','type')=='http-request'; answer_invalid += added if n.dig('data','type')=='answer' }
# full llm checks
full_cond = nodes.select { |n| n.dig('data','title').to_s.start_with?('条件分支_是否使用满血LLM_') }
full_http = nodes.select { |n| n.dig('data','title').to_s.start_with?('HTTP请求_调用满血LLM_') }
full_check = nodes.select { |n| n.dig('data','title').to_s.start_with?('代码执行_检查满血LLM响应_') }
full_success = nodes.select { |n| n.dig('data','title').to_s.start_with?('条件分支_满血LLM是否成功_') }
full_unify = nodes.select { |n| n.dig('data','title').to_s.start_with?('代码执行_统一LLM输出_') }
full_cond.each do |n|
  sel=n.dig('data','cases',0,'conditions',0,'variable_selector')
  errors << "bad full condition #{n['id']}" unless sel && sel[0]=='1776100000000' && sel[1].to_s.start_with?('effective_use_full_llm_')
end
full_http.each do |n|
  data=n['data']; body=data.dig('body','data',0,'value') rescue nil
  ok=data['method'].to_s.downcase=='post' && data['url'].to_s.strip!='' && data['headers'].to_s.include?('Content-Type') && data['headers'].to_s.match?(/Authorization|API-Key/i) && body && JSON.parse(body.gsub(/\{\{#[^#]+#\}\}/,'PLACEHOLDER')) rescue false
  errors << "bad http #{n['id']}" unless ok
end
summary={orphan_nodes: orphan.length, dangling_edges: edge_errors.length, invalid_variables: invalid.length, answer_invalid_variables: answer_invalid.length, condition_invalid_variables: cond_invalid.length, http_invalid_variables: http_invalid.length, llm_nodes: nodes.count{|n|n.dig('data','type')=='llm'}, full_conditions: full_cond.length, full_http: full_http.length, full_checks: full_check.length, full_success_conditions: full_success.length, full_unify_nodes: full_unify.length, errors: errors}
puts JSON.pretty_generate(summary)
exit(errors.empty? && invalid.empty? ? 0 : 1)
