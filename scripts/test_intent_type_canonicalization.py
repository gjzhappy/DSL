#!/usr/bin/env python3
"""Regression tests for intent_type alias canonicalization across validator/compiler."""

from __future__ import annotations

import json
from semantic_plan_validator_phase8 import semantic_plan_validator


SCHEMA = {
    "contract_version": "schema_metadata_contract",
    "collections": {
        "orders": {
            "collection_name": "orders",
            "default_time_field": "created_at",
            "fields": {
                "brand": {"name": "brand", "groupable": True, "filterable": True, "sortable": True, "projectable": True, "returnable": True},
                "status": {"name": "status", "groupable": True, "filterable": True, "sortable": True, "projectable": True, "returnable": True},
                "order_id": {"name": "order_id", "groupable": False, "filterable": True, "sortable": True, "projectable": True, "returnable": True},
                "created_at": {"name": "created_at", "semantic_type": "time", "role": "time", "filterable": True, "sortable": True, "projectable": True, "returnable": True},
            },
            "metrics": {
                "order_count": {"name": "order_count", "function": "count", "field": "order_id", "allowed_dimensions": ["brand", "status"], "default_sort": "desc"}
            },
            "relations": [],
            "query_rules": {"require_time_range_for_aggregate": True, "max_limit": 100, "default_limit": 100, "max_time_range_days": 366},
        }
    },
}


PLAN = {
    "contract_version": "semantic_plan_contract",
    "primary_collection": "orders",
    "related_collections": [],
    "execution_mode": "single_stage",
    "stages": [
        {
            "collection": "orders",
            "intent_type": "aggregate",
            "time_range": {"field": "created_at", "mode": "relative_days", "relative_days": 30},
            "group_fields": ["brand"],
            "metric_function": "count",
            "metric_field": "order_id",
            "metric_alias": "order_count",
            "sort": [{"field": "order_count", "direction": "desc"}],
            "limit": 100,
            "projection_fields": ["brand", "order_id"],
        }
    ],
    "chart_request": {"enabled": True, "chart_type": "bar", "x_field": "brand", "y_field": "order_count"},
}


def _extract_code_node(path: str, node_id: str) -> str:
    text = open(path, encoding="utf-8").read()
    id_pos = text.index(f"id: '{node_id}'")
    marker_pos = text.rfind("        code:", 0, id_pos)
    start = text.index("\n", marker_pos) + 1
    end = text.index("        code_language:", start)
    lines = text[start:end].splitlines()
    return "\n".join(line[10:] if line.startswith("          ") else line for line in lines).lstrip()


def load_compiler_namespace() -> dict:
    ns: dict = {}
    exec(_extract_code_node("NL2MGQL_CHATFLOW_DSL/NL2MGQL_CHATFLOW_04.yml", "1775000000007"), ns)
    return ns


def test_validator_aggregate_alias_is_canonicalized() -> None:
    result = semantic_plan_validator(
        question="过去一个月所有订单, 按厂商进行分组统计,做一个柱状图进行对比",
        semantic_plan_json=json.dumps(PLAN, ensure_ascii=False),
        schema_metadata_json=json.dumps(SCHEMA, ensure_ascii=False),
        collection_selection_json=json.dumps({"selected_primary_collection": "orders", "confidence": 0.9}, ensure_ascii=False),
    )
    assert result["validator_route"] == "valid", result
    assert result["normalized_plan"]["stages"][0]["intent_type"] == "aggregate_summary", result
    assert any(a.get("type") == "intent_type_canonicalize" and a.get("from") == "aggregate" and a.get("to") == "aggregate_summary" for a in result["autofixes"]), result["autofixes"]


def test_compiler_normalize_and_compile_accepts_aggregate_alias() -> None:
    ns = load_compiler_namespace()
    normalized = ns["_normalize_plan"](PLAN, {"primary_collection": "orders", "related_collections": []}, "过去一个月所有订单, 按厂商进行分组统计")
    stage = normalized["stages"][0]
    assert stage["intent_type"] == "aggregate_summary", normalized
    _validated_ast, pipeline, skip_query = ns["_compile_stage"](stage, "", {})
    assert not skip_query
    assert any("$group" in item for item in pipeline), pipeline
    assert any("$match" in item for item in pipeline), pipeline


def test_prompts_prohibit_generic_aggregate_aliases() -> None:
    planner_prompt = open("NL2MGQL_CHATFLOW_DSL/NL2MGQL_CHATFLOW_02.yml", encoding="utf-8").read()
    replan_builder = open("NL2MGQL_CHATFLOW_DSL/NL2MGQL_CHATFLOW_19.yml", encoding="utf-8").read()
    assert "intent_type 不得使用 aggregate" in planner_prompt
    assert "不得使用 aggregate" in replan_builder
    assert "aggregate_summary" in planner_prompt and "aggregate_summary" in replan_builder


if __name__ == "__main__":
    test_validator_aggregate_alias_is_canonicalized()
    test_compiler_normalize_and_compile_accepts_aggregate_alias()
    test_prompts_prohibit_generic_aggregate_aliases()
    print("PASS intent_type canonicalization regressions")
