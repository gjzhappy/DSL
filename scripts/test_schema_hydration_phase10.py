import json

from schema_hydration_phase10 import (
    build_hydration_retrieval_tasks,
    merge_hydrated_schema_context,
    prepare_hydrated_validator_runtime_schema_context,
)
from schema_runtime_context_phase9 import prepare_validator_schema_context
from semantic_plan_validator_phase8 import semantic_plan_validator


SCHEMA_DOC = '''
## Schema Metadata
```json
{"contract_version":"schema_metadata_contract","schema_version":"v1","collections":{"orders":{"collection_name":"orders","collection_label":"Orders","primary_key":"_id","default_time_field":"created_at","fields":{"_id":{"name":"_id","type":"string","filterable":true,"groupable":false,"returnable":false},"created_at":{"name":"created_at","type":"date","semantic_type":"time","filterable":true,"groupable":true,"returnable":true},"brand":{"name":"brand","type":"string","filterable":true,"groupable":true,"returnable":true,"aliases":["品牌"]}},"metrics":{"order_count":{"name":"order_count","function":"count","field":"_id","aliases":["订单数"]}},"relations":[],"query_rules":{"require_time_range_for_aggregate":true}}}}
```
'''


def _plan(collection="orders"):
    return {
        "contract_version": "semantic_plan_contract",
        "primary_collection": collection,
        "related_collections": [],
        "execution_mode": "single_stage",
        "answer_mode": "data",
        "needs_chart": False,
        "stages": [
            {
                "stage_name": "stage1",
                "stage_type": "primary_query",
                "collection": collection,
                "intent_type": "aggregate_summary",
                "time_range": {"field": "created_at", "mode": "relative_days", "relative_days": 60},
                "filters": [],
                "group_fields": ["brand"],
                "metric_function": "count",
                "metric_field": "_id",
                "metric_alias": "order_count",
                "sort": [],
                "limit": 100,
                "projection_fields": [],
            }
        ],
        "final_merge": {"merge_mode": "none"},
        "chart_request": {"enabled": False},
        "requires_clarification": False,
        "clarification_question": "",
        "needs_schema_retrieval": False,
        "warnings": [],
    }


def test_use_patch_hydrates_from_compact_context_and_validator_valid():
    plan = _plan()
    compact = {"schema_context_ref": {"collections": ["orders"], "schema_digest": "old", "schema_version": "v0", "catalog_digest": "cat1"}}
    runtime = prepare_validator_schema_context(
        semantic_plan_json=json.dumps(plan),
        schema_metadata_json="{}",
        schema_alias_index_json="{}",
        compact_context_json=json.dumps(compact),
    )
    assert runtime["schema_hydration_needed"] is True
    tasks = build_hydration_retrieval_tasks(
        schema_runtime_context_json=runtime["schema_runtime_context_json"],
        semantic_plan_json=json.dumps(plan),
        compact_context_json=json.dumps(compact),
    )
    assert tasks["hydration_collections"] == ["orders"]
    merged = merge_hydrated_schema_context(
        hydrated_schema_results=[json.dumps({"collection_name": "orders", "context_text": SCHEMA_DOC})],
        hydration_collections=tasks["hydration_collections"],
        compact_context_json=json.dumps(compact),
    )
    assert merged["hydration_success"] is True
    hydrated = prepare_hydrated_validator_runtime_schema_context(**merged)
    assert hydrated["schema_source"] == "hydrated_runtime"
    result = semantic_plan_validator(
        semantic_plan_json=json.dumps(plan),
        schema_metadata_json=hydrated["schema_metadata_json"],
        schema_alias_index_json=hydrated["schema_alias_index_json"],
        schema_runtime_context_json=hydrated["schema_runtime_context_json"],
        collection_selection_json=json.dumps({"selected_primary_collection": "orders", "confidence": 0.9}),
    )
    assert result["validator_route"] == "valid"


def test_missing_collection_does_not_build_hydration_task():
    tasks = build_hydration_retrieval_tasks(
        schema_runtime_context_json=json.dumps({"schema_hydration_collections": ["unknown"]}),
        semantic_plan_json=json.dumps(_plan("unknown")),
        compact_context_json="{}",
        collection_selection_json="{}",
    )
    assert tasks["hydration_task_valid"] is False
    assert tasks["hydration_collection_tasks"] == []


def test_hydrated_schema_missing_plan_collection_fails_validator():
    plan = _plan("customers")
    merged = merge_hydrated_schema_context(
        hydrated_schema_results=[json.dumps({"collection_name": "orders", "context_text": SCHEMA_DOC})],
        hydration_collections=["orders"],
        compact_context_json="{}",
    )
    hydrated = prepare_hydrated_validator_runtime_schema_context(**merged)
    result = semantic_plan_validator(
        semantic_plan_json=json.dumps(plan),
        schema_metadata_json=hydrated["schema_metadata_json"],
        schema_alias_index_json=hydrated["schema_alias_index_json"],
        schema_runtime_context_json=hydrated["schema_runtime_context_json"],
        collection_selection_json=json.dumps({"selected_primary_collection": "customers", "confidence": 0.9}),
    )
    assert result["validator_route"] in {"needs_replan", "blocked"}
