#!/usr/bin/env python3
from __future__ import annotations
import ast, copy, json, sys
from graph_phone_common import load_doc, graph, path, check_frontend_checkvalid_schema

def must(p,msg):
    if not p: raise AssertionError(msg)

def check_query_memory_schema(doc):
    workflow = doc.get('workflow') or {}
    variables = workflow.get('conversation_variables')
    must(isinstance(variables, list), 'conversation_variables must be list')
    names = []
    for item in variables:
        must(item.get('id') and item.get('name'), 'conversation variable identity missing')
        must(isinstance(item.get('description'), str), 'conversation variable description invalid')
        must(item.get('value_type') == 'string' and isinstance(item.get('value'), str), 'conversation variable string schema invalid')
        must(item.get('selector') == ['conversation', item['name']], 'conversation variable selector invalid')
        names.append(item['name'])
    must(len(names) == len(set(names)) and 'jf_query_memory_json' in names, 'conversation variable missing/duplicate')
    nodes = workflow.get('graph', {}).get('nodes')
    edges = workflow.get('graph', {}).get('edges')
    must(isinstance(nodes, list) and all(isinstance(n.get('data'), dict) for n in nodes), 'node data missing')
    by_id = {n['id']: n for n in nodes}
    must(len(by_id) == len(nodes), 'duplicate node id')
    plan = by_id['jf_sql_plan']['data']
    must(isinstance(plan.get('variables'), list) and isinstance(plan.get('outputs'), dict), 'code schema containers invalid')
    for variable in plan['variables']:
        must(isinstance(variable.get('value_selector'), list) and all(isinstance(x, str) for x in variable['value_selector']), 'selector is not string array')
    args = [a.arg for a in ast.parse(plan['code']).body[0].args.args]
    must(args == [v['variable'] for v in plan['variables']], 'main parameters differ from variables')
    must(plan['source_code'] == plan['code'], 'source_code differs from code')
    for output in plan['outputs'].values():
        must('type' in output and 'children' in output, 'code output schema incomplete')
    must(plan['outputs']['jf_memory_write_ready']['type'] == 'boolean', 'memory ready output is not boolean')
    must("'jf_memory_write_ready': bool(memory_write_ready)" in plan['code'], 'memory ready return is not bool')
    condition = by_id['if_memory_write']['data']['cases'][0]['conditions'][0]
    must(condition.get('id') and condition.get('variable_selector') == ['jf_sql_plan','jf_memory_write_ready'], 'boolean IF selector/id invalid')
    must(condition.get('comparison_operator') == 'is' and condition.get('varType') == 'boolean' and condition.get('value') is True, 'boolean IF contract invalid')
    assign = by_id['save_query_memory']['data']
    must(assign.get('type') == 'assigner' and assign.get('version') == '2' and isinstance(assign.get('desc'), str) and isinstance(assign.get('selected'), bool), 'assigner v2 schema invalid')
    must(isinstance(assign.get('items'), list) and assign['items'], 'assigner items invalid')
    item = assign['items'][0]
    must(item.get('input_type') == 'variable' and item.get('operation') == 'over-write', 'assigner operation invalid')
    must(item.get('variable_selector') == ['conversation','jf_query_memory_json'], 'assigner target invalid')
    must(item.get('value') == ['jf_sql_plan','jf_current_execution_context_json'], 'assigner source invalid')
    ids = [e.get('id') for e in edges]
    must(len(ids) == len(set(ids)), 'duplicate edge id')
    must(all(e.get('source') in by_id and e.get('target') in by_id for e in edges), 'dangling edge')
    triples = {(e['source'],e.get('sourceHandle'),e['target']) for e in edges}
    must({('if_sql_ok','success','if_memory_write'),('if_memory_write','memory_write_true','save_query_memory'),('if_memory_write','false','llm_input'),('save_query_memory','source','llm_input')} <= triples, 'memory graph wiring invalid')

def negative_schema_injections(doc):
    cases = []
    def add(name, mutate): cases.append((name, mutate))
    add('conversation value object', lambda d: d['workflow']['conversation_variables'][0].update(value={}))
    add('conversation description missing', lambda d: d['workflow']['conversation_variables'][0].pop('description'))
    for key in ('children','type'):
        add('output missing '+key, lambda d,k=key: d['workflow']['graph']['nodes'][next(i for i,n in enumerate(d['workflow']['graph']['nodes']) if n['id']=='jf_sql_plan')]['data']['outputs']['jf_memory_write_ready'].pop(k))
    add('boolean declared string', lambda d: next(n for n in d['workflow']['graph']['nodes'] if n['id']=='jf_sql_plan')['data']['outputs']['jf_memory_write_ready'].update(type='string'))
    add('boolean returned string', lambda d: next(n for n in d['workflow']['graph']['nodes'] if n['id']=='jf_sql_plan')['data'].update(code=next(n for n in d['workflow']['graph']['nodes'] if n['id']=='jf_sql_plan')['data']['code'].replace("'jf_memory_write_ready': bool(memory_write_ready)", "'jf_memory_write_ready': 'true'")))
    def cond(d): return next(n for n in d['workflow']['graph']['nodes'] if n['id']=='if_memory_write')['data']['cases'][0]['conditions'][0]
    add('IF string true', lambda d: cond(d).update(value='true'))
    add('IF string vartype', lambda d: cond(d).update(varType='string'))
    add('IF cases missing', lambda d: next(n for n in d['workflow']['graph']['nodes'] if n['id']=='if_memory_write')['data'].pop('cases'))
    add('IF conditions missing', lambda d: next(n for n in d['workflow']['graph']['nodes'] if n['id']=='if_memory_write')['data']['cases'][0].pop('conditions'))
    add('selector scalar', lambda d: cond(d).update(variable_selector='bad'))
    def ass(d): return next(n for n in d['workflow']['graph']['nodes'] if n['id']=='save_query_memory')['data']
    add('assigner old type', lambda d: ass(d).update(type='variable-assigner'))
    add('assigner items missing', lambda d: ass(d).pop('items'))
    add('assigner operation', lambda d: ass(d)['items'][0].update(operation='append'))
    add('assigner input type', lambda d: ass(d)['items'][0].update(input_type='constant'))
    add('assigner target missing', lambda d: ass(d)['items'][0].update(variable_selector=['conversation','missing']))
    add('assigner source missing', lambda d: ass(d)['items'][0].update(value=['jf_sql_plan','missing']))
    add('node data missing', lambda d: next(n for n in d['workflow']['graph']['nodes'] if n['id']=='save_query_memory').pop('data'))
    add('edge target missing', lambda d: d['workflow']['graph']['edges'][0].update(target='missing'))
    caught = 0
    for name, mutate in cases:
        broken = copy.deepcopy(doc); mutate(broken)
        try: check_query_memory_schema(broken)
        except Exception: caught += 1
        else: raise AssertionError('negative injection not caught: '+name)
    return caught

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

    def compile(question, value, memory=''):
        parsed = parse_ns['main'](json.dumps(value, ensure_ascii=False), 'ok')
        must(parsed['jf_registry_parse_status'] == 'ok', parsed['jf_registry_error'])
        result = plan_ns['main'](
            json.dumps({'question': question}, ensure_ascii=False), parsed['jf_registry_json'],
            parsed['jf_alias_index_json'], parsed['jf_registry_parse_status'], parsed['jf_registry_error'], memory)
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

    memory_registry = registry(protected)
    first = compile('vvo X200 Pto', memory_registry)
    memory = json.dumps({
        'version': 'jf_query_memory_v1', 'previous_user_query': '',
        'current_user_query': 'vvo X200 Pto', 'query_scopes': first['query_scopes'],
        'sql': 'must be ignored', 'params': ['must be ignored'],
    }, ensure_ascii=False)
    follow = compile('厂商', memory_registry, memory)
    must(follow['query_scopes'][0]['where_filters'] == {'device_model': ['Vivo X200 pro']}, 'memory filter inheritance failed')
    must('manufacturer' in follow['select_fields'], 'memory field follow-up failed')
    must(follow['params'] == ['Vivo X200 pro', 200], 'memory params are unstable or untrusted fields leaked')
    must(follow['sql'].count('`device_model` IN (?)') == 1, 'scoped WHERE compilation/duplicate removal failed')
    invalid = compile('Magic6', memory_registry, '{bad json')
    must(any(w.get('type') == 'QUERY_MEMORY_INVALID_IGNORED' for w in invalid['warnings']), 'invalid memory warning missing')
    must(invalid['where_filters'] == {'device_model': ['Magic6']}, 'invalid memory did not preserve single-turn behavior')
    return 48

def main():
    d = load_doc()
    nodes, title, out, inc = graph(d)
    check_frontend_checkvalid_schema(d)
    check_query_memory_schema(d)
    negative_count = negative_schema_injections(d)
    sample_results = parse_slot_samples(nodes)
    fuzzy_count = fuzzy_device_model_samples(nodes)
    print('PASS frontend schema')
    print('PASS query-memory schema/graph/contract and negative injections: %d' % negative_count)
    for idx, actual in enumerate(sample_results, 1):
        print('PASS slot sample %d target_objects: %s' % (idx, json.dumps(actual, ensure_ascii=False)))
    print('PASS fuzzy device_model regression groups: %d' % fuzzy_count)
if __name__=='__main__':
    try: main()
    except Exception as e: print(f'FAIL: {e}', file=sys.stderr); raise SystemExit(1)
