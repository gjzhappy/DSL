#!/usr/bin/env python3
from __future__ import annotations
import sys
from collections import defaultdict, deque
from graph_phone_common import load_doc, graph, path, check_frontend_checkvalid_schema, check_ifslot_validate_contract

def fail(m): raise AssertionError(m)
def main():
    d=load_doc()
    if d.get('version')!='0.6.0': fail('version == 0.6.0 failed')
    if d.get('kind')!='app': fail('kind == app failed')
    if d.get('app',{}).get('mode')!='advanced-chat': fail('app.mode == advanced-chat failed')
    g=d.get('workflow',{}).get('graph',{})
    nodes_list=g.get('nodes',[]); edges=g.get('edges',[])
    if not nodes_list or not edges or not g.get('viewport'): fail('graph nodes/edges/viewport required')
    check_frontend_checkvalid_schema(d)
    check_ifslot_validate_contract(d)
    ids=[n['id'] for n in nodes_list]; eids=[e['id'] for e in edges]
    if len(ids)!=len(set(ids)): fail('node id unique failed')
    if len(eids)!=len(set(eids)): fail('edge id unique failed')
    nodes,title,out,inc=graph(d)
    for e in edges:
        if e['source'] not in nodes or e['target'] not in nodes: fail(f'dangling edge: {e}')
        if e.get('targetHandle')!='target': fail(f'bad targetHandle: {e}')
    start=[i for i,n in nodes.items() if n['data']['type']=='start']
    if len(start)!=1: fail('one start required')
    orphan=[i for i in ids if i!=start[0] and not inc[i]]
    if orphan: fail(f'orphan nodes: {orphan}')
    for i,n in nodes.items():
        if n['data']['type']!='answer' and not out[i]: fail(f'non-End without outgoing: {i}')
        if n.get('position') != n.get('positionAbsolute'): fail(f'position sync failed: {i}')
        if not all(k in n for k in ('width','height')): fail(f'size missing: {i}')
    rects={}
    for i,n in nodes.items():
        x=n['position']['x']; y=n['position']['y']; w=n['width']; h=n['height']
        for j,(ox,oy,ow,oh) in rects.items():
            if x < ox+ow and x+w > ox and y < oy+oh and y+h > oy: fail(f'position overlap: {i} {j}')
        rects[i]=(x,y,w,h)
    adj={k:[t for _,t,_ in v] for k,v in out.items()}; q=deque(start); reach=set(start)
    while q:
        c=q.popleft()
        for nx in adj.get(c,[]):
            if nx not in reach: reach.add(nx); q.append(nx)
    if len(reach)!=len(ids): fail(f'unreachable from Start: {set(ids)-reach}')
    rev=defaultdict(list)
    for e in edges: rev[e['target']].append(e['source'])
    ends=[i for i,n in nodes.items() if n['data']['type']=='answer']; can=set(ends); q=deque(ends)
    while q:
        c=q.popleft()
        for p in rev[c]:
            if p not in can: can.add(p); q.append(p)
    bad=[i for i,n in nodes.items() if n['data']['type']!='answer' and i not in can]
    if bad: fail(f'nodes cannot reach End: {bad}')
    for i,n in nodes.items():
        if n['data']['type']=='if-else':
            handles={c['case_id'] for c in n['data'].get('cases',[])}|{'false'}
            used={h for h,_,_ in out[i]}
            if None in used: fail(f'null edge must not be treated as branch: {i}')
            if not used <= handles: fail(f'IF branch table invalid: {i} used={used} cases={handles}')
    banned={'代码执行_准备报告LLM输入','LLM_生成竞分对比报告_本地版','代码执行_准备满血版Token请求','HTTP请求_获取满血版LLM Token','代码执行_准备满血版LLM请求','HTTP请求_调用满血版LLM接口'}
    assert path(title,out,nodes,'IF_output_type是否为报告类','结束_返回最终回答','false',banned)
    assert path(title,out,nodes,'IF_output_type是否为报告类','代码执行_准备报告LLM输入','report')
    assert path(title,out,nodes,'IF_满血版LLM开关','LLM_生成竞分对比报告_本地版','false',{'代码执行_准备满血版Token请求','HTTP请求_获取满血版LLM Token','HTTP请求_调用满血版LLM接口'})
    assert path(title,out,nodes,'IF_满血版LLM开关','HTTP请求_调用满血版LLM接口','enabled')
    assert path(title,out,nodes,'IF_满血版Token是否成功','LLM_生成竞分对比报告_本地版','false',{'代码执行_准备满血版LLM请求','HTTP请求_调用满血版LLM接口'})
    assert path(title,out,nodes,'IF_满血版LLM是否成功','LLM_生成竞分对比报告_本地版','false')
    producers=defaultdict(set)
    for n in nodes.values():
        for v in n['data'].get('outputs',{}) or {}: producers[v].add(n['id'])
    for v in ['route_card_json','slot_validate_result_json','ready_to_query_flag','query_plan_json','mongo_request_json','mongo_result_json','normalized_query_result_json','analysis_result_json','report_input_json','full_llm_token_request_body_json','full_llm_token_result_json','full_llm_request_body_json','final_answer']:
        if v not in producers: fail(f'variable producer missing: {v}')
    print(f'OK: {len(nodes)} nodes, {len(edges)} edges; orphan=0 dangling=0 position_overlap=0')
if __name__=='__main__':
    try: main()
    except Exception as e: print(f'FAIL: {e}', file=sys.stderr); raise SystemExit(1)
