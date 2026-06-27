#!/usr/bin/env python3
import json, sys, collections, re
p=sys.argv[1] if len(sys.argv)>1 else 'NL2SEARCH_CHATFLOW_DSL/PHONE_MODULE_JF_CHATFLOW.yml'
d=json.load(open(p,encoding='utf-8'))
errs=[]
for k in ['app','kind','version','workflow']:
 if k not in d: errs.append(f'missing top-level {k}')
g=d.get('workflow',{}).get('graph',{})
nodes=g.get('nodes'); edges=g.get('edges')
if not isinstance(nodes,list): errs.append('workflow.graph.nodes must be array')
if not isinstance(edges,list): errs.append('workflow.graph.edges must be array')
ids=set()
for n in nodes or []:
 for k in ['id','type','data','position','positionAbsolute','width','height']:
  if k not in n: errs.append(f"node {n.get('id')} missing {k}")
 ids.add(n.get('id'))
 data=n.get('data',{}); typ=data.get('type')
 if typ in ['start','code','answer','llm']: 
  if 'variables' not in data or not isinstance(data.get('variables'),list): errs.append(f"node {n.get('id')} variables must be array")
 if typ=='if-else' and not isinstance(data.get('cases'),list): errs.append(f"if node {n.get('id')} cases must be array")
 if typ=='llm':
  if not isinstance(data.get('prompt_template'),list): errs.append(f"llm {n.get('id')} prompt_template must be array")
  m=data.get('model',{})
  for k in ['provider','name','mode','completion_params']:
   if k not in m: errs.append(f"llm {n.get('id')} model missing {k}")
 if typ=='knowledge-retrieval':
  ds=data.get('dataset_ids')
  if not isinstance(ds,list) or '4d7c7b04-e8d5-47cf-8bd1-dfdfe6022cb7' not in ds: errs.append('knowledge retrieval dataset_ids missing required phone_module_jf id')
 if typ=='http-request':
  for k in ['method','url','headers','params','body','authorization','timeout']:
   if k not in data: errs.append(f"http node missing {k}")
for e in edges or []:
 for k in ['id','source','target','sourceHandle','targetHandle','data']:
  if k not in e: errs.append(f"edge {e.get('id')} missing {k}")
 if e.get('source') not in ids or e.get('target') not in ids: errs.append(f"edge {e.get('id')} has unknown endpoint")
# connectivity from start
adj=collections.defaultdict(list)
for e in edges or []: adj[e['source']].append(e['target'])
seen=set(); stack=['start']
while stack:
 x=stack.pop();
 if x in seen: continue
 seen.add(x); stack += adj[x]
if ids-seen: errs.append('unreachable nodes: '+','.join(sorted(ids-seen)))
text=open(p,encoding='utf-8').read()
for bad in ['semantic_plan','full_replan','patch_result','refine']:
 if re.search(bad,text,re.I): errs.append(f'forbidden token found: {bad}')
if errs:
 print('FAIL')
 print('\n'.join(errs)); sys.exit(1)
print('PASS dify-dsl-schema dataset_ids graph-connectivity contracts variables grep path')
