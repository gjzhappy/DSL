#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from chart_payload_builder_phase12 import build_chart_payload, normalize_query_result


def qr(rows):
    return {"contract_version": "query_result_contract", "success": True, "rows": rows, "row_count": len(rows), "fields": list(rows[0].keys()) if rows else [], "profile": {}, "error": "", "warnings": []}


def req(chart_type="bar", x="status", y="order_count", enabled=True):
    return {"enabled": enabled, "chart_type": chart_type, "x_field": x, "y_field": y, "series_name": "订单数", "title": "测试图表", "field_labels": {}}


def main():
    out = build_chart_payload(req(), qr([{"status": "paid", "order_count": 10}]))
    assert out["enabled"] and out["renderer"] == "echarts" and out["chart_type"] == "bar" and out["echarts_markdown"]
    out = build_chart_payload(req(), qr([]))
    assert not out["enabled"] and "空" in out["warning"]
    out = build_chart_payload(req(x="brand"), qr([{"status": "paid", "order_count": 10}]))
    assert not out["enabled"] and "字段不在查询结果" in out["warning"]
    out = build_chart_payload(req(), qr([{"status": "paid", "order_count": "abc"}]))
    assert not out["enabled"] and "不是数字" in out["warning"]
    out = build_chart_payload(req("pie"), qr([{"status": "paid", "order_count": 10}]))
    assert out["enabled"] and out["option"]["series"][0]["type"] == "pie"
    out = build_chart_payload(req("radar"), qr([{"status": "paid", "order_count": 10}]))
    assert not out["enabled"] and "暂不支持" in out["warning"]
    out = build_chart_payload(req("line"), qr([{"status": "paid", "order_count": 10}]), chart_only_mode=True)
    assert out["enabled"] and "Mongo" not in str(out)
    out = build_chart_payload(req("bar"), qr([]), chart_only_mode=True)
    assert not out["enabled"] and "摘要不足" in out["warning"]
    legacy = normalize_query_result("", "", 0, "", '[{"status":"paid","order_count":10}]')
    assert "legacy_rows_fallback_used" in legacy["warnings"]
    print("Phase 12 chart payload builder smoke tests passed")


if __name__ == "__main__":
    main()
