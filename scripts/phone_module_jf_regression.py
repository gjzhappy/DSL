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

    def registry(models, fuzzy=True, gap=0.08, min_alias_length=5, min_score=0.84):
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
                'min_score': min_score, 'min_score_gap': gap,
                'distance_rules': [{'max_length': 7, 'max_distance': 1}, {'max_length': 12, 'max_distance': 2}, {'max_length': 999, 'max_distance': 2}],
                'protected_tokens': ['pro', 'promax', 'max', 'ultra', 'plus', 'mini', 'se'],
                'require_protected_tokens_equal': True,
            }}}
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
    for typo in ('Maigic6', 'Magc6', 'Mgaic6'):
        # Magic6↔Magc6 scores 5/6 under the mandated formula, so that
        # algorithm case uses an explicitly lower test policy threshold.
        plan = compile(typo, registry(base, min_score=0.84 if typo == 'Maigic6' else 0.83))
        must(plan['where_filters'].get('device_model') == ['Magic6'], f"fuzzy typo failed: {typo}: {plan}")
        must(plan['fuzzy_alias_hits']['device_model'][0]['target'] == 'Magic6', f'fuzzy audit failed: {typo}')
    plan = compile('对比 iPhone16 Pro Max 和 Maigic6', registry(base))
    must(plan['where_filters']['device_model'] == ['Iphone16 Pro Max', 'Magic6'], 'partial exact/fuzzy order failed')
    must(plan['value_alias_hits']['device_model'] == ['Iphone16 Pro Max'], 'exact alias audit changed')
    must(compile('Magic7', registry(base))['where_filters'].get('device_model') is None, 'digit gate failed')
    suffixes = {'Magic6': {'aliases': ['magic6']}, 'Magic6 Pro': {'aliases': ['magic6pro']}}
    must(compile('Magic6', registry(suffixes))['where_filters']['device_model'] == ['Magic6'], 'protected suffix addition failed')
    must(compile('Magic6 Pro', registry(suffixes))['where_filters']['device_model'] == ['Magic6 Pro'], 'protected suffix removal failed')
    same = {'Magic6': {'aliases': ['magic6', 'magix6']}}
    plan = compile('magio6', registry(same, gap=0.20, min_score=0.83))
    must(plan['where_filters']['device_model'] == ['Magic6'], 'same-canonical alias merge failed')
    ambiguous = {'ModelA': {'aliases': ['magic6']}, 'ModelB': {'aliases': ['magix6']}}
    plan = compile('magio6', registry(ambiguous, gap=0.20, min_score=0.83))
    must(plan['where_filters']['device_model'] == ['ModelA', 'ModelB'], 'small score gap failed')
    must('matches' in plan['fuzzy_alias_hits']['device_model'][0], 'ambiguous audit failed')
    plan = compile('magix6', registry(ambiguous, gap=0.08))
    must(plan['where_filters']['device_model'] == ['ModelB'], 'large score gap/exact priority failed')
    short = {'SE': {'aliases': ['se']}}
    must(compile('sx', registry(short))['where_filters'].get('device_model') is None, 'short alias fuzzy gate failed')
    must(compile('se', registry(short))['where_filters']['device_model'] == ['SE'], 'short alias exact failed')
    no_fuzzy = compile('honot', registry(base, fuzzy=False))
    must(no_fuzzy['where_filters'].get('manufacturer') is None and not no_fuzzy.get('fuzzy_alias_hits'), 'unconfigured field fuzzy changed')
    exact = compile('Magic6', registry(base))
    must(exact['where_filters']['device_model'] == ['Magic6'] and not exact.get('fuzzy_alias_hits'), 'exact priority failed')
    must(compile('查询主摄规格', registry(base))['where_filters'].get('device_model') is None, 'no-model false positive')

    invalid = registry(base)
    invalid['match_policies']['device_model']['fuzzy']['min_score'] = 1.1
    parsed = parse_ns['main'](json.dumps(invalid, ensure_ascii=False), 'ok')
    must(parsed['jf_registry_parse_status'] == 'error', 'invalid fuzzy policy accepted')
    return 15

def main():
    d = load_doc()
    nodes, title, out, inc = graph(d)
    check_frontend_checkvalid_schema(d)
    sample_results = parse_slot_samples(nodes)
    fuzzy_count = fuzzy_device_model_samples(nodes)
    print('PASS frontend schema')
    for idx, actual in enumerate(sample_results, 1):
        print('PASS slot sample %d target_objects: %s' % (idx, json.dumps(actual, ensure_ascii=False)))
    print('PASS fuzzy device_model regression groups: %d' % fuzzy_count)
if __name__=='__main__':
    try: main()
    except Exception as e: print(f'FAIL: {e}', file=sys.stderr); raise SystemExit(1)
