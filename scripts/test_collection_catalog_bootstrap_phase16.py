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
    nodes = workflow['workflow']['graph']['nodes']
    edges = workflow['workflow']['graph']['edges']
    return nodes, edges


def by_title(nodes, title):
    matches = [node for node in nodes if node.get('data', {}).get('title') == title]
    assert matches, f'missing node: {title}'
    return matches[0]


def run_code(node, **kwargs):
    namespace = {}
    exec(node['data']['code'], namespace)
    return namespace['main'](**kwargs)


def reachable(edges, start, blocked=None):
    blocked = blocked or set()
    adj = {}
    for edge in edges:
        adj.setdefault(str(edge['source']), []).append((str(edge['target']), str(edge.get('sourceHandle', ''))))
    seen = set()
    queue = [str(start)]
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


def candidate_docs():
    orders = """```yaml
metadata_type: mongo_schema
schema_version: v1
collection_name: orders
collection_label: 订单
collection_aliases:
  - 所有订单
default_time_field: created_at
priority: 90
fields:
  - name: vendor
    label: 厂商
    role: dimension
    aliases: [供应商]
    groupable: true
    filterable: true
  - name: created_at
    label: 创建时间
    role: time
    groupable: true
    filterable: true
metrics:
  - name: order_count
    label: 订单数
    aliases: [订单量]
    function: count
    field: _id
relations: []
query_rules:
  max_limit: 100
```"""
    products = """```yaml
metadata_type: mongo_schema
schema_version: v1
collection_name: products
collection_label: 商品
collection_aliases:
  - 产品
default_time_field: created_at
priority: 10
fields:
  - name: brand
    label: 品牌
    role: dimension
    groupable: true
    filterable: true
metrics:
  - name: product_count
    label: 商品数
    function: count
    field: _id
relations: []
query_rules:
  max_limit: 100
```"""
    return [{'title': 'orders.md', 'content': orders}, {'title': 'products.md', 'content': products}]


def test_first_turn_catalog_bootstrap(nodes):
    pack = by_title(nodes, '代码执行_打包CandidateSchemas并派生Catalog')
    selector = by_title(nodes, '代码执行_CollectionCatalog候选选择')
    packed = run_code(pack, docs=candidate_docs())
    selection = run_code(
        selector,
        question='过去一个月所有订单, 按厂商进行分组统计,做一个柱状图进行对比',
        turn_intent_json=json.dumps({'turn_intent': 'aggregate_summary'}, ensure_ascii=False),
        compact_context_json='{}',
        schema_metadata_json='',
        collection_catalog_json=packed['collection_catalog_json'],
    )
    assert selection['selected_primary_collection'] == 'orders', selection
    assert selection['selected_related_collections'] == [], selection


def test_missing_catalog_safe_stop_topology(nodes, edges):
    pack = by_title(nodes, '代码执行_打包CandidateSchemas并派生Catalog')
    selector = by_title(nodes, '代码执行_CollectionCatalog候选选择')
    safe = by_title(nodes, '代码执行_CollectionSelectionSafeStop')
    branch = by_title(nodes, '条件分支_CollectionSelection结果判断')
    packed = run_code(pack, docs=[])
    selection = run_code(
        selector,
        question='过去一个月所有订单, 按厂商进行分组统计,做一个柱状图进行对比',
        turn_intent_json='{}',
        compact_context_json='{}',
        schema_metadata_json='',
        collection_catalog_json=packed['collection_catalog_json'],
    )
    assert selection['selected_primary_collection'] == '', selection
    assert selection['requires_clarification'] is True, selection
    answer = run_code(
        safe,
        question='过去一个月所有订单, 按厂商进行分组统计,做一个柱状图进行对比',
        collection_selection_json=selection['collection_selection_json'],
        collection_catalog_json=selection['collection_catalog_json'],
    )
    assert '未检索到可用 schema' in answer['final_answer_markdown'], answer
    safe_reachable = reachable(edges, branch['id'], blocked={(str(branch['id']), 'has_primary')})
    forbidden = {
        by_title(nodes, 'LLM_查询规划')['id'],
        by_title(nodes, '代码执行_semantic_plan_validator')['id'],
    }
    assert not {str(x) for x in forbidden}.intersection(safe_reachable), safe_reachable


def test_non_valid_does_not_pollute_success_context(nodes):
    saver = by_title(nodes, '变量赋值_validator非valid保存上下文')
    items = saver['data'].get('items') or []
    assert all(item.get('variable_selector') != ['conversation', 'last_context_json'] for item in items), items
    conversation = {'last_context_json': '{success context}'}
    node_output = {
        'final_answer_markdown': 'validator failed',
        'answer_payload_json': '{answer}',
        'context_update_json': '{update}',
    }
    for item in items:
        selector = item.get('variable_selector')
        value = item.get('value')
        if selector and selector[0] == 'conversation':
            conversation[selector[1]] = node_output.get(value[1], '')
    assert conversation['last_error'] == 'validator failed', conversation
    assert conversation['last_answer_payload_json'] == '{answer}', conversation
    assert conversation['last_context_update_json'] == '{update}', conversation
    assert conversation['last_context_json'] == '{success context}', conversation


def test_no_schema_retrieval_after_selector(nodes, edges):
    pack = by_title(nodes, '代码执行_打包CandidateSchemas并派生Catalog')
    selector = by_title(nodes, '代码执行_CollectionCatalog候选选择')
    trim = by_title(nodes, '代码执行_裁剪SelectedSchema上下文')
    merge = by_title(nodes, '代码执行_合并Schema上下文并准备语义计划提示词')
    schema = by_title(nodes, '遍历Collections检索Schema')
    pack_reached = reachable(edges, pack['id'])
    selector_reached = reachable(edges, selector['id'])
    trim_reached = reachable(edges, trim['id'])
    assert str(selector['id']) in pack_reached, pack_reached
    assert str(trim['id']) in selector_reached, selector_reached
    assert str(merge['id']) in trim_reached, trim_reached
    assert str(schema['id']) not in selector_reached, selector_reached


def main():
    nodes, edges = load_workflow()
    test_first_turn_catalog_bootstrap(nodes)
    test_missing_catalog_safe_stop_topology(nodes, edges)
    test_non_valid_does_not_pollute_success_context(nodes)
    test_no_schema_retrieval_after_selector(nodes, edges)
    print('PASS collection catalog bootstrap phase16')


if __name__ == '__main__':
    main()
