from __future__ import annotations
import json, re
from collections import defaultdict, deque
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'NL2SEARCH_CHATFLOW_DSL'
PART_RE=re.compile(r'PHONE_MODULE_JF_CHATFLOW_(\d+)\.yml$')

FULL_FILE=BASE/'PHONE_MODULE_JF_CHATFLOW.yml'

def parts():
    ps=sorted([p for p in BASE.glob('PHONE_MODULE_JF_CHATFLOW_*.yml') if PART_RE.match(p.name)], key=lambda p:int(PART_RE.match(p.name).group(1)))
    if not ps: raise AssertionError('PHONE fragments missing')
    nums=[int(PART_RE.match(p.name).group(1)) for p in ps]
    if nums != list(range(0,len(nums))): raise AssertionError(f'PHONE fragments must be continuous from 00: {nums}')
    return ps

def merged_text():
    text=''.join(p.read_text(encoding='utf-8') for p in parts())
    if not text.strip(): raise AssertionError('PHONE merged fragments are empty')
    return text

def check_full_consistency(text=None):
    text=merged_text() if text is None else text
    if FULL_FILE.exists() and FULL_FILE.read_text(encoding='utf-8') != text:
        raise AssertionError('PHONE_MODULE_JF_CHATFLOW.yml exists but does not match raw-concatenated fragments')

def load_doc():
    text=merged_text()
    check_full_consistency(text)
    try:
        import yaml
        return yaml.safe_load(text)
    except ModuleNotFoundError:
        return json.loads(text)

def graph(doc):
    nodes={n['id']:n for n in doc['workflow']['graph']['nodes']}
    title={n['data']['title']:n['id'] for n in doc['workflow']['graph']['nodes']}
    out=defaultdict(list); inc=defaultdict(list)
    for e in doc['workflow']['graph']['edges']:
        out[e['source']].append((e.get('sourceHandle'),e['target'],e)); inc[e['target']].append(e)
    return nodes,title,out,inc

def path(title,out,nodes,src_title,dst_title,first_handle=None,banned=()):
    src=title[src_title]; dst=title[dst_title]; banned=set(banned); q=deque([(src,[],True)]); seen=set()
    while q:
        cur,p,first=q.popleft()
        key=(cur, first)
        if key in seen: continue
        seen.add(key)
        if cur==dst: return [nodes[i]['data']['title'] for i in p+[cur]]
        for h,nxt,_ in out.get(cur,[]):
            if first and first_handle is not None and h!=first_handle: continue
            if nodes[nxt]['data']['title'] in banned: continue
            q.append((nxt,p+[cur],False))
    return None
