#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'NL2MGQL_CHATFLOW_DSL' / 'NL2MGQL_CHATFLOW.manifest.yml'


def load_workflow():
    ruby = """
require 'yaml'
require 'json'
manifest = YAML.load_file(ARGV[0])
base = File.dirname(ARGV[0])
text = manifest.fetch('parts').map { |part| File.read(File.join(base, part)) }.join
puts JSON.generate(YAML.safe_load(text, aliases: true))
"""
    raw = subprocess.check_output(['ruby', '-e', ruby, str(MANIFEST)], text=True)
    workflow = json.loads(raw)
    return workflow['workflow']['graph']['nodes'], workflow['workflow']['graph']['edges']


def by_title(nodes, title):
    matches = [node for node in nodes if node.get('data', {}).get('title') == title]
    assert matches, f'missing node: {title}'
    return matches[0]


def run_code(node, **kwargs):
    ns = {}
    exec(node['data']['code'], ns)
    return ns['main'](**kwargs)


def reachable(edges, start, blocked=None):
    blocked = blocked or set()
    adj = {}
    for edge in edges:
        adj.setdefault(str(edge['source']), []).append((str(edge['target']), str(edge.get('sourceHandle', ''))))
    seen, queue = set(), [str(start)]
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        for target, handle in adj.get(cur, []):
            if (cur, handle) in blocked:
                continue
            if target not in seen:
                queue.append(target)
    return seen


def schema_doc(name, label, aliases, fields, metrics, relations=None, priority=60):
    def yaml_list(items, indent=''):
        return ''.join(f'{indent}- {item}\n' for item in items)
    field_yaml = ''
    for f in fields:
        field_yaml += f"  - name: {f['name']}\n    label: {f['label']}\n    role: {f.get('role','dimension')}\n    type: {f.get('type','string')}\n    aliases: [{', '.join(f.get('aliases', []))}]\n    groupable: {str(f.get('groupable', True)).lower()}\n    filterable: {str(f.get('filterable', True)).lower()}\n"
    metric_yaml = ''
    for m in metrics:
        metric_yaml += f"  - name: {m['name']}\n    label: {m['label']}\n    aliases: [{', '.join(m.get('aliases', []))}]\n    function: {m.get('function','count')}\n    field: {m.get('field','_id')}\n"
    rel_yaml = ''
    for r in relations or []:
        rel_yaml += f"  - target_collection: {r}\n    relation_type: enrich\n    join_keys:\n      - source_field: {r}_id\n        target_field: _id\n"
    content = f"""# {name}.md
```yaml
metadata_type: mongo_schema
schema_version: v1
collection_name: {name}
collection_label: {label}
collection_aliases:
{yaml_list(aliases, '  ')}purpose: {label} schema
default_time_field: created_at
priority: {priority}
fields:
{field_yaml}metrics:
{metric_yaml}relations:
{rel_yaml}query_rules:
  max_limit: 100
```
"""
    return {'title': f'{name}.md', 'content': content, 'metadata': {'score': 1.0}}


def candidate_docs():
    return [
        schema_doc('orders', '订单', ['所有订单'], [
            {'name': 'created_at', 'label': '创建时间', 'role': 'time', 'aliases': ['下单时间']},
            {'name': 'vendor', 'label': '厂商', 'aliases': ['供应商']},
        ], [{'name': 'order_count', 'label': '订单数', 'aliases': ['订单量']}], priority=90),
        schema_doc('products', '商品', ['产品'], [
            {'name': 'brand', 'label': '品牌', 'aliases': ['牌子']},
        ], [{'name': 'product_count', 'label': '商品数', 'aliases': ['产品数']}], priority=10),
    ]


def test_candidate_schema_bootstrap_success(nodes, edges):
    pack = by_title(nodes, '代码执行_打包CandidateSchemas并派生Catalog')
    selector = by_title(nodes, '代码执行_CollectionCatalog候选选择')
    trim = by_title(nodes, '代码执行_裁剪SelectedSchema上下文')
    packed = run_code(pack, docs=candidate_docs())
    assert json.loads(packed['candidate_schema_collections_json']) == ['orders', 'products'], packed
    selection = run_code(
        selector,
        question='过去一个月所有订单, 按厂商进行分组统计,做一个柱状图进行对比',
        turn_intent_json=json.dumps({'turn_intent': 'aggregate_summary'}, ensure_ascii=False),
        compact_context_json='{}',
        schema_metadata_json=packed['schema_metadata_json'],
        collection_catalog_json=packed['collection_catalog_json'],
    )
    assert selection['selected_primary_collection'] == 'orders', selection
    assert selection['selected_related_collections'] == [], selection
    trimmed = run_code(
        trim,
        selected_primary_collection=selection['selected_primary_collection'],
        selected_related_collections=selection['selected_related_collections'],
        schema_context=packed['schema_context'],
        schema_metadata_json=packed['schema_metadata_json'],
        schema_alias_index_json=packed['schema_alias_index_json'],
        schema_context_ref_json=packed['schema_context_ref_json'],
        collection_catalog_json=packed['collection_catalog_json'],
    )
    assert trimmed['selected_schema_ready'] is True, trimmed
    assert json.loads(trimmed['schema_context_ref_json'])['collections'] == ['orders'], trimmed
    selector_reached = reachable(edges, selector['id'])
    removed_normal_schema_ids = {'1774083951307', '1775000000001', '1775000000001start', '1775000000002', '1775000000003', '1775000000004'}
    assert not removed_normal_schema_ids.intersection({str(node['id']) for node in nodes})
    assert not removed_normal_schema_ids.intersection(selector_reached), selector_reached


def test_candidate_schemas_empty_safe_stop(nodes, edges):
    pack = by_title(nodes, '代码执行_打包CandidateSchemas并派生Catalog')
    selector = by_title(nodes, '代码执行_CollectionCatalog候选选择')
    safe = by_title(nodes, '代码执行_CollectionSelectionSafeStop')
    branch = by_title(nodes, '条件分支_CollectionSelection结果判断')
    packed = run_code(pack, docs=[])
    selection = run_code(selector, question='订单统计', turn_intent_json='{}', compact_context_json='{}', schema_metadata_json=packed['schema_metadata_json'], collection_catalog_json=packed['collection_catalog_json'])
    assert selection['selected_primary_collection'] == '', selection
    assert selection['requires_clarification'] is True, selection
    answer = run_code(safe, question='订单统计', collection_selection_json=selection['collection_selection_json'], collection_catalog_json=selection['collection_catalog_json'])
    assert '未检索到可用 schema' in answer['final_answer_markdown'], answer
    safe_reachable = reachable(edges, branch['id'], blocked={(str(branch['id']), 'has_primary')})
    forbidden = {by_title(nodes, 'LLM_查询规划')['id'], by_title(nodes, '代码执行_semantic_plan_validator')['id']}
    assert not forbidden.intersection(safe_reachable), safe_reachable


def test_selected_primary_missing_safe_stop(nodes, edges):
    pack = by_title(nodes, '代码执行_打包CandidateSchemas并派生Catalog')
    trim = by_title(nodes, '代码执行_裁剪SelectedSchema上下文')
    selected_branch = by_title(nodes, '条件分支_SelectedSchema结果判断')
    packed = run_code(pack, docs=candidate_docs())
    trimmed = run_code(trim, selected_primary_collection='customers', selected_related_collections=[], schema_context=packed['schema_context'], schema_metadata_json=packed['schema_metadata_json'], schema_alias_index_json=packed['schema_alias_index_json'], schema_context_ref_json=packed['schema_context_ref_json'], collection_catalog_json=packed['collection_catalog_json'])
    assert trimmed['selected_schema_ready'] is False, trimmed
    assert 'selected primary collection not found in candidate schemas' in json.loads(trimmed['selected_schema_warnings_json'])
    false_reachable = reachable(edges, selected_branch['id'], blocked={(str(selected_branch['id']), 'schema_ready')})
    assert by_title(nodes, 'LLM_查询规划')['id'] not in false_reachable, false_reachable
    assert by_title(nodes, '代码执行_semantic_plan_validator')['id'] not in false_reachable, false_reachable


def main():
    nodes, edges = load_workflow()
    test_candidate_schema_bootstrap_success(nodes, edges)
    test_candidate_schemas_empty_safe_stop(nodes, edges)
    test_selected_primary_missing_safe_stop(nodes, edges)
    print('PASS candidate schema bootstrap')


if __name__ == '__main__':
    main()
