#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.graph_phone_common import load_phone_dsl_text
from scripts.graph_phone_common import check_ifslot_validate_contract

FIELDS = {
    'start': {'variables': list},
    'answer': {'outputs': list, 'variables': list, 'answer': str},
    'if-else': {'cases': list, 'logical_operator': str},
    'code': {'variables': list, 'outputs': dict, 'code_language': str, 'code': str},
    'llm': {'model': dict, 'prompt_template': list, 'context': dict, 'vision': dict},
    'knowledge-retrieval': {'dataset_ids': list, 'query_variable_selector': list, 'retrieval_mode': str, 'multiple_retrieval_config': dict},
    'http-request': {'method': str, 'url': str, 'headers': str, 'params': str, 'body': dict, 'authorization': dict, 'variables': list},
}

def list_paths(obj, prefix='data'):
    out=[]
    if isinstance(obj, list): out.append(prefix)
    if isinstance(obj, dict):
        for k,v in obj.items(): out += list_paths(v, f'{prefix}.{k}')
    elif isinstance(obj, list):
        for i,v in enumerate(obj): out += list_paths(v, f'{prefix}[{i}]')
    return out

def main():
    p=Path('NL2SEARCH_CHATFLOW_DSL/PHONE_MODULE_JF_CHATFLOW.yml')
    d=load_phone_dsl_text(p.read_text(encoding='utf-8'))
    risk_missing=risk_null=risk_type=0
    print('node_id\ttitle\ttype\tdata_keys\tlist_fields\tmissing\tnull\ttype_errors')
    for n in d['workflow']['graph']['nodes']:
        data=n.get('data') or {}; t=data.get('type'); spec=FIELDS.get(t,{})
        missing=[]; nulls=[]; terr=[]
        for key,typ in spec.items():
            if key not in data: missing.append(key)
            elif data[key] is None: nulls.append(key)
            elif not isinstance(data[key], typ): terr.append(f'{key}:{type(data[key]).__name__}!={typ.__name__}')
        if t=='if-else' and isinstance(data.get('cases'), list):
            for i,c in enumerate(data['cases']):
                for k in ('case_id','logical_operator','conditions'):
                    if k not in c: missing.append(f'cases[{i}].{k}')
                if 'conditions' in c and not isinstance(c['conditions'], list): terr.append(f'cases[{i}].conditions')
                elif isinstance(c.get('conditions'), list):
                    for j,cond in enumerate(c['conditions']):
                        condition_keys=('variable_selector','comparison_operator','value')
                        if n.get('id') == 'ifslot':
                            condition_keys=('id',) + condition_keys
                        for k in condition_keys:
                            if k not in cond: missing.append(f'cases[{i}].conditions[{j}].{k}')
                        if cond.get('value') in (None,''): nulls.append(f'cases[{i}].conditions[{j}].value')
        risk_missing+=len(missing); risk_null+=len(nulls); risk_type+=len(terr)
        print(f"{n.get('id')}\t{data.get('title')}\t{t}\t{','.join(data.keys())}\t{','.join(list_paths(data))}\t{','.join(missing) or '-'}\t{','.join(nulls) or '-'}\t{','.join(terr) or '-'}")
    print(f'SUMMARY nodes={len(d["workflow"]["graph"]["nodes"])} missing={risk_missing} null={risk_null} type_errors={risk_type}')
    check_ifslot_validate_contract(d)
if __name__=='__main__': main()
