import json
import re


def _strip_quotes(v):
    v = str(v or '').strip()
    if (len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}):
        return v[1:-1]
    return v


def _parse_scalar(v):
    v = str(v or '').strip()
    if not v:
        return ''
    if v in {'|', '>', '|-', '>-'}:
        return ''
    if v in {'true', 'True'}:
        return True
    if v in {'false', 'False'}:
        return False
    if v.startswith('[') and v.endswith(']'):
        body = v[1:-1].strip()
        return [_strip_quotes(x.strip()) for x in re.split(r'\s*,\s*', body) if x.strip()]
    if re.match(r'^-?\d+$', v):
        try: return int(v)
        except Exception: pass
    if re.match(r'^-?\d+\.\d+$', v):
        try: return float(v)
        except Exception: pass
    return _strip_quotes(v)


def _parse_simple_yaml(text):
    root = {}
    stack = [(-1, root)]
    pending_key = None
    lines = str(text or '').replace('\r\n','\n').replace('\r','\n').split('\n')
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith('#'):
            i += 1; continue
        indent = len(raw) - len(raw.lstrip(' '))
        s = raw.strip()
        # block scalar continuation for key: > or |
        mblock = re.match(r'^([A-Za-z_][\w\-]*)\s*:\s*([>|][\-+]?)\s*$', s)
        if mblock:
            key = mblock.group(1)
            parts=[]; base=None; j=i+1
            while j < len(lines):
                nr=lines[j]
                if nr.strip():
                    ni=len(nr)-len(nr.lstrip(' '))
                    if ni <= indent: break
                    if base is None: base=ni
                    parts.append(nr[base:] if len(nr)>=base else nr.strip())
                else:
                    parts.append('')
                j += 1
            while stack and indent <= stack[-1][0]: stack.pop()
            parent=stack[-1][1]
            if isinstance(parent, dict): parent[key]='\n'.join(parts).strip()
            i=j; continue
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if s.startswith('- '):
            item = s[2:].strip()
            if not isinstance(parent, list):
                # malformed under scalar, skip
                i += 1; continue
            if ':' in item and re.match(r'^[A-Za-z_][\w\-]*\s*:', item):
                k, v = item.split(':',1)
                obj = {k.strip(): _parse_scalar(v)} if v.strip() else {k.strip(): {}}
                parent.append(obj)
                stack.append((indent, obj))
                if not v.strip():
                    stack.append((indent+2, obj[k.strip()]))
            else:
                parent.append(_parse_scalar(item))
            i += 1; continue
        if ':' in s:
            k, v = s.split(':',1)
            key = k.strip()
            val = v.strip()
            if isinstance(parent, dict):
                if val:
                    parent[key] = _parse_scalar(val)
                    i += 1; continue
                # determine next container
                j=i+1; nxt=''
                while j < len(lines):
                    if lines[j].strip() and not lines[j].lstrip().startswith('#'):
                        nxt=lines[j].strip(); break
                    j += 1
                container = [] if nxt.startswith('- ') else {}
                parent[key] = container
                stack.append((indent, container))
        i += 1
    return root


def extract_schema_metadata_blocks(text):
    blocks = []
    pattern = re.compile(r'(^|\n)##\s*Schema Metadata\s*\n+```(?:yaml|yml)\s*\n(.*?)\n```', re.I|re.S)
    for m in pattern.finditer(str(text or '')):
        blocks.append(m.group(2))
    if not blocks:
        pattern2 = re.compile(r'```(?:yaml|yml)\s*\n(.*?metadata_type\s*:\s*mongo_schema.*?)\n```', re.I|re.S)
        blocks = [m.group(1) for m in pattern2.finditer(str(text or ''))]
    return blocks


def _normalize_schema_doc(doc, warnings):
    if not isinstance(doc, dict): return None
    if doc.get('metadata_type') != 'mongo_schema' and not (doc.get('collection_name') and isinstance(doc.get('fields'), list)):
        return None
    cname = str(doc.get('collection_name') or '').strip()
    if not cname: return None
    fields={}
    for f in doc.get('fields') or []:
        if isinstance(f, dict) and f.get('name'):
            name=str(f.get('name')).strip(); item=dict(f); item['name']=name
            item['aliases']=[str(x).strip() for x in (item.get('aliases') or []) if str(x).strip()]
            fields[name]=item
    metrics={}
    for m in doc.get('metrics') or []:
        if isinstance(m, dict) and m.get('name'):
            name=str(m.get('name')).strip(); item=dict(m); item['name']=name
            item['aliases']=[str(x).strip() for x in (item.get('aliases') or []) if str(x).strip()]
            metrics[name]=item
    return {
        'collection_name': cname,
        'collection_label': str(doc.get('collection_label') or '').strip(),
        'collection_aliases': [str(x).strip() for x in (doc.get('collection_aliases') or []) if str(x).strip()],
        'primary_key': str(doc.get('primary_key') or '').strip(),
        'default_time_field': str(doc.get('default_time_field') or '').strip(),
        'fields': fields,
        'metrics': metrics,
        'relations': doc.get('relations') if isinstance(doc.get('relations'), list) else [],
        'query_rules': doc.get('query_rules') if isinstance(doc.get('query_rules'), dict) else {},
    }


def extract_schema_metadata(schema_context):
    warnings=[]; collections={}
    raw = schema_context if isinstance(schema_context, str) else json.dumps(schema_context, ensure_ascii=False)
    # allow already-json metadata
    if isinstance(schema_context, dict) and isinstance(schema_context.get('collections'), dict):
        return {'collections': schema_context.get('collections') or {}, 'warnings': schema_context.get('warnings') or []}
    for block in extract_schema_metadata_blocks(raw):
        try:
            doc=_parse_simple_yaml(block)
            norm=_normalize_schema_doc(doc, warnings)
            if norm: collections[norm['collection_name']]=norm
        except Exception as e:
            warnings.append('Schema Metadata YAML 解析失败: '+str(e)[:160])
    return {'collections': collections, 'warnings': warnings}


def _add_alias(index, alias, collection, kind, name, label):
    alias=str(alias or '').strip()
    if not alias: return
    item={'collection':collection,'kind':kind,'name':name,'label':str(label or '').strip()}
    bucket=index.setdefault(alias, [])
    if not any(x.get('collection')==collection and x.get('kind')==kind and x.get('name')==name for x in bucket):
        bucket.append(item)


def build_alias_index(schema_metadata):
    meta=schema_metadata if isinstance(schema_metadata, dict) else extract_schema_metadata(schema_metadata)
    index={}; coll_index={}
    for cname, coll in (meta.get('collections') or {}).items():
        _add_alias(coll_index, cname, cname, 'collection', cname, coll.get('collection_label'))
        _add_alias(coll_index, coll.get('collection_label'), cname, 'collection', cname, coll.get('collection_label'))
        for a in coll.get('collection_aliases') or []: _add_alias(coll_index, a, cname, 'collection', cname, coll.get('collection_label'))
        for fname, f in (coll.get('fields') or {}).items():
            for a in [fname, f.get('label')] + list(f.get('aliases') or []): _add_alias(index, a, cname, 'field', fname, f.get('label'))
        for mname, m in (coll.get('metrics') or {}).items():
            for a in [mname, m.get('label')] + list(m.get('aliases') or []): _add_alias(index, a, cname, 'metric', mname, m.get('label'))
    return {'aliases': index, 'collection_aliases': coll_index}


def prettify_field_name(field_name):
    return str(field_name or '').strip().replace('_',' ')


def _meta_and_index(schema_metadata):
    meta=schema_metadata if isinstance(schema_metadata, dict) and 'collections' in schema_metadata else extract_schema_metadata(schema_metadata)
    return meta, build_alias_index(meta)


def resolve_field_name(raw_name, schema_metadata, collection_name=None, old_plan_fields=None):
    raw=str(raw_name or '').strip(); old=set(str(x).strip() for x in (old_plan_fields or []) if str(x).strip())
    meta, idx=_meta_and_index(schema_metadata); colls=meta.get('collections') or {}
    cname=str(collection_name or '').strip()
    if cname in colls and raw in (colls[cname].get('fields') or {}):
        f=colls[cname]['fields'][raw]; return {'input':raw,'resolved':raw,'kind':'field','collection':cname,'source':'schema_field_name','label':f.get('label') or prettify_field_name(raw),'confidence':1.0,'warning':''}
    candidates=[x for x in (idx.get('aliases') or {}).get(raw, []) if x.get('kind')=='field']
    if cname:
        scoped=[x for x in candidates if x.get('collection')==cname]
        if scoped:
            x=scoped[0]; return {'input':raw,'resolved':x['name'],'kind':'field','collection':x['collection'],'source':'schema_alias','label':x.get('label') or prettify_field_name(x['name']),'confidence':1.0,'warning':''}
    if len(candidates)==1:
        x=candidates[0]; return {'input':raw,'resolved':x['name'],'kind':'field','collection':x['collection'],'source':'schema_alias_unique','label':x.get('label') or prettify_field_name(x['name']),'confidence':1.0,'warning':''}
    if raw in old:
        return {'input':raw,'resolved':raw,'kind':'field','collection':cname,'source':'old_plan_fields','label':prettify_field_name(raw),'confidence':0.8,'warning':''}
    warning = '字段别名未在 schema metadata 中声明，未自动归一。' if not candidates else '字段别名在多个 collection 中存在，缺少 collection 上下文，未自动归一。'
    return {'input':raw,'resolved':raw,'kind':'unknown','collection':cname,'source':'unresolved','label':raw or prettify_field_name(raw),'confidence':0,'warning':warning}


def resolve_metric_name(raw_name, schema_metadata, collection_name=None):
    raw=str(raw_name or '').strip(); meta, idx=_meta_and_index(schema_metadata); colls=meta.get('collections') or {}; cname=str(collection_name or '').strip()
    if cname in colls and raw in (colls[cname].get('metrics') or {}):
        m=colls[cname]['metrics'][raw]; return {'input':raw,'resolved':raw,'kind':'metric','collection':cname,'source':'schema_metric_name','label':m.get('label') or prettify_field_name(raw),'confidence':1.0,'warning':''}
    candidates=[x for x in (idx.get('aliases') or {}).get(raw, []) if x.get('kind')=='metric']
    if cname:
        scoped=[x for x in candidates if x.get('collection')==cname]
        if scoped:
            x=scoped[0]; return {'input':raw,'resolved':x['name'],'kind':'metric','collection':x['collection'],'source':'schema_alias','label':x.get('label') or prettify_field_name(x['name']),'confidence':1.0,'warning':''}
    if len(candidates)==1:
        x=candidates[0]; return {'input':raw,'resolved':x['name'],'kind':'metric','collection':x['collection'],'source':'schema_alias_unique','label':x.get('label') or prettify_field_name(x['name']),'confidence':1.0,'warning':''}
    return {'input':raw,'resolved':raw,'kind':'unknown','collection':cname,'source':'unresolved','label':raw,'confidence':0,'warning':'指标别名未在 schema metadata 中声明，未自动归一。'}


def resolve_field_label(field_name, schema_metadata, collection_name=None):
    meta,_=_meta_and_index(schema_metadata); fname=str(field_name or '').strip(); cname=str(collection_name or '').strip()
    if cname and cname in meta.get('collections',{}) and fname in meta['collections'][cname].get('fields',{}):
        return meta['collections'][cname]['fields'][fname].get('label') or prettify_field_name(fname)
    hits=[]
    for cn,c in (meta.get('collections') or {}).items():
        if fname in c.get('fields',{}): hits.append(c['fields'][fname].get('label') or prettify_field_name(fname))
    return hits[0] if len(hits)==1 else prettify_field_name(fname)


def resolve_metric_label(metric_name, schema_metadata, collection_name=None):
    meta,_=_meta_and_index(schema_metadata); name=str(metric_name or '').strip(); cname=str(collection_name or '').strip()
    if cname and cname in meta.get('collections',{}) and name in meta['collections'][cname].get('metrics',{}):
        return meta['collections'][cname]['metrics'][name].get('label') or prettify_field_name(name)
    hits=[]
    for cn,c in (meta.get('collections') or {}).items():
        if name in c.get('metrics',{}): hits.append(c['metrics'][name].get('label') or prettify_field_name(name))
    return hits[0] if len(hits)==1 else prettify_field_name(name)


def build_chart_title(group_field, metric_name, schema_metadata, collection_name=None, user_title=''):
    if str(user_title or '').strip(): return str(user_title).strip(), 'chart_request'
    x=resolve_field_label(group_field, schema_metadata, collection_name)
    y=resolve_metric_label(metric_name, schema_metadata, collection_name)
    return ('按'+x+'统计'+y if x and y else (x+' 与 '+y+' 对比')).strip(), 'schema_label'
