#!/usr/bin/env python3
"""Merge, validate, or print PHONE_MODULE_JF_CHATFLOW fragments.

PHONE_MODULE_JF_CHATFLOW_*.yml fragments are the source of truth.  The full
PHONE_MODULE_JF_CHATFLOW.yml file is a local generated Dify import artifact and
must not be maintained as source.
"""
from __future__ import annotations
import argparse, re, subprocess, sys
from collections import defaultdict, deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.graph_phone_common import load_phone_dsl_text

BASE_DIR=Path(__file__).resolve().parent
FULL_FILE=BASE_DIR/'PHONE_MODULE_JF_CHATFLOW.yml'
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
    try:
        doc=load_phone_dsl_text(text)
    except Exception as e:
        fail(f'merged DSL is not parseable YAML/JSON: {e}')
    if not isinstance(doc,dict): fail('merged DSL root must be a mapping/object')
    return doc


ENV_BRACE_RE=re.compile(r'\{\{#env\.([A-Za-z_][A-Za-z0-9_]*)#\}\}')
ENV_DOT_RE=re.compile(r'(?<![A-Za-z0-9_])env\.([A-Za-z_][A-Za-z0-9_]*)')
ENV_DOLLAR_RE=re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')
REQUIRED_ENV_NAMES={
    'ENABLE_FULL_LLM','SN_MONGODB_QUERY_URLS','LLM_TOKEN_URL','LLM_CHAT_URL',
    'LLM_APP_ID','LLM_STATIC_TOKEN','LLM_MODEL','LLM_USER','LLM_BASIC_AUTH',
    'LLM_TIMEOUT_MS','LLM_TEMPERATURE','LLM_TOP_P','LLM_MAX_COMPLETION_TOKENS','LLM_STREAM',
}
DEPRECATED_ENV_NAMES={'MONGO_QUERY_URL','FULL_LLM_TOKEN_URL','FULL_LLM_CHAT_URL'}
EXPECTED_HTTP_URLS={
    'HTTP请求_执行Mongo查询':'{{#env.SN_MONGODB_QUERY_URLS#}}',
    'HTTP请求_获取满血版LLM Token':'{{#env.LLM_TOKEN_URL#}}',
    'HTTP请求_调用满血版LLM接口':'{{#env.LLM_CHAT_URL#}}',
}


def node_label(node):
    data=node.get('data') or {}
    return node.get('id'), data.get('title',''), data.get('type','')


def collect_env_refs(doc):
    refs=defaultdict(list)
    nodes=doc.get('workflow',{}).get('graph',{}).get('nodes') or []
    def add(key, kind, path, node, value):
        nid,title,ntype=node_label(node)
        refs[key].append({'kind':kind,'path':'.'.join(map(str,path)),'node_id':nid,'title':title,'type':ntype,'value':value})
    def scan_value(v, path, node, keyname=''):
        if isinstance(v, str):
            for m in ENV_BRACE_RE.finditer(v): add(m.group(1),'template-env',path,node,v)
            for m in ENV_DOT_RE.finditer(v): add(m.group(1),'dot-env',path,node,v)
            for m in ENV_DOLLAR_RE.finditer(v): add(m.group(1),'dollar-env',path,node,v)
        elif isinstance(v, list):
            if len(v) >= 2 and v[0] == 'env' and isinstance(v[1], str):
                add(v[1],'selector-env',path,node,v)
            for i,x in enumerate(v): scan_value(x, path+[i], node)
        elif isinstance(v, dict):
            for k,x in v.items(): scan_value(x, path+[k], node, str(k))
    for n in nodes:
        scan_value(n, ['nodes', n.get('id')], n)
    return refs


def env_defs(doc):
    envs=doc.get('workflow',{}).get('environment_variables') or []
    if not isinstance(envs,list): fail('workflow.environment_variables must be a list')
    names=[]
    for e in envs:
        if not isinstance(e,dict): fail('environment_variables entries must be objects')
        name=e.get('name')
        if not name: fail(f'environment variable missing name: {e}')
        names.append(name)
        selector=e.get('selector')
        if selector is not None and selector != ['env', name]: fail(f'environment variable selector mismatch for {name}: {selector}')
        for field in ('description','id','name','selector','value','value_type'):
            if field not in e: fail(f'environment variable missing {field}: {name}')
    dup=[n for n in set(names) if names.count(n)>1]
    if dup: fail(f'environment variable names must be unique: {sorted(dup)}')
    return {e['name']:e for e in envs}


def validate_env(doc):
    refs=collect_env_refs(doc); defs=env_defs(doc)
    missing=sorted(set(refs)-set(defs))
    if missing: fail(f'env references missing workflow.environment_variables definitions: {missing}')
    missing_required=sorted(REQUIRED_ENV_NAMES-set(defs))
    if missing_required: fail(f'required environment variables missing: {missing_required}')
    deprecated=sorted((set(refs)|set(defs)) & DEPRECATED_ENV_NAMES)
    if deprecated: fail(f'deprecated PHONE env names found; use roadmap env names instead: {deprecated}')
    for name in REQUIRED_ENV_NAMES:
        e=defs.get(name,{})
        selector = e.get("selector")
        if selector != ["env", name]:
            fail(f"environment variable selector mismatch for {name}: {selector}")
    if defs.get('ENABLE_FULL_LLM',{}).get('value_type') != 'string': fail('ENABLE_FULL_LLM value_type must be string')
    if defs.get('LLM_STATIC_TOKEN',{}).get('value_type') != 'secret' or defs.get('LLM_STATIC_TOKEN',{}).get('value') != '': fail('LLM_STATIC_TOKEN must be secret with empty value')
    nodes=doc.get('workflow',{}).get('graph',{}).get('nodes') or []
    actual={}
    for n in nodes:
        title=(n.get('data') or {}).get('title')
        if title in EXPECTED_HTTP_URLS: actual[title]=(n.get('data') or {}).get('url')
    for title,expected in EXPECTED_HTTP_URLS.items():
        if actual.get(title) != expected: fail(f'HTTP URL for {title} must be {expected}, got {actual.get(title)!r}')
    return refs, defs


def full_file_ignored():
    try:
        r=subprocess.run(['git','check-ignore','-q',str(FULL_FILE.relative_to(ROOT))],cwd=ROOT)
        return r.returncode == 0
    except Exception:
        return False


def print_env_report(refs, defs):
    ref_names=sorted(refs)
    def_names=sorted(defs)
    http_refs=sorted({k for k,v in refs.items() for r in v if r['kind'] in ('template-env','dollar-env') and '.url' in r['path']})
    deprecated=sorted((set(refs)|set(defs)) & DEPRECATED_ENV_NAMES)
    print(f'all_env_refs: {ref_names}')
    print(f'defined_env_names: {def_names}')
    print(f'missing_env_defs: {sorted(set(ref_names)-set(def_names))}')
    print(f'unused_env_defs: {sorted(set(def_names)-set(ref_names))}')
    print(f'deprecated_env_names: {deprecated}')
    print(f'http_env_refs: {http_refs}')
    print('HTTP URL actual values:')
    for title, expected in EXPECTED_HTTP_URLS.items():
        actual=''
        for refs_for_name in refs.values():
            for r in refs_for_name:
                if r.get('title') == title and '.url' in r.get('path',''):
                    actual=r.get('value')
        print(f'  {title}: {actual!r} (expected {expected!r})')
    e=defs.get('ENABLE_FULL_LLM',{})
    print(f'ENABLE_FULL_LLM default/value: {e.get("value")!r}, value_type: {e.get("value_type")!r}')
    st=defs.get('LLM_STATIC_TOKEN',{})
    print(f'LLM_STATIC_TOKEN value_type: {st.get("value_type")!r}, value: {st.get("value")!r}')

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
            handles={c.get('case_id') or c.get('id') for c in n['data'].get('cases',[]) if c.get('case_id') or c.get('id')}|{'false'}
            used={e.get('sourceHandle') for e in out[n['id']]}
            if None in used: fail(f'null compatibility edge is intentionally not used: {n["id"]}')
            if not used <= handles: fail(f'IF handle mismatch at {n["id"]}: {used} vs {handles}')
    refs, defs = validate_env(doc)
    if not full_file_ignored(): fail('PHONE_MODULE_JF_CHATFLOW.yml must be ignored as a local generated artifact')
    producers=defaultdict(set)
    for n in nodes:
        for k in (n.get('data',{}).get('outputs') or {}): producers[k].add(n['id'])
    for var in ['route_card_json','slot_validate_result_json','query_plan_json','mongo_request_json','mongo_result_json','normalized_query_result_json','analysis_result_json','report_input_json','full_llm_token_request_body_json','full_llm_token_result_json','full_llm_request_body_json','full_llm_result_json','local_llm_result_json','final_answer']:
        if var not in producers: fail(f'variable producer missing: {var}')
    return len(nodes),len(edges),refs,defs

def check_full_consistency(text):
    if not FULL_FILE.exists():
        return False
    if FULL_FILE.read_text(encoding='utf-8') != text:
        fail('PHONE_MODULE_JF_CHATFLOW.yml exists but does not match raw-concatenated fragments')
    return True

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    mode=ap.add_mutually_exclusive_group()
    mode.add_argument('--check',action='store_true',help='validate fragments and compare existing generated full file if present')
    mode.add_argument('--stdout',action='store_true',help='print raw-concatenated full DSL to stdout without writing')
    args=ap.parse_args(argv)
    parts=discover_parts(); text=merged_text(parts); doc=load_doc(text); nodes,edges,refs,defs=validate(doc)
    if args.stdout:
        sys.stdout.write(text); return 0
    if args.check:
        if check_full_consistency(text):
            print_env_report(refs, defs)
            print(f'OK: PHONE split fragments valid; full file matches fragments ({len(parts)} fragments, {nodes} nodes, {edges} edges)')
        else:
            print_env_report(refs, defs)
            print('OK: PHONE split fragments valid; full file not found, run merge to generate local import file')
        return 0
    FULL_FILE.write_text(text,encoding='utf-8')
    print(f'OK: generated local PHONE_MODULE_JF_CHATFLOW.yml from {len(parts)} fragments ({nodes} nodes, {edges} edges)')
    return 0
if __name__=='__main__': raise SystemExit(main())
