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

    dataset_ids = []
    for node in nodes:
        dataset_ids.extend(node.get("data", {}).get("dataset_ids", []) or [])
    if DATASET_ID not in dataset_ids:
        fail(f"dataset_ids must include {DATASET_ID}")

    print(f"OK: {len(nodes)} nodes, {len(edges)} edges, code/http/layout checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
