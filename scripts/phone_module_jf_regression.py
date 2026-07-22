#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from graph_phone_common import load_doc, graph, path, check_frontend_checkvalid_schema

def must(p,msg):
    if not p: raise AssertionError(msg)

def parse_slot_samples(nodes):
    if 'parse' not in nodes:
        return []
    code = nodes['parse']['data']['code']
    ns = {}
    exec(code, ns)
    route = {
        'required_slots': ['target_objects', 'object_scope', 'analysis_dimension', 'output_type'],
        'slot_definitions': {
            'target_objects': {
                'type': 'multi_enum', 'min_items': 2,
                'values': [
                    {'value': 'L&LU(P80 Pro/P80 Pro+)', 'aliases': ['p80 pro', 'p80 pro+', 'pro']},
                    {'value': 'CE(Mate 70)', 'aliases': ['mate 70']},
                    {'value': 'Delphi(Mate X7)', 'aliases': ['mate x7', 'matex7', 'x7']},
                    {'value': 'PI(Mate 70 Pro)', 'aliases': ['mate 70 pro']},
                    {'value': 'Iphone16 Pro Max', 'aliases': ['iphone16 pro max']},
                    {'value': '小米15Ultra', 'aliases': ['小米15Ultra', 'mi15u']},
                    {'value': 'vivo_x200_pro', 'aliases': ['vivo x200 pro']},
                ],
            },
            'object_scope': {'type': 'enum', 'values': [{'value': '主摄', 'aliases': ['主摄']}]},
            'analysis_dimension': {'type': 'enum', 'values': [
                {'value': 'tline', 'aliases': ['tline'], 'topic': 'cis_tline'},
                {'value': 'CIS规格', 'aliases': ['CIS规格', 'CIS基础规格']},
                {'value': '功耗', 'aliases': ['功耗']},
            ]},
            'output_type': {'type': 'enum', 'values': [{'value': '对比报告', 'aliases': ['对比报告', '报告'], 'output_kind': 'report'}]},
        },
    }
    samples = [
        ('请对比mate 70 pro与matex7的主摄CIS的tline(区分物理/等效)能力, 覆盖关键模式，如4k 30fps录像场景（开启DCG HDR），拍照预览（开启NDOL HDR）等,请生成他们的对比报告', ['PI(Mate 70 Pro)', 'Delphi(Mate X7)']),
        ('请对比mate 70与mate x7的主摄tline', ['CE(Mate 70)', 'Delphi(Mate X7)']),
        ('请对比p80 pro与mate 70 pro的主摄CIS规格', ['L&LU(P80 Pro/P80 Pro+)', 'PI(Mate 70 Pro)']),
        ('请对比Iphone16 Pro Max与小米15Ultra的主摄CIS基础规格', ['Iphone16 Pro Max', '小米15Ultra']),
        ('请对比vivo x200 pro与小米15Ultra的主摄功耗', ['vivo_x200_pro', '小米15Ultra']),
    ]
    results = []
    for question, expected in samples:
        raw = ns['main'](json.dumps({'question': question}, ensure_ascii=False), json.dumps(route, ensure_ascii=False))
        slots = json.loads(raw['slot_parse_result_json'])
        actual = slots.get('target_objects')
        must(actual == expected, f'target_objects mismatch for {question}: {actual} != {expected}')
        results.append(actual)
    return results

def fuzzy_device_model_samples(nodes):
    parse_ns, plan_ns = {}, {}
    exec(nodes['jf_registry_parse']['data']['code'], parse_ns)
    exec(nodes['jf_sql_plan']['data']['code'], plan_ns)

    def registry(models, fuzzy=True, gap=0.08, min_alias_length=5, legacy_min_score=None):
        value = {
            'version': 'test', 'table': 'phone_test',
            'query_roles': {'sql_filter_fields': ['device_model', 'manufacturer'], 'sql_select_fields': 'all_fields'},
            'fields': {
                'device_model': {'label': '机型', 'aliases': ['机型']},
                'manufacturer': {'label': '厂商', 'aliases': ['厂商']},
            },
            'composite_fields': {},
            'value_aliases': {'device_model': models, 'manufacturer': {'Honor': {'aliases': ['honor']}}},
        }
        if fuzzy:
            value['match_policies'] = {'device_model': {'fuzzy': {
                'enabled': True, 'min_alias_length': min_alias_length, 'require_same_digits': True,
                'min_score_gap': gap,
                'distance_rules': [{'max_length': 7, 'max_distance': 1}, {'max_length': 12, 'max_distance': 2}, {'max_length': 999, 'max_distance': 2}],
                'protected_tokens': ['pro', 'promax', 'max', 'ultra', 'plus', 'mini', 'se'],
                'require_protected_tokens_equal': True,
                'protected_token_fuzzy': {'enabled': True, 'max_distance': 1},
            }}}
            if legacy_min_score is not None:
                value['match_policies']['device_model']['fuzzy']['min_score'] = legacy_min_score
        return value

    def compile(question, value):
        parsed = parse_ns['main'](json.dumps(value, ensure_ascii=False), 'ok')
        must(parsed['jf_registry_parse_status'] == 'ok', parsed['jf_registry_error'])
        result = plan_ns['main'](
            json.dumps({'question': question}, ensure_ascii=False), parsed['jf_registry_json'],
            parsed['jf_alias_index_json'], parsed['jf_registry_parse_status'], parsed['jf_registry_error'])
        must(result['jf_sql_compile_status'] == 'ok', result['jf_sql_compile_error_answer'])
        return json.loads(result['jf_sql_plan_json'])

    base = {'Magic6': {'aliases': ['magic6']}, 'Iphone16 Pro Max': {'aliases': ['iphone16promax']}}
    for typo in ('Maigic6', 'Mgaic6', 'Magc6', 'Magix6'):
        plan = compile(typo, registry(base))
        must(plan['where_filters'].get('device_model') == ['Magic6'], f"fuzzy typo failed: {typo}: {plan}")
        must(plan['fuzzy_alias_hits']['device_model'][0]['target'] == 'Magic6', f'fuzzy audit failed: {typo}')
        must(plan['fuzzy_alias_hits']['device_model'][0]['mode'] == 'overall', f'fuzzy mode failed: {typo}')
        if typo == 'Mgaic6':
            hit = plan['fuzzy_alias_hits']['device_model'][0]
            must(hit['distance'] == 1 and hit['score'] == 0.833333, 'historical below-min_score candidate was rejected')
            must(plan['params'][0] == 'Magic6', 'canonical fuzzy value did not reach SQL params')
    protected = {
        'Magic6': {'aliases': ['magic6']},
        'Iphone16 Pro Max': {'aliases': ['iphone16promax']},
        'Vivo X200 pro': {'aliases': ['vivox200pro']},
        '小米15Ultra': {'aliases': ['小米15ultra', 'xiaomi15ultra']},
    }
    cases = (
        ('iPhone 16 Pto Max', 'Iphone16 Pro Max'),
        ('iPhone 16 Por Max', 'Iphone16 Pro Max'),
        ('小米15 Utlra', '小米15Ultra'),
        ('Xiaomi15 Ulrta', '小米15Ultra'),
        ('vvo X200 Pto', 'Vivo X200 pro'),
    )
    for question, expected in cases:
        plan = compile(question, registry(protected))
        must(plan['where_filters'].get('device_model') == [expected], f'segmented typo failed: {question}: {plan}')
        hit = plan['fuzzy_alias_hits']['device_model'][0]
        must(hit['mode'] == 'segmented', f'segmented audit mode failed: {question}: {hit}')
    vivo_hit = compile('vvo X200 Pto', registry(protected))['fuzzy_alias_hits']['device_model'][0]
    must(vivo_hit['score'] < 0.84 and vivo_hit['distance'] == 2, 'segmented path did not preserve overall audit score')
    mixed = compile('对比 Magic6、Mgaic6、iPhone 16 Pto Max 和 vvo X200 Pto', registry(protected))
    must(mixed['where_filters']['device_model'] == ['Magic6', 'Iphone16 Pro Max', 'Vivo X200 pro'], 'mixed exact/segmented failed')
    must(mixed['value_alias_hits']['device_model'] == ['Magic6'], 'mixed exact audit changed')
    must([hit['mode'] for hit in mixed['fuzzy_alias_hits']['device_model']] == ['overall', 'segmented', 'segmented'], 'mixed fuzzy modes failed')
    must(compile('小米16 Ultra', registry(protected))['where_filters'].get('device_model') is None, 'segmented digit mismatch accepted')
    must(compile('X20 Pto', registry(protected))['where_filters'].get('device_model') is None, 'segmented missing digit accepted')
    must(compile('X200 Ultra', registry({'X200 Pro': {'aliases': ['x200pro']}}))['where_filters'].get('device_model') is None, 'protected semantics changed')
    plan = compile('对比 iPhone16 Pro Max 和 Maigic6', registry(base))
    must(plan['where_filters']['device_model'] == ['Iphone16 Pro Max', 'Magic6'], 'partial exact/fuzzy order failed')
    must(plan['value_alias_hits']['device_model'] == ['Iphone16 Pro Max'], 'exact alias audit changed')
    must(compile('Magic7', registry(base))['where_filters'].get('device_model') is None, 'digit gate failed')
    suffixes = {'Magic6': {'aliases': ['magic6']}, 'Magic6 Pro': {'aliases': ['magic6pro']}}
    must(compile('Magic6', registry(suffixes))['where_filters']['device_model'] == ['Magic6'], 'protected suffix addition failed')
    must(compile('Magic6 Pro', registry(suffixes))['where_filters']['device_model'] == ['Magic6 Pro'], 'protected suffix removal failed')
    same = {'Magic6': {'aliases': ['magic6', 'magix6']}}
    plan = compile('magio6', registry(same, gap=0.20))
    must(plan['where_filters']['device_model'] == ['Magic6'], 'same-canonical alias merge failed')
    ambiguous = {'ModelA': {'aliases': ['magic6']}, 'ModelB': {'aliases': ['magix6']}}
    plan = compile('magio6', registry(ambiguous, gap=0.20))
    must(plan['where_filters']['device_model'] == ['ModelA', 'ModelB'], 'small score gap failed')
    must('matches' in plan['fuzzy_alias_hits']['device_model'][0], 'ambiguous audit failed')
    plan = compile('magix6', registry(ambiguous, gap=0.08))
    must(plan['where_filters']['device_model'] == ['ModelB'], 'large score gap/exact priority failed')
    short = {'SE': {'aliases': ['se']}}
    must(compile('sx', registry(short))['where_filters'].get('device_model') is None, 'short alias fuzzy gate failed')
    must(compile('se', registry(short))['where_filters']['device_model'] == ['SE'], 'short alias exact failed')
    short_suffix = {'Model SE': {'aliases': ['modelse']}}
    must(compile('modelsx', registry(short_suffix, min_alias_length=2))['where_filters'].get('device_model') is None, 'short protected token fuzzy accepted')
    no_fuzzy = compile('honot', registry(base, fuzzy=False))
    must(no_fuzzy['where_filters'].get('manufacturer') is None and not no_fuzzy.get('fuzzy_alias_hits'), 'unconfigured field fuzzy changed')
    exact = compile('Magic6', registry(base))
    must(exact['where_filters']['device_model'] == ['Magic6'] and not exact.get('fuzzy_alias_hits'), 'exact priority failed')
    must(compile('查询主摄规格', registry(base))['where_filters'].get('device_model') is None, 'no-model false positive')

    words = {'Milla': {'aliases': ['milla']}, 'Delphi': {'aliases': ['delphi']}, 'Iceland': {'aliases': ['iceland']}}
    for exact_word in ('milla', 'delphi', 'iceland'):
        plan = compile(exact_word, registry(words))
        must(plan['where_filters'].get('device_model'), f'non-numeric exact alias failed: {exact_word}')
        must(not plan.get('fuzzy_alias_hits'), f'non-numeric exact alias became fuzzy: {exact_word}')
    for typo in ('mila', 'delphx', 'iceladn', 'icelaand'):
        must(compile(typo, registry(words))['where_filters'].get('device_model') is None, f'non-numeric alias fuzzy accepted: {typo}')
    must(compile('please deliver an ordinary english question', registry(words))['where_filters'].get('device_model') is None, 'ordinary prose recalled non-numeric alias')
    must(compile('Mxxic6', registry(base))['where_filters'].get('device_model') is None, 'over-distance typo accepted')

    legacy = registry(base, legacy_min_score=0.99)
    parsed = parse_ns['main'](json.dumps(legacy, ensure_ascii=False), 'ok')
    must(parsed['jf_registry_parse_status'] == 'ok', 'legacy min_score rejected')
    must('min_score' in json.loads(parsed['jf_registry_json'])['match_policies']['device_model']['fuzzy'], 'legacy registry contract unexpectedly rewrote input')
    must(compile('Mgaic6', legacy)['where_filters'].get('device_model') == ['Magic6'], 'legacy min_score still gates candidates')
    invalid = registry(base)
    invalid['match_policies']['device_model']['fuzzy']['min_score_gap'] = 1.1
    parsed = parse_ns['main'](json.dumps(invalid, ensure_ascii=False), 'ok')
    must(parsed['jf_registry_parse_status'] == 'error', 'invalid fuzzy policy accepted')
    invalid = registry(base)
    invalid['match_policies']['device_model']['fuzzy']['require_same_digits'] = 'yes'
    parsed = parse_ns['main'](json.dumps(invalid, ensure_ascii=False), 'ok')
    must(parsed['jf_registry_parse_status'] == 'error', 'invalid require_same_digits accepted')
    invalid = registry(base)
    invalid['match_policies']['device_model']['fuzzy']['distance_rules'][0]['max_distance'] = -1
    parsed = parse_ns['main'](json.dumps(invalid, ensure_ascii=False), 'ok')
    must(parsed['jf_registry_parse_status'] == 'error', 'invalid distance_rules accepted')
    for bad_value in ('bad', {'enabled': 'yes'}, {'enabled': True, 'max_distance': -1}):
        invalid = registry(base)
        invalid['match_policies']['device_model']['fuzzy']['protected_token_fuzzy'] = bad_value
        parsed = parse_ns['main'](json.dumps(invalid, ensure_ascii=False), 'ok')
        must(parsed['jf_registry_parse_status'] == 'error', f'invalid protected token fuzzy policy accepted: {bad_value}')
    strict = registry({'Iphone16 Pro Max': {'aliases': ['iphone16promax']}})
    del strict['match_policies']['device_model']['fuzzy']['protected_token_fuzzy']
    must(compile('iPhone16 Pto Max', strict)['where_filters'].get('device_model') is None, 'missing protected fuzzy config changed strict behavior')
    return 48

def query_memory_regressions(doc, nodes):
    parse_ns, plan_ns = {}, {}
    exec(nodes['jf_registry_parse']['data']['code'], parse_ns)
    exec(nodes['jf_sql_plan']['data']['code'], plan_ns)
    registry_raw = next(item['value'] for item in doc['workflow']['environment_variables'] if item['name'] == 'JF_QUERY_REGISTRY_JSON')
    parsed = parse_ns['main'](registry_raw, 'ok')
    must(parsed['jf_registry_parse_status'] == 'ok', parsed['jf_registry_error'])

    def compile(question, memory='{}'):
        result = plan_ns['main'](
            json.dumps({'question': question}, ensure_ascii=False),
            parsed['jf_registry_json'], parsed['jf_alias_index_json'], 'ok', '', memory)
        must(result['jf_sql_compile_status'] == 'ok', result['jf_sql_compile_error_answer'])
        return result, json.loads(result['jf_sql_plan_json']), json.loads(result['jf_current_execution_context_json'])

    first_result, first, first_context = compile('请查询 vvo X200 Pto 的主摄规格。')
    must(first_context['version'] == 'jf_query_memory_v1' and len(first_context['query_scopes']) == 1, 'first scope missing')
    first_scope = first_context['query_scopes'][0]
    must(first_scope['where_filters'] == {'device_model': ['Vivo X200 pro'], 'module_name': ['主摄']}, 'first canonical scope changed')
    must(first['current_query_delta']['explicit_where_filters'] == first_scope['where_filters'], 'current query delta audit invalid')
    must(first_result['jf_memory_write_ready'] == 'true', 'persist-ready output missing')

    second_result, second, second_context = compile('它的Sensor型号呢。', first_result['jf_current_execution_context_json'])
    must(second_context['previous_user_query'] == '请查询 vvo X200 Pto 的主摄规格。', 'previous query missing')
    must(second_context['current_user_query'] == '它的Sensor型号呢。', 'current query missing')
    must(second_context['query_scopes'][0] == first_scope and len(second_context['query_scopes']) in (1, 2), 'field continuation did not preserve/deduplicate immutably')
    must('sensor_model' in second['select_fields'], 'continued field absent from SELECT')

    _, module_plan, module_context = compile('长焦呢。', first_result['jf_current_execution_context_json'])
    must([scope['where_filters']['module_name'] for scope in module_context['query_scopes']] == [['主摄'], ['长焦']], 'module scopes not independent')
    must(' OR ' in module_plan['sql'] and module_plan['params'][:4] == ['Vivo X200 pro', '主摄', 'Vivo X200 pro', '长焦'], 'module OR SQL/params invalid')

    _, compare, compare_context = compile('和小米15 Ultra对比一下。', first_result['jf_current_execution_context_json'])
    must([scope['where_filters']['device_model'] for scope in compare_context['query_scopes']] == [['Vivo X200 pro'], ['小米15Ultra']], 'model scopes not independent')
    must(compare['sql'].count('`device_model` IN (?)') == 2 and ' OR ' in compare['sql'], 'scope SQL was flattened')
    must(compare['params'][:4] == ['Vivo X200 pro', '主摄', '小米15Ultra', '主摄'], 'stable scope params changed')

    _, analysis, analysis_context = compile('分析一下这些规格。', first_result['jf_current_execution_context_json'])
    must(analysis_context['query_scopes'] == [first_scope], 'empty delta appended a scope')
    must(analysis['query_scopes'] == [first_scope], 'memory scope not re-queried')

    _, duplicate, duplicate_context = compile('请查询 Vivo X200 Pro 的主摄规格。', first_result['jf_current_execution_context_json'])
    must(len(duplicate_context['query_scopes']) == 1, 'identical scope was duplicated')

    empty_result, empty_plan, empty_context = compile('分析一下。', '')
    must(empty_context['query_scopes'] == [] and empty_result['jf_memory_write_ready'] == 'false', 'empty scope became persistable')
    must(empty_plan['params'] == [200] and any(w.get('type') == 'NO_STRUCTURED_FILTER_FOUND' for w in empty_plan['warnings']), 'no-filter compatibility changed')

    for invalid in ('{', json.dumps({'version': 'bad', 'query_scopes': [first_scope]}), json.dumps({'version': 'jf_query_memory_v1', 'query_scopes': [{'source_user_query': 'x', 'where_filters': {'unregistered': ['x']}, 'select_fields': [], 'selected_composites': []}], 'sql': 'DELETE FROM x', 'params': ['x']})):
        _, invalid_plan, invalid_context = compile('查询Sensor型号。', invalid)
        must(invalid_context['query_scopes'] == [], 'invalid memory entered execution context')
        must(any(w.get('type') == 'QUERY_MEMORY_INVALID_IGNORED' for w in invalid_plan['warnings']), 'invalid memory warning missing')
        must('DELETE' not in invalid_plan['sql'] and invalid_plan['params'] == [200], 'untrusted memory SQL/params executed')

    # Same WHERE with different field semantics stays in context but compiles one WHERE group.
    same_where_memory = dict(first_context)
    extra = dict(first_scope)
    extra['select_fields'] = list(first_scope['select_fields']) + ['sensor_model']
    same_where_memory['query_scopes'] = [first_scope, extra]
    _, dedup_plan, dedup_context = compile('分析一下。', json.dumps(same_where_memory, ensure_ascii=False))
    must(len(dedup_context['query_scopes']) == 2, 'semantic scopes were removed from context')
    must(dedup_plan['sql'].count('`device_model` IN (?)') == 1, 'duplicate WHERE execution group retained')
    must('sensor_model' in dedup_plan['select_fields'], 'scope SELECT union missing')
    return 16

def graph_memory_contract(doc, nodes, out, inc):
    conversation = {item['name']: item for item in doc['workflow'].get('conversation_variables', [])}
    memory = conversation.get('jf_query_memory_json')
    must(memory and memory['selector'] == ['conversation', 'jf_query_memory_json'] and memory['value'] == '{}' and memory['value_type'] == 'string', 'conversation variable schema invalid')
    plan = nodes['jf_sql_plan']['data']
    must(any(v['variable'] == 'jf_query_memory_json' and v['value_selector'] == ['conversation', 'jf_query_memory_json'] for v in plan['variables']), 'plan memory selector invalid')
    must({'jf_current_execution_context_json', 'jf_memory_write_ready'} <= set(plan['outputs']), 'plan memory outputs missing')
    assign = nodes['save_query_memory']['data']['items'][0]
    must(assign['variable_selector'] == ['conversation', 'jf_query_memory_json'] and assign['value'] == ['jf_sql_plan', 'jf_current_execution_context_json'], 'assigner selector invalid')
    must(any(source == 'if_sql_ok' and handle == 'success' for source, edges in out.items() for handle, target, _ in edges if target == 'if_memory_write'), 'memory branch not downstream of MySQL success')
    must(not inc.get('save_query_memory') == [], 'memory assigner orphaned')
    for node in nodes.values():
        data = node['data']
        if data.get('type') == 'code':
            must(data.get('source_code') == data.get('code'), 'source_code != code: %s' % node['id'])

def main():
    d = load_doc()
    nodes, title, out, inc = graph(d)
    check_frontend_checkvalid_schema(d)
    sample_results = parse_slot_samples(nodes)
    fuzzy_count = fuzzy_device_model_samples(nodes)
    memory_count = query_memory_regressions(d, nodes)
    graph_memory_contract(d, nodes, out, inc)
    print('PASS frontend schema')
    for idx, actual in enumerate(sample_results, 1):
        print('PASS slot sample %d target_objects: %s' % (idx, json.dumps(actual, ensure_ascii=False)))
    print('PASS fuzzy device_model regression groups: %d' % fuzzy_count)
    print('PASS query memory regression groups: %d' % memory_count)
    print('PASS query memory graph/variable/frontend contracts')
if __name__=='__main__':
    try: main()
    except Exception as e: print(f'FAIL: {e}', file=sys.stderr); raise SystemExit(1)
