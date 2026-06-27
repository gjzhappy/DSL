#!/usr/bin/env python3
"""Static checks for the PHONE_MODULE_JF Dify chatflow DSL."""
from __future__ import annotations

import json
import sys
from pathlib import Path

DSL_PATH = Path("NL2SEARCH_CHATFLOW_DSL/PHONE_MODULE_JF_CHATFLOW.yml")
DATASET_ID = "4d7c7b04-e8d5-47cf-8bd1-dfdfe6022cb7"


def fail(msg: str) -> None:
    raise AssertionError(msg)


def main() -> int:
    doc = json.loads(DSL_PATH.read_text(encoding="utf-8"))
    graph = doc.get("workflow", {}).get("graph", {})
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list):
        fail("workflow.graph.nodes must be an array")
    if not isinstance(edges, list):
        fail("workflow.graph.edges must be an array")

    node_by_id = {node.get("id"): node for node in nodes}
    titles = {node.get("data", {}).get("title"): node for node in nodes}

    for node in nodes:
        data = node.get("data", {})
        if data.get("type") == "code":
            code = data.get("code")
            if not isinstance(code, str) or not code.strip():
                fail(f"Code node {node.get('id')} has empty/missing data.code")
            if data.get("code_language") != "python3":
                fail(f"Code node {node.get('id')} must use code_language=python3")
            if not data.get("title") or data.get("desc") is None or data.get("type") != "code":
                fail(f"Code node {node.get('id')} is missing title/desc/type")
            if not isinstance(data.get("outputs"), dict) or not data["outputs"]:
                fail(f"Code node {node.get('id')} must declare non-empty outputs")

    mongo = node_by_id.get("mongo") or fail("mongo node missing")
    mongo_outputs = mongo.get("data", {}).get("outputs", {})
    if "mongo_request_body_json" not in mongo_outputs:
        fail("mongo node must output mongo_request_body_json for HTTP body")

    http = titles.get("HTTP请求_执行Mongo查询") or fail("HTTP请求_执行Mongo查询 node missing")
    http_data = http.get("data", {})
    if http_data.get("url") != "{{#env.MONGO_QUERY_API_URL#}}":
        fail("HTTP URL must reference {{#env.MONGO_QUERY_API_URL#}}")
    body_data = http_data.get("body", {}).get("data")
    if body_data != "{{#mongo.mongo_request_body_json#}}":
        fail("HTTP body must reference {{#mongo.mongo_request_body_json#}}")

    seen_positions: set[tuple[int, int]] = set()
    for node in nodes:
        pos = node.get("position")
        if not isinstance(pos, dict) or not isinstance(pos.get("x"), (int, float)) or not isinstance(pos.get("y"), (int, float)):
            fail(f"Node {node.get('id')} missing numeric position")
        xy = (int(pos["x"]), int(pos["y"]))
        if xy in seen_positions:
            fail(f"Overlapping node position: {xy}")
        seen_positions.add(xy)


    ifslot = titles.get("IF_是否缺槽") or fail("IF_是否缺槽 node missing")
    if ifslot.get("data", {}).get("type") != "if-else":
        fail("IF_是否缺槽 must be an if-else node")
    cases = ifslot.get("data", {}).get("cases", [])
    case_ids = {case.get("id") for case in cases}
    if not {"need_slot", "success"}.issubset(case_ids):
        fail("IF_是否缺槽 cases must include need_slot and success")
    outgoing = [edge for edge in edges if edge.get("source") == ifslot.get("id")]
    if len(outgoing) < 2:
        fail("IF_是否缺槽 must have at least two outgoing edges")
    outgoing_by_target = {edge.get("target"): edge for edge in outgoing}
    fill = titles.get("代码执行_生成填槽请求") or fail("代码执行_生成填槽请求 node missing")
    plan = titles.get("代码执行_构建QueryPlan") or fail("代码执行_构建QueryPlan node missing")
    ansslot = titles.get("结束_返回填槽请求") or fail("结束_返回填槽请求 node missing")
    fill_edge = outgoing_by_target.get(fill.get("id"))
    plan_edge = outgoing_by_target.get(plan.get("id"))
    if not fill_edge:
        fail("IF_是否缺槽 need_slot branch must connect to 代码执行_生成填槽请求")
    if fill_edge.get("sourceHandle") != "need_slot" or fill_edge.get("targetHandle") != "target":
        fail("IF_是否缺槽 -> 代码执行_生成填槽请求 must use sourceHandle=need_slot and targetHandle=target")
    if not plan_edge:
        fail("IF_是否缺槽 success branch must connect to 代码执行_构建QueryPlan")
    if plan_edge.get("sourceHandle") != "success" or plan_edge.get("targetHandle") != "target":
        fail("IF_是否缺槽 -> 代码执行_构建QueryPlan must use sourceHandle=success and targetHandle=target")
    if not any(edge.get("source") == fill.get("id") and edge.get("target") == ansslot.get("id") for edge in edges):
        fail("代码执行_生成填槽请求 must connect to 结束_返回填槽请求")

    dataset_ids = []
    for node in nodes:
        dataset_ids.extend(node.get("data", {}).get("dataset_ids", []) or [])
    if DATASET_ID not in dataset_ids:
        fail(f"dataset_ids must include {DATASET_ID}")

    print(f"OK: {len(nodes)} nodes, {len(edges)} edges, code/http/layout/IF branch checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
