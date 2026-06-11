import copy
import hashlib
import json
import re


CONTRACT_VERSION = 'schema_metadata_contract'
ALIAS_INDEX_CONTRACT_VERSION = 'schema_alias_index_contract'
NON_SEMANTIC_SCHEMA_KEYS = {'warnings', 'parse_errors', 'source_refs', 'raw_schema', 'raw_schema_text', 'schema_digest'}


def default_query_rules():
    return {
        'require_time_range_for_aggregate': True,
        'max_limit': 100,
        'allowed_operations': ['find', 'aggregate'],
        'aggregate_required_when': [],
        'find_allowed_when': [],
        'sensitive_field_policy': 'deny_return_group_sort',
        'default_limit': 100,
        'max_time_range_days': 366,
    }


def _strip_quotes(v):
    v = str(v or '').strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}:
        return v[1:-1]
    return v


def _split_inline_list(body):
    # Simple comma split that keeps quoted Chinese/ASCII aliases intact for the schema docs used here.
    return [_strip_quotes(x.strip()) for x in re.split(r'\s*,\s*', str(body or '')) if x.strip()]


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
    if v in {'null', 'Null', 'NULL', '~'}:
        return None
    if v.startswith('[') and v.endswith(']'):
        return _split_inline_list(v[1:-1].strip())
    if v.startswith('{') and v.endswith('}'):
        try:
            return json.loads(v)
        except Exception:
            return _strip_quotes(v)
    if re.match(r'^-?\d+$', v):
        try:
            return int(v)
        except Exception:
            pass
    if re.match(r'^-?\d+\.\d+$', v):
        try:
            return float(v)
        except Exception:
            pass
    return _strip_quotes(v)


def _parse_simple_yaml(text):
    root = {}
    stack = [(-1, root)]
    lines = str(text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith('#'):
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        s = raw.strip()
        mblock = re.match(r'^([A-Za-z_][\w\-]*)\s*:\s*([>|][\-+]?)\s*$', s)
        if mblock:
            key = mblock.group(1)
            parts = []
            base = None
            j = i + 1
            while j < len(lines):
                nr = lines[j]
                if nr.strip():
                    ni = len(nr) - len(nr.lstrip(' '))
                    if ni <= indent:
                        break
                    if base is None:
                        base = ni
                    parts.append(nr[base:] if len(nr) >= base else nr.strip())
                else:
                    parts.append('')
                j += 1
            while stack and indent <= stack[-1][0]:
                stack.pop()
            parent = stack[-1][1]
            if isinstance(parent, dict):
                parent[key] = '\n'.join(parts).strip()
            i = j
            continue
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if s.startswith('- '):
            item = s[2:].strip()
            if not isinstance(parent, list):
                i += 1
                continue
            if ':' in item and re.match(r'^[A-Za-z_][\w\-]*\s*:', item):
                k, v = item.split(':', 1)
                obj = {k.strip(): _parse_scalar(v)} if v.strip() else {k.strip(): {}}
                parent.append(obj)
                stack.append((indent, obj))
                if not v.strip():
                    stack.append((indent + 2, obj[k.strip()]))
            else:
                parent.append(_parse_scalar(item))
            i += 1
            continue
        if ':' in s:
            k, v = s.split(':', 1)
            key = k.strip()
            val = v.strip()
            if isinstance(parent, dict):
                if val:
                    parent[key] = _parse_scalar(val)
                    i += 1
                    continue
                j = i + 1
                nxt = ''
                while j < len(lines):
                    if lines[j].strip() and not lines[j].lstrip().startswith('#'):
                        nxt = lines[j].strip()
                        break
                    j += 1
                container = [] if nxt.startswith('- ') else {}
                parent[key] = container
                stack.append((indent, container))
        i += 1
    return root


def _json_loads_maybe(text):
    text = str(text or '').strip()
    if not text:
        raise ValueError('empty JSON text')
    return json.loads(text)


def _parse_metadata_block(block):
    block = str(block or '').strip()
    if not block:
        return {}
    if block.startswith('{') or block.startswith('['):
        return json.loads(block)
    return _parse_simple_yaml(block)


def extract_schema_metadata_blocks(text):
    raw = str(text or '')
    blocks = []
    # Prefer explicit Schema Metadata fenced blocks, YAML or JSON.
    pattern = re.compile(r'(^|\n)##\s*Schema Metadata\s*\n+```(?:yaml|yml|json)?\s*\n(.*?)\n```', re.I | re.S)
    blocks.extend(m.group(2) for m in pattern.finditer(raw))
    if blocks:
        return blocks
    # Then accept any fenced YAML/JSON block that declares mongo/schema contract metadata.
    pattern2 = re.compile(r'```(?:yaml|yml|json)?\s*\n(.*?(?:metadata_type\s*:\s*mongo_schema|"metadata_type"\s*:\s*"mongo_schema"|"contract_version"\s*:\s*"schema_metadata_contract").*?)\n```', re.I | re.S)
    blocks.extend(m.group(1) for m in pattern2.finditer(raw))
    return blocks


def _as_list(value):
    if value is None or value == '':
        return []
    if isinstance(value, list):
        return [x for x in value if x is not None and str(x).strip()]
    return [x for x in _split_inline_list(value) if str(x).strip()]


def _as_str_list(value):
    return [str(x).strip() for x in _as_list(value) if str(x).strip()]


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _normalize_named_map(value, kind, warnings, collection_name):
    result = {}
    if value is None or value == '':
        return result
    if isinstance(value, dict):
        iterable = []
        for name, item in value.items():
            if isinstance(item, dict):
                merged = dict(item)
                merged.setdefault('name', name)
                iterable.append(merged)
            else:
                warnings.append(f'{collection_name}.{kind}.{name} 不是对象，已忽略。')
        source_shape = 'dict'
    elif isinstance(value, list):
        iterable = value
        source_shape = 'list'
    else:
        warnings.append(f'{collection_name}.{kind} 不是 list/dict，已按空对象处理。')
        return result
    for item in iterable:
        if not isinstance(item, dict):
            warnings.append(f'{collection_name}.{kind} 中存在非对象条目，已忽略。')
            continue
        name = str(item.get('name') or '').strip()
        if not name:
            warnings.append(f'{collection_name}.{kind} 中存在缺少 name 的条目，已忽略。')
            continue
        normalized = copy.deepcopy(item)
        normalized['name'] = name
        result[name] = normalized
    return result


def _infer_function(expression):
    expr = str(expression or '').strip().lower()
    m = re.match(r'^\s*([a-z_][\w]*)\s*\(', expr)
    return m.group(1) if m else ''


def _normalize_field(field, collection_name, warnings):
    item = copy.deepcopy(field) if isinstance(field, dict) else {}
    name = str(item.get('name') or '').strip()
    sensitive = bool(item.get('sensitive', False))
    pii = bool(item.get('pii', False))
    role = str(item.get('role') or 'dimension').strip() or 'dimension'
    if 'returnable' not in item:
        item['returnable'] = False if sensitive or pii else True
        if not (sensitive or pii):
            warnings.append(f'{collection_name}.{name}.returnable 缺失，C-MVP 兼容期默认 true。')
    if 'projectable' not in item:
        item['projectable'] = False if sensitive or pii else True
        if not (sensitive or pii):
            warnings.append(f'{collection_name}.{name}.projectable 缺失，C-MVP 兼容期默认 true。')
    normalized = {
        'name': name,
        'label': str(item.get('label') or name).strip(),
        'type': str(item.get('type') or '').strip(),
        'role': role,
        'semantic_type': str(item.get('semantic_type') or role).strip() or role,
        'description': str(item.get('description') or '').strip(),
        'aliases': _as_str_list(item.get('aliases')),
        'allowed_values': _as_str_list(item.get('allowed_values')),
        'value_aliases': {str(k).strip(): str(v).strip() for k, v in _as_dict(item.get('value_aliases')).items() if str(k).strip() and str(v).strip()},
        'filterable': bool(item.get('filterable', False)),
        'groupable': False if (sensitive or pii) and 'groupable' not in item else bool(item.get('groupable', False)),
        'sortable': False if (sensitive or pii) and 'sortable' not in item else bool(item.get('sortable', False)),
        'returnable': bool(item.get('returnable', False)),
        'projectable': bool(item.get('projectable', False)),
        'chartable': bool(item.get('chartable', False)),
        'aggregatable': _as_str_list(item.get('aggregatable')),
        'sensitive': sensitive,
        'pii': pii,
        'pii_category': str(item.get('pii_category') or '').strip(),
        'default_display': bool(item.get('default_display', False)),
    }
    # Preserve current/forward-compatible extra field properties.
    for k, v in item.items():
        if k not in normalized and k not in NON_SEMANTIC_SCHEMA_KEYS:
            normalized[k] = v
    return normalized


def _normalize_metric(metric, primary_key):
    item = copy.deepcopy(metric) if isinstance(metric, dict) else {}
    name = str(item.get('name') or '').strip()
    function = str(item.get('function') or _infer_function(item.get('expression'))).strip()
    field = str(item.get('field') or '').strip()
    if not field and function == 'count' and primary_key:
        field = primary_key
    source_fields = _as_str_list(item.get('source_fields'))
    if not source_fields and field:
        source_fields = [field]
    normalized = {
        'name': name,
        'label': str(item.get('label') or name).strip(),
        'role': str(item.get('role') or 'metric').strip() or 'metric',
        'description': str(item.get('description') or '').strip(),
        'aliases': _as_str_list(item.get('aliases')),
        'expression': str(item.get('expression') or '').strip(),
        'function': function,
        'field': field,
        'source_fields': source_fields,
        'output_type': str(item.get('output_type') or 'number').strip() or 'number',
        'aggregate_only': bool(item.get('aggregate_only', True)),
        'allowed_dimensions': _as_str_list(item.get('allowed_dimensions')),
        'default_sort': str(item.get('default_sort') or 'desc').strip() or 'desc',
        'chartable': bool(item.get('chartable', True)),
    }
    for k, v in item.items():
        if k not in normalized and k not in NON_SEMANTIC_SCHEMA_KEYS:
            normalized[k] = v
    return normalized


def _normalize_relation(relation, collection_name, warnings):
    item = copy.deepcopy(relation) if isinstance(relation, dict) else {}
    join_keys = item.get('join_keys') if isinstance(item.get('join_keys'), list) else []
    if not join_keys:
        warnings.append(f'{collection_name}.relations 关系缺少 join_keys。')
    normalized_join_keys = []
    for jk in join_keys:
        if isinstance(jk, dict):
            normalized_join_keys.append({
                'source_field': str(jk.get('source_field') or '').strip(),
                'target_field': str(jk.get('target_field') or '').strip(),
                'match_type': str(jk.get('match_type') or 'exact').strip() or 'exact',
            })
    normalized = {
        'target_collection': str(item.get('target_collection') or '').strip(),
        'relation_type': str(item.get('relation_type') or 'enrich').strip() or 'enrich',
        'description': str(item.get('description') or '').strip(),
        'join_keys': normalized_join_keys,
        'allowed_usage': _as_str_list(item.get('allowed_usage')),
        'forbidden_usage': _as_str_list(item.get('forbidden_usage')),
        'cardinality': str(item.get('cardinality') or 'unknown').strip() or 'unknown',
    }
    for k, v in item.items():
        if k not in normalized and k not in NON_SEMANTIC_SCHEMA_KEYS:
            normalized[k] = v
    return normalized


def _normalize_query_rules(value):
    rules = default_query_rules()
    if isinstance(value, dict):
        rules.update(copy.deepcopy(value))
    rules['allowed_operations'] = _as_str_list(rules.get('allowed_operations')) or ['find', 'aggregate']
    rules['aggregate_required_when'] = _as_list(rules.get('aggregate_required_when'))
    rules['find_allowed_when'] = _as_list(rules.get('find_allowed_when'))
    return rules


def _looks_like_schema_doc(doc):
    if not isinstance(doc, dict):
        return False
    if doc.get('metadata_type') == 'mongo_schema' or doc.get('contract_version') == CONTRACT_VERSION:
        return True
    return bool(doc.get('collection_name') or isinstance(doc.get('collections'), dict))


def _normalize_collection_doc(doc, warnings):
    if not isinstance(doc, dict):
        return None
    cname = str(doc.get('collection_name') or '').strip()
    if not cname:
        return None
    primary_key = str(doc.get('primary_key') or '_id').strip() or '_id'
    fields = _normalize_named_map(doc.get('fields'), 'fields', warnings, cname)
    fields = {name: _normalize_field(field, cname, warnings) for name, field in fields.items()}
    metrics = _normalize_named_map(doc.get('metrics'), 'metrics', warnings, cname)
    metrics = {name: _normalize_metric(metric, primary_key) for name, metric in metrics.items()}
    relations = doc.get('relations') if isinstance(doc.get('relations'), list) else []
    if doc.get('relations') not in (None, '') and not isinstance(doc.get('relations'), list):
        warnings.append(f'{cname}.relations 不是 list，已按空列表处理。')
    normalized = {
        'collection_name': cname,
        'collection_label': str(doc.get('collection_label') or cname).strip() or cname,
        'domain': str(doc.get('domain') or '').strip(),
        'description': str(doc.get('description') or '').strip(),
        'collection_aliases': _as_str_list(doc.get('collection_aliases')),
        'primary_key': primary_key,
        'default_time_field': str(doc.get('default_time_field') or '').strip(),
        'fields': fields,
        'metrics': metrics,
        'relations': [_normalize_relation(r, cname, warnings) for r in relations if isinstance(r, dict)],
        'query_rules': _normalize_query_rules(doc.get('query_rules')),
        'aggregation_policy': _as_dict(doc.get('aggregation_policy')),
        'chart_label': _as_dict(doc.get('chart_label')),
        'default_sort': _as_list(doc.get('default_sort')),
        'examples': _as_list(doc.get('examples')),
        'warnings': _as_str_list(doc.get('warnings')),
    }
    for k, v in doc.items():
        if k not in normalized and k not in NON_SEMANTIC_SCHEMA_KEYS and k not in {'metadata_type', 'metadata_version'}:
            normalized[k] = v
    return normalized


def _metadata_from_collections_map(schema_context, warnings, parse_errors):
    collections = {}
    for cname, coll in (schema_context.get('collections') or {}).items():
        if not isinstance(coll, dict):
            warnings.append(f'collections.{cname} 不是对象，已忽略。')
            continue
        item = copy.deepcopy(coll)
        item.setdefault('collection_name', cname)
        norm = _normalize_collection_doc(item, warnings)
        if norm:
            collections[norm['collection_name']] = norm
    return collections


def compute_schema_digest(schema_metadata):
    meta = copy.deepcopy(schema_metadata if isinstance(schema_metadata, dict) else extract_schema_metadata(schema_metadata))
    def strip_non_semantic(obj):
        if isinstance(obj, dict):
            return {k: strip_non_semantic(v) for k, v in sorted(obj.items()) if k not in NON_SEMANTIC_SCHEMA_KEYS}
        if isinstance(obj, list):
            return [strip_non_semantic(x) for x in obj]
        return obj
    canonical = strip_non_semantic(meta)
    canonical['contract_version'] = CONTRACT_VERSION
    payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def extract_schema_metadata(schema_context):
    warnings = []
    parse_errors = []
    collections = {}
    schema_version = ''
    try:
        if isinstance(schema_context, dict):
            if isinstance(schema_context.get('collections'), dict):
                schema_version = str(schema_context.get('schema_version') or schema_context.get('metadata_version') or '').strip()
                collections = _metadata_from_collections_map(schema_context, warnings, parse_errors)
            elif _looks_like_schema_doc(schema_context):
                schema_version = str(schema_context.get('schema_version') or schema_context.get('metadata_version') or '').strip()
                norm = _normalize_collection_doc(schema_context, warnings)
                if norm:
                    collections[norm['collection_name']] = norm
            else:
                warnings.append('输入对象不是 schema metadata，已返回空 contract。')
        else:
            raw = str(schema_context or '')
            stripped = raw.strip()
            parsed_json = None
            if stripped.startswith('{') or stripped.startswith('['):
                try:
                    parsed_json = json.loads(stripped)
                except Exception as e:
                    parse_errors.append('Schema Metadata JSON 解析失败: ' + str(e)[:160])
            if isinstance(parsed_json, dict):
                return extract_schema_metadata(parsed_json)
            blocks = extract_schema_metadata_blocks(raw)
            if not blocks and parsed_json is None and stripped:
                # Last resort: treat the whole text as a YAML-ish schema doc.
                blocks = [raw]
            for block in blocks:
                try:
                    doc = _parse_metadata_block(block)
                    if not isinstance(doc, dict) or not _looks_like_schema_doc(doc):
                        warnings.append('跳过非 schema metadata block。')
                        continue
                    if not schema_version:
                        schema_version = str(doc.get('schema_version') or doc.get('metadata_version') or '').strip()
                    if isinstance(doc.get('collections'), dict):
                        collections.update(_metadata_from_collections_map(doc, warnings, parse_errors))
                    else:
                        norm = _normalize_collection_doc(doc, warnings)
                        if norm:
                            collections[norm['collection_name']] = norm
                except Exception as e:
                    parse_errors.append('Schema Metadata block 解析失败: ' + str(e)[:160])
    except Exception as e:
        parse_errors.append('Schema Metadata 解析失败: ' + str(e)[:160])
    meta = {
        'contract_version': CONTRACT_VERSION,
        'schema_version': schema_version,
        'schema_digest': '',
        'collections': collections,
        'warnings': warnings,
        'parse_errors': parse_errors,
    }
    meta['schema_digest'] = compute_schema_digest(meta)
    return meta



COLLECTION_CATALOG_VERSION = 'collection_catalog_contract'
COLLECTION_SELECTION_CONTRACT_VERSION = 'collection_selection_contract'


def _catalog_str_list(value, limit=None):
    items = []
    seen = set()
    for item in _as_str_list(value):
        if item and item not in seen:
            seen.add(item)
            items.append(item)
            if limit and len(items) >= limit:
                break
    return items


def _collection_priority(coll, index):
    if isinstance(coll, dict):
        raw = coll.get('priority')
    else:
        raw = None
    try:
        if raw is not None and str(raw).strip() != '':
            return int(raw)
    except Exception:
        pass
    return max(10, 60 - index)


def _infer_supported_intents(coll):
    intents = {'detail', 'aggregate_summary'}
    fields = coll.get('fields') or {}
    metrics = coll.get('metrics') or {}
    if coll.get('default_time_field') or any((f or {}).get('semantic_type') == 'time' or (f or {}).get('role') == 'time' for f in fields.values()):
        intents.add('trend')
    if metrics:
        intents.add('ranking')
    if coll.get('relations'):
        intents.add('enrich')
    return sorted(intents)


def _derive_collection_catalog_from_metadata(schema_metadata):
    meta = schema_metadata if isinstance(schema_metadata, dict) and 'collections' in schema_metadata else extract_schema_metadata(schema_metadata)
    warnings = []
    collections = []
    for idx, (cname, coll) in enumerate(sorted((meta.get('collections') or {}).items())):
        fields = coll.get('fields') or {}
        metrics = coll.get('metrics') or {}
        aliases = [cname, coll.get('collection_label')]
        aliases.extend(coll.get('collection_aliases') or [])
        primary_metrics = []
        for mname, metric in metrics.items():
            if len(primary_metrics) >= 12:
                break
            primary_metrics.append(mname)
            primary_metrics.extend(_catalog_str_list([metric.get('label')] + (metric.get('aliases') or []), limit=3))
        primary_dimensions = []
        for fname, field in fields.items():
            if len(primary_dimensions) >= 16:
                break
            if field.get('groupable') or field.get('filterable') or field.get('chartable') or field.get('role') in {'dimension', 'time'} or field.get('semantic_type') in {'dimension', 'time'}:
                primary_dimensions.append(fname)
                primary_dimensions.extend(_catalog_str_list([field.get('label')] + (field.get('aliases') or []), limit=2))
        related = []
        for rel in coll.get('relations') or []:
            target = str((rel or {}).get('target_collection') or '').strip()
            if target and target not in related:
                related.append(target)
        collections.append({
            'collection_name': cname,
            'collection_label': str(coll.get('collection_label') or cname).strip() or cname,
            'domain': str(coll.get('domain') or '').strip(),
            'aliases': _catalog_str_list(aliases, limit=24),
            'description': str(coll.get('description') or '').strip(),
            'default_time_field': str(coll.get('default_time_field') or '').strip(),
            'primary_metrics': _catalog_str_list(primary_metrics, limit=24),
            'primary_dimensions': _catalog_str_list(primary_dimensions, limit=32),
            'supported_intents': _infer_supported_intents(coll),
            'related_collections': related[:8],
            'priority': _collection_priority(coll, idx),
        })
    if not collections:
        warnings.append('schema metadata 中没有 collections，collection catalog 为空。')
    catalog = {
        'catalog_version': COLLECTION_CATALOG_VERSION,
        'catalog_digest': '',
        'collections': collections,
        'warnings': warnings + _as_str_list((meta or {}).get('warnings'))[:5],
    }
    catalog['catalog_digest'] = compute_collection_catalog_digest(catalog)
    return catalog


def compute_collection_catalog_digest(collection_catalog):
    catalog = copy.deepcopy(collection_catalog or {})
    catalog.pop('catalog_digest', None)
    payload = json.dumps(catalog, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def build_collection_catalog(schema_metadata=None, collection_catalog=None):
    if isinstance(collection_catalog, str) and collection_catalog.strip():
        try:
            collection_catalog = json.loads(collection_catalog)
        except Exception:
            collection_catalog = None
    if isinstance(collection_catalog, dict) and collection_catalog.get('catalog_version') == COLLECTION_CATALOG_VERSION:
        catalog = copy.deepcopy(collection_catalog)
        catalog.setdefault('collections', [])
        catalog.setdefault('warnings', [])
        catalog['catalog_digest'] = catalog.get('catalog_digest') or compute_collection_catalog_digest(catalog)
        return catalog
    return _derive_collection_catalog_from_metadata(schema_metadata or {})


def default_collection_selection():
    return {
        'contract_version': COLLECTION_SELECTION_CONTRACT_VERSION,
        'selected_primary_collection': '',
        'selected_related_collections': [],
        'confidence': 0,
        'needs_schema_retrieval': True,
        'primary_candidates': [],
        'related_candidates': [],
        'low_confidence': False,
        'requires_clarification': False,
        'clarification_question': '',
        'warnings': [],
    }


def _load_selection_obj(value):
    if isinstance(value, dict):
        return copy.deepcopy(value)
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return {}
    return {}


def _term_matches(question, terms):
    q = str(question or '')
    return [t for t in _catalog_str_list(terms, limit=100) if t and t in q]


def _explicit_collection_matches(question, coll):
    return _term_matches(question, [coll.get('collection_name'), coll.get('collection_label')] + (coll.get('aliases') or []))


def _coverage_matches(question, coll):
    return _term_matches(question, (coll.get('primary_metrics') or []) + (coll.get('primary_dimensions') or []))


def _has_cross_collection_intent(question):
    q = str(question or '').lower()
    terms = ['补充', '关联', '带出', '主数据', '详情', 'join', 'lookup', 'enrich']
    return any(t in q for t in terms)


def _negates_related(question, related_name):
    q = str(question or '').lower()
    name = str(related_name or '').lower()
    negative_terms = ['不要', '不需要', '无需', '不用', '排除', 'without', 'no ']
    return bool(name and any((neg + name) in q or (neg in q and name in q) for neg in negative_terms))


def select_collections(question, compact_context_json=None, turn_intent_json=None, schema_metadata=None, collection_catalog=None, planner=None):
    catalog = build_collection_catalog(schema_metadata, collection_catalog)
    result = default_collection_selection()
    result['collection_catalog_digest'] = catalog.get('catalog_digest', '')
    if not catalog.get('collections'):
        result.update({
            'confidence': 0,
            'low_confidence': True,
            'requires_clarification': True,
            'clarification_question': '缺少 collection catalog 或 schema metadata，无法确定要查询的业务集合。',
            'warnings': ['collection catalog unavailable'],
        })
        return result
    q = str(question or '')
    ctx = _load_selection_obj(compact_context_json)
    turn = _load_selection_obj(turn_intent_json)
    planner = _load_selection_obj(planner)
    intent = str(turn.get('turn_intent') or turn.get('intent') or '').strip()
    last_primary = str(ctx.get('last_primary_collection') or '').strip()
    warnings = []
    scored = []
    explicit_hits = set()
    for coll in catalog.get('collections') or []:
        cname = str(coll.get('collection_name') or '').strip()
        if not cname:
            continue
        score = 0.0
        matched = []
        reasons = []
        for alias in _catalog_str_list((coll.get('aliases') or []) + [coll.get('collection_label'), cname]):
            if alias and alias in q:
                score += 0.55
                matched.append(alias)
                explicit_hits.add(cname)
        for token in _catalog_str_list(coll.get('primary_metrics') or []):
            if token and token in q:
                score += 0.22
                matched.append(token)
        for token in _catalog_str_list(coll.get('primary_dimensions') or []):
            if token and token in q:
                score += 0.14
                matched.append(token)
        if intent and intent in (coll.get('supported_intents') or []):
            score += 0.05
        if last_primary and cname == last_primary and intent in {'refine_query', 'fix_result', 'continue_previous'}:
            if cname not in explicit_hits:
                score += 0.35
                reasons.append('命中上一轮 primary collection prior')
            else:
                score += 0.12
                reasons.append('当前问题命中且叠加上一轮 primary collection prior')
        score += min(float(coll.get('priority') or 0) / 1000.0, 0.1)
        if matched:
            reasons.append('命中：' + '、'.join(_catalog_str_list(matched, limit=8)))
        if not reasons and score > 0:
            reasons.append('collection priority tie-breaker')
        scored.append({'collection': cname, 'score': round(min(score, 1.0), 4), 'reason': '；'.join(reasons), 'matched_aliases': _catalog_str_list(matched, limit=12)})
    scored.sort(key=lambda x: (-x['score'], x['collection']))
    result['primary_candidates'] = [x for x in scored if x['score'] > 0][:3]
    top = result['primary_candidates'][0] if result['primary_candidates'] else None
    second = result['primary_candidates'][1] if len(result['primary_candidates']) > 1 else None
    if top and top['score'] >= 0.5:
        result['selected_primary_collection'] = top['collection']
        result['confidence'] = top['score']
    elif top:
        result['confidence'] = top['score']
        result['low_confidence'] = True
        result['requires_clarification'] = True
        result['clarification_question'] = '请明确要查询的业务对象或 collection。'
        warnings.append('collection selector low confidence')
    else:
        result['low_confidence'] = True
        result['requires_clarification'] = True
        result['clarification_question'] = '请明确要查询的业务对象或 collection。'
        warnings.append('没有命中 collection catalog 候选')
    if top and second and top['score'] - second['score'] < 0.12:
        warnings.append('primary collection 候选分数接近，存在歧义')
    selected = result['selected_primary_collection']
    related = []
    related_candidates = []
    schema_ref = ctx.get('schema_context_ref') if isinstance(ctx.get('schema_context_ref'), dict) else {}
    context_related = _catalog_str_list((ctx.get('last_related_collections') or []) + (schema_ref.get('collections') or []), limit=12)
    if selected:
        cmap = {c.get('collection_name'): c for c in catalog.get('collections') or []}
        selected_coll = cmap.get(selected) or {}
        primary_terms = _catalog_str_list((selected_coll.get('aliases') or []) + [selected_coll.get('collection_label'), selected] + (selected_coll.get('primary_metrics') or []) + (selected_coll.get('primary_dimensions') or []), limit=120)
        primary_matched = set(_term_matches(q, primary_terms))
        for rname in selected_coll.get('related_collections') or []:
            rc = cmap.get(rname) or {}
            explicit = _explicit_collection_matches(q, {'collection_name': rname, **rc})
            related_coverage = _coverage_matches(q, rc)
            matched = _catalog_str_list(explicit + related_coverage, limit=12)
            candidate = {'collection': rname, 'score': 0.0, 'reason': '', 'matched_aliases': matched[:8]}
            if not matched and not (intent in {'refine_query', 'fix_result', 'continue_previous'} and rname in context_related):
                continue
            necessary = False
            reason = ''
            if explicit:
                necessary = True
                reason = '用户问题明确命中 related collection name/label/alias：' + '、'.join(explicit[:6])
            elif _has_cross_collection_intent(q) and matched:
                necessary = True
                reason = '用户问题包含跨集合意图词且命中 related 字段/指标/维度：' + '、'.join(matched[:6])
            elif related_coverage and not all(t in primary_matched for t in related_coverage):
                necessary = True
                reason = 'related collection 覆盖 primary 未覆盖的字段/指标/维度：' + '、'.join([t for t in related_coverage if t not in primary_matched][:6])
            elif intent in {'refine_query', 'fix_result', 'continue_previous'} and rname in context_related and not _negates_related(q, rname):
                necessary = True
                reason = 'refine/continue compact context 已包含该 related collection 且当前问题未否定'
            elif matched:
                reason = 'related collection also matched terms, but primary collection already covers them; not selected'
            else:
                reason = 'low confidence related candidate; not selected'
            candidate['score'] = 0.72 if necessary else 0.36
            candidate['reason'] = reason
            related_candidates.append(candidate)
            if necessary:
                related.append(rname)
    result['selected_related_collections'] = related[:3]
    result['related_candidates'] = related_candidates[:5]
    if not result['selected_primary_collection'] and planner.get('primary_collection'):
        legacy = str(planner.get('primary_collection') or '').strip()
        if legacy and legacy != 'unknown':
            result['primary_candidates'].append({'collection': legacy, 'score': 0.45, 'reason': '兼容 fallback：旧 planner primary_collection', 'matched_aliases': []})
            warnings.append('selector 未高置信命中，保留旧 planner collection 候选')
    result['warnings'] = warnings
    return result

def _add_alias(index, alias, collection, kind, name, label, source, field=None):
    alias = str(alias or '').strip()
    if not alias:
        return
    item = {
        'collection': collection,
        'kind': kind,
        'name': name,
        'label': str(label or '').strip(),
        'confidence': 1.0,
        'source': source,
    }
    if field:
        item['field'] = field
    bucket = index.setdefault(alias, [])
    if not any(x.get('collection') == collection and x.get('kind') == kind and x.get('name') == name and x.get('field') == field for x in bucket):
        bucket.append(item)


def build_alias_index(schema_metadata):
    meta = schema_metadata if isinstance(schema_metadata, dict) and 'collections' in schema_metadata else extract_schema_metadata(schema_metadata)
    aliases = {}
    collection_aliases = {}
    field_aliases = {}
    metric_aliases = {}
    value_aliases = {}
    warnings = []
    for cname, coll in (meta.get('collections') or {}).items():
        _add_alias(collection_aliases, cname, cname, 'collection', cname, coll.get('collection_label'), 'collection.name')
        _add_alias(collection_aliases, coll.get('collection_label'), cname, 'collection', cname, coll.get('collection_label'), 'collection.label')
        _add_alias(aliases, cname, cname, 'collection', cname, coll.get('collection_label'), 'collection.name')
        _add_alias(aliases, coll.get('collection_label'), cname, 'collection', cname, coll.get('collection_label'), 'collection.label')
        for a in coll.get('collection_aliases') or []:
            _add_alias(collection_aliases, a, cname, 'collection', cname, coll.get('collection_label'), 'collection.aliases')
            _add_alias(aliases, a, cname, 'collection', cname, coll.get('collection_label'), 'collection.aliases')
        for fname, f in (coll.get('fields') or {}).items():
            for alias, source in [(fname, 'field.name'), (f.get('label'), 'field.label')]:
                _add_alias(aliases, alias, cname, 'field', fname, f.get('label'), source)
                _add_alias(field_aliases, alias, cname, 'field', fname, f.get('label'), source)
            for a in f.get('aliases') or []:
                _add_alias(aliases, a, cname, 'field', fname, f.get('label'), 'field.aliases')
                _add_alias(field_aliases, a, cname, 'field', fname, f.get('label'), 'field.aliases')
            for val_alias, canonical_value in (f.get('value_aliases') or {}).items():
                _add_alias(aliases, val_alias, cname, 'value', canonical_value, val_alias, 'field.value_aliases', field=fname)
                _add_alias(value_aliases, val_alias, cname, 'value', canonical_value, val_alias, 'field.value_aliases', field=fname)
        for mname, m in (coll.get('metrics') or {}).items():
            for alias, source in [(mname, 'metric.name'), (m.get('label'), 'metric.label')]:
                _add_alias(aliases, alias, cname, 'metric', mname, m.get('label'), source)
                _add_alias(metric_aliases, alias, cname, 'metric', mname, m.get('label'), source)
            for a in m.get('aliases') or []:
                _add_alias(aliases, a, cname, 'metric', mname, m.get('label'), 'metric.aliases')
                _add_alias(metric_aliases, a, cname, 'metric', mname, m.get('label'), 'metric.aliases')
    return {
        'contract_version': ALIAS_INDEX_CONTRACT_VERSION,
        'aliases': aliases,
        'collection_aliases': collection_aliases,
        'field_aliases': field_aliases,
        'metric_aliases': metric_aliases,
        'value_aliases': value_aliases,
        'warnings': warnings,
    }


def prettify_field_name(field_name):
    return str(field_name or '').strip().replace('_', ' ')


def _meta_and_index(schema_metadata):
    meta = schema_metadata if isinstance(schema_metadata, dict) and 'collections' in schema_metadata else extract_schema_metadata(schema_metadata)
    return meta, build_alias_index(meta)


def _resolve_from_candidates(raw, candidates, cname, kind, unresolved_warning):
    if cname:
        scoped = [x for x in candidates if x.get('collection') == cname]
        if len(scoped) == 1:
            x = scoped[0]
            return {'input': raw, 'resolved': x['name'], 'kind': kind, 'collection': x['collection'], 'source': x.get('source') or 'schema_alias', 'label': x.get('label') or prettify_field_name(x['name']), 'confidence': 1.0, 'warning': ''}
        if len(scoped) > 1:
            return {'input': raw, 'resolved': raw, 'kind': 'unknown', 'collection': cname, 'source': 'ambiguous_schema_alias', 'label': raw, 'confidence': 0, 'warning': f'{kind} 别名在当前 collection 内存在多个候选，未自动归一。', 'candidates': scoped}
    if len(candidates) == 1:
        x = candidates[0]
        return {'input': raw, 'resolved': x['name'], 'kind': kind, 'collection': x['collection'], 'source': x.get('source') or 'schema_alias_unique', 'label': x.get('label') or prettify_field_name(x['name']), 'confidence': 1.0, 'warning': ''}
    if len(candidates) > 1:
        return {'input': raw, 'resolved': raw, 'kind': 'unknown', 'collection': cname, 'source': 'ambiguous_schema_alias', 'label': raw, 'confidence': 0, 'warning': f'{kind} 别名在多个 collection 中存在，缺少 collection 上下文，未自动归一。', 'candidates': candidates}
    return {'input': raw, 'resolved': raw, 'kind': 'unknown', 'collection': cname, 'source': 'unresolved', 'label': raw, 'confidence': 0, 'warning': unresolved_warning}


def resolve_field_name(raw_name, schema_metadata, collection_name=None, old_plan_fields=None):
    raw = str(raw_name or '').strip()
    old = set(str(x).strip() for x in (old_plan_fields or []) if str(x).strip())
    meta, idx = _meta_and_index(schema_metadata)
    colls = meta.get('collections') or {}
    cname = str(collection_name or '').strip()
    if cname in colls and raw in (colls[cname].get('fields') or {}):
        f = colls[cname]['fields'][raw]
        return {'input': raw, 'resolved': raw, 'kind': 'field', 'collection': cname, 'source': 'schema_field_name', 'label': f.get('label') or prettify_field_name(raw), 'confidence': 1.0, 'warning': ''}
    candidates = [x for x in (idx.get('field_aliases') or idx.get('aliases') or {}).get(raw, []) if x.get('kind') == 'field']
    result = _resolve_from_candidates(raw, candidates, cname, 'field', '字段别名未在 schema metadata 中声明，未自动归一。')
    if result.get('source') != 'unresolved':
        return result
    if raw in old:
        return {'input': raw, 'resolved': raw, 'kind': 'field', 'collection': cname, 'source': 'old_plan_fields', 'label': prettify_field_name(raw), 'confidence': 0.8, 'warning': ''}
    return result


def resolve_metric_name(raw_name, schema_metadata, collection_name=None):
    raw = str(raw_name or '').strip()
    meta, idx = _meta_and_index(schema_metadata)
    colls = meta.get('collections') or {}
    cname = str(collection_name or '').strip()
    if cname in colls and raw in (colls[cname].get('metrics') or {}):
        m = colls[cname]['metrics'][raw]
        return {'input': raw, 'resolved': raw, 'kind': 'metric', 'collection': cname, 'source': 'schema_metric_name', 'label': m.get('label') or prettify_field_name(raw), 'confidence': 1.0, 'warning': ''}
    candidates = [x for x in (idx.get('metric_aliases') or idx.get('aliases') or {}).get(raw, []) if x.get('kind') == 'metric']
    return _resolve_from_candidates(raw, candidates, cname, 'metric', '指标别名未在 schema metadata 中声明，未自动归一。')


def resolve_value_alias(raw_value, schema_metadata, collection_name=None, field_name=None):
    raw = str(raw_value or '').strip()
    meta, idx = _meta_and_index(schema_metadata)
    cname = str(collection_name or '').strip()
    fname = str(field_name or '').strip()
    candidates = [x for x in (idx.get('value_aliases') or idx.get('aliases') or {}).get(raw, []) if x.get('kind') == 'value']
    if cname:
        candidates = [x for x in candidates if x.get('collection') == cname]
    if fname:
        candidates = [x for x in candidates if x.get('field') == fname]
    warning = ''
    if not candidates:
        warning = '值别名未在 schema metadata 中声明，未自动归一。'
    elif len(candidates) > 1:
        warning = '值别名存在多个候选，返回 candidates，未自动选择。'
    return {'input': raw, 'kind': 'value', 'collection': cname, 'field': fname, 'candidates': candidates, 'resolved': candidates[0].get('name') if len(candidates) == 1 else raw, 'warning': warning}


def resolve_field_label(field_name, schema_metadata, collection_name=None):
    meta, _ = _meta_and_index(schema_metadata)
    fname = str(field_name or '').strip()
    cname = str(collection_name or '').strip()
    if cname and cname in meta.get('collections', {}) and fname in meta['collections'][cname].get('fields', {}):
        return meta['collections'][cname]['fields'][fname].get('label') or prettify_field_name(fname)
    hits = []
    for cn, c in (meta.get('collections') or {}).items():
        if fname in c.get('fields', {}):
            hits.append(c['fields'][fname].get('label') or prettify_field_name(fname))
    return hits[0] if len(hits) == 1 else prettify_field_name(fname)


def resolve_metric_label(metric_name, schema_metadata, collection_name=None):
    meta, _ = _meta_and_index(schema_metadata)
    name = str(metric_name or '').strip()
    cname = str(collection_name or '').strip()
    if cname and cname in meta.get('collections', {}) and name in meta['collections'][cname].get('metrics', {}):
        return meta['collections'][cname]['metrics'][name].get('label') or prettify_field_name(name)
    hits = []
    for cn, c in (meta.get('collections') or {}).items():
        if name in c.get('metrics', {}):
            hits.append(c['metrics'][name].get('label') or prettify_field_name(name))
    return hits[0] if len(hits) == 1 else prettify_field_name(name)


def build_chart_title(group_field, metric_name, schema_metadata, collection_name=None, user_title=''):
    if str(user_title or '').strip():
        return str(user_title).strip(), 'chart_request'
    x = resolve_field_label(group_field, schema_metadata, collection_name)
    y = resolve_metric_label(metric_name, schema_metadata, collection_name)
    return ('按' + x + '统计' + y if x and y else (x + ' 与 ' + y + ' 对比')).strip(), 'schema_label'
