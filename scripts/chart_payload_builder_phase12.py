# frozen -*- coding: utf-8 -*-
"""Phase 12 chart payload builder.

Pure adapter from chart_request_contract + query_result_contract to chart_payload_contract.
It does not infer business fields, resolve aliases, alter query semantics, or call Mongo.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Dict, List

SUPPORTED_CHART_TYPES = {"bar", "line", "pie", "scatter"}
DEFAULT_CHART_REQUEST = {
    "enabled": False,
    "chart_type": "",
    "x_field": "",
    "y_field": "",
    "series_name": "",
    "title": "",
    "field_labels": {},
}
DEFAULT_QUERY_RESULT = {
    "contract_version": "query_result_contract",
    "success": True,
    "rows": [],
    "row_count": 0,
    "fields": [],
    "profile": {"preview_rows": [], "numeric_fields": [], "dimension_fields": []},
    "error": "",
    "warnings": [],
}


def loads_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return fallback
    return fallback


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def rows_from(value: Any) -> List[Dict[str, Any]]:
    parsed = loads_json(value, value)
    if isinstance(parsed, dict) and isinstance(parsed.get("rows"), list):
        parsed = parsed.get("rows")
    if isinstance(parsed, list):
        return [r for r in parsed if isinstance(r, dict)]
    return []


def number_value(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except Exception:
        return None


def is_numeric_field(rows: List[Dict[str, Any]], field: str) -> bool:
    vals = [r.get(field) for r in rows[:200] if r.get(field) is not None]
    if not vals:
        return False
    numeric = sum(1 for v in vals if number_value(v) is not None)
    return numeric >= max(1, int(len(vals) * 0.7))


def detect_profile(rows: List[Dict[str, Any]], fields: List[str] | None = None) -> Dict[str, Any]:
    field_list: List[str] = []
    for f in fields or []:
        if str(f or "").strip() and str(f) not in field_list:
            field_list.append(str(f))
    for row in rows[:20]:
        for k in row.keys():
            if str(k) not in field_list:
                field_list.append(str(k))
    numeric = [f for f in field_list if is_numeric_field(rows, f)]
    dimensions = [f for f in field_list if f not in numeric]
    return {"preview_rows": rows[:5], "numeric_fields": numeric, "dimension_fields": dimensions}


def normalize_chart_request(*sources: Any) -> Dict[str, Any]:
    req: Dict[str, Any] = {}
    for source in sources:
        obj = loads_json(source, {})
        if isinstance(obj, dict):
            if isinstance(obj.get("normalized_plan"), dict):
                obj = obj["normalized_plan"]
            if isinstance(obj.get("execution_plan"), dict) and isinstance(obj["execution_plan"].get("chart_request"), dict):
                obj = obj["execution_plan"]
            candidate = obj.get("chart_request") if isinstance(obj.get("chart_request"), dict) else obj
            if isinstance(candidate, dict) and any(k in candidate for k in DEFAULT_CHART_REQUEST):
                req = candidate
                break
    out = deepcopy(DEFAULT_CHART_REQUEST)
    if isinstance(req, dict):
        for key in out:
            if key in req:
                out[key] = req[key]
    out["enabled"] = to_bool(out.get("enabled"))
    out["chart_type"] = str(out.get("chart_type") or "").strip().lower()
    out["x_field"] = str(out.get("x_field") or "").strip()
    out["y_field"] = str(out.get("y_field") or "").strip()
    out["series_name"] = str(out.get("series_name") or "").strip()
    out["title"] = str(out.get("title") or "").strip()
    out["field_labels"] = out.get("field_labels") if isinstance(out.get("field_labels"), dict) else {}
    return out


def normalize_query_result(query_result_json: Any = "", rows_json: Any = "", row_count: Any = 0, fields_json: Any = "", merged_rows_json: Any = "") -> Dict[str, Any]:
    warnings: List[str] = []
    qr = loads_json(query_result_json, {})
    if isinstance(qr, dict) and ("rows" in qr or qr.get("contract_version") in {"query_result_contract", "query_result_profile_contract"}):
        rows = rows_from(qr.get("rows", []))
        fields = loads_json(qr.get("fields", []), [])
        if not rows and rows_json:
            rows = rows_from(rows_json)
        if not fields and fields_json:
            fields = loads_json(fields_json, [])
        success = bool(qr.get("success", True)) and not bool(qr.get("error"))
        warnings.extend(qr.get("warnings") if isinstance(qr.get("warnings"), list) else [])
        profile = qr.get("profile") if isinstance(qr.get("profile"), dict) else detect_profile(rows, fields)
        return {"contract_version": "query_result_contract", "success": success, "rows": rows, "row_count": int(qr.get("row_count") or len(rows)), "fields": [str(f) for f in fields] if isinstance(fields, list) else [], "profile": profile, "error": str(qr.get("error") or ""), "warnings": warnings}
    rows = rows_from(rows_json)
    fields = loads_json(fields_json, [])
    if not rows and merged_rows_json:
        rows = rows_from(merged_rows_json)
        warnings.append("legacy_rows_fallback_used")
    try:
        rc = int(row_count if row_count not in (None, "") else len(rows))
    except Exception:
        rc = len(rows)
    if not isinstance(fields, list) or not fields:
        fields = list(rows[0].keys()) if rows else []
    return {"contract_version": "query_result_contract", "success": True, "rows": rows, "row_count": rc, "fields": [str(f) for f in fields], "profile": detect_profile(rows, [str(f) for f in fields]), "error": "", "warnings": warnings}


def default_payload(chart_request: Dict[str, Any] | None = None, query_result: Dict[str, Any] | None = None) -> Dict[str, Any]:
    req = chart_request or DEFAULT_CHART_REQUEST
    qr = query_result or DEFAULT_QUERY_RESULT
    rows = qr.get("rows") if isinstance(qr.get("rows"), list) else []
    fields = qr.get("fields") if isinstance(qr.get("fields"), list) else []
    return {
        "contract_version": "chart_payload_contract",
        "enabled": False,
        "renderer": "echarts",
        "chart_type": str(req.get("chart_type") or ""),
        "option": {},
        "echarts_markdown": "",
        "summary": "",
        "warning": "",
        "data_profile": {"row_count": int(qr.get("row_count") or len(rows or [])), "x_field": str(req.get("x_field") or ""), "y_field": str(req.get("y_field") or ""), "fields": [str(f) for f in fields], "preview_rows": rows[:5]},
        "chart_error": "",
        "chart_debug": {},
    }


def build_title(req: Dict[str, Any]) -> str:
    if req.get("title"):
        return str(req["title"])
    labels = req.get("field_labels") if isinstance(req.get("field_labels"), dict) else {}
    x = labels.get(req.get("x_field")) or req.get("x_field") or "维度"
    y = labels.get(req.get("y_field")) or req.get("series_name") or req.get("y_field") or "指标"
    if x and y:
        return f"按{x}统计{y}"
    return "查询结果图表"


def markdown(option: Dict[str, Any]) -> str:
    fence = "`" * 3
    return fence + "echarts\n" + json.dumps(option, ensure_ascii=False, indent=2) + "\n" + fence


def build_chart_payload(chart_request: Dict[str, Any], query_result: Dict[str, Any], chart_only_mode: bool = False) -> Dict[str, Any]:
    req = normalize_chart_request(chart_request)
    qr = normalize_query_result(query_result)
    payload = default_payload(req, qr)
    rows = qr.get("rows") if isinstance(qr.get("rows"), list) else []
    fields = set(qr.get("fields") if isinstance(qr.get("fields"), list) else [])
    for row in rows[:20]:
        fields.update(row.keys())
    payload["data_profile"]["fields"] = [str(f) for f in fields]
    payload["data_profile"]["preview_rows"] = rows[:5]
    if not req.get("enabled"):
        payload["warning"] = "chart_request disabled"
        return payload
    if not qr.get("success", True):
        payload["warning"] = "查询执行失败，未生成图表。"
        payload["chart_error"] = payload["warning"]
        return payload
    if not rows:
        payload["warning"] = "上一轮结果摘要不足以重新生成图表" if chart_only_mode else "查询结果为空，未生成图表。"
        return payload
    x = req.get("x_field") or ""
    y = req.get("y_field") or ""
    if not x or not y or x not in fields or y not in fields:
        payload["warning"] = "图表字段不在查询结果中"
        return payload
    if not is_numeric_field(rows, y):
        payload["warning"] = "图表纵轴字段不是数字，未生成图表。"
        return payload
    ctype = req.get("chart_type") or "bar"
    if ctype not in SUPPORTED_CHART_TYPES:
        payload["warning"] = "暂不支持该图表类型"
        return payload
    title = build_title(req)
    series_name = req.get("series_name") or y
    if ctype == "pie":
        data = [{"name": str(r.get(x) if r.get(x) is not None else "未分类"), "value": number_value(r.get(y)) or 0} for r in rows[:20]]
        option = {"title": {"text": title}, "tooltip": {"trigger": "item"}, "series": [{"name": series_name, "type": "pie", "data": data}]}
        if len(rows) > 20:
            payload["warning"] = "饼图分类较多，仅展示前20项。"
    elif ctype == "scatter":
        if not is_numeric_field(rows, x):
            payload["warning"] = "散点图横轴字段不是数字，未生成图表。"
            return payload
        data = [[number_value(r.get(x)), number_value(r.get(y))] for r in rows if number_value(r.get(x)) is not None and number_value(r.get(y)) is not None]
        option = {"title": {"text": title}, "tooltip": {"trigger": "item"}, "xAxis": {"type": "value", "name": x}, "yAxis": {"type": "value", "name": y}, "series": [{"name": series_name, "type": "scatter", "data": data}]}
    else:
        categories = [str(r.get(x) if r.get(x) is not None else "未分类") for r in rows]
        values = [number_value(r.get(y)) or 0 for r in rows]
        series = {"name": series_name, "type": "line" if ctype == "line" else "bar", "data": values}
        option = {"title": {"text": title}, "tooltip": {"trigger": "axis"}, "xAxis": {"type": "category", "name": x, "data": categories}, "yAxis": {"type": "value", "name": y}, "series": [series]}
        if ctype == "line" and not re.search(r"date|time|day|month|year|日期|时间|日|月|年", x, flags=re.I):
            payload["warning"] = "折线图横轴不是明显时间或有序字段。"
    payload.update({"enabled": True, "chart_type": ctype, "option": option, "echarts_markdown": markdown(option), "summary": f"已生成{ctype}图表，共{len(rows)}条数据。"})
    return payload
