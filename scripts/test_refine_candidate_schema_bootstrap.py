#!/usr/bin/env python3
import json

from test_candidate_schema_bootstrap import load_workflow, by_title, run_code, reachable, schema_doc


def refine_docs():
    return [
        schema_doc('orders', '订单', ['所有订单', '订单'], [
            {'name': 'created_at', 'label': '创建时间', 'role': 'time', 'aliases': ['下单时间']},
            {'name': 'brand', 'label': '品牌', 'aliases': ['牌子']},
            {'name': 'status', 'label': '订单状态', 'aliases': ['状态', '订单状态']},
        ], [{'name': 'order_count', 'label': '订单数', 'aliases': ['订单量']}], priority=90),
        schema_doc('products', '商品', ['产品'], [
            {'name': 'brand', 'label': '品牌', 'aliases': ['牌子']},
        ], [{'name': 'product_count', 'label': '商品数', 'aliases': ['产品数']}], priority=10),
    ]


def test_merge_prompt_signature_compat(nodes):
    merge = by_title(nodes, '代码执行_合并Schema上下文并准备语义计划提示词')
    out = run_code(
        merge,
        arg1='## collection=orders\nfields: status',
        arg2=json.dumps({'selected_primary_collection': 'orders'}, ensure_ascii=False),
        arg3='订单状态统计',
        schema_context_ref_json='{"collections":["orders"]}',
        future_extra_input='x',
    )
    assert out['schema_context_ref_json'] == '{"collections": ["orders"]}' or json.loads(out['schema_context_ref_json'])['collections'] == ['orders'], out
    assert 'schema_context_ref_json' in out['prompt'], out


def test_refine_full_replan_candidate_schemas(nodes, edges):
    build = by_title(nodes, '代码执行_构建CandidateSchemas检索查询_Refine')
    pack = by_title(nodes, '代码执行_打包CandidateSchemas并派生Catalog_Refine')
    selector = by_title(nodes, '代码执行_CollectionCatalog候选选择_Refine')
    trim = by_title(nodes, '代码执行_裁剪SelectedSchema上下文_Refine')
    current = '不对，我想按订单状态统计过去两个月的订单数，不再按品牌统计, 生成新的柱状图'
    last_context = {
        'has_last_context': True,
        'last_question': '过去一个月所有订单按品牌统计订单数',
        'last_primary_collection': 'orders',
        'last_related_collections': [],
        'schema_context_ref': {'collections': ['orders']},
    }
    old_plan = {
        'execution_plan': {'stages': [{'collection': 'orders', 'group_fields': ['brand'], 'metric_field': '_id', 'metric_alias': 'order_count'}]},
        'primary_collection': 'orders',
    }
    qref = {'changed_fields': ['group_fields'], 'negated_fields': ['brand'], 'new_fields': ['订单状态'], 'refined_question': current}
    built = run_code(
        build,
        question=current,
        query_refinement_json=json.dumps(qref, ensure_ascii=False),
        normalized_turn_intent_json=json.dumps({'turn_intent': 'refine_query', 'route': 'full_replan'}, ensure_ascii=False),
        last_context_json=json.dumps(last_context, ensure_ascii=False),
        old_normalized_semantic_plan_json=json.dumps(old_plan, ensure_ascii=False),
        patch_reason='结构性变化 full-replan',
        changed_fields='group_fields',
    )
    assert '订单状态' in built['query_text'] and 'group by' in built['query_text'] and 'metadata_type: mongo_schema' in built['query_text'], built['query_text']
    packed = run_code(pack, docs=refine_docs())
    selection = run_code(
        selector,
        question=current,
        turn_intent_json=json.dumps({'turn_intent': 'refine_query'}, ensure_ascii=False),
        compact_context_json=json.dumps(last_context, ensure_ascii=False),
        schema_metadata_json=packed['schema_metadata_json'],
        collection_catalog_json=packed['collection_catalog_json'],
        query_refinement_json=json.dumps(qref, ensure_ascii=False),
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
    old_titles = {'遍历Collections检索Schema_Refine', '知识检索_当前CollectionSchema_Refine', '代码执行_打包Schema结果_Refine', '知识检索_当前CollectionSchema', '代码执行_打包Schema结果'}
    assert not old_titles.intersection({node.get('data', {}).get('title') for node in nodes})
    assert by_title(nodes, '代码执行_构建CandidateSchemas检索查询_Refine')['id'] in reachable(edges, '1776000000023')


def test_refine_empty_and_missing_primary_safe_stop(nodes, edges):
    pack = by_title(nodes, '代码执行_打包CandidateSchemas并派生Catalog_Refine')
    selector = by_title(nodes, '代码执行_CollectionCatalog候选选择_Refine')
    trim = by_title(nodes, '代码执行_裁剪SelectedSchema上下文_Refine')
    branch = by_title(nodes, '条件分支_CollectionSelection结果判断_Refine')
    selected_branch = by_title(nodes, '条件分支_SelectedSchema结果判断_Refine')
    planner = by_title(nodes, 'LLM_Refine查询规划')
    validator = by_title(nodes, '代码执行_semantic_plan_validator')
    packed_empty = run_code(pack, docs=[])
    selection = run_code(selector, question='订单状态统计', turn_intent_json='{}', compact_context_json='{}', schema_metadata_json=packed_empty['schema_metadata_json'], collection_catalog_json=packed_empty['collection_catalog_json'], query_refinement_json='{}')
    assert selection['requires_clarification'] is True and selection['selected_primary_collection'] == '', selection
    false_reachable = reachable(edges, branch['id'], blocked={(str(branch['id']), 'has_primary')})
    assert planner['id'] not in false_reachable and validator['id'] not in false_reachable, false_reachable
    packed = run_code(pack, docs=refine_docs())
    trimmed = run_code(trim, selected_primary_collection='customers', selected_related_collections=[], schema_context=packed['schema_context'], schema_metadata_json=packed['schema_metadata_json'], schema_alias_index_json=packed['schema_alias_index_json'], schema_context_ref_json=packed['schema_context_ref_json'], collection_catalog_json=packed['collection_catalog_json'])
    assert trimmed['selected_schema_ready'] is False, trimmed
    selected_false = reachable(edges, selected_branch['id'], blocked={(str(selected_branch['id']), 'schema_ready')})
    assert planner['id'] not in selected_false and validator['id'] not in selected_false, selected_false


def main():
    nodes, edges = load_workflow()
    test_merge_prompt_signature_compat(nodes)
    test_refine_full_replan_candidate_schemas(nodes, edges)
    test_refine_empty_and_missing_primary_safe_stop(nodes, edges)
    print('PASS refine candidate schema bootstrap')


if __name__ == '__main__':
    main()
