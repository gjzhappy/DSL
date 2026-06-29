#!/usr/bin/env python3
import json, re, types
from pathlib import Path
p=Path('NL2SEARCH_CHATFLOW_DSL/PHONE_MODULE_JF_CHATFLOW.yml')
part_re=re.compile(r'PHONE_MODULE_JF_CHATFLOW_(\d+)\.yml$')
parts=sorted([x for x in p.parent.glob('PHONE_MODULE_JF_CHATFLOW_*.yml') if part_re.match(x.name)], key=lambda x:int(part_re.match(x.name).group(1)))
if parts:
    text=''.join(x.read_text(encoding='utf-8') for x in parts)
    if p.exists() and p.read_text(encoding='utf-8') != text:
        raise SystemExit('PHONE_MODULE_JF_CHATFLOW.yml differs from fragments; run merge_chatflow_yml.py')
    d=json.loads(text)
else:
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
('7','请对比iPhone17和Xiaomi17的主摄功耗，并输出对比表', True, None),
('8','请对比iPhone17和Xiaomi17的主摄规格，并生成对比报告', True, None),
('9','请对比iPhone17和Xiaomi17的主摄规格，并生成对比报告', True, None),
('10','请对比iPhone17和Xiaomi17的主摄规格，并生成对比报告', True, None),
('11','请对比iPhone17和Xiaomi17的主摄规格，并生成对比报告', True, None),
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


# Report LLM graph regression samples 7-11.
title={n['data'].get('title'): n['id'] for n in d['workflow']['graph']['nodes']}
adj={}
for e in d['workflow']['graph']['edges']:
    adj.setdefault(e['source'], []).append((e.get('sourceHandle'), e['target']))
def must_edge(src_title, handle, dst_title):
    src=title[src_title]; dst=title[dst_title]
    if (handle,dst) not in adj.get(src, []):
        raise SystemExit(f'missing graph branch {src_title} --{handle}--> {dst_title}')
must_edge('IF_output_type是否为报告类','false','代码执行_合并最终回答')
must_edge('IF_output_type是否为报告类','report','代码执行_准备报告LLM输入')
must_edge('IF_满血版LLM开关','false','LLM_生成竞分对比报告_本地版')
must_edge('IF_满血版LLM开关','enabled','代码执行_准备满血版Token请求')
must_edge('IF_满血版Token是否成功','false','LLM_生成竞分对比报告_本地版')
must_edge('IF_满血版LLM是否成功','false','LLM_生成竞分对比报告_本地版')
must_edge('IF_满血版LLM是否成功','ok','代码执行_合并最终回答')

def reachable(src_title, dst_title, banned=()):
    src=title[src_title]; dst=title[dst_title]
    seen=set(); stack=[src]
    while stack:
        cur=stack.pop()
        if cur == dst: return True
        if cur in seen: continue
        seen.add(cur)
        for _, nxt in adj.get(cur, []):
            nt=nodes[nxt]['data'].get('title','')
            if nt in banned: continue
            stack.append(nxt)
    return False
if not reachable('IF_output_type是否为报告类','代码执行_合并最终回答', {'LLM_生成竞分对比报告_本地版','HTTP请求_获取满血版LLM Token','HTTP请求_调用满血版LLM接口'}):
    raise SystemExit('non-report branch can enter report/full LLM path')
if not reachable('IF_output_type是否为报告类','代码执行_准备报告LLM输入'):
    raise SystemExit('report branch cannot reach report LLM input')
if not reachable('IF_是否缺槽','代码执行_构建QueryPlan'):
    raise SystemExit('QueryPlan branch unreachable')
if not reachable('IF_是否缺槽','代码执行_生成填槽请求'):
    raise SystemExit('slot-fill branch unreachable')
print('PASS report LLM graph regression samples 7-11')

# Additional graph-path regressions for Dify branch semantics.
edges=d['workflow']['graph']['edges']
by_id=nodes
title_by_id={n['id']: n['data'].get('title') for n in d['workflow']['graph']['nodes']}
case_handles={n['id']:{c.get('id') for c in n['data'].get('cases',[]) if c.get('id')}|({'false'} if n['data'].get('type')=='if-else' else set()) for n in d['workflow']['graph']['nodes']}
for e in edges:
    if by_id[e['source']]['data'].get('type')=='if-else' and e.get('sourceHandle') not in case_handles[e['source']]:
        raise SystemExit(f'IF sourceHandle not in branch config: {title_by_id[e["source"]]} {e.get("sourceHandle")}')
    if e.get('targetHandle')!='target':
        raise SystemExit(f'non-Dify targetHandle: {e}')

def reachable_handles(src_title, dst_title, allowed_first_handle=None, banned_titles=()):
    src=title[src_title]; dst=title[dst_title]
    seen=set(); stack=[(src, True, [])]
    banned=set(banned_titles)
    while stack:
        cur, first, path=stack.pop()
        if cur==dst: return path+[cur]
        if cur in seen: continue
        seen.add(cur)
        for h,nxt in adj.get(cur,[]):
            if first and allowed_first_handle is not None and h!=allowed_first_handle: continue
            if title_by_id[nxt] in banned: continue
            stack.append((nxt, False, path+[cur]))
    return None
non_report_banned={'代码执行_准备报告LLM输入','LLM_生成竞分对比报告_本地版','代码执行_准备满血版Token请求','HTTP请求_获取满血版LLM Token','代码执行_准备满血版LLM请求','HTTP请求_调用满血版LLM接口'}
if not reachable_handles('IF_是否缺槽','结束_返回填槽请求','need_slot'):
    raise SystemExit('缺槽问题不能进入填槽请求 End')
if not reachable_handles('IF_是否缺槽','代码执行_构建QueryPlan','success'):
    raise SystemExit('槽位完整问题不能进入 QueryPlan')
if not reachable_handles('IF_output_type是否为报告类','结束_返回最终回答','false',non_report_banned):
    raise SystemExit('非报告路径未避开报告/full LLM 到达最终 End')
if not reachable_handles('IF_output_type是否为报告类','代码执行_准备报告LLM输入','report'):
    raise SystemExit('报告类问题未进入报告分支')
if not reachable_handles('IF_满血版LLM开关','LLM_生成竞分对比报告_本地版','false',{'代码执行_准备满血版Token请求','HTTP请求_获取满血版LLM Token','代码执行_准备满血版LLM请求','HTTP请求_调用满血版LLM接口'}):
    raise SystemExit('ENABLE_FULL_LLM=false 未避开满血版链路')
if not reachable_handles('IF_满血版LLM开关','HTTP请求_调用满血版LLM接口','enabled'):
    raise SystemExit('ENABLE_FULL_LLM=true token ok 不能进入满血版 LLM 请求')
if not reachable_handles('IF_满血版Token是否成功','结束_返回最终回答','false',{'代码执行_准备满血版LLM请求','HTTP请求_调用满血版LLM接口'}):
    raise SystemExit('token 失败未降级到最终合并路径')
print('PASS graph-path branch regressions')
