#!/usr/bin/env ruby
# frozen_string_literal: true

require 'yaml'
require 'set'

ROOT = File.expand_path('..', __dir__)
DEFAULT_MANIFEST = File.join(ROOT, 'NL2MGQL_CHATFLOW_DSL', 'NL2MGQL_CHATFLOW.manifest.yml')
MANIFEST_PATH = ARGV[0] && ARGV[0] != '--report' ? File.expand_path(ARGV[0], Dir.pwd) : DEFAULT_MANIFEST
REPORT = ARGV.include?('--report')
MAX_SOURCE_PART_LINES = 1000
REQUIRED_NODES = {
  '1773909192919' => '用户输入',
  '1775000000005' => '代码执行_合并Schema上下文并准备语义计划提示词',
  '1775000000011' => '代码执行_构造满血LLM语义计划请求',
  '1775000000012' => '代码执行_调用满血LLM并保留错误响应',
  '1775000000013' => '代码执行_提取语义计划结果',
  '1775000000007' => '代码执行_遍历语义计划并统一编译执行',
  '1775000000018' => 'LLM_本地兜底生成语义计划',
  '1773910028939' => 'Answer'
}.freeze
REQUIRED_EXTRACT_OUTPUTS = {
  'normalized_semantic_plan_json' => 'string',
  'semantic_plan_json' => 'string',
  'plan_valid' => 'boolean',
  'used_local_llm' => 'boolean',
  'normalizer_warning' => 'string',
  'normalizer_error' => 'string',
  'debug_summary_json' => 'string'
}.freeze
FORBIDDEN_EXACT = %w[semantic_execution_plan_v2 ast_version legacy_ast old_ast FULL_LLM_LEGACY].freeze

errors = []
warnings = []

abort "Manifest not found: #{MANIFEST_PATH}" unless File.file?(MANIFEST_PATH)
manifest = YAML.load_file(MANIFEST_PATH)
parts = manifest.fetch('parts')
manifest_dir = File.dirname(MANIFEST_PATH)
part_paths = parts.map { |part| File.expand_path(part, manifest_dir) }
part_texts = part_paths.to_h { |path| [path, File.file?(path) ? File.read(path) : ''] }

part_paths.each do |path|
  unless File.file?(path)
    errors << "missing part: #{path}"
    next
  end
  line_count = File.foreach(path).count
  errors << "#{path} has #{line_count} lines, exceeds #{MAX_SOURCE_PART_LINES}" if line_count > MAX_SOURCE_PART_LINES
  FORBIDDEN_EXACT.each do |term|
    errors << "#{path} contains forbidden protocol term #{term}" if part_texts[path].include?(term)
  end
end

assembled = part_paths.map { |path| part_texts[path] }.join
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
  next if declared_outputs.empty?
  errors << "selector #{selector.inspect} at #{path.join('.')} references missing output #{field}" unless declared_outputs.key?(field)
end

REQUIRED_NODES.each do |id, expected_title|
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
  ['1773909192919', 'source', '1773975766025'] => 'start must enter planner',
  ['1775000000001', 'source', '1775000000005'] => 'schema retrieval must feed prompt builder',
  ['1775000000005', 'source', '1775000000014'] => 'prompt builder must enter full/local routing',
  ['1775000000010', 'source', '1775000000011'] => 'token response must build full LLM request',
  ['1775000000012', 'source', '1775000000016'] => 'full LLM response must be checked before extraction/fallback',
  ['1775000000017', 'true', '1775000000013'] => 'successful full LLM path must enter extraction',
  ['1775000000017', 'false', '1775000000018'] => 'failed full LLM path must enter fallback',
  ['1775000000020', 'fallback', '1775000000018'] => 'invalid full semantic plan must enter fallback once',
  ['1775000000018', 'source', '1775000000019'] => 'local fallback LLM must be wrapped',
  ['1775000000019', 'source', '1775000000013'] => 'local fallback body must enter extraction',
  ['1775000000013', 'source', '1775000000020'] => 'extraction must enter plan_valid gate',
  ['1775000000020', 'true', '1775000000007'] => 'only valid plans may enter compiler',
  ['1775000000007', 'source', '1773910028939'] => 'compiler must feed answer'
}.each do |pair, message|
  errors << "missing edge #{pair.join(' -> ')} (#{message})" unless edge_pairs.include?(pair)
end
errors << '1775000000013 still connects directly to 1775000000007 without plan_valid gate' if edge_pairs.include?(['1775000000013', 'source', '1775000000007'])

connected = Set.new
edges.each { |edge| connected << edge['source'].to_s << edge['target'].to_s }
critical_isolated = REQUIRED_NODES.keys.select { |id| node_id_set.include?(id) && !connected.include?(id) }
errors << "critical nodes without graph edges: #{critical_isolated.join(', ')}" unless critical_isolated.empty?
isolated = node_ids.reject { |id| connected.include?(id) || id.end_with?('start') }
warnings << "isolated node count: #{isolated.length} (#{isolated.join(', ')})" if isolated.length > 2

if REPORT
  puts 'Part report:'
  parts.each do |part|
    path = File.expand_path(part, manifest_dir)
    text = part_texts[path]
    puts "- #{part}: lines=#{text.lines.count}, node_marker_count=#{text.scan(/^    - data:\n/).length}, edge_source_count=#{text.scan(/^      source: /).length}"
  end
  puts 'Node ids:'
  node_ids.each { |id| puts "- #{id}: #{node_by_id[id].dig('data', 'title')}" }
  puts "Duplicate node ids: #{duplicates.empty? ? 'none' : duplicates.join(', ')}"
  dangling_edges = edges.select { |edge| !node_id_set.include?(edge['source'].to_s) || !node_id_set.include?(edge['target'].to_s) }
  puts "Dangling edges: #{dangling_edges.empty? ? 'none' : dangling_edges.map { |e| e['id'] }.join(', ')}"
  puts "Selector count: #{selectors.length}"
  puts "Critical isolated nodes: #{critical_isolated.empty? ? 'none' : critical_isolated.join(', ')}"
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
