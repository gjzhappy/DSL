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

def load_phone_dsl_text(text):
    """Load PHONE DSL text with local PyYAML fallback semantics.

    PHONE fragments are maintained as JSON-compatible YAML. Prefer PyYAML when
    available; if it is not installed, fall back locally to json.loads without
    shadowing the global yaml module name.
    """
    try:
        import yaml as pyyaml
    except ModuleNotFoundError:
        return json.loads(text)
    return pyyaml.safe_load(text)

def load_doc():
    text=merged_text()
    check_full_consistency(text)
    return load_phone_dsl_text(text)

def graph(doc):
    nodes={n['id']:n for n in doc['workflow']['graph']['nodes']}
    title={n['data']['title']:n['id'] for n in doc['workflow']['graph']['nodes']}
    out=defaultdict(list); inc=defaultdict(list)
    for e in doc['workflow']['graph']['edges']:
        out[e['source']].append((e.get('sourceHandle'),e['target'],e)); inc[e['target']].append(e)
    return nodes,title,out,inc

def check_frontend_checkvalid_schema(doc):
    """Validate array fields that Dify web checkValid implementations read."""
    missing=[]
    for n in doc['workflow']['graph']['nodes']:
        data=n.get('data') or {}
        t=data.get('type'); title=data.get('title'); nid=n.get('id')
        def need_list(key):
            if key not in data:
                missing.append(f'{nid}({title}) {t}.data.{key} missing')
            elif data.get(key) is None:
                missing.append(f'{nid}({title}) {t}.data.{key} is null')
            elif not isinstance(data.get(key), list):
                missing.append(f'{nid}({title}) {t}.data.{key} is {type(data.get(key)).__name__}, expected list')
        if t == 'start':
            need_list('variables')
        elif t == 'if-else':
            need_list('cases')
            if isinstance(data.get('cases'), list):
                if 'logical_operator' not in data:
                    missing.append(f'{nid}({title}) if-else.data.logical_operator missing')
                for idx,c in enumerate(data['cases']):
                    if 'case_id' not in c:
                        missing.append(f'{nid}({title}) if-else.data.cases[{idx}].case_id missing')
                    if 'id' in c:
                        missing.append(f'{nid}({title}) if-else.data.cases[{idx}].id present; use case_id')
                    if not isinstance(c.get('conditions'), list):
                        missing.append(f'{nid}({title}) if-else.data.cases[{idx}].conditions not list')
                    else:
                        for j,cond in enumerate(c['conditions']):
                            for key in ('variable_selector','comparison_operator','value'):
                                if key not in cond:
                                    missing.append(f'{nid}({title}) if-else.data.cases[{idx}].conditions[{j}].{key} missing')
                    if 'logical_operator' not in c:
                        missing.append(f'{nid}({title}) if-else.data.cases[{idx}].logical_operator missing')
        elif t == 'answer':
            need_list('outputs')
            need_list('variables')
        elif t == 'code':
            need_list('variables')
    if missing:
        raise AssertionError('frontend checkValid schema failed:\n' + '\n'.join(missing))

def check_ifslot_validate_contract(doc):
    nodes,title,out,inc=graph(doc)
    if 'ifslot' not in nodes or 'validate' not in nodes:
        raise AssertionError('ifslot/validate node missing')
    ifslot=nodes['ifslot']['data']
    validate=nodes['validate']['data']
    outputs=validate.get('outputs') or {}
    if 'ready_to_query_flag' not in outputs:
        raise AssertionError('validate.data.outputs.ready_to_query_flag missing')
    code=(validate.get('source_code') or '') + '\n' + (validate.get('code') or '')
    if 'slot_validate_result_json' not in outputs or 'slot_validate_result_json' not in code:
        raise AssertionError('validate must keep slot_validate_result_json output contract')
    expected={
        'need_slot': ('ifslot-need-slot','false'),
        'success': ('ifslot-success','true'),
    }
    cases={c.get('case_id'):c for c in ifslot.get('cases',[])}
    if set(expected) - set(cases):
        raise AssertionError(f'ifslot cases missing: {sorted(set(expected)-set(cases))}')
    for case_id,(cond_id,value) in expected.items():
        case=cases[case_id]
        if case.get('logical_operator')!='and':
            raise AssertionError(f'ifslot.{case_id}.logical_operator must be and')
        conds=case.get('conditions')
        if not isinstance(conds,list) or len(conds)!=1:
            raise AssertionError(f'ifslot.{case_id}.conditions must contain one condition')
        cond=conds[0]
        if cond.get('id')!=cond_id:
            raise AssertionError(f'ifslot.{case_id}.condition.id invalid: {cond.get("id")}')
        if cond.get('variable_selector')!=['validate','ready_to_query_flag']:
            raise AssertionError(f'ifslot.{case_id}.variable_selector must point to validate.ready_to_query_flag')
        if cond.get('comparison_operator')!='=':
            raise AssertionError(f'ifslot.{case_id}.comparison_operator must be =')
        if cond.get('value') in (None,''):
            raise AssertionError(f'ifslot.{case_id}.condition.value empty')
        if cond.get('value')!=value:
            raise AssertionError(f'ifslot.{case_id}.condition.value must be {value!r}')
    edges={(e['source'],e.get('sourceHandle'),e['target']) for e in doc['workflow']['graph']['edges']}
    for edge in (('ifslot','need_slot','fill'),('fill','source','ansslot'),('ifslot','success','plan')):
        if edge not in edges:
            raise AssertionError(f'expected edge missing/changed: {edge}')

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
