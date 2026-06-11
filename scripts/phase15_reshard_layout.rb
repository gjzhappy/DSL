#!/usr/bin/env ruby
# frozen_string_literal: true

# Phase 15 maintenance helper: applies display-only node coordinates and
# rewrites the DSL manifest parts with safer source shards. It intentionally
# does not modify node ids, titles, variables, code, edge ids, or edge endpoints.

require 'yaml'
require 'fileutils'

ROOT = File.expand_path('..', __dir__)
DSL_DIR = File.join(ROOT, 'NL2MGQL_CHATFLOW_DSL')
MANIFEST_PATH = File.join(DSL_DIR, 'NL2MGQL_CHATFLOW.manifest.yml')
TARGET_PART_LINES = 950

manifest = YAML.load_file(MANIFEST_PATH)
old_parts = manifest.fetch('parts')
assembled = old_parts.map { |part| File.read(File.join(DSL_DIR, part)) }.join

layout = {
  '1773909192919' => [0, 0],
  '1776000000001' => [460, 0],
  '1776000000002' => [920, 0],
  '1776000000003' => [1380, 0],
  '1776000000004' => [1840, 0],
  '1776000000013' => [2300, 0],
  '1776000000005' => [2760, 0],
  '1776000000022' => [3220, 0],
  '1776000000019' => [3680, 180],
  '1776000000023' => [4140, 180],

  # new_query lane
  '1777000000001' => [3680, -620],
  '1773975766025' => [4140, -620],
  '1774083951307' => [4600, -620],
  '1775000000001' => [5060, -620],
  '1775000000001start' => [40, 90],
  '1775000000002' => [380, 90],
  '1775000000003' => [800, 90],
  '1775000000004' => [1220, 90],
  '1775000000005' => [6720, -620],
  '1775000000014' => [7180, -620],
  '1775000000010' => [7640, -920],
  '1775000000011' => [8100, -920],
  '1775000000015' => [8560, -920],
  '1775000000012' => [9020, -920],
  '1775000000016' => [9480, -920],
  '1775000000017' => [9940, -920],
  '1775000000018' => [9020, -320],
  '1775000000019' => [9480, -320],
  '1775000000013' => [10400, -620],
  '1775000000020' => [10860, -620],
  '1776000000012' => [11320, -300],
  '1775000000021' => [11780, -300],

  # refine/full-replan lane
  '1777000000002' => [4600, 520],
  '1776000000030' => [5060, 520],
  '1776000000025' => [5520, 520],
  '1776000000032' => [5980, 520],
  '1776000000032start' => [40, 90],
  '1776000000033' => [380, 90],
  '1776000000034' => [800, 90],
  '1776000000035' => [1220, 90],
  '1776000000026' => [7640, 520],
  '1776000000027' => [8100, 520],
  '1776000000020' => [8560, 520],
  '1776000000028' => [9020, 520],
  '1776000000021' => [9480, 520],

  # use_patch / runtime schema / validation / success lane
  '1776000000024' => [11320, 120],
  '1779000000001' => [11780, 120],
  '1780000000001' => [12240, 120],
  '1780000000009' => [14080, 120],
  '1778000000001' => [14540, 120],
  '1778000000002' => [15000, 120],
  '1775000000007' => [15460, -120],
  '1781000000001' => [15920, -120],
  '1775000000022' => [16380, -120],
  '1775000000023' => [16840, -120],
  '1773910028939' => [17300, -120],

  # hydration lane
  '1780000000002' => [12700, 760],
  '1780000000003' => [13160, 760],
  '1780000000003start' => [40, 90],
  '1780000000004' => [380, 90],
  '1780000000005' => [800, 90],
  '1780000000006' => [1220, 90],
  '1780000000007' => [14820, 760],
  '1780000000008' => [15280, 760],

  # chart-only lane
  '1776000000014' => [3220, 1120],
  '1776000000015' => [3680, 1120],
  '1776000000016' => [4140, 1120],
  '1776000000006' => [4600, 1120],
  '1776000000009' => [5060, 1120],

  # analysis / clarification / non-valid lanes
  '1776000000017' => [3220, 1480],
  '1776000000018' => [3680, 1480],
  '1776000000010' => [4140, 1480],
  '1776000000008' => [3220, 1840],
  '1776000000011' => [3680, 1840],
  '1778000000004' => [15460, 420],
  '1778000000003' => [15920, 420]
}

parent_by_id = {}
workflow = YAML.safe_load(assembled, aliases: true)
workflow.dig('workflow', 'graph', 'nodes').each do |node|
  parent_by_id[node['id'].to_s] = node['parentId'].to_s if node['parentId']
end

absolute_layout = {}
layout.each do |id, (x, y)|
  parent_id = parent_by_id[id]
  if parent_id && !parent_id.empty? && layout[parent_id]
    px, py = layout[parent_id]
    absolute_layout[id] = [px + x, py + y]
  else
    absolute_layout[id] = [x, y]
  end
end

format_number = lambda do |num|
  num.is_a?(Integer) ? num.to_s : num.to_f.to_s
end

replace_block = lambda do |snippet, key, x, y|
  block = "      #{key}:\n        x: #{format_number.call(x)}\n        y: #{format_number.call(y)}\n"
  if snippet.match?(/^      #{Regexp.escape(key)}:\n        x: .*\n        [\"']?y[\"']?: .*\n/)
    snippet.sub(/^      #{Regexp.escape(key)}:\n        x: .*\n        [\"']?y[\"']?: .*\n/, block)
  else
    insert_at = snippet.index(/^      selected:/) || snippet.index(/^      sourcePosition:/) || snippet.index(/^      targetPosition:/)
    raise "cannot insert #{key} for node snippet" unless insert_at

    snippet.insert(insert_at, block)
  end
end

node_start = assembled.lines.index { |line| line == "    nodes:\n" }
viewport_start = assembled.lines.index { |line| line == "    viewport:\n" }
raise 'cannot find workflow.graph.nodes' unless node_start && viewport_start

lines = assembled.lines
node_starts = []
(node_start + 1...viewport_start).each { |idx| node_starts << idx if lines[idx].match?(/^    - data:/) }
node_starts << viewport_start
updated_nodes = []
node_starts.each_cons(2) do |from, to|
  snippet = lines[from...to].join
  id = snippet[/^      id: '?([^'\n]+)'?\n/, 1]
  if layout[id]
    x, y = layout.fetch(id)
    ax, ay = absolute_layout.fetch(id)
    snippet = replace_block.call(snippet, 'position', x, y)
    snippet = replace_block.call(snippet, 'positionAbsolute', ax, ay)
  end
  updated_nodes << snippet
end

pre_nodes = lines[0..node_start].join
footer = lines[viewport_start..].join.sub(/^    viewport:\n      x: .*\n      [\"']?y[\"']?: .*\n      zoom: .*\n/, "    viewport:\n      x: -120\n      y: 80\n      zoom: 0.55\n")

segments = []
pre_lines = pre_nodes.lines
until pre_lines.empty?
  chunk = pre_lines.shift(TARGET_PART_LINES)
  segments << chunk.join
end

current = +' '
current.clear
updated_nodes.each do |snippet|
  snippet_lines = snippet.lines.length
  if snippet_lines > TARGET_PART_LINES
    segments << current unless current.empty?
    current = +' '
    current.clear
    segments << snippet
  elsif current.lines.length + snippet_lines > TARGET_PART_LINES
    segments << current unless current.empty?
    current = snippet.dup
  else
    current << snippet
  end
end

if current.lines.length + footer.lines.length > TARGET_PART_LINES
  segments << current unless current.empty?
  segments << footer
else
  current << footer
  segments << current unless current.empty?
end

old_numbered = Dir[File.join(DSL_DIR, 'NL2MGQL_CHATFLOW_[0-9]*.yml')]
old_numbered.each { |path| FileUtils.rm_f(path) }
new_parts = []
segments.each_with_index do |content, idx|
  part = format('NL2MGQL_CHATFLOW_%02d.yml', idx)
  File.write(File.join(DSL_DIR, part), content)
  new_parts << part
end

manifest_text = <<~YAML
  # NL2MGQL Chatflow DSL source partition manifest.
  # Load parts in the listed order and concatenate them in memory before importing/validating.
  # This file is intentionally a lightweight assembly list; it does not contain workflow DSL nodes.
  name: NL2MGQL_CHATFLOW
  entrypoint: #{new_parts.first}
  assembly: ordered-concat
  parts:
YAML
new_parts.each { |part| manifest_text << "  - #{part}\n" }
File.write(MANIFEST_PATH, manifest_text)

puts "Wrote #{new_parts.length} DSL parts"
new_parts.each do |part|
  puts format('%s %d lines', part, File.foreach(File.join(DSL_DIR, part)).count)
end
