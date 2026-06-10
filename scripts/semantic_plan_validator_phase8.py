"""Phase 8 semantic_plan_validator runtime contract helper.

This module mirrors the code embedded in the DSL node `代码执行_semantic_plan_validator`.
It intentionally validates and canonicalizes semantic_plan_contract JSON without generating
Mongo pipelines or rewriting compiler/executor behavior.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

VALID_ROUTES = {"valid", "needs_replan", "requires_clarification", "blocked", "invalid"}
ALLOWED_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "nin", "contains", "regex", "between"}
SUPPORTED_CHART_TYPES = {"", "bar", "line", "pie", "table", "area", "scatter", "柱状图", "折线图", "饼图", "表格"}
PII_WORDS = ("手机", "手机号", "电话", "身份证", "邮箱", "email", "phone", "mobile", "id_card")


def _loads(v: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except Exception:
            return default
    return default


def _list(v: Any) -> List[Any]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [x for x in v if x is not None and str(x).strip()]
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [x for x in parsed if x is not None and str(x).strip()]
        except Exception:
            pass
        return [x.strip() for x in re.split(r"\s*,\s*", s) if x.strip()]
    return [v]


def _str(v: Any) -> str:
    return str(v or "").strip()


def _result(route: str, plan: Dict[str, Any] | None, errors=None, warnings=None, autofixes=None, clarification="", blocked="", debug=None, needs_schema_retrieval=False) -> Dict[str, Any]:
    route = route if route in VALID_ROUTES else "invalid"
    normalized = plan if isinstance(plan, dict) else {}
    return {
        "contract_version": "validator_result_contract",
        "valid": route == "valid",
        "validator_route": route,
        "normalized_plan": normalized,
        "normalized_plan_json": json.dumps(normalized, ensure_ascii=False) if route == "valid" and normalized else "{}",
        "semantic_plan_json": json.dumps(normalized, ensure_ascii=False) if route == "valid" and normalized else "{}",
        "normalized_semantic_plan_json": json.dumps(normalized, ensure_ascii=False) if route == "valid" and normalized else "{}",
        "errors": errors or [],
        "warnings": warnings or [],
        "autofixes": autofixes or [],
        "needs_replan": route == "needs_replan",
        "needs_schema_retrieval": bool(needs_schema_retrieval),
        "requires_clarification": route == "requires_clarification",
        "clarification_question": clarification or ("我需要确认一下：当前查询中的字段或指标对应哪个 schema 字段？" if route == "requires_clarification" else ""),
        "blocked_reason": blocked or ("当前查询不符合安全或 schema 关系约束，未进入 Mongo 编译执行。" if route == "blocked" else ""),
        "debug": debug or {},
        "validator_result_json": "",  # filled by main for DSL convenience
        "answer_payload_json": "",
        "answer_type": {"requires_clarification": "clarification", "blocked": "validation_error", "needs_replan": "validation_error", "invalid": "system_error"}.get(route, "data"),
        "final_answer_markdown": "",
    }


def _field_alias(raw: str, schema: Dict[str, Any], collection: str) -> Tuple[str, str, List[Dict[str, Any]]]:
    raw = _str(raw)
    coll = (schema.get("collections") or {}).get(collection) or {}
    if raw in (coll.get("fields") or {}):
        return raw, "schema_field_name", []
    hits = []
    for fname, field in (coll.get("fields") or {}).items():
        aliases = [fname, field.get("label")] + _list(field.get("aliases"))
        if raw and raw in [_str(x) for x in aliases]:
            hits.append({"name": fname, "source": "field_alias", "label": field.get("label")})
    if len(hits) == 1:
        return hits[0]["name"], "field_alias", hits
    if len(hits) > 1:
        return raw, "ambiguous", hits
    return raw, "unresolved", []


def _metric_alias(raw: str, schema: Dict[str, Any], collection: str) -> Tuple[str, str, List[Dict[str, Any]]]:
    raw = _str(raw)
    coll = (schema.get("collections") or {}).get(collection) or {}
    if raw in (coll.get("metrics") or {}):
        return raw, "schema_metric_name", []
    hits = []
    for mname, metric in (coll.get("metrics") or {}).items():
        aliases = [mname, metric.get("label")] + _list(metric.get("aliases"))
        if raw and raw in [_str(x) for x in aliases]:
            hits.append({"name": mname, "source": "metric_alias", "label": metric.get("label")})
    if len(hits) == 1:
        return hits[0]["name"], "metric_alias", hits
    if len(hits) > 1:
        return raw, "ambiguous", hits
    return raw, "unresolved", []


def _value_alias(value: Any, field: Dict[str, Any]) -> Tuple[Any, bool]:
    aliases = field.get("value_aliases") or {}
    key = _str(value)
    if key in aliases:
        return aliases[key], True
    return value, False


def _field(schema: Dict[str, Any], collection: str, name: str) -> Dict[str, Any]:
    return ((schema.get("collections") or {}).get(collection) or {}).get("fields", {}).get(name) or {}


def _metric(schema: Dict[str, Any], collection: str, name: str) -> Dict[str, Any]:
    return ((schema.get("collections") or {}).get(collection) or {}).get("metrics", {}).get(name) or {}


def _is_sensitive(f: Dict[str, Any]) -> bool:
    return bool(f.get("sensitive") or f.get("pii") or f.get("pii_category"))


def _canonical_field(raw: str, schema: Dict[str, Any], collection: str, place: str, errors: List[str], warnings: List[str], autofixes: List[Dict[str, Any]], require_flag: str = "") -> str:
    name, source, hits = _field_alias(raw, schema, collection)
    if source == "ambiguous":
        errors.append(f"字段 {raw} 在 collection={collection} 中存在多个候选，需要澄清。")
        return raw
    if source == "unresolved":
        errors.append(f"未知字段 {raw}，未在 schema metadata 中声明。")
        return raw
    if name != raw:
        autofixes.append({"type": "field_alias_canonicalize", "place": place, "from": raw, "to": name, "collection": collection})
    f = _field(schema, collection, name)
    if require_flag and not bool(f.get(require_flag)):
        if require_flag in {"projectable", "returnable"} and (f.get("projectable") or f.get("returnable")):
            warnings.append(f"字段 {name} 使用 {require_flag} 兼容策略放行。")
        else:
            errors.append(f"字段 {name} 不允许用于 {place}（{require_flag}=false）。")
    if _is_sensitive(f) and place in {"group_fields", "projection_fields", "sort", "chart_x", "chart_y"}:
        errors.append(f"敏感/PII 字段 {name} 不允许用于 {place}。")
    return name


def _stage_is_aggregate(stage: Dict[str, Any]) -> bool:
    return bool(stage.get("group_fields") or stage.get("metric_alias") or stage.get("metric_function") or stage.get("metric_field") or _str(stage.get("intent_type")) in {"aggregate_summary", "ranking", "trend"})


def _date_ok(s: str) -> bool:
    if not s:
        return True
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            datetime.strptime(s[:19], fmt)
            return True
        except Exception:
            pass
    return False


def _negative_fields(compact: Dict[str, Any], patch: Dict[str, Any], patch_debug: Dict[str, Any], qref: Dict[str, Any]) -> List[str]:
    out = []
    sources = [patch.get("negative_fields"), patch_debug.get("negative_fields"), qref.get("negative_fields")]
    for item in _list(patch.get("unapplied_refinements")):
        if isinstance(item, dict):
            sources.append(item.get("negative_fields") or item.get("field"))
    text = json.dumps([patch, patch_debug, qref], ensure_ascii=False)
    for word in ("brand", "品牌"):
        if ("不再按" in text or "negative_fields" in text) and word in text:
            out.append(word)
    for src in sources:
        for x in _list(src):
            if isinstance(x, dict):
                x = x.get("field") or x.get("name")
            if _str(x):
                out.append(_str(x))
    return list(dict.fromkeys(out))


def semantic_plan_validator(question: str = "", semantic_plan_json: str = "{}", schema_metadata_json: str = "{}", schema_alias_index_json: str = "{}", collection_selection_json: str = "{}", compact_context_json: str = "{}", patch_result_json: str = "{}", patch_debug_json: str = "{}", query_refinement_json: str = "{}", normalizer_valid: Any = True, normalizer_route: str = "ok") -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    autofixes: List[Dict[str, Any]] = []
    debug: Dict[str, Any] = {"normalizer_route": _str(normalizer_route)}

    plan = _loads(semantic_plan_json, {})
    schema = _loads(schema_metadata_json, {})
    selection = _loads(collection_selection_json, {})
    compact = _loads(compact_context_json, {})
    patch = _loads(patch_result_json, {})
    if isinstance(patch, list):
        patch = {"unapplied_refinements": patch}
    elif not isinstance(patch, dict):
        patch = {}
    patch_debug = _loads(patch_debug_json, {})
    if not isinstance(patch_debug, dict):
        patch_debug = {}
    qref = _loads(query_refinement_json, {})
    if not isinstance(qref, dict):
        qref = {}

    if str(normalizer_valid).lower() in {"false", "0", "no"}:
        return _finalize(_result("invalid", {}, ["semantic_plan_normalizer 未通过，validator 拒绝进入 Mongo 编译。"], debug=debug))
    if not isinstance(plan, dict) or not plan:
        return _finalize(_result("invalid", {}, ["semantic_plan_json 为空或不是 JSON 对象。"], debug=debug))
    stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    if not stages:
        return _finalize(_result("invalid", plan, ["semantic_plan_contract 缺少 stages。"], debug=debug))
    collections = schema.get("collections") or {}
    if not collections:
        return _finalize(_result("needs_replan", plan, ["缺少 schema_metadata.collections，无法校验字段与指标。"], debug=debug, needs_schema_retrieval=True))

    plan = copy.deepcopy(plan)
    primary = _str(plan.get("primary_collection") or (stages[0] or {}).get("collection"))
    selected = _str(selection.get("selected_primary_collection"))
    confidence = float(selection.get("confidence") or 0) if isinstance(selection, dict) else 0.0
    if not primary or primary not in collections:
        errors.append(f"primary_collection={primary or '<empty>'} 不存在于 schema metadata。")
    if selected and primary and selected != primary:
        msg = f"collection_selection.selected_primary_collection={selected} 与 plan.primary_collection={primary} 不一致。"
        if confidence and confidence < 0.6:
            return _finalize(_result("requires_clarification", plan, [msg], clarification="我需要确认一下：当前查询应该使用哪个业务集合？", debug=debug))
        errors.append(msg)
    related = [_str(x) for x in _list(plan.get("related_collections"))]
    for rc in related:
        if rc not in collections:
            errors.append(f"related_collection={rc} 不存在于 schema metadata。")

    route_hint = "valid"
    for i, stage in enumerate(plan.get("stages") or []):
        if not isinstance(stage, dict):
            errors.append(f"stage[{i}] 不是对象。")
            continue
        coll = _str(stage.get("collection") or primary)
        stage["collection"] = coll
        if coll not in collections:
            errors.append(f"stage[{i}].collection={coll} 不存在。")
            continue
        if primary and coll != primary and coll not in related:
            errors.append(f"stage[{i}].collection={coll} 不属于 primary/related collections。")
        coll_meta = collections.get(coll) or {}
        rules = coll_meta.get("query_rules") or {}

        group_fields = []
        for gf in _list(stage.get("group_fields")):
            group_fields.append(_canonical_field(_str(gf), schema, coll, "group_fields", errors, warnings, autofixes, "groupable"))
        stage["group_fields"] = group_fields

        metric_alias = _str(stage.get("metric_alias"))
        if metric_alias:
            mname, msrc, _ = _metric_alias(metric_alias, schema, coll)
            if msrc == "ambiguous":
                errors.append(f"指标 {metric_alias} 存在多个候选，需要澄清。")
            elif msrc == "unresolved":
                errors.append(f"未知指标 {metric_alias}，不允许当作字段或自动猜测。")
            else:
                if mname != metric_alias:
                    autofixes.append({"type": "metric_alias_canonicalize", "from": metric_alias, "to": mname, "collection": coll})
                stage["metric_alias"] = mname
                metric = _metric(schema, coll, mname)
                if not _str(stage.get("metric_function")) and _str(metric.get("function")):
                    stage["metric_function"] = _str(metric.get("function")); autofixes.append({"type": "metric_function_fill", "metric": mname, "value": stage["metric_function"]})
                elif _str(stage.get("metric_function")) and _str(metric.get("function")) and _str(stage.get("metric_function")) != _str(metric.get("function")):
                    errors.append(f"metric_function 与 schema metric {mname}.function 不一致。")
                if not _str(stage.get("metric_field")) and _str(metric.get("field")):
                    stage["metric_field"] = _str(metric.get("field")); autofixes.append({"type": "metric_field_fill", "metric": mname, "value": stage["metric_field"]})
                elif _str(stage.get("metric_field")) and _str(metric.get("field")) and _str(stage.get("metric_field")) != _str(metric.get("field")):
                    errors.append(f"metric_field 与 schema metric {mname}.field 不一致。")
                for sf in _list(metric.get("source_fields")):
                    if sf not in (coll_meta.get("fields") or {}):
                        errors.append(f"metric {mname}.source_field={sf} 不存在。")
                allowed_dims = [_str(x) for x in _list(metric.get("allowed_dimensions"))]
                if allowed_dims:
                    for gf in group_fields:
                        if gf not in allowed_dims:
                            errors.append(f"字段 {gf} 不在 metric {mname}.allowed_dimensions 中。")
        elif _stage_is_aggregate(stage) and _str(stage.get("metric_field")):
            mf = _canonical_field(_str(stage.get("metric_field")), schema, coll, "metric_field", errors, warnings, autofixes, "")
            stage["metric_field"] = mf

        filters = []
        for f in _list(stage.get("filters")):
            if not isinstance(f, dict):
                errors.append("filter 不是对象，已拒绝。")
                continue
            nf = copy.deepcopy(f)
            fname = _canonical_field(_str(nf.get("field")), schema, coll, "filters", errors, warnings, autofixes, "filterable")
            nf["field"] = fname
            field = _field(schema, coll, fname)
            op = _str(nf.get("operator") or nf.get("op") or "eq")
            if op not in ALLOWED_OPERATORS:
                errors.append(f"filter operator={op} 不在允许范围。")
            nf["operator"] = op
            if _is_sensitive(field) and not bool((rules.get("allow_sensitive_filter") or rules.get("allow_pii_filter"))):
                errors.append(f"敏感/PII 字段 {fname} 默认不允许 filter。")
            if "value" in nf:
                new_value, changed = _value_alias(nf.get("value"), field)
                if changed:
                    autofixes.append({"type": "value_alias_canonicalize", "field": fname, "from": nf.get("value"), "to": new_value})
                    nf["value"] = new_value
                allowed_values = [_str(x) for x in _list(field.get("allowed_values"))]
                vals = _list(nf.get("value")) if op in {"in", "nin"} else [nf.get("value")]
                if allowed_values:
                    for val in vals:
                        if _str(val) not in allowed_values:
                            errors.append(f"字段 {fname} 的枚举值 {val} 不在 allowed_values 中。")
            filters.append(nf)
        stage["filters"] = filters

        projections = []
        for pf in _list(stage.get("projection_fields")):
            projections.append(_canonical_field(_str(pf), schema, coll, "projection_fields", errors, warnings, autofixes, "projectable"))
        stage["projection_fields"] = projections

        tr = stage.get("time_range") if isinstance(stage.get("time_range"), dict) else {}
        if _stage_is_aggregate(stage) and bool(rules.get("require_time_range_for_aggregate")) and not tr:
            return _finalize(_result("requires_clarification", plan, ["聚合查询按 query_rules 要求必须提供 time_range。"], clarification="我需要确认一下：这个聚合查询要统计哪个时间范围？", debug=debug))
        if tr:
            tf = _str(tr.get("field"))
            if not tf and _str(coll_meta.get("default_time_field")):
                tr["field"] = _str(coll_meta.get("default_time_field")); autofixes.append({"type": "time_range_field_fill", "value": tr["field"], "collection": coll})
            elif tf:
                tr["field"] = _canonical_field(tf, schema, coll, "time_range", errors, warnings, autofixes, "")
            if _str(tr.get("field")):
                fld = _field(schema, coll, _str(tr.get("field")))
                if _str(fld.get("semantic_type") or fld.get("role") or fld.get("type")).lower() not in {"time", "date", "datetime", "timestamp"}:
                    warnings.append(f"time_range.field={tr.get('field')} 未显式标记为时间字段。")
            try:
                rd = int(tr.get("relative_days") or 0)
            except Exception:
                rd = 0
            if _str(tr.get("mode")) == "relative_days" and rd <= 0:
                errors.append("time_range.relative_days 必须大于 0。")
            max_days = int(rules.get("max_time_range_days") or 0)
            if rd and max_days and rd > max_days:
                tr["relative_days"] = max_days; warnings.append(f"relative_days 超过 max_time_range_days，已截断为 {max_days}。"); autofixes.append({"type": "relative_days_truncate", "from": rd, "to": max_days})
            if not _date_ok(_str(tr.get("start"))) or not _date_ok(_str(tr.get("end"))):
                errors.append("time_range.start/end 日期格式不合理。")
            stage["time_range"] = tr

        try:
            limit = int(stage.get("limit") or rules.get("default_limit") or 100)
        except Exception:
            limit = int(rules.get("default_limit") or 100)
            autofixes.append({"type": "limit_default", "value": limit})
        if limit <= 0:
            limit = int(rules.get("default_limit") or 100); autofixes.append({"type": "limit_default", "value": limit})
        max_limit = int(rules.get("max_limit") or 0)
        if max_limit and limit > max_limit:
            autofixes.append({"type": "limit_truncate", "from": limit, "to": max_limit}); warnings.append(f"limit 超过 max_limit，已截断为 {max_limit}。"); limit = max_limit
        stage["limit"] = limit

        valid_sort_fields = set(group_fields + projections + [_str(stage.get("metric_alias"))])
        for fname, fld in (coll_meta.get("fields") or {}).items():
            if fld.get("sortable"):
                valid_sort_fields.add(fname)
        sorts = []
        for s in _list(stage.get("sort")):
            if not isinstance(s, dict):
                errors.append("sort 不是对象。")
                continue
            ns = copy.deepcopy(s)
            sf = _str(ns.get("field"))
            if sf and sf not in valid_sort_fields:
                cf = _canonical_field(sf, schema, coll, "sort", errors, warnings, autofixes, "sortable")
                ns["field"] = cf
                if cf not in valid_sort_fields and cf != _str(stage.get("metric_alias")):
                    errors.append(f"sort.field={sf} 不是 group/metric/projection/schema sortable 字段。")
            d = ns.get("direction", ns.get("order", "desc"))
            if d in (1, "1", "asc", "ASC", "ascending"):
                nd = "asc"
            elif d in (-1, "-1", "desc", "DESC", "descending"):
                nd = "desc"
            else:
                errors.append(f"sort.direction={d} 不合法。")
                nd = "desc"
            if nd != d:
                autofixes.append({"type": "sort_direction_normalize", "from": d, "to": nd})
            ns["direction"] = nd
            sorts.append(ns)
        if not sorts and _str(stage.get("metric_alias")):
            m = _metric(schema, coll, _str(stage.get("metric_alias")))
            if _str(m.get("default_sort")):
                sorts.append({"field": stage.get("metric_alias"), "direction": _str(m.get("default_sort"))}); autofixes.append({"type": "metric_default_sort", "metric": stage.get("metric_alias"), "direction": m.get("default_sort")})
        stage["sort"] = sorts

    # relations
    for rc in related:
        rels = [r for r in ((collections.get(primary) or {}).get("relations") or []) if _str(r.get("target_collection")) == rc]
        if not rels:
            errors.append(f"schema relations 未声明 {primary}->{rc}，禁止 LLM 发明 join。")
            continue
        for rel in rels:
            if not rel.get("join_keys"):
                errors.append(f"relation {primary}->{rc} 缺少 join_keys。")
            for jk in rel.get("join_keys") or []:
                if _str(jk.get("source_field")) not in ((collections.get(primary) or {}).get("fields") or {}):
                    errors.append(f"relation source_field={jk.get('source_field')} 不存在。")
                if _str(jk.get("target_field")) not in ((collections.get(rc) or {}).get("fields") or {}):
                    errors.append(f"relation target_field={jk.get('target_field')} 不存在。")
            intent_text = json.dumps(plan.get("stages") or [], ensure_ascii=False)
            for forbidden in _list(rel.get("forbidden_usage")):
                if _str(forbidden) and _str(forbidden) in intent_text:
                    return _finalize(_result("blocked", plan, [f"relation {primary}->{rc} 命中 forbidden_usage={forbidden}。"], blocked="当前查询使用了 schema 禁止的关联关系。", debug=debug))

    chart = plan.get("chart_request") if isinstance(plan.get("chart_request"), dict) else {}
    if bool(chart.get("enabled")) or bool(plan.get("needs_chart")):
        first = (plan.get("stages") or [{}])[0]
        group_fields = _list(first.get("group_fields"))
        metric_alias = _str(first.get("metric_alias"))
        ctype = _str(chart.get("chart_type"))
        if ctype not in SUPPORTED_CHART_TYPES:
            errors.append(f"chart_type={ctype} 不支持。")
        x = _str(chart.get("x_field"))
        y = _str(chart.get("y_field"))
        if not x and len(group_fields) == 1:
            chart["x_field"] = group_fields[0]; autofixes.append({"type": "chart_x_fill", "value": group_fields[0]})
        elif x and x not in group_fields:
            if len(group_fields) == 1:
                chart["x_field"] = group_fields[0]; autofixes.append({"type": "chart_x_align", "from": x, "to": group_fields[0]})
            else:
                errors.append("chart_request.x_field 未与 group_fields/输出字段对齐。")
        if not y and metric_alias:
            chart["y_field"] = metric_alias; autofixes.append({"type": "chart_y_fill", "value": metric_alias})
        elif y and metric_alias and y != metric_alias:
            ym, ysrc, _ = _metric_alias(y, schema, primary)
            if ym == metric_alias:
                chart["y_field"] = metric_alias; autofixes.append({"type": "chart_y_metric_alias_align", "from": y, "to": metric_alias})
            else:
                errors.append("chart_request.y_field 未与 metric_alias 对齐。")
        if _str(chart.get("x_field")) and _is_sensitive(_field(schema, primary, _str(chart.get("x_field")))):
            errors.append("chart_request.x_field 命中敏感/PII 字段。")
        if any(k in json.dumps(chart, ensure_ascii=False).lower() for k in ["debug", "schema", "raw prompt", "prompt"]):
            warnings.append("chart_request.title 含 debug/schema/prompt 字样，已清空 title。")
            chart["title"] = ""; autofixes.append({"type": "chart_title_sanitize"})
        plan["chart_request"] = chart

    # user negative old field residue
    negative = _negative_fields(compact, patch, patch_debug, qref)
    canonical_negative = []
    for nf in negative:
        cn, _, _ = _field_alias(nf, schema, primary)
        canonical_negative.append(cn)
    used = []
    for st in plan.get("stages") or []:
        used.extend(_list(st.get("group_fields")))
    if any(x in canonical_negative or x in negative for x in used):
        return _finalize(_result("needs_replan", plan, ["检测到用户本轮否定的旧字段仍残留在 group_fields，拒绝进入 compiler。"], debug={**debug, "negative_fields": negative}))

    question_l = _str(question).lower()
    if any(w in question_l for w in PII_WORDS):
        for st in plan.get("stages") or []:
            for gf in _list(st.get("group_fields")):
                if _is_sensitive(_field(schema, _str(st.get("collection") or primary), gf)):
                    return _finalize(_result("blocked", plan, ["用户要求按敏感/PII 字段统计。"], blocked="这个查询涉及敏感字段，不能直接按该字段分组或展示。", debug=debug))

    if errors:
        joined = "；".join(errors)
        if any("敏感/PII" in e or "forbidden_usage" in e or "relation" in e or "join" in e for e in errors):
            return _finalize(_result("blocked", plan, errors, warnings, autofixes, blocked="这个查询涉及敏感字段或非法关联，不能直接执行。", debug=debug))
        if any("未知字段" in e or "多个候选" in e or "枚举值" in e for e in errors):
            return _finalize(_result("requires_clarification", plan, errors, warnings, autofixes, clarification="我需要确认一下：当前查询中的字段、指标或枚举值对应哪个 schema 定义？", debug=debug))
        return _finalize(_result("needs_replan", plan, errors, warnings, autofixes, debug=debug))

    return _finalize(_result(route_hint, plan, [], warnings, autofixes, debug=debug))


def _finalize(res: Dict[str, Any]) -> Dict[str, Any]:
    route = res.get("validator_route")
    if route == "valid":
        text = ""
    elif route == "requires_clarification":
        text = res.get("clarification_question") or "我需要确认一下查询字段后再执行。"
    elif route == "blocked":
        text = res.get("blocked_reason") or "这个查询不符合安全策略，未执行。"
    elif route == "needs_replan":
        text = "当前问题需要重新规划，请补充或调整字段、指标、集合或时间范围后再试。"
    else:
        text = "语义计划校验失败，未进入 Mongo 编译执行。请补充查询对象、字段、指标或时间范围后重试。"
    res["final_answer_markdown"] = text
    res["answer_payload_json"] = json.dumps({"answer_type": res.get("answer_type"), "answer": text, "validator_route": route}, ensure_ascii=False)
    res["validator_result_json"] = json.dumps({k: v for k, v in res.items() if k not in {"validator_result_json"}}, ensure_ascii=False)
    return res


def main(**kwargs: Any) -> Dict[str, Any]:
    return semantic_plan_validator(**kwargs)


def _sample_schema() -> Dict[str, Any]:
    return {"contract_version":"schema_metadata_contract","collections":{"orders":{"collection_name":"orders","default_time_field":"created_at","fields":{"brand":{"name":"brand","label":"品牌","aliases":[],"groupable":True,"filterable":True,"sortable":True,"projectable":True,"returnable":True},"status":{"name":"status","label":"订单状态","aliases":["状态"],"allowed_values":["paid","unpaid"],"value_aliases":{"已支付":"paid"},"groupable":True,"filterable":True,"sortable":True,"projectable":True,"returnable":True},"amount":{"name":"amount","label":"金额","groupable":False,"filterable":True,"sortable":True,"projectable":True,"returnable":True},"created_at":{"name":"created_at","label":"创建时间","semantic_type":"time","role":"time","groupable":False,"filterable":True,"sortable":True,"projectable":True,"returnable":True},"user_phone":{"name":"user_phone","label":"手机号","pii":True,"groupable":False,"filterable":False,"sortable":False,"projectable":False,"returnable":False}},"metrics":{"order_count":{"name":"order_count","label":"订单数","function":"count","field":"_id","source_fields":[],"output_type":"number","allowed_dimensions":["brand","status"],"default_sort":"desc"},"gmv_sum":{"name":"gmv_sum","label":"销售额","aliases":["GMV"],"function":"sum","field":"amount","source_fields":["amount"],"output_type":"number","allowed_dimensions":["brand","status"],"default_sort":"desc"}},"relations":[],"query_rules":{"require_time_range_for_aggregate":False,"max_limit":100,"default_limit":100,"max_time_range_days":366,"sensitive_field_policy":"deny_return_group_sort"}}}}


def run_selftests() -> List[Tuple[str, bool, str]]:
    schema = _sample_schema()
    base = {"contract_version":"semantic_plan_contract","primary_collection":"orders","related_collections":[],"stages":[{"collection":"orders","intent_type":"aggregate_summary","time_range":{"mode":"relative_days","relative_days":30},"filters":[],"group_fields":["brand"],"metric_alias":"order_count","sort":[],"limit":10,"projection_fields":[]}],"chart_request":{"enabled":False}}
    cases = []
    def check(name, plan, expect_route, pred=lambda r: True, extra=None):
        kwargs = dict(question="", semantic_plan_json=json.dumps(plan, ensure_ascii=False), schema_metadata_json=json.dumps(schema, ensure_ascii=False), collection_selection_json=json.dumps({"selected_primary_collection":"orders","confidence":0.9}, ensure_ascii=False))
        if extra: kwargs.update(extra)
        r = semantic_plan_validator(**kwargs)
        cases.append((name, r.get("validator_route") == expect_route and pred(r), r.get("validator_route") + " " + json.dumps(r.get("errors"), ensure_ascii=False)))
    check("normal aggregate", copy.deepcopy(base), "valid")
    p=copy.deepcopy(base); p["stages"][0]["group_fields"]=["订单状态"]; check("alias canonicalize", p, "valid", lambda r: r["normalized_plan"]["stages"][0]["group_fields"]==["status"])
    p=copy.deepcopy(base); p["stages"][0]["metric_alias"]="销售额"; p["stages"][0]["metric_function"]=""; p["stages"][0]["metric_field"]=""; check("metric canonicalize", p, "valid", lambda r: r["normalized_plan"]["stages"][0]["metric_alias"]=="gmv_sum" and r["normalized_plan"]["stages"][0]["metric_field"]=="amount")
    p=copy.deepcopy(base); p["stages"][0]["group_fields"]=["customer_level"]; check("unknown field", p, "requires_clarification")
    p=copy.deepcopy(base); p["stages"][0]["group_fields"]=["user_phone"]; check("pii group", p, "blocked")
    p=copy.deepcopy(base); p["stages"][0]["time_range"]={"mode":"relative_days","relative_days":7}; check("missing time field", p, "valid", lambda r: r["normalized_plan"]["stages"][0]["time_range"]["field"]=="created_at")
    schema2=copy.deepcopy(schema); schema2["collections"]["orders"]["query_rules"]["require_time_range_for_aggregate"]=True
    p=copy.deepcopy(base); p["stages"][0]["time_range"]={}; r=semantic_plan_validator(semantic_plan_json=json.dumps(p),schema_metadata_json=json.dumps(schema2),collection_selection_json=json.dumps({"selected_primary_collection":"orders","confidence":0.9})); cases.append(("aggregate requires time", r["validator_route"]=="requires_clarification", r["validator_route"]))
    p=copy.deepcopy(base); p["stages"][0]["limit"]=10000; check("limit over max", p, "valid", lambda r: r["normalized_plan"]["stages"][0]["limit"]==100)
    p=copy.deepcopy(base); p["stages"][0]["group_fields"]=["status"]; p["chart_request"]={"enabled":True,"chart_type":"bar","x_field":"brand","y_field":"order_count"}; check("chart x align", p, "valid", lambda r: r["normalized_plan"]["chart_request"]["x_field"]=="status")
    p=copy.deepcopy(base); check("negative residue", p, "needs_replan", extra={"patch_result_json":json.dumps({"negative_fields":["brand"]}, ensure_ascii=False)})
    p=copy.deepcopy(base); p["related_collections"]=["products"]; schema3=copy.deepcopy(schema); schema3["collections"]["products"]={"collection_name":"products","fields":{},"metrics":{},"relations":[],"query_rules":{}}; r=semantic_plan_validator(semantic_plan_json=json.dumps(p),schema_metadata_json=json.dumps(schema3),collection_selection_json=json.dumps({"selected_primary_collection":"orders","confidence":0.9})); cases.append(("invalid relation", r["validator_route"] in {"blocked","needs_replan"}, r["validator_route"]))
    return cases


if __name__ == "__main__":
    failed = False
    for name, ok, detail in run_selftests():
        print(("PASS" if ok else "FAIL"), name, detail)
        failed = failed or not ok
    raise SystemExit(1 if failed else 0)
