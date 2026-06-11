#!/usr/bin/env python3
"""Phase 13 node-level smoke tests for answer/context contracts.

The chatflow DSL is intentionally split into ordered YAML parts. This smoke test
uses Ruby's built-in Psych YAML parser (already used by validate_chatflow_parts.rb)
to extract embedded Python code from selected nodes, then executes those isolated
nodes with representative inputs.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def extract_node_code(node_id: str) -> str:
    script = f"""
require 'yaml'
root = '{ROOT.as_posix()}'
manifest = YAML.load_file(File.join(root, 'NL2MGQL_CHATFLOW_DSL', 'NL2MGQL_CHATFLOW.manifest.yml'))
text = manifest['parts'].map {{ |part| File.read(File.join(root, 'NL2MGQL_CHATFLOW_DSL', part)) }}.join
workflow = YAML.safe_load(text, aliases: true)
node = workflow.dig('workflow', 'graph', 'nodes').find {{ |n| n['id'].to_s == '{node_id}' }}
abort 'missing node' unless node
STDOUT.write(node.dig('data', 'code').to_s)
"""
    return subprocess.check_output(["ruby", "-e", script], text=True)


def load_node(node_id: str) -> ModuleType:
    module = ModuleType(f"node_{node_id}")
    exec(compile(extract_node_code(node_id), f"node_{node_id}.py", "exec"), module.__dict__)
    return module


def assert_contracts(result: dict) -> tuple[dict, dict, dict]:
    payload = json.loads(result["answer_payload_json"])
    update = json.loads(result["context_update_json"])
    compact = json.loads(update["last_context_json"])
    assert payload["contract_version"] == "answer_payload_contract"
    assert update["contract_version"] == "context_update_contract"
    assert compact["context_version"] == "compact_context_contract"
    forbidden = json.dumps(compact, ensure_ascii=False).lower()
    for token in ["schema_metadata_json", "schema_alias_index_json", "pipeline_json", "request_body_json", "rows_json", "merged_rows_json", "echarts_markdown", "chart_debug", "compiler_debug", "validator_debug", "option"]:
        assert token not in forbidden, token
    assert "chart_debug" not in json.dumps(payload.get("chart_payload", {}), ensure_ascii=False)
    return payload, update, compact


def query_kwargs(**overrides):
    base = {
        "question": "过去30天按品牌统计订单数并画图",
        "semantic_plan_json": json.dumps({"primary_collection":"orders","stages":[{"collection":"orders","group_fields":["brand"],"metric_alias":"order_count","time_range":{"mode":"relative_days","relative_days":30}}],"chart_request":{"enabled":True,"chart_type":"bar","x_field":"brand","y_field":"order_count","title":"订单数"}}, ensure_ascii=False),
        "normalized_semantic_plan_json": json.dumps({"primary_collection":"orders","related_collections":[],"stages":[{"collection":"orders","group_fields":["brand"],"metric_alias":"order_count","metric_field":"_id","time_range":{"mode":"relative_days","relative_days":30},"filters":[],"sort":[],"limit":10,"projection_fields":[]}],"chart_request":{"enabled":True,"chart_type":"bar","x_field":"brand","y_field":"order_count","title":"订单数"}}, ensure_ascii=False),
        "rows": json.dumps([{"brand":"A","order_count":10},{"brand":"B","order_count":5}], ensure_ascii=False),
        "chart_echarts_markdown": "",
        "chart_payload_json": json.dumps({"contract_version":"chart_payload_contract","enabled":True,"chart_type":"bar","title":"订单数","echarts_markdown":"```echarts\n{}\n```","summary":"品牌订单数图","data_profile":{"row_count":2,"fields":["brand","order_count"],"preview_rows":[{"brand":"A","order_count":10},{"brand":"B","order_count":5}]},"chart_debug":{"secret":True},"option":{"xAxis":{}}}, ensure_ascii=False),
        "answer_mode": "data",
        "schema_metadata_json": json.dumps({"schema_digest":"abc","schema_version":"v1"}, ensure_ascii=False),
        "collection_catalog_json": json.dumps({"catalog_digest":"cat"}, ensure_ascii=False),
        "collection_selection_json": json.dumps({"selected_primary_collection":"orders","selected_related_collections":[]}, ensure_ascii=False),
        "query_result_json": json.dumps({"success":True,"row_count":2,"fields":["brand","order_count"],"profile":{"preview_rows":[{"brand":"A","order_count":10},{"brand":"B","order_count":5}]}}, ensure_ascii=False),
        "row_count": 2,
        "fields_json": json.dumps(["brand","order_count"], ensure_ascii=False),
        "execution_success": True,
        "execution_error": "",
    }
    base.update(overrides)
    return base


def test_query_with_chart(merge):
    result = merge.main(**query_kwargs())
    payload, _, compact = assert_contracts(result)
    assert payload["answer_type"] == "query_with_chart"
    assert "```echarts" in result["answer_payload_markdown"]
    assert "```echarts" not in json.dumps(compact, ensure_ascii=False)


def test_query_only(merge):
    result = merge.main(**query_kwargs(chart_payload_json=json.dumps({"enabled":False,"warning":"disabled"}), chart_echarts_markdown=""))
    payload, _, _ = assert_contracts(result)
    assert payload["answer_type"] == "query_only"
    assert not payload["chart_echarts_markdown"]
    assert payload["query_result_profile"]["row_count"] == 2


def test_empty_result(merge):
    result = merge.main(**query_kwargs(rows="[]", query_result_json=json.dumps({"success":True,"row_count":0,"fields":["brand"],"profile":{"preview_rows":[]}}), row_count=0, chart_payload_json=json.dumps({"enabled":False})))
    payload, _, _ = assert_contracts(result)
    assert payload["answer_type"] == "empty_result"
    assert "没有查到结果" in payload["answer_text"]


def test_execution_error(merge):
    result = merge.main(**query_kwargs(query_result_json=json.dumps({"success":False,"execution_error":"timeout at executor"}), execution_success=False, execution_error="timeout at executor", row_count=0, chart_payload_json=json.dumps({"enabled":False})))
    payload, _, _ = assert_contracts(result)
    assert payload["answer_type"] == "execution_error"
    assert "查询执行失败" in payload["answer_text"]


def test_chart_only(chart_adapter):
    last_context = {"context_version":"compact_context_contract","has_last_context":True,"last_question":"q","last_turn_intent":{},"last_primary_collection":"orders","last_related_collections":[],"last_plan_summary":{"metric_alias":"order_count"},"last_resolved_fields":["brand"],"last_resolved_metrics":["order_count"],"last_chart_request":{"chart_type":"bar"},"last_result_profile":{"row_count":2,"fields":["brand","order_count"],"preview_rows":[{"brand":"A","order_count":1}]},"schema_context_ref":{"collections":["orders"],"schema_digest":"sha256:abc","schema_version":"v1","catalog_digest":"sha256:cat"},"answer_summary":"old","size_guard":{},"context_warnings":[]}
    result = chart_adapter.main(chart_payload_json=json.dumps({"enabled":True,"echarts_markdown":"```echarts\n{}\n```","summary":"updated","data_profile":{"row_count":2,"fields":["brand","order_count"],"preview_rows":[{"brand":"A","order_count":1}]}}), chart_request_json=json.dumps({"enabled":True,"chart_type":"line","title":"新图"}, ensure_ascii=False), last_context_json=json.dumps(last_context, ensure_ascii=False))
    payload, _, compact = assert_contracts(result)
    assert payload["answer_type"] == "chart_only"
    assert compact["last_plan_summary"] == {"metric_alias":"order_count"}
    assert compact["last_primary_collection"] == "orders"
    assert compact["schema_context_ref"]["schema_digest"] == "sha256:abc"
    assert compact["last_chart_request"]["chart_type"] == "line"


def test_chart_only_insufficient(chart_adapter):
    result = chart_adapter.main(chart_payload_json=json.dumps({"enabled":False,"warning":"preview insufficient"}), chart_request_json=json.dumps({"chart_type":"bar"}), last_context_json="{}")
    payload, _, _ = assert_contracts(result)
    assert payload["answer_type"] == "chart_only"
    assert "不足" in payload["answer_text"]
    assert not payload["chart_echarts_markdown"]


def test_validator_nonvalid(validator):
    schema = {"contract_version":"schema_metadata_contract","collections":{"orders":{"collection_name":"orders","fields":{},"metrics":{},"relations":[],"query_rules":{}}}}
    plan = {"contract_version":"semantic_plan_contract","primary_collection":"orders","stages":[{"collection":"orders","group_fields":["unknown"],"metric_alias":"order_count"}],"chart_request":{"enabled":False}}
    result = validator.semantic_plan_validator(question="按未知字段统计", semantic_plan_json=json.dumps(plan, ensure_ascii=False), schema_metadata_json=json.dumps(schema, ensure_ascii=False), collection_selection_json=json.dumps({"selected_primary_collection":"orders"}, ensure_ascii=False))
    payload = json.loads(result["answer_payload_json"])
    update = json.loads(result["context_update_json"])
    assert payload["answer_type"] in {"clarification", "validation_error"}
    assert update["contract_version"] == "context_update_contract"
    assert not update["compact_context"].get("last_plan_summary")


def main() -> None:
    merge = load_node("1775000000022")
    chart_adapter = load_node("1776000000006")
    validator = load_node("1778000000001")
    for case in [test_query_with_chart, test_query_only, test_empty_result, test_execution_error]:
        case(merge)
    for case in [test_chart_only, test_chart_only_insufficient]:
        case(chart_adapter)
    test_validator_nonvalid(validator)
    print("Phase 13 answer/context contract smoke tests passed")


if __name__ == "__main__":
    main()
