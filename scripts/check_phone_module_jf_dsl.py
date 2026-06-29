#!/usr/bin/env python3
"""Static graph/layout/regression checks for the PHONE_MODULE_JF Dify chatflow DSL."""
from __future__ import annotations
import json, re, sys
from collections import defaultdict, deque
from pathlib import Path
DSL_PATH=Path('NL2SEARCH_CHATFLOW_DSL/PHONE_MODULE_JF_CHATFLOW.yml')
PART_RE=re.compile(r'PHONE_MODULE_JF_CHATFLOW_(\d+)\.yml$')
DATASET_ID='4d7c7b04-e8d5-47cf-8bd1-dfdfe6022cb7'
def fail(msg): raise AssertionError(msg)
def parts():
    ps=sorted([p for p in DSL_PATH.parent.glob('PHONE_MODULE_JF_CHATFLOW_*.yml') if PART_RE.match(p.name)], key=lambda p:int(PART_RE.match(p.name).group(1)))
    if not ps: fail('PHONE fragments missing')
    nums=[int(PART_RE.match(p.name).group(1)) for p in ps]
    if nums!=list(range(nums[0], nums[0]+len(nums))): fail(f'PHONE fragments must be continuous: {nums}')
    return ps
def load_text():
    merged=''.join(p.read_text(encoding='utf-8') for p in parts())
    if DSL_PATH.read_text(encoding='utf-8')!=merged: fail('PHONE_MODULE_JF_CHATFLOW.yml differs from raw-concatenated fragments; run merge_chatflow_yml.py')
    return merged
def main():
    doc=json.loads(load_text()); g=doc.get('workflow',{}).get('graph',{})
    nodes=g.get('nodes'); edges=g.get('edges')
    if not isinstance(nodes,list) or not isinstance(edges,list): fail('workflow.graph.nodes/edges must be arrays')
    ids=[n.get('id') for n in nodes]; eids=[e.get('id') for e in edges]
    if len(ids)!=len(set(ids)): fail('node id must be unique')
    if len(eids)!=len(set(eids)): fail('edge id must be unique')
    by={n['id']:n for n in nodes}; title={n.get('data',{}).get('title'):n for n in nodes}
    dup=set(); seen=set(); incoming=defaultdict(list); outgoing=defaultdict(list)
    for e in edges:
        if e.get('source') not in by: fail(f'edge source missing: {e}')
        if e.get('target') not in by: fail(f'edge target missing: {e}')
        key=(e.get('source'),e.get('target'),e.get('sourceHandle'),e.get('targetHandle'))
        if key in seen: dup.add(key)
        seen.add(key); incoming[e['target']].append(e); outgoing[e['source']].append(e)
        if e.get('targetHandle')!='target': fail(f'edge targetHandle must be target for Dify node input: {e}')
        if by[e['source']]['data'].get('type')!='if-else' and e.get('sourceHandle')!='source': fail(f'non-IF sourceHandle must be source: {e}')
    if dup: fail(f'duplicate edge tuples: {dup}')
    starts=[n['id'] for n in nodes if n.get('data',{}).get('type')=='start']
    if len(starts)!=1: fail(f'exactly one Start required, got {starts}')
    start=starts[0]
    for n in nodes:
        typ=n['data'].get('type')
        if n['id']!=start and not incoming[n['id']]: fail(f'orphan node without upstream edge: {n['data'].get('title')}')
        if typ!='answer' and not outgoing[n['id']]: fail(f'non-End node without downstream edge: {n['data'].get('title')}')
    # reachability and can reach end
    adj={k:[e['target'] for e in v] for k,v in outgoing.items()}; rev=defaultdict(list)
    for e in edges: rev[e['target']].append(e['source'])
    reach=set([start]); q=deque([start])
    while q:
        c=q.popleft()
        for nx in adj.get(c,[]):
            if nx not in reach: reach.add(nx); q.append(nx)
    un=[by[i]['data'].get('title') for i in ids if i not in reach]
    if un: fail(f'business node unreachable from Start: {un}')
    ends=[n['id'] for n in nodes if n['data'].get('type')=='answer']
    can=set(ends); q=deque(ends)
    while q:
        c=q.popleft()
        for p in rev.get(c,[]):
            if p not in can: can.add(p); q.append(p)
    bad=[by[i]['data'].get('title') for i in ids if by[i]['data'].get('type')!='answer' and i not in can]
    if bad: fail(f'nodes cannot reach End: {bad}')
    # IF handles: explicit cases plus Dify implicit false/default branch
    for n in nodes:
        if n['data'].get('type')=='if-else':
            handles={c.get('id') for c in n['data'].get('cases',[]) if c.get('id')}; handles.add('false')
            used={e.get('sourceHandle') for e in outgoing[n['id']]}
            if not used <= handles: fail(f"IF branch handle mismatch at {n['data'].get('title')}: used={used}, handles={handles}")
            required={c.get('id') for c in n['data'].get('cases',[]) if c.get('id')}
            if n['data'].get('title') in {'IF_output_type是否为报告类','IF_满血版LLM开关','IF_满血版Token是否成功','IF_满血版LLM是否成功'}:
                required.add('false')
            for h in required:
                if h not in used:
                    fail(f"IF branch {h} has no outgoing edge at {n['data'].get('title')}")
    # layout
    seenpos={}
    for n in nodes:
        pos=n.get('position')
        if not isinstance(pos,dict) or not isinstance(pos.get('x'),(int,float)) or not isinstance(pos.get('y'),(int,float)): fail(f"Node {n['id']} missing numeric position")
        if abs(pos['x'])>10000 or abs(pos['y'])>3000: fail(f"Node {n['id']} position drifts too far: {pos}")
        if n.get('positionAbsolute') != pos:
            fail(f"Node {n['id']} positionAbsolute must match position for Dify layout")
        xy=(pos['x'],pos['y'])
        if xy in seenpos: fail(f"position overlap: {xy} for {seenpos[xy]} and {n['id']}")
        seenpos[xy]=n['id']
    mainline=['start','ctx','kr','route','parse','validate','ifslot','plan','mongo','http','norm','analysis','ifreport','final','ans']
    xs=[by[i]['position']['x'] for i in mainline]
    if xs!=sorted(xs): fail(f'main flow x positions must be non-decreasing: {list(zip(mainline,xs))}')
    # expected edges / critical paths
    def node(t): return title.get(t) or fail(f'{t} node missing')
    def edge_to(src,h,dst): return any(e['source']==src['id'] and e.get('sourceHandle')==h and e['target']==dst['id'] for e in edges)
    checks=[('IF_是否缺槽','need_slot','代码执行_生成填槽请求'),('IF_是否缺槽','success','代码执行_构建QueryPlan'),('IF_output_type是否为报告类','report','代码执行_准备报告LLM输入'),('IF_output_type是否为报告类','false','代码执行_合并最终回答'),('IF_满血版LLM开关','enabled','代码执行_准备满血版Token请求'),('IF_满血版LLM开关','false','LLM_生成竞分对比报告_本地版'),('IF_满血版Token是否成功','ok','代码执行_准备满血版LLM请求'),('IF_满血版Token是否成功','false','LLM_生成竞分对比报告_本地版')]
    for a,h,b in checks:
        if not edge_to(node(a),h,node(b)): fail(f'missing graph branch {a} --{h}--> {b}')
    def path(src,dst,banned=()):
        s=node(src)['id']; t=node(dst)['id']; ban=set(banned); qq=deque([s]); seen=set()
        while qq:
            c=qq.popleft()
            if c==t: return True
            if c in seen: continue
            seen.add(c)
            for nx in adj.get(c,[]):
                if by[nx]['data'].get('title') not in ban: qq.append(nx)
        return False
    banned={'代码执行_准备报告LLM输入','LLM_生成竞分对比报告_本地版','HTTP请求_获取满血版LLM Token','代码执行_准备满血版LLM请求','HTTP请求_调用满血版LLM接口'}
    if not path('IF_output_type是否为报告类','代码执行_合并最终回答',banned): fail('non-report branch must reach final without report/full LLM nodes')
    for a,b in [('IF_output_type是否为报告类','代码执行_准备报告LLM输入'),('IF_满血版LLM开关','HTTP请求_获取满血版LLM Token'),('IF_满血版Token是否成功','HTTP请求_调用满血版LLM接口'),('IF_满血版Token是否成功','LLM_生成竞分对比报告_本地版')]:
        if not path(a,b): fail(f'path missing: {a} -> {b}')
    # variable producers upstream
    owners=defaultdict(set)
    for n in nodes:
        for o in (n.get('data',{}).get('outputs') or {}): owners[o].add(n['id'])
        if n.get('data',{}).get('title') == 'HTTP请求_执行Mongo查询':
            owners['mongo_result_json'].add(n['id'])
    required=['route_card_json','slot_validate_result_json','query_plan_json','mongo_request_json','mongo_result_json','normalized_query_result_json','analysis_result_json','report_input_json','token_request_body_json','full_llm_token_result_json','full_llm_request_body_json','final_answer']
    for r in required:
        if r not in owners: fail(f'variable producer missing: {r}')
    # ensure representative consumers have upstream producer
    consumers={'代码执行_构建QueryPlan':['slot_validate_result_json'],'代码执行_准备报告LLM输入':['analysis_result_json'],'代码执行_准备满血版Token请求':['report_input_json'],'代码执行_准备满血版LLM请求':['report_input_json','full_llm_token_result_json'],'代码执行_合并最终回答':['analysis_result_json']}
    def upstream(nid):
        s=set(); qq=deque(rev[nid])
        while qq:
            c=qq.popleft()
            if c not in s: s.add(c); qq.extend(rev[c])
        return s
    for t,vars in consumers.items():
        up=upstream(node(t)['id'])
        for v in vars:
            if not owners[v] & up: fail(f'{t} input {v} has no upstream reachable producer')
    dataset_ids=[]
    for n in nodes: dataset_ids.extend(n.get('data',{}).get('dataset_ids',[]) or [])
    if DATASET_ID not in dataset_ids: fail(f'dataset_ids must include {DATASET_ID}')
    print(f'OK: {len(nodes)} nodes, {len(edges)} edges, reachability/layout/IF branch/producer checks passed')
    return 0
if __name__=='__main__':
    try: raise SystemExit(main())
    except AssertionError as e:
        print(f'FAIL: {e}', file=sys.stderr); raise SystemExit(1)
