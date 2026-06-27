#!/usr/bin/env python3
import json, types
from pathlib import Path
p=Path('NL2SEARCH_CHATFLOW_DSL/PHONE_MODULE_JF_CHATFLOW.yml')
d=json.load(open(p,encoding='utf-8'))
nodes={n['id']:n for n in d['workflow']['graph']['nodes']}
def run_code(nid, **kw):
    ns={}
    exec(nodes[nid]['data']['source_code'], ns)
    return ns['main'](**kw)
samples=[
('1','请对比iPhone 17与小米17的主摄CIS基础规格，包括靶面、像素大小、像素数量等，请生成CIS规格对比报告', True, None),
('2','请对比iPhone17、Xiaomi17、OPPO17的主摄规格，并输出对比表', True, 3),
('3','请对比主摄CIS规格，并生成报告', False, None),
('4','请对比iPhone17和Xiaomi17的CIS规格，并生成报告', False, None),
('5','请对比iPhone17和Xiaomi17的主摄供电拓扑，并生成对比报告', True, None),
('6','请对比iPhone17和Xiaomi17的主摄功耗，并输出对比表', True, None),
]
for sid,q,ready_exp,in_count in samples:
    ctx=run_code('ctx', question=q, pending_route_card_json='', partial_placeholder_values_json='', pending_route_id='', pending_missing_slots_json='')
    route=run_code('route', context_json=ctx['context_json'], retrieval_result=[])
    parse=run_code('parse', context_json=ctx['context_json'], route_card_json=route['route_card_json'])
    val=run_code('validate', route_card_json=route['route_card_json'], slot_parse_result_json=parse['slot_parse_result_json'])
    sv=json.loads(val['slot_validate_result_json'])
    if sv['ready_to_query'] != ready_exp: raise SystemExit(f'sample {sid} ready expected {ready_exp} got {sv}')
    if ready_exp:
        mongo=run_code('mongo', slot_validate_result_json=val['slot_validate_result_json'])
        mr=json.loads(mongo['mongo_request_json'])
        if mr['status']!='ok': raise SystemExit(f'sample {sid} mongo not ok {mr}')
        pipe=mr['mongo_request']['pipeline']
        if len([st for st in pipe if '$match' in st])!=1: raise SystemExit(f'sample {sid} expected one match')
        targets=pipe[0]['$match']['phone_model']['$in']
        if in_count and len(targets)!=in_count: raise SystemExit(f'sample {sid} expected $in count {in_count} got {targets}')
        print(f'PASS sample {sid}: ready_to_query=true targets={targets} template={sv.get("selected_template_id")}')
    else:
        print(f'PASS sample {sid}: ready_to_query=false missing={[m["slot"] for m in sv["missing_slots"]]}')
print('PASS all regression samples')
