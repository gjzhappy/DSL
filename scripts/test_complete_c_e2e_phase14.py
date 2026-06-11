#!/usr/bin/env python3
"""Phase 14 complete-C mock E2E contract fixtures.

These tests do not call Dify or Mongo.  They stitch together the runtime helper
contracts used by the DSL main path with mock planner/compiler outputs so the
release path can regress schema/runtime validation, chart payloads, answer
payloads, and compact-context boundaries end to end.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.chart_payload_builder_phase12 import build_chart_payload  # noqa: E402
from scripts.schema_hydration_phase10 import build_hydration_retrieval_tasks  # noqa: E402
from scripts.schema_metadata_c_mvp_lib import build_alias_index, compute_schema_digest, select_collections  # noqa: E402
from scripts.schema_runtime_context_phase9 import prepare_validator_schema_context  # noqa: E402
from scripts.semantic_plan_validator_phase8 import semantic_plan_validator  # noqa: E402
from scripts.test_phase13_answer_context_contracts import load_node  # noqa: E402

BANNED_CONTEXT_TOKENS = [
    "schema_metadata_json",
    "schema_alias_index_json",
    "schema_runtime_context_json",
    "pipeline_json",
    "request_body_json",
    "rows_json",
    "merged_rows_json",
    "echarts_markdown",
    "chart option",
    "chart_option",
    "echarts_option",
    "compiler_debug",
    "validator_debug",
    "chart_debug",
    "stack trace",
]

SCHEMA: Dict[str, Any] = {
    "contract_version": "schema_metadata_contract",
    "schema_version": "phase14-fixture-v1",
    "catalog_digest": "catalog-fixture",
    "collections": {
        "orders": {
            "collection_name": "orders",
            "collection_label": "订单",
            "default_time_field": "created_at",
            "fields": {
                "brand": {"name": "brand", "label": "厂商", "aliases": ["品牌", "厂商"], "groupable": True, "filterable": True, "sortable": True, "projectable": True, "returnable": True},
                "status": {"name": "status", "label": "订单状态", "aliases": ["状态"], "allowed_values": ["paid", "unpaid", "cancelled"], "value_aliases": {"已支付": "paid"}, "groupable": True, "filterable": True, "sortable": True, "projectable": True, "returnable": True},
                "created_at": {"name": "created_at", "label": "创建时间", "semantic_type": "time", "role": "time", "groupable": False, "filterable": True, "sortable": True, "projectable": True, "returnable": True},
                "amount": {"name": "amount", "label": "金额", "groupable": False, "filterable": True, "sortable": True, "projectable": True, "returnable": True},
                "user_phone": {"name": "user_phone", "label": "用户手机号", "aliases": ["手机号"], "pii": True, "sensitive": True, "groupable": False, "filterable": False, "sortable": False, "projectable": False, "returnable": False},
            },
            "metrics": {
                "order_count": {"name": "order_count", "label": "订单数", "aliases": ["订单量"], "function": "count", "field": "_id", "source_fields": [], "output_type": "number", "allowed_dimensions": ["brand", "status"], "default_sort": "desc"},
            },
            "relations": [],
            "query_rules": {"require_time_range_for_aggregate": False, "max_limit": 100, "default_limit": 100, "max_time_range_days": 366, "sensitive_field_policy": "deny_return_group_sort"},
        }
    },
}
SCHEMA["schema_digest"] = compute_schema_digest(SCHEMA)
ALIAS_INDEX = build_alias_index(SCHEMA)
SELECTION_JSON = json.dumps({"selected_primary_collection": "orders", "selected_related_collections": [], "confidence": 0.96}, ensure_ascii=False)
CATALOG_JSON = json.dumps({"catalog_digest": "catalog-fixture", "collections": [{"name": "orders", "label": "订单"}]}, ensure_ascii=False)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def base_plan(group_field: str = "brand", days: int = 30, chart_type: str = "bar") -> Dict[str, Any]:
    return {
        "contract_version": "semantic_plan_contract",
        "primary_collection": "orders",
        "related_collections": [],
        "stages": [
            {
                "collection": "orders",
                "intent_type": "aggregate_summary",
                "time_range": {"mode": "relative_days", "relative_days": days},
                "filters": [],
                "group_fields": [group_field],
                "metric_alias": "order_count",
                "metric_function": "count",
                "metric_field": "_id",
                "sort": [{"field": "order_count", "direction": "desc"}],
                "limit": 100,
                "projection_fields": [],
            }
        ],
        "chart_request": {"enabled": True, "chart_type": chart_type, "x_field": group_field, "y_field": "order_count", "series_name": "订单数", "title": "订单数对比"},
    }


def runtime_context(plan: Dict[str, Any], schema: Dict[str, Any] | None = SCHEMA, alias: Dict[str, Any] | None = ALIAS_INDEX, compact: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return prepare_validator_schema_context(
        semantic_plan_json=dumps(plan),
        schema_metadata_json=dumps(schema or {}),
        schema_alias_index_json=dumps(alias or {}),
        collection_selection_json=SELECTION_JSON,
        compact_context_json=dumps(compact or {}),
    )


def validate_plan(question: str, plan: Dict[str, Any], schema: Dict[str, Any] | None = SCHEMA, alias: Dict[str, Any] | None = ALIAS_INDEX, compact: Dict[str, Any] | None = None, runtime: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ctx = runtime or runtime_context(plan, schema=schema, alias=alias, compact=compact)
    return semantic_plan_validator(
        question=question,
        semantic_plan_json=dumps(plan),
        schema_metadata_json=dumps(schema or {}),
        schema_alias_index_json=dumps(alias or {}),
        collection_selection_json=SELECTION_JSON,
        compact_context_json=dumps(compact or {}),
        normalizer_valid=True,
        normalizer_route="ok",
        schema_runtime_context_json=ctx["schema_runtime_context_json"],
    )


def mock_compile(validated: Dict[str, Any], rows: List[Dict[str, Any]] | None = None, execution_success: bool = True, execution_error: str = "") -> Dict[str, Any]:
    plan = validated.get("normalized_plan") or {}
    if rows:
        fields = list(rows[0].keys())
    else:
        plan_stage = ((plan.get("stages") or [{}])[0] if isinstance(plan.get("stages"), list) else {})
        fields = list(plan_stage.get("group_fields") or []) + ([plan_stage.get("metric_alias")] if plan_stage.get("metric_alias") else [])
    query_result = {"contract_version": "query_result_contract", "success": execution_success, "rows": rows or [], "row_count": len(rows or []), "fields": fields, "profile": {"preview_rows": (rows or [])[:5], "numeric_fields": ["order_count"] if rows else [], "dimension_fields": [f for f in fields if f != "order_count"]}, "error": execution_error, "warnings": []}
    return {
        "execution_success": execution_success,
        "execution_error": execution_error,
        "row_count": len(rows or []),
        "fields_json": dumps(fields),
        "rows_json": dumps(rows or []),
        "query_result_json": dumps(query_result),
        "chart_request_json": dumps(plan.get("chart_request") or {}),
    }


def merge_answer(merge: ModuleType, question: str, plan: Dict[str, Any], compile_out: Dict[str, Any], chart_payload: Dict[str, Any], last_context: Dict[str, Any] | None = None) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    result = merge.main(
        question=question,
        semantic_plan_json=dumps(plan),
        normalized_semantic_plan_json=dumps(plan),
        rows=compile_out["rows_json"],
        chart_echarts_markdown=chart_payload.get("echarts_markdown", ""),
        chart_payload_json=dumps(chart_payload),
        answer_mode="data",
        schema_metadata_json=dumps(SCHEMA),
        collection_catalog_json=CATALOG_JSON,
        collection_selection_json=SELECTION_JSON,
        query_result_json=compile_out["query_result_json"],
        row_count=compile_out["row_count"],
        fields_json=compile_out["fields_json"],
        execution_success=compile_out["execution_success"],
        execution_error=compile_out["execution_error"],
        last_context_json=dumps(last_context or {}),
    )
    payload = json.loads(result["answer_payload_json"])
    update = json.loads(result["context_update_json"])
    compact = json.loads(update["last_context_json"])
    assert compact == json.loads(result["compact_context_json"])
    assert payload["contract_version"] == "answer_payload_contract"
    assert update["contract_version"] == "context_update_contract"
    assert compact["context_version"] == "compact_context_contract"
    assert_compact_boundary(compact)
    assert "debug" not in dumps(payload).lower()
    return result, payload, update, compact


def assert_compact_boundary(compact: Dict[str, Any]) -> None:
    body = dumps(compact).lower()
    for token in BANNED_CONTEXT_TOKENS:
        assert token.lower() not in body, token
    assert "schema_context_ref" in compact or not compact.get("last_plan_summary")
    if "last_result_profile" in compact:
        assert "preview_rows" in compact["last_result_profile"]


def assert_group(compact: Dict[str, Any], expected: str) -> None:
    plan = compact.get("last_plan_summary") or {}
    if "group_fields" in plan:
        assert plan.get("group_fields") == [expected], plan
        return
    stages = plan.get("stages") or []
    assert stages and stages[0].get("group_fields") == [expected], stages


def run_new_query(merge: ModuleType) -> Dict[str, Any]:
    question = "过去一个月所有订单, 按厂商进行分组统计,做一个柱状图进行对比"
    plan = base_plan("brand", 30, "bar")
    validated = validate_plan(question, plan)
    assert validated["validator_route"] == "valid"
    rows = [{"brand": "A厂商", "order_count": 12}, {"brand": "B厂商", "order_count": 7}]
    compiled = mock_compile(validated, rows)
    chart_payload = build_chart_payload(plan.get("chart_request"), json.loads(compiled["query_result_json"]))
    assert chart_payload["enabled"] is True
    _, payload, _, compact = merge_answer(merge, question, validated["normalized_plan"], compiled, chart_payload)
    assert payload["answer_type"] == "query_with_chart"
    assert compact["schema_context_ref"]["collections"] == ["orders"]
    assert_group(compact, "brand")
    return compact


def run_refine_full_replan(merge: ModuleType, previous: Dict[str, Any]) -> Dict[str, Any]:
    question = "不对，我想按订单状态统计过去两个月的订单数，不再按品牌统计, 生成新的柱状图"
    patch_route = "full_replan"
    plan = base_plan("status", 60, "bar")
    validated = validate_plan(question, plan, compact=previous)
    assert patch_route == "full_replan"
    assert validated["validator_route"] == "valid"
    rows = [{"status": "paid", "order_count": 20}, {"status": "unpaid", "order_count": 3}]
    compiled = mock_compile(validated, rows)
    chart_payload = build_chart_payload(plan.get("chart_request"), json.loads(compiled["query_result_json"]))
    _, payload, _, compact = merge_answer(merge, question, validated["normalized_plan"], compiled, chart_payload)
    assert payload["answer_type"] == "query_with_chart"
    assert_group(compact, "status")
    assert "brand" not in dumps(compact.get("last_plan_summary", {}))
    return compact


def run_use_patch_time_only(merge: ModuleType, previous: Dict[str, Any]) -> Dict[str, Any]:
    question = "改成过去两个月"
    patch_route = "use_patch"
    plan = base_plan("brand", 60, "bar")
    stale_ctx = runtime_context(plan, schema={}, alias={}, compact=previous)
    assert stale_ctx["schema_hydration_needed"] is True
    hydration = build_hydration_retrieval_tasks(stale_ctx["schema_runtime_context_json"], dumps(plan), dumps(previous), SELECTION_JSON)
    assert hydration["hydration_task_valid"] is True
    hydrated_ctx = runtime_context(plan, schema=SCHEMA, alias=ALIAS_INDEX, compact=previous)
    validated = validate_plan(question, plan, schema={}, alias={}, compact=previous, runtime=hydrated_ctx)
    assert patch_route == "use_patch"
    assert validated["validator_route"] == "valid"
    normalized = validated["normalized_plan"]
    assert normalized["stages"][0]["time_range"]["relative_days"] == 60
    assert normalized["stages"][0]["group_fields"] == ["brand"]
    rows = [{"brand": "A厂商", "order_count": 24}, {"brand": "B厂商", "order_count": 11}]
    compiled = mock_compile(validated, rows)
    chart_payload = build_chart_payload(normalized.get("chart_request"), json.loads(compiled["query_result_json"]))
    _, payload, _, compact = merge_answer(merge, question, normalized, compiled, chart_payload)
    assert payload["answer_type"] == "query_with_chart"
    assert "schema_metadata_json" not in dumps(compact)
    assert_group(compact, "brand")
    return compact


def run_chart_only(chart_adapter: ModuleType, previous: Dict[str, Any]) -> Dict[str, Any]:
    question = "换成饼图"
    patch_route = "chart_only"
    prior_chart = copy.deepcopy(previous["last_chart_request"])
    prior_chart["chart_type"] = "pie"
    payload = build_chart_payload(prior_chart, {"contract_version": "query_result_contract", "success": True, "rows": previous["last_result_profile"]["preview_rows"], "row_count": previous["last_result_profile"]["row_count"], "fields": previous["last_result_profile"].get("fields", []), "profile": previous["last_result_profile"]}, chart_only_mode=True)
    result = chart_adapter.main(chart_payload_json=dumps(payload), chart_request_json=dumps(prior_chart), last_context_json=dumps(previous))
    answer_payload = json.loads(result["answer_payload_json"])
    compact = json.loads(result["compact_context_json"])
    assert patch_route == "chart_only"
    assert answer_payload["answer_type"] == "chart_only"
    assert compact.get("last_plan_summary") == previous.get("last_plan_summary")
    assert compact.get("last_chart_request", {}).get("chart_type") == "pie"
    assert_compact_boundary(compact)
    return compact


def run_unknown_field() -> Dict[str, Any]:
    previous = {"context_version": "compact_context_contract", "has_last_context": True, "last_plan_summary": {"group_fields": ["brand"]}, "last_result_profile": {"row_count": 1, "fields": ["brand", "order_count"], "preview_rows": [{"brand": "A厂商", "order_count": 1}]}, "schema_context_ref": {"collections": ["orders"]}}
    plan = base_plan("客户星级", 30, "bar")
    result = validate_plan("按客户星级统计订单数", plan, compact=previous)
    assert result["validator_route"] == "requires_clarification"
    payload = json.loads(result["answer_payload_json"])
    compact = json.loads(result["compact_context_json"])
    assert payload["answer_type"] == "clarification"
    assert compact.get("last_plan_summary") == previous.get("last_plan_summary")
    assert_compact_boundary(compact)
    return compact


def run_pii_blocked() -> Dict[str, Any]:
    plan = base_plan("user_phone", 30, "bar")
    result = validate_plan("按用户手机号统计订单数", plan)
    assert result["validator_route"] == "blocked"
    payload = json.loads(result["answer_payload_json"])
    compact = json.loads(result["compact_context_json"])
    assert payload["answer_type"] == "validation_error"
    assert "validator_debug" not in dumps(payload).lower()
    assert_compact_boundary(compact)
    return compact


def run_empty_result(merge: ModuleType) -> Dict[str, Any]:
    plan = base_plan("brand", 30, "bar")
    validated = validate_plan("过去一个月按厂商统计订单数", plan)
    compiled = mock_compile(validated, [])
    chart_payload = build_chart_payload(plan.get("chart_request"), json.loads(compiled["query_result_json"]))
    assert chart_payload["enabled"] is False
    _, payload, _, compact = merge_answer(merge, "过去一个月按厂商统计订单数", validated["normalized_plan"], compiled, chart_payload)
    assert payload["answer_type"] == "empty_result"
    assert compact["last_result_profile"]["row_count"] == 0
    return compact


def run_execution_error(merge: ModuleType) -> Dict[str, Any]:
    previous = {"context_version": "compact_context_contract", "has_last_context": True, "last_plan_summary": {"group_fields": ["brand"]}, "last_result_profile": {"row_count": 1, "fields": ["brand", "order_count"], "preview_rows": [{"brand": "A厂商", "order_count": 1}]}, "schema_context_ref": {"collections": ["orders"]}}
    plan = base_plan("status", 30, "bar")
    validated = validate_plan("按状态统计订单数", plan, compact=previous)
    compiled = mock_compile(validated, [], execution_success=False, execution_error="executor timeout: internal stack trace / request body omitted")
    chart_payload = build_chart_payload(plan.get("chart_request"), json.loads(compiled["query_result_json"]))
    _, payload, _, compact = merge_answer(merge, "按状态统计订单数", validated["normalized_plan"], compiled, chart_payload, previous)
    assert payload["answer_type"] == "execution_error"
    assert "request body" not in dumps(payload).lower()
    assert "stack trace" not in dumps(payload).lower()
    assert compact.get("last_plan_summary") == previous.get("last_plan_summary")
    return compact


def run_hydration_failure() -> Dict[str, Any]:
    plan = base_plan("brand", 60, "bar")
    plan.pop("primary_collection", None)
    plan["stages"][0].pop("collection", None)
    ctx = prepare_validator_schema_context(semantic_plan_json=dumps(plan), schema_metadata_json="{}", schema_alias_index_json="{}", collection_selection_json="{}", compact_context_json="{}")
    hydration = build_hydration_retrieval_tasks(ctx["schema_runtime_context_json"], dumps(plan), "{}", "{}")
    assert hydration["hydration_task_valid"] is False
    result = validate_plan("改成过去两个月", plan, schema={}, alias={}, compact={}, runtime=ctx)
    assert result["validator_route"] != "valid"
    assert result["answer_payload_json"]
    compact = json.loads(result["compact_context_json"])
    assert_compact_boundary(compact)
    return compact



def run_generic_collection_contract_regressions() -> None:
    schema = copy.deepcopy(SCHEMA)
    schema["collections"]["orders"]["relations"] = [{"target_collection": "products", "join_keys": [{"source_field": "brand", "target_field": "brand"}]}]
    schema["collections"]["products"] = {
        "collection_name": "products",
        "collection_label": "商品",
        "collection_aliases": ["产品"],
        "fields": {"brand": {"name": "brand", "label": "厂商", "aliases": ["厂商"], "groupable": True, "filterable": True, "sortable": True, "projectable": True, "returnable": True}},
        "metrics": {"product_count": {"name": "product_count", "label": "商品数", "function": "count", "field": "brand", "allowed_dimensions": ["brand"]}},
        "relations": [],
        "query_rules": {},
    }
    selection = select_collections("过去一个月所有订单按厂商统计", schema_metadata=schema)
    assert selection["selected_primary_collection"] == "orders"
    assert selection["selected_related_collections"] == []
    assert any(c.get("collection") == "products" and "primary collection already covers" in c.get("reason", "") for c in selection.get("related_candidates", []))

    plan = base_plan()
    plan["related_collections"] = ["products"]
    result = validate_plan("过去一个月所有订单按厂商统计", plan, schema=schema, alias=build_alias_index(schema), compact={})
    assert result["validator_route"] == "valid"
    assert result["normalized_plan"]["related_collections"] == []
    assert any(a.get("type") == "unused_related_collection_prune" for a in result.get("autofixes", []))

    used = base_plan()
    used["related_collections"] = ["products"]
    used["stages"].append({"collection": "products", "intent_type": "aggregate_summary", "time_range": {}, "filters": [], "group_fields": ["brand"], "metric_alias": "product_count", "sort": [], "limit": 100, "projection_fields": []})
    result = validate_plan("订单关联商品按厂商统计", used, schema=schema, alias=build_alias_index(schema), compact={})
    assert result["validator_route"] == "valid"
    assert result["normalized_plan"]["related_collections"] == ["products"]

    runtime = prepare_validator_schema_context(
        semantic_plan_json=dumps({"primary_collection": "primary_a", "related_collections": ["related_b"], "stages": [{"collection": "primary_a"}]}),
        schema_metadata_json=dumps({"collections": {"primary_a": {}}, "schema_digest": "d1", "schema_version": "v1"}),
        schema_alias_index_json=dumps({"field_aliases": {"primary_a": {}}}),
        compact_context_json=dumps({"schema_context_ref": {"collections": ["primary_a", "related_b"]}}),
        collection_selection_json=dumps({"selected_primary_collection": "primary_a", "selected_related_collections": ["related_b"]}),
    )
    assert runtime["schema_context_ref"]["collections"] == ["primary_a"]
    assert any(isinstance(w, dict) and w.get("type") == "schema_context_ref_pruned_to_parsed_collections" and w.get("removed") == ["related_b"] for w in runtime.get("warnings", []))

def main() -> None:
    merge = load_node("1775000000022")
    chart_adapter = load_node("1776000000006")
    contexts = []
    first = run_new_query(merge); contexts.append(first)
    contexts.append(run_refine_full_replan(merge, first))
    patched = run_use_patch_time_only(merge, first); contexts.append(patched)
    contexts.append(run_chart_only(chart_adapter, patched))
    contexts.append(run_unknown_field())
    contexts.append(run_pii_blocked())
    contexts.append(run_empty_result(merge))
    contexts.append(run_execution_error(merge))
    contexts.append(run_hydration_failure())
    run_generic_collection_contract_regressions()
    for ctx in contexts:
        assert_compact_boundary(ctx)
    print("PASS Phase 14 complete-C mock E2E fixtures: 11 scenarios including global compact boundary checks")


if __name__ == "__main__":
    main()
