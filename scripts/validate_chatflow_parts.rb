#!/usr/bin/env ruby
# frozen_string_literal: true

require 'yaml'
require 'set'

ROOT = File.expand_path('..', __dir__)
DEFAULT_MANIFEST = File.join(ROOT, 'NL2MGQL_CHATFLOW_DSL', 'NL2MGQL_CHATFLOW.manifest.yml')
MANIFEST_PATH = ARGV[0] ? File.expand_path(ARGV[0], Dir.pwd) : DEFAULT_MANIFEST
MAX_SOURCE_PART_LINES = 1000
REQUIRED_CHAIN = {
  '1775000000012' => '代码执行_调用满血LLM并保留错误响应',
  '1775000000013' => '代码执行_提取语义计划结果',
  '1775000000007' => '代码执行_遍历语义计划并统一编译执行'
}.freeze
REQUIRED_EXTRACT_OUTPUTS = {
  'normalized_semantic_plan_json' => 'string',
  'semantic_plan_json' => 'string',
  'plan_valid' => 'boolean',
  'debug_summary_json' => 'string'
}.freeze
REQUIRED_PREPARE_OUTPUTS = {
  'normalized_semantic_plan_json' => 'string',
  'planner' => 'object',
  'schema_context' => 'string',
  'execution_input_valid' => 'boolean',
  'execution_input_error' => 'string'
}.freeze
REQUIRED_VALIDATOR_OUTPUTS = {
  'validator_result_json' => 'string',
  'validator_route' => 'string',
  'valid' => 'boolean',
  'normalized_plan_json' => 'string',
  'normalized_semantic_plan_json' => 'string',
  'semantic_plan_json' => 'string',
  'answer_payload_json' => 'string',
  'final_answer_markdown' => 'string',
  'context_update_json' => 'string'
}.freeze
REQUIRED_COMPILER_OUTPUTS = {
  'contract_version' => 'string',
  'compile_success' => 'boolean',
  'execution_success' => 'boolean',
  'primary_collection' => 'string',
  'pipeline_json' => 'string',
  'request_body_json' => 'string',
  'query_result_json' => 'string',
  'rows_json' => 'string',
  'row_count' => 'number',
  'fields_json' => 'string',
  'compile_warnings_json' => 'string',
  'execution_error' => 'string',
  'compiler_debug_json' => 'string'
}.freeze

errors = []
warnings = []

unless File.file?(MANIFEST_PATH)
  abort "Manifest not found: #{MANIFEST_PATH}"
end

manifest = YAML.load_file(MANIFEST_PATH)
parts = manifest.fetch('parts')
manifest_dir = File.dirname(MANIFEST_PATH)
part_paths = parts.map { |part| File.expand_path(part, manifest_dir) }

part_paths.each do |path|
  unless File.file?(path)
    errors << "missing part: #{path}"
    next
  end
  line_count = File.foreach(path).count
  warnings << "#{path} has #{line_count} lines, exceeds legacy #{MAX_SOURCE_PART_LINES}-line guideline" if line_count > MAX_SOURCE_PART_LINES
end

assembled = part_paths.map { |path| File.read(path) }.join
workflow = YAML.safe_load(assembled, aliases: true)
graph = workflow.dig('workflow', 'graph')
errors << 'workflow.graph is missing' unless graph.is_a?(Hash)

nodes = graph.fetch('nodes', [])
edges = graph.fetch('edges', [])
node_ids = nodes.map { |node| node['id'].to_s }
duplicates = node_ids.tally.select { |_id, count| count > 1 }.keys
errors << "duplicate node ids: #{duplicates.join(', ')}" unless duplicates.empty?
node_by_id = nodes.to_h { |node| [node['id'].to_s, node] }
node_id_set = node_by_id.keys.to_set

edges.each do |edge|
  source = edge['source'].to_s
  target = edge['target'].to_s
  errors << "edge #{edge['id']} source #{source} does not exist" unless node_id_set.include?(source)
  errors << "edge #{edge['id']} target #{target} does not exist" unless node_id_set.include?(target)
end

outputs_by_id = {}
nodes.each do |node|
  id = node['id'].to_s
  outputs = node.dig('data', 'outputs')
  outputs_by_id[id] = outputs.is_a?(Hash) ? outputs.transform_keys(&:to_s) : {}
  type = node.dig('data', 'type').to_s
  # Dify LLM nodes expose text even when the exported node has no explicit outputs block.
  if type == 'llm'
    outputs_by_id[id]['text'] ||= { 'type' => 'string' }
    outputs_by_id[id]['structured_output'] ||= { 'type' => 'object' }
  end
  outputs_by_id[id]['item'] ||= { 'type' => 'object' } if type == 'iteration'
  outputs_by_id[id]['output'] ||= { 'type' => 'array[object]' } if type == 'iteration'
end

selectors = []
walk = lambda do |obj, path|
  case obj
  when Hash
    selectors << [obj['value_selector'], path + ['value_selector']] if obj.key?('value_selector')
    selectors << [obj['variable_selector'], path + ['variable_selector']] if obj.key?('variable_selector')
    obj.each { |key, value| walk.call(value, path + [key]) }
  when Array
    obj.each_with_index { |value, index| walk.call(value, path + [index]) }
  end
end
walk.call(graph, [])

selectors.each do |selector, path|
  next unless selector.is_a?(Array) && selector.length >= 2
  source_id = selector[0].to_s
  field = selector[1].to_s
  next if %w[sys env conversation].include?(source_id)
  unless node_id_set.include?(source_id)
    errors << "selector #{selector.inspect} at #{path.join('.')} references missing node #{source_id}"
    next
  end
  declared_outputs = outputs_by_id[source_id]
  next if declared_outputs.empty? # Some Dify node types have implicit outputs not serialized in all exports.
  errors << "selector #{selector.inspect} at #{path.join('.')} references missing output #{field}" unless declared_outputs.key?(field)
end

REQUIRED_CHAIN.each do |id, expected_title|
  node = node_by_id[id]
  if node.nil?
    errors << "required node #{id} is missing"
  elsif node.dig('data', 'title') != expected_title
    warnings << "required node #{id} title is #{node.dig('data', 'title').inspect}, expected #{expected_title.inspect}"
  end
end

extract_outputs = outputs_by_id['1775000000013'] || {}
REQUIRED_EXTRACT_OUTPUTS.each do |field, type|
  actual_type = extract_outputs.dig(field, 'type')
  errors << "1775000000013 output #{field} type is #{actual_type.inspect}, expected #{type.inspect}" unless actual_type == type
end

prepare_outputs = outputs_by_id['1776000000024'] || {}
REQUIRED_PREPARE_OUTPUTS.each do |field, type|
  actual_type = prepare_outputs.dig(field, 'type')
  errors << "1776000000024 output #{field} type is #{actual_type.inspect}, expected #{type.inspect}" unless actual_type == type
end

validator_outputs = outputs_by_id['1778000000001'] || {}
REQUIRED_VALIDATOR_OUTPUTS.each do |field, type|
  actual_type = validator_outputs.dig(field, 'type')
  errors << "1778000000001 output #{field} type is #{actual_type.inspect}, expected #{type.inspect}" unless actual_type == type
end
validator_node = node_by_id['1778000000001']
errors << 'required Phase 8 validator node 1778000000001 is missing' if validator_node.nil?
if validator_node && validator_node.dig('data', 'title') != '代码执行_semantic_plan_validator'
  errors << "1778000000001 title is #{validator_node.dig('data', 'title').inspect}, expected 代码执行_semantic_plan_validator"
end

runtime_node = node_by_id['1779000000001']
errors << 'required Phase 9 runtime schema context node 1779000000001 is missing' if runtime_node.nil?
if runtime_node && runtime_node.dig('data', 'title') != '代码执行_准备Validator运行时Schema上下文'
  errors << "1779000000001 title is #{runtime_node.dig('data', 'title').inspect}, expected 代码执行_准备Validator运行时Schema上下文"
end
runtime_outputs = outputs_by_id['1779000000001'] || {}
{
  'schema_runtime_context_json' => 'string',
  'schema_context_ready' => 'boolean',
  'schema_hydration_needed' => 'boolean',
  'schema_hydration_collections' => 'array[string]',
  'schema_metadata_json' => 'string',
  'schema_alias_index_json' => 'string',
  'schema_source' => 'string'
}.each do |field, type|
  actual_type = runtime_outputs.dig(field, 'type')
  errors << "1779000000001 output #{field} type is #{actual_type.inspect}, expected #{type.inspect}" unless actual_type == type
end

validator_branch = node_by_id['1778000000002']
errors << 'required Phase 8 validator route branch 1778000000002 is missing' if validator_branch.nil?
if validator_branch && validator_branch.dig('data', 'title') != '条件分支_validator结果判断'
  errors << "1778000000002 title is #{validator_branch.dig('data', 'title').inspect}, expected 条件分支_validator结果判断"
end

compile_node = node_by_id['1775000000007']
compile_vars = compile_node&.dig('data', 'variables') || []
compile_semantic_selector = compile_vars.find { |var| var['variable'] == 'semantic_plan' }&.dig('value_selector')
unless compile_semantic_selector == ['1778000000001', 'normalized_plan_json']
  errors << "1775000000007 semantic_plan selector is #{compile_semantic_selector.inspect}, expected ['1778000000001', 'normalized_plan_json']"
end
compile_validator_route_selector = compile_vars.find { |var| var['variable'] == 'validator_route' }&.dig('value_selector')
unless compile_validator_route_selector == ['1778000000001', 'validator_route']
  errors << "1775000000007 validator_route selector is #{compile_validator_route_selector.inspect}, expected ['1778000000001', 'validator_route']"
end
compile_validator_result_selector = compile_vars.find { |var| var['variable'] == 'validator_result_json' }&.dig('value_selector')
unless compile_validator_result_selector == ['1778000000001', 'validator_result_json']
  errors << "1775000000007 validator_result_json selector is #{compile_validator_result_selector.inspect}, expected ['1778000000001', 'validator_result_json']"
end
compiler_outputs = outputs_by_id['1775000000007'] || {}
REQUIRED_COMPILER_OUTPUTS.each do |field, type|
  actual_type = compiler_outputs.dig(field, 'type')
  errors << "1775000000007 output #{field} type is #{actual_type.inspect}, expected #{type.inspect}" unless actual_type == type
end
compiler_code = compile_node&.dig('data', 'code').to_s
unless compiler_code.include?("validator_route") && compiler_code.include?("normalized_plan_json") && compiler_code.include?("legacy_unvalidated_plan_fallback")
  errors << '1775000000007 must select validator normalized plan first and expose legacy_unvalidated_plan_fallback debug marker'
end
if compiler_code.include?("stage_time_range = _force_relative_month_time_range(question") || compiler_code.include?("intent = _detect_visualization_intent(question")
  errors << '1775000000007 compiler path still performs question-based time/chart semantic inference'
end

edge_pairs = edges.map { |edge| [edge['source'].to_s, edge['sourceHandle'].to_s, edge['target'].to_s] }.to_set
{
  ['1775000000012', 'source', '1775000000016'] => 'full LLM response must be checked before extraction/fallback',
  ['1775000000017', 'true', '1775000000013'] => 'successful full LLM path must enter extraction',
  ['1775000000018', 'source', '1775000000019'] => 'local fallback LLM must be wrapped',
  ['1775000000019', 'source', '1775000000013'] => 'local fallback body must enter extraction',
  ['1775000000013', 'source', '1775000000020'] => 'extraction must enter plan_valid gate',
  ['1775000000020', 'true', '1776000000024'] => 'valid new-query plans must enter unified Mongo input preparation',
  ['1776000000024', 'source', '1779000000001'] => 'prepared normalized plans must enter Phase 9 runtime schema context before validator',
  ['1779000000001', 'source', '1780000000001'] => 'Phase 10 runtime schema context must enter schema ready/hydration route',
  ['1780000000001', 'schema_ready', '1780000000009'] => 'schema ready route must select final validator runtime context',
  ['1780000000001', 'schema_hydration_needed', '1780000000002'] => 'schema hydration route must build validator schema hydration retrieval tasks',
  ['1780000000001', 'false', '1780000000009'] => 'missing/invalid schema context route must still enter validator through final runtime context',
  ['1780000000002', 'source', '1780000000003'] => 'hydration retrieval tasks must enter schema retrieval iteration',
  ['1780000000003', 'source', '1780000000007'] => 'hydrated schema retrieval output must be merged and parsed before validator',
  ['1780000000007', 'source', '1780000000008'] => 'hydrated schema metadata/alias must be prepared as validator runtime context',
  ['1780000000008', 'source', '1780000000009'] => 'hydrated runtime context must enter final validator runtime selector',
  ['1780000000009', 'source', '1778000000001'] => 'final validator runtime schema context must enter validator',
  ['1778000000001', 'source', '1778000000002'] => 'validator result must enter validator route branch',
  ['1778000000002', 'valid', '1775000000007'] => 'only validator valid branch may enter compiler',
  ['1778000000002', 'requires_clarification', '1778000000004'] => 'validator clarification branch must save context without compiler',
  ['1778000000002', 'blocked', '1778000000004'] => 'validator blocked branch must save context without compiler',
  ['1778000000002', 'needs_replan', '1778000000004'] => 'validator needs_replan branch must save context without compiler',
  ['1778000000002', 'false', '1778000000004'] => 'validator invalid/default branch must save context without compiler',
  ['1778000000004', 'source', '1778000000003'] => 'validator non-valid context save must continue to answer',
  ['1776000000021', 'source', '1776000000024'] => 'guarded refine replan must enter unified Mongo input preparation'
}.each do |pair, message|
  errors << "missing edge #{pair.join(' -> ')} (#{message})" unless edge_pairs.include?(pair)
end

if edge_pairs.include?(['1775000000013', 'source', '1775000000007'])
  errors << '1775000000013 still connects directly to 1775000000007 without unified preparation'
end
if edge_pairs.include?(['1775000000020', 'true', '1775000000007'])
  errors << '1775000000020 true branch still bypasses unified preparation and enters compiler directly'
end
if edge_pairs.include?(['1776000000024', 'source', '1778000000001'])
  errors << '1776000000024 still connects directly to validator without Phase 9/10 runtime schema context'
end
if edge_pairs.include?(['1779000000001', 'source', '1778000000001'])
  errors << '1779000000001 still connects directly to validator without Phase 10 schema ready/hydration route'
end
# Phase 9 guardrails: full schema/alias may be carried at runtime but must not be
# persisted back into compact conversation context or validator non-valid context updates.
save_node = node_by_id['1775000000023']
(save_node&.dig('data', 'items') || []).each do |item|
  selector = item['variable_selector'] || []
  value = item['value'] || []
  if selector == ['conversation', 'last_schema_metadata_json'] || selector == ['conversation', 'last_schema_alias_index_json']
    errors << "1775000000023 persists full schema runtime #{value.inspect} into #{selector.inspect}, forbidden in Phase 9"
  end
end
nonvalid_save = node_by_id['1778000000004']
nonvalid_ctx_item = (nonvalid_save&.dig('data', 'items') || []).find { |item| item['variable_selector'] == ['conversation', 'last_context_update_json'] }
unless nonvalid_ctx_item && nonvalid_ctx_item['value'] == ['1778000000001', 'context_update_json']
  errors << "1778000000004 last_context_update_json must use validator context_update_json minimal summary, got #{nonvalid_ctx_item&.dig('value').inspect}"
end


# Phase 10 schema hydration static guardrails.
phase10_nodes = {
  '1780000000001' => '条件分支_schema_runtime_context判断',
  '1780000000002' => '代码执行_构建ValidatorSchemaHydration检索任务',
  '1780000000003' => '遍历Collections检索Schema_Hydration',
  '1780000000007' => '代码执行_合并HydratedSchema上下文并解析metadata',
  '1780000000008' => '代码执行_准备HydratedValidator运行时Schema上下文',
  '1780000000009' => '代码执行_选择最终Validator运行时Schema上下文'
}
phase10_nodes.each do |id, title|
  node = node_by_id[id]
  errors << "required Phase 10 node #{id} is missing" if node.nil?
  errors << "#{id} title is #{node.dig('data', 'title').inspect}, expected #{title}" if node && node.dig('data', 'title') != title
end

{
  'hydration_collection_tasks' => 'array[object]',
  'hydration_collections' => 'array[string]',
  'hydration_reason' => 'string',
  'hydration_task_valid' => 'boolean',
  'retrieval_mode' => 'string'
}.each do |field, type|
  actual_type = outputs_by_id.dig('1780000000002', field, 'type')
  errors << "1780000000002 output #{field} type is #{actual_type.inspect}, expected #{type.inspect}" unless actual_type == type
end
{
  'hydrated_schema_metadata_json' => 'string',
  'hydrated_schema_alias_index_json' => 'string',
  'hydrated_schema_digest' => 'string',
  'hydrated_schema_version' => 'string',
  'hydrated_schema_context_ref_json' => 'string',
  'hydration_success' => 'boolean'
}.each do |field, type|
  actual_type = outputs_by_id.dig('1780000000007', field, 'type')
  errors << "1780000000007 output #{field} type is #{actual_type.inspect}, expected #{type.inspect}" unless actual_type == type
end
%w[1780000000008 1780000000009].each do |id|
  %w[schema_runtime_context_json schema_metadata_json schema_alias_index_json schema_source].each do |field|
    actual_type = outputs_by_id.dig(id, field, 'type')
    errors << "#{id} output #{field} is missing" unless actual_type
  end
end

validator_vars = validator_node&.dig('data', 'variables') || []
{
  'schema_metadata_json' => ['1780000000009', 'schema_metadata_json'],
  'schema_alias_index_json' => ['1780000000009', 'schema_alias_index_json'],
  'schema_runtime_context_json' => ['1780000000009', 'schema_runtime_context_json']
}.each do |var, expected|
  actual = validator_vars.find { |v| v['variable'] == var }&.dig('value_selector')
  errors << "1778000000001 #{var} selector is #{actual.inspect}, expected #{expected.inspect}" unless actual == expected
end

adj = Hash.new { |h, k| h[k] = [] }
edges.each { |edge| adj[edge['source'].to_s] << [edge['target'].to_s, edge['sourceHandle'].to_s] }
reachable = lambda do |start, forbidden_handles = Set.new|
  seen = Set.new
  queue = [start]
  until queue.empty?
    cur = queue.shift
    next if seen.include?(cur)
    seen << cur
    adj[cur].each do |target, handle|
      next if forbidden_handles.include?([cur, handle])
      queue << target unless seen.include?(target)
    end
  end
  seen
end
hydration_reachable = reachable.call('1780000000002')
%w[1773975766025 1776000000020 1777000000002].each do |planner_id|
  errors << "Phase 10 hydration path can reach planner node #{planner_id}, forbidden" if hydration_reachable.include?(planner_id)
end
errors << 'Phase 10 hydration path cannot reach validator' unless hydration_reachable.include?('1778000000001')
compiler_predecessors = edges.select { |edge| edge['target'].to_s == '1775000000007' }.map { |edge| [edge['source'].to_s, edge['sourceHandle'].to_s] }
unless compiler_predecessors == [['1778000000002', 'valid']]
  errors << "compiler predecessors are #{compiler_predecessors.inspect}, expected only validator valid"
end
nonvalid_handles = %w[requires_clarification blocked needs_replan false]
nonvalid_handles.each do |handle|
  if edge_pairs.include?(['1778000000002', handle, '1775000000007'])
    errors << "validator non-valid handle #{handle} enters compiler, forbidden"
  end
end

forbidden_persist_fields = %w[schema_metadata_json schema_alias_index_json hydrated_schema_metadata_json hydrated_schema_alias_index_json schema_runtime_context_json]
nodes.each do |node|
  title = node.dig('data', 'title').to_s
  next unless title.include?('保存多轮上下文') || node.dig('data', 'type').to_s == 'assigner'
  serialized = node.to_s
  forbidden_persist_fields.each do |field|
    if serialized.include?(field) && serialized.include?('conversation')
      errors << "#{node['id']} appears to persist full runtime schema field #{field} into conversation/compact context"
    end
  end
end

compact_saver = nodes.find { |node| node.dig('data', 'code').to_s.include?('BANNED_CONTEXT_KEYS') && node.dig('data', 'code').to_s.include?('compact_context_contract') }
compact_code = compact_saver&.dig('data', 'code').to_s
%w[pipeline_json request_body_json compiler_debug_json schema_metadata schema_alias_index schema_context full_rows].each do |token|
  unless compact_code.include?(token)
    errors << "compact context saver BANNED_CONTEXT_KEYS must include #{token}"
  end
end
if compact_code.include?("'last_pipeline_json'") || compact_code.include?("last_pipeline_json") && !compact_code.include?('BANNED_CONTEXT_KEYS')
  errors << 'compact context saver appears to persist last_pipeline_json/full pipeline'
end



# Phase 12 chart payload contract guardrails.
chart_builder_ids = nodes.select { |node| node.dig('data', 'title').to_s == '代码执行_ChartPayloadBuilder' }.map { |node| node['id'].to_s }
if chart_builder_ids.empty?
  errors << 'Phase 12 requires at least one 代码执行_ChartPayloadBuilder node'
end
%w[1781000000001 1776000000016].each do |id|
  unless chart_builder_ids.include?(id)
    errors << "Phase 12 expected #{id} to be titled 代码执行_ChartPayloadBuilder"
  end
  %w[chart_payload_json chart_payload_contract chart_echarts_markdown chart_warning chart_enabled data_profile_json].each do |field|
    errors << "#{id} output #{field} is missing for Phase 12" unless outputs_by_id.dig(id, field)
  end
  code = node_by_id[id]&.dig('data', 'code').to_s
  errors << "#{id} must contain chart_payload_contract" unless code.include?('chart_payload_contract')
  errors << "#{id} must not use Mongo/API request execution" if code.match?(/requests\.|httpx|pymongo|MongoClient|query_urls|request_body_json|pipeline_json/)
  errors << "#{id} must not resolve aliases or business field names" if code.match?(/alias_index|schema_alias|field_alias|metric_alias|group_fields/)
end

unless outputs_by_id.dig('1781000000001', 'chart_payload_json')
  errors << 'normal query ChartPayloadBuilder must output chart_payload_json'
end
unless edge_pairs.include?(['1775000000007', 'source', '1781000000001']) && edge_pairs.include?(['1781000000001', 'source', '1775000000022'])
  errors << 'Phase 12 normal valid query path must be compiler -> ChartPayloadBuilder -> final answer merge'
end
if edge_pairs.include?(['1775000000007', 'source', '1775000000022'])
  errors << 'Phase 12 compiler must not bypass ChartPayloadBuilder into final answer merge'
end
unless edge_pairs.include?(['1776000000015', 'source', '1776000000016']) && edge_pairs.include?(['1776000000016', 'source', '1776000000006'])
  errors << 'Phase 12 chart-only path must enter ChartPayloadBuilder before chart answer adapter'
end

phase12_forbidden_targets = %w[1773975766025 1776000000020 1777000000002 1779000000001 1780000000002 1780000000003 1780000000007 1780000000008 1780000000009]
chart_builder_ids.each do |id|
  reached = reachable.call(id)
  phase12_forbidden_targets.each do |target_id|
    errors << "Phase 12 ChartPayloadBuilder #{id} can reach forbidden planner/schema node #{target_id}" if reached.include?(target_id)
  end
end

merge_node = node_by_id['1775000000022']
merge_code = merge_node&.dig('data', 'code').to_s
merge_vars = merge_node&.dig('data', 'variables') || []
unless merge_vars.any? { |var| var['variable'] == 'chart_payload_json' && var['value_selector'] == ['1781000000001', 'chart_payload_json'] }
  errors << 'final answer merge must read chart_payload_json from normal ChartPayloadBuilder'
end
unless merge_code.include?('chart_payload_json') && merge_code.include?("chart_payload.get('echarts_markdown')")
  errors << 'final answer merge must prefer chart_payload_json / chart_payload.echarts_markdown'
end
unless merge_code.include?('chart_echarts_markdown')
  errors << 'final answer merge must retain old chart_echarts_markdown fallback'
end

compact_serialized = compact_saver.to_s
%w[chart_payload.option echarts option echarts_markdown full_rows compiler_debug_json pipeline_json request_body_json].each do |token|
  errors << "compact context must not persist #{token}" if compact_serialized.include?("'#{token}' =>") || compact_serialized.include?("\"#{token}\" =>")
end
save_node = node_by_id['1775000000023']
(save_node&.dig('data', 'items') || []).each do |item|
  selector = item['variable_selector'] || []
  value = item['value'] || []
  if selector == ['conversation', 'last_chart_echarts_markdown'] && value != ['1781000000001', 'chart_summary']
    errors << "Phase 12 must not persist full echarts markdown; last_chart_echarts_markdown selector is #{value.inspect}"
  end
  if selector == ['conversation', 'last_query_result_json'] && value != ['1775000000007', 'query_result_json']
    errors << "Phase 12 last_query_result_json must use query_result_json contract, got #{value.inspect}"
  end
end



# Phase 13 final answer/context saver contract guardrails.
merge_node = node_by_id['1775000000022']
merge_code = merge_node&.dig('data', 'code').to_s
%w[answer_payload_contract context_update_contract compact_context_contract].each do |token|
  errors << "Phase 13 final answer merge must contain #{token}" unless merge_code.include?(token)
end
%w[answer_payload_json answer_payload_markdown answer_text chart_echarts_markdown context_update_json compact_context_json result_summary].each do |field|
  errors << "1775000000022 output #{field} is missing for Phase 13" unless outputs_by_id.dig('1775000000022', field)
end
%w[query_with_chart query_only empty_result execution_error system_error].each do |answer_type|
  errors << "Phase 13 merge must handle #{answer_type}" unless merge_code.include?(answer_type)
end
unless merge_code.include?("'chart_payload':") && merge_code.include?("'query_result_profile':") && merge_code.include?("'state_update':")
  errors << 'Phase 13 answer_payload_json must use stable chart_payload/query_result_profile/state_update fields'
end
%w[chart_debug compiler_debug validator_debug].each do |token|
  if merge_code.match?(/answer_payload.*#{token}/m) && !merge_code.include?("out.pop('#{token}', None)")
    errors << "answer_payload_json must not expose #{token}"
  end
end

main_answer = node_by_id['1773910028939']
unless main_answer&.dig('data', 'answer').to_s.include?('1775000000022.answer_payload_markdown')
  errors << 'Final Answer must primarily read answer_payload_markdown from 1775000000022'
end
if main_answer&.dig('data', 'answer').to_s.match?(/rows_json|merged_rows_json|pipeline_json|compiler_debug|schema_metadata|chart_payload\.option/)
  errors << 'Final Answer must not directly read rows/pipeline/schema/debug/chart option fields'
end

save_node = node_by_id['1775000000023']
save_items = save_node&.dig('data', 'items') || []
expected_save_order = [
  [['conversation', 'last_context_json'], ['1775000000022', 'compact_context_json']],
  [['conversation', 'last_answer_payload_json'], ['1775000000022', 'answer_payload_json']],
  [['conversation', 'last_context_update_json'], ['1775000000022', 'context_update_json']]
]
expected_save_order.each do |selector, expected_value|
  item = save_items.find { |it| it['variable_selector'] == selector }
  errors << "Phase 13 save node missing #{selector.inspect}" unless item
  errors << "Phase 13 #{selector.inspect} must use #{expected_value.inspect}, got #{item&.dig('value').inspect}" unless item&.dig('value') == expected_value
end
save_serialized = save_node.to_s
%w[rows_json merged_rows_json pipeline_json request_body_json compiler_debug_json schema_metadata_json schema_alias_index_json].each do |token|
  errors << "Phase 13 context saver must not persist full #{token}" if save_serialized.include?(token)
end



# Phase 14 legacy cleanup / complete-C path guardrails.
phase14_forbidden_compiler_sources = edges.select { |edge| edge['target'].to_s == '1775000000007' }.reject { |edge| edge['source'].to_s == '1778000000002' && edge['sourceHandle'].to_s == 'valid' }
phase14_forbidden_compiler_sources.each do |edge|
  errors << "Phase 14 compiler input must come only from validator valid; found #{edge['source']}[#{edge['sourceHandle']}] -> 1775000000007"
end
%w[1776000000006 1776000000009 1776000000015 1776000000016].each do |id|
  reached = reachable.call(id)
  %w[1775000000007 1773975766025 1775000000012 1776000000020 1776000000024 1777000000002 1779000000001 1780000000002 1780000000003 1780000000007 1780000000008 1780000000009].each do |target_id|
    errors << "Phase 14 chart-only path node #{id} can reach forbidden compiler/planner/schema node #{target_id}" if reached.include?(target_id)
  end
end
%w[1775000000022 1778000000001 1776000000006].each do |id|
  code = node_by_id[id]&.dig('data', 'code').to_s
  errors << "Phase 14 answer payload node #{id} must not expose debug_summary" if code.include?('debug_summary')
end
if merge_node
  unless (merge_node.dig('data', 'variables') || []).any? { |var| var['variable'] == 'last_context_json' && var['value_selector'] == ['conversation', 'last_context_json'] }
    errors << 'Phase 14 final answer merge must read prior last_context_json so execution_error does not pollute the previous successful context'
  end
  unless merge_code.include?("answer_type == 'execution_error'") && merge_code.include?('last_context_json') && merge_code.include?('隐藏内部错误细节')
    errors << 'Phase 14 execution_error path must preserve prior compact context and sanitize internal execution details'
  end
end
[merge_node, node_by_id['1778000000001'], node_by_id['1776000000006']].compact.each do |node|
  code = node.dig('data', 'code').to_s
  unless code.include?('BANNED_CONTEXT_KEYS') || code.include?('compact_context_contract')
    errors << "Phase 14 compact context producer #{node['id']} must declare/use compact_context_contract boundary"
  end
end
save_items.each do |item|
  selector = item['variable_selector'] || []
  value = item['value'] || []
  if selector == ['conversation', 'last_context_json'] && value != ['1775000000022', 'compact_context_json']
    errors << "Phase 14 saver must write last_context_json from final compact_context_json, got #{value.inspect}"
  end
  if selector == ['conversation', 'last_answer_payload_json'] && value != ['1775000000022', 'answer_payload_json']
    errors << "Phase 14 saver must write last_answer_payload_json from answer_payload_json, got #{value.inspect}"
  end
  if selector == ['conversation', 'last_context_update_json'] && value != ['1775000000022', 'context_update_json']
    errors << "Phase 14 saver must write last_context_update_json from context_update_json, got #{value.inspect}"
  end
end
legacy_reader_writer_nodes = nodes.select { |node| node.to_s.match?(/execution_plan|normalized_execution_plan|merged_rows_json|chart_echarts_markdown|echarts_option|schema_metadata_json|schema_alias_index_json/) }
legacy_reader_writer_nodes.each do |node|
  code = node.dig('data', 'code').to_s
  next unless code.include?('execution_plan') || code.include?('merged_rows_json') || code.include?('chart_echarts_markdown')
  if code.match?(/legacy|fallback|compat|BANNED_CONTEXT_KEYS|chart_payload_json|answer_payload_json/).nil?
    warnings << "Phase 14 legacy reference in node #{node['id']} (#{node.dig('data', 'title')}) is not visibly marked compat/fallback"
  end
end

# Non-valid validator branch must also emit stable answer/context payloads and must not enter compiler.
validator_code = node_by_id['1778000000001']&.dig('data', 'code').to_s
%w[answer_payload_contract context_update_contract compact_context_contract answer_payload_markdown compact_context_json].each do |token|
  errors << "Phase 13 validator non-valid contract missing #{token}" unless validator_code.include?(token) || outputs_by_id.dig('1778000000001', token)
end
%w[answer_payload_json context_update_json answer_payload_markdown compact_context_json].each do |field|
  errors << "1778000000001 output #{field} is missing for Phase 13 non-valid branch" unless outputs_by_id.dig('1778000000001', field)
end
unless node_by_id['1778000000003']&.dig('data', 'answer').to_s.include?('1778000000001.answer_payload_markdown')
  errors << 'validator non-valid Answer must read answer_payload_markdown'
end
nonvalid_save = node_by_id['1778000000004']
%w[last_answer_payload_json last_context_update_json last_context_json].each do |field|
  unless (nonvalid_save&.dig('data', 'items') || []).any? { |item| item['variable_selector'] == ['conversation', field] }
    errors << "validator non-valid save node must persist #{field}"
  end
end

# Chart-only adapter must produce answer_payload_contract and preserve previous query plan fields.
chart_adapter = node_by_id['1776000000006']
chart_adapter_code = chart_adapter&.dig('data', 'code').to_s
%w[answer_payload_contract context_update_contract compact_context_contract chart_only answer_payload_markdown answer_payload_json context_update_json compact_context_json].each do |token|
  errors << "Phase 13 chart-only adapter missing #{token}" unless chart_adapter_code.include?(token) || outputs_by_id.dig('1776000000006', token)
end
unless chart_adapter_code.include?('prior_plan') && chart_adapter_code.include?('prior_primary') && chart_adapter_code.include?('prior_schema')
  errors << 'chart-only adapter must preserve last_plan_summary, last_primary_collection, and schema_context_ref'
end
unless node_by_id['1776000000009']&.dig('data', 'answer').to_s.include?('1776000000006.answer_payload_markdown')
  errors << 'chart-only Answer must read answer_payload_markdown'
end

nodes.select { |node| node.dig('data', 'type').to_s == 'answer' }.each do |node|
  unless node.dig('data', 'answer').to_s.include?('answer_payload_markdown')
    errors << "Phase 13 answer node #{node['id']} (#{node.dig('data', 'title')}) must prioritize answer_payload_markdown"
  end
end

# Compact context / last_context_json boundary: code may list banned keys, but no saver should write full banned fields.
compact_boundary_tokens = %w[schema_metadata_json schema_alias_index_json schema_runtime_context_json hydrated_schema_metadata_json hydrated_schema_alias_index_json raw_schema_docs raw_llm_response full_prompt pipeline_json request_body_json query_response_body query_response_headers compiler_debug_json validator_debug chart_debug stage_execution_trace full_rows rows_json merged_rows_json echarts_markdown chart_payload.option]
[save_node, nonvalid_save, chart_adapter].compact.each do |node|
  serialized = node.to_s
  compact_boundary_tokens.each do |token|
    next if node == chart_adapter && chart_adapter_code.include?('BANNED_CONTEXT_KEYS')
    if serialized.match?(/variable_selector.*conversation/m) && serialized.include?(token) && !serialized.include?('last_context_json')
      errors << "#{node['id']} may persist banned compact-context token #{token}"
    end
  end
end

incoming_counts = Hash.new(0)
outgoing_counts = Hash.new(0)
edges.each do |edge|
  outgoing_counts[edge['source'].to_s] += 1
  incoming_counts[edge['target'].to_s] += 1
end

start_like_types = Set.new(%w[start iteration-start])
terminal_types = Set.new(%w[answer end])
iteration_parent_ids = nodes.select { |node| node.dig('data', 'type').to_s == 'iteration' }.map { |node| node['id'].to_s }.to_set

nodes.each do |node|
  id = node['id'].to_s
  type = node.dig('data', 'type').to_s
  parent_id = node['parentId'].to_s
  inside_iteration = iteration_parent_ids.include?(parent_id)
  next if inside_iteration || type == 'iteration-start'
  if incoming_counts[id].zero? && !start_like_types.include?(type)
    errors << "node #{id} (#{node.dig('data', 'title')}) has no incoming edge"
  end
  if outgoing_counts[id].zero? && !terminal_types.include?(type)
    errors << "node #{id} (#{node.dig('data', 'title')}) has no outgoing edge"
  end
end

answer_refs = []
nodes.select { |node| node.dig('data', 'type').to_s == 'answer' }.each do |node|
  answer_text = node.dig('data', 'answer').to_s
  answer_text.scan(/\{\{#([^#.]+)\.([^#]+)#\}\}/).each do |source_id, field|
    next if %w[sys env conversation].include?(source_id)
    answer_refs << [node['id'].to_s, source_id.to_s, field.to_s]
  end
end
answer_refs.each do |answer_id, source_id, field|
  unless node_id_set.include?(source_id)
    errors << "answer #{answer_id} references missing node #{source_id}"
    next
  end
  declared_outputs = outputs_by_id[source_id]
  next if declared_outputs.empty?
  errors << "answer #{answer_id} references missing output #{source_id}.#{field}" unless declared_outputs.key?(field)
end

# Phase 15 display-only layout guardrails. These checks intentionally validate only
# canvas readability fields and do not change workflow semantics.
NODE_LAYOUT_SIZES = {
  'start' => [240, 120],
  'answer' => [240, 120],
  'if-else' => [260, 160],
  'condition' => [260, 160],
  'llm' => [320, 180],
  'code' => [360, 220],
  'iteration' => [520, 360],
  'knowledge-retrieval' => [320, 180]
}.freeze
DEFAULT_NODE_LAYOUT_SIZE = [300, 160].freeze
layout_missing = []
coordinate_groups = Hash.new { |hash, key| hash[key] = [] }
node_layout = {}

nodes.each do |node|
  id = node['id'].to_s
  position = node['positionAbsolute'].is_a?(Hash) ? node['positionAbsolute'] : node['position']
  if !position.is_a?(Hash) || position['x'].nil? || position['y'].nil?
    layout_missing << id
    next
  end
  x = position['x'].to_f
  y = position['y'].to_f
  width, height = NODE_LAYOUT_SIZES.fetch(node.dig('data', 'type').to_s, DEFAULT_NODE_LAYOUT_SIZE)
  node_layout[id] = {
    'node' => node,
    'x' => x,
    'y' => y,
    'width' => width.to_f,
    'height' => height.to_f,
    'parent_id' => node['parentId'].to_s
  }
  coordinate_groups[[x, y]] << node
end

errors << "nodes missing position: #{layout_missing.join(', ')}" unless layout_missing.empty?
duplicate_coordinates = coordinate_groups.select { |_coord, grouped_nodes| grouped_nodes.length > 1 }
unless duplicate_coordinates.empty?
  duplicate_messages = duplicate_coordinates.map do |coord, grouped_nodes|
    "#{coord.inspect}=#{grouped_nodes.map { |node| "#{node['id']}(#{node.dig('data', 'title')})" }.join('|')}"
  end
  errors << "duplicate node coordinates: #{duplicate_messages.join('; ')}"
end

layout_overlaps = []
iteration_internal_overlaps = []
node_layout.values.combination(2) do |a, b|
  a_id = a['node']['id'].to_s
  b_id = b['node']['id'].to_s
  # Parent iteration containers are expected to visually contain their children.
  next if a_id == b['parent_id'] || b_id == a['parent_id']

  overlaps = a['x'] < b['x'] + b['width'] && a['x'] + a['width'] > b['x'] &&
             a['y'] < b['y'] + b['height'] && a['y'] + a['height'] > b['y']
  next unless overlaps

  pair = "#{a_id}(#{a['node'].dig('data', 'title')}) <-> #{b_id}(#{b['node'].dig('data', 'title')})"
  if !a['parent_id'].empty? && a['parent_id'] == b['parent_id']
    iteration_internal_overlaps << pair
  else
    layout_overlaps << pair
  end
end
warnings << "layout overlap pairs: #{layout_overlaps.join('; ')}" unless layout_overlaps.empty?
errors << "iteration internal layout overlaps: #{iteration_internal_overlaps.join('; ')}" unless iteration_internal_overlaps.empty?

phase15_lane_sequences = {
  'turn_intent' => %w[1773909192919 1776000000001 1776000000002 1776000000003 1776000000004 1776000000013 1776000000005],
  'new_query' => %w[1777000000001 1773975766025 1774083951307 1775000000001 1775000000005 1775000000014 1775000000013 1775000000020],
  'refine_full_replan' => %w[1777000000002 1776000000030 1776000000025 1776000000032 1776000000026 1776000000027 1776000000020 1776000000028 1776000000021],
  'runtime_validator_success' => %w[1776000000024 1779000000001 1780000000001 1780000000009 1778000000001 1778000000002 1775000000007 1781000000001 1775000000022 1775000000023 1773910028939],
  'chart_only' => %w[1776000000014 1776000000015 1776000000016 1776000000006 1776000000009],
  'non_valid' => %w[1778000000004 1778000000003]
}
phase15_lane_sequences.each do |lane, ids|
  xs = ids.filter_map { |id| node_layout.dig(id, 'x') }
  next unless xs.length == ids.length
  unless xs.each_cons(2).all? { |left, right| right > left }
    warnings << "layout lane #{lane} is not strictly left-to-right: #{ids.zip(xs).inspect}"
  end
end

puts "Manifest: #{MANIFEST_PATH}"
puts "Parts: #{parts.join(', ')}"
puts "Nodes: #{nodes.length}"
puts "Edges: #{edges.length}"
puts "Warnings: #{warnings.length}"
puts "Layout: missing=#{layout_missing.length} duplicate_coordinates=#{duplicate_coordinates.length} overlaps=#{layout_overlaps.length} iteration_internal_overlaps=#{iteration_internal_overlaps.length}"
warnings.each { |warning| warn "WARNING: #{warning}" }

if errors.empty?
  puts 'Validation passed: assembled graph has no dangling node selectors or edges for checked outputs.'
else
  warn 'Validation failed:'
  errors.each { |error| warn "- #{error}" }
  exit 1
end
