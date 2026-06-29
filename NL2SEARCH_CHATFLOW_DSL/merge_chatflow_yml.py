#!/usr/bin/env python3
"""Validate or print split-only PHONE_MODULE_JF_CHATFLOW fragments.

Default and --check never write PHONE_MODULE_JF_CHATFLOW.yml.  Fragments are
raw-concatenated in memory in numeric order.
"""
from __future__ import annotations
import argparse, json, re, sys
from collections import defaultdict, deque
from pathlib import Path
BASE_DIR=Path(__file__).resolve().parent
PART_RE=re.compile(r'PHONE_MODULE_JF_CHATFLOW_(\d+)\.yml$')

def fail(msg): raise SystemExit(f'FAIL: {msg}')
def discover_parts():
    numbered=[]
    for p in BASE_DIR.glob('PHONE_MODULE_JF_CHATFLOW_*.yml'):
        m=PART_RE.match(p.name)
        if m: numbered.append((int(m.group(1)),p))
    if not numbered: fail('no PHONE_MODULE_JF_CHATFLOW_*.yml fragments found')
    numbered.sort(key=lambda x:(x[0],x[1].name))
    nums=[n for n,_ in numbered]
    if nums != list(range(nums[0], nums[0]+len(nums))): fail(f'fragment numbering must be continuous; found {nums}')
    if nums[0] != 0: fail(f'fragment numbering must start at 0; found {nums[0]}')
    return [p for _,p in numbered]
def merged_text(parts):
    chunks=[]
    for p in parts:
        t=p.read_text(encoding='utf-8')
        if not t: fail(f'fragment is empty: {p.name}')
        chunks.append(t)
    text=''.join(chunks)
    if not text.strip(): fail('merged output is empty')
    return text
def load_doc(text):
    try: return json.loads(text)
    except json.JSONDecodeError as e: fail(f'merged DSL is not parseable YAML/JSON subset: {e}')
def validate(doc):
    if doc.get('version')!='0.6.0': fail('version must be 0.6.0')
    if doc.get('kind')!='app': fail('kind must be app')
    if doc.get('app',{}).get('mode')!='advanced-chat': fail('app.mode must be advanced-chat')
    g=doc.get('workflow',{}).get('graph',{})
    nodes=g.get('nodes'); edges=g.get('edges'); vp=g.get('viewport')
    if not isinstance(nodes,list) or not isinstance(edges,list): fail('workflow.graph.nodes/edges missing')
    if not isinstance(vp,dict) or not all(k in vp for k in ('x','y','zoom')): fail('viewport missing')
    ids=[n.get('id') for n in nodes]; eids=[e.get('id') for e in edges]
    if len(ids)!=len(set(ids)): fail('node id unique check failed')
    if len(eids)!=len(set(eids)): fail('edge id unique check failed')
    by={n['id']:n for n in nodes}; inc=defaultdict(list); out=defaultdict(list)
    for e in edges:
        if e.get('source') not in by: fail(f'dangling edge source: {e}')
        if e.get('target') not in by: fail(f'dangling edge target: {e}')
        inc[e['target']].append(e); out[e['source']].append(e)
        if e.get('targetHandle')!='target': fail(f'targetHandle must be target: {e}')
        if by[e['source']]['data'].get('type')!='if-else' and e.get('sourceHandle')!='source': fail(f'non-IF sourceHandle must be source: {e}')
    starts=[i for i in ids if by[i]['data'].get('type')=='start']
    if len(starts)!=1: fail('exactly one start required')
    start=starts[0]
    for n in nodes:
        if n['id']!=start and not inc[n['id']]: fail(f'orphan node: {n["id"]}')
        if n['data'].get('type')!='answer' and not out[n['id']]: fail(f'non-answer node without outgoing: {n["id"]}')
        pos=n.get('position'); pa=n.get('positionAbsolute')
        if not isinstance(pos,dict) or not isinstance(pos.get('x'),(int,float)) or not isinstance(pos.get('y'),(int,float)): fail(f'position missing: {n["id"]}')
        if pa != pos: fail(f'positionAbsolute mismatch: {n["id"]}')
        if not isinstance(n.get('width'),(int,float)) or not isinstance(n.get('height'),(int,float)): fail(f'size missing: {n["id"]}')
    seen={}
    for n in nodes:
        rect=(n['position']['x'],n['position']['y'],n['width'],n['height'])
        x,y,w,h=rect
        for oid,(ox,oy,ow,oh) in seen.items():
            if x < ox+ow and x+w > ox and y < oy+oh and y+h > oy: fail(f'position overlap: {n["id"]} with {oid}')
        seen[n['id']]=rect
    adj={k:[e['target'] for e in v] for k,v in out.items()}; q=deque([start]); reach={start}
    while q:
        c=q.popleft()
        for nx in adj.get(c,[]):
            if nx not in reach: reach.add(nx); q.append(nx)
    if len(reach)!=len(nodes): fail(f'unreachable nodes: {sorted(set(ids)-reach)}')
    ends=[i for i in ids if by[i]['data'].get('type')=='answer']; rev=defaultdict(list)
    for e in edges: rev[e['target']].append(e['source'])
    can=set(ends); q=deque(ends)
    while q:
        c=q.popleft()
        for p in rev[c]:
            if p not in can: can.add(p); q.append(p)
    bad=[i for i in ids if by[i]['data'].get('type')!='answer' and i not in can]
    if bad: fail(f'nodes cannot reach answer: {bad}')
    for n in nodes:
        if n['data'].get('type')=='if-else':
            handles={c.get('id') for c in n['data'].get('cases',[]) if c.get('id')}|{'false'}
            used={e.get('sourceHandle') for e in out[n['id']]}
            if None in used: fail(f'null compatibility edge is intentionally not used: {n["id"]}')
            if not used <= handles: fail(f'IF handle mismatch at {n["id"]}: {used} vs {handles}')
    producers=defaultdict(set)
    for n in nodes:
        for k in (n.get('data',{}).get('outputs') or {}): producers[k].add(n['id'])
    for var in ['route_card_json','slot_validate_result_json','query_plan_json','mongo_request_json','mongo_result_json','normalized_query_result_json','analysis_result_json','report_input_json','full_llm_token_request_body_json','full_llm_token_result_json','full_llm_request_body_json','full_llm_result_json','local_llm_result_json','final_answer']:
        if var not in producers: fail(f'variable producer missing: {var}')
    print(f'OK: split-only PHONE DSL valid ({len(nodes)} nodes, {len(edges)} edges, {len(discover_parts())} fragments)')
def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--check',action='store_true',help='validate raw-concatenated fragments in memory')
    ap.add_argument('--stdout',action='store_true',help='print raw-concatenated full DSL to stdout')
    args=ap.parse_args(argv)
    parts=discover_parts(); text=merged_text(parts); doc=load_doc(text)
    if args.stdout:
        sys.stdout.write(text); return 0
    validate(doc); return 0
if __name__=='__main__': raise SystemExit(main())
