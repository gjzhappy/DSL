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
  errors << "#{path} has #{line_count} lines, exceeds #{MAX_SOURCE_PART_LINES}" if line_count > MAX_SOURCE_PART_LINES
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

compile_node = node_by_id['1775000000007']
compile_vars = compile_node&.dig('data', 'variables') || []
compile_semantic_selector = compile_vars.find { |var| var['variable'] == 'semantic_plan' }&.dig('value_selector')
unless compile_semantic_selector == ['1775000000013', 'normalized_semantic_plan_json']
  errors << "1775000000007 semantic_plan selector is #{compile_semantic_selector.inspect}, expected ['1775000000013', 'normalized_semantic_plan_json']"
end

edge_pairs = edges.map { |edge| [edge['source'].to_s, edge['sourceHandle'].to_s, edge['target'].to_s] }.to_set
{
  ['1775000000012', 'source', '1775000000016'] => 'full LLM response must be checked before extraction/fallback',
  ['1775000000017', 'true', '1775000000013'] => 'successful full LLM path must enter extraction',
  ['1775000000018', 'source', '1775000000019'] => 'local fallback LLM must be wrapped',
  ['1775000000019', 'source', '1775000000013'] => 'local fallback body must enter extraction',
  ['1775000000013', 'source', '1775000000020'] => 'extraction must enter plan_valid gate',
  ['1775000000020', 'true', '1775000000007'] => 'only valid plans may enter compiler'
}.each do |pair, message|
  errors << "missing edge #{pair.join(' -> ')} (#{message})" unless edge_pairs.include?(pair)
end

if edge_pairs.include?(['1775000000013', 'source', '1775000000007'])
  errors << '1775000000013 still connects directly to 1775000000007 without plan_valid gate'
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
