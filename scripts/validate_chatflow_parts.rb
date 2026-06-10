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
  'final_answer_markdown' => 'string'
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
validator_branch = node_by_id['1778000000002']
errors << 'required Phase 8 validator route branch 1778000000002 is missing' if validator_branch.nil?
if validator_branch && validator_branch.dig('data', 'title') != '条件分支_validator结果判断'
  errors << "1778000000002 title is #{validator_branch.dig('data', 'title').inspect}, expected 条件分支_validator结果判断"
end

compile_node = node_by_id['1775000000007']
compile_vars = compile_node&.dig('data', 'variables') || []
compile_semantic_selector = compile_vars.find { |var| var['variable'] == 'semantic_plan' }&.dig('value_selector')
unless compile_semantic_selector == ['1778000000001', 'normalized_semantic_plan_json']
  errors << "1775000000007 semantic_plan selector is #{compile_semantic_selector.inspect}, expected ['1778000000001', 'normalized_semantic_plan_json']"
end

edge_pairs = edges.map { |edge| [edge['source'].to_s, edge['sourceHandle'].to_s, edge['target'].to_s] }.to_set
{
  ['1775000000012', 'source', '1775000000016'] => 'full LLM response must be checked before extraction/fallback',
  ['1775000000017', 'true', '1775000000013'] => 'successful full LLM path must enter extraction',
  ['1775000000018', 'source', '1775000000019'] => 'local fallback LLM must be wrapped',
  ['1775000000019', 'source', '1775000000013'] => 'local fallback body must enter extraction',
  ['1775000000013', 'source', '1775000000020'] => 'extraction must enter plan_valid gate',
  ['1775000000020', 'true', '1776000000024'] => 'valid new-query plans must enter unified Mongo input preparation',
  ['1776000000024', 'source', '1778000000001'] => 'prepared normalized plans must enter Phase 8 validator before compiler',
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
#{insert}
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

puts "Manifest: #{MANIFEST_PATH}"
puts "Parts: #{parts.join(', ')}"
puts "Nodes: #{nodes.length}"
puts "Edges: #{edges.length}"
puts "Warnings: #{warnings.length}"
warnings.each { |warning| warn "WARNING: #{warning}" }

if errors.empty?
  puts 'Validation passed: assembled graph has no dangling node selectors or edges for checked outputs.'
else
  warn 'Validation failed:'
  errors.each { |error| warn "- #{error}" }
  exit 1
end
