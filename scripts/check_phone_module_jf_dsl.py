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



    # Report LLM flow checks (G principle)
    def node(title: str):
        return titles.get(title) or fail(f"{title} node missing")

    adj = {}
    for edge in edges:
        adj.setdefault(edge.get("source"), []).append(edge)

    def reaches(start_id: str, target_id: str, banned_titles: set[str] | None = None) -> bool:
        banned_titles = banned_titles or set()
        seen = set()
        stack = [start_id]
        while stack:
            cur = stack.pop()
            if cur == target_id:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            for edge in adj.get(cur, []):
                nxt = edge.get("target")
                title = node_by_id.get(nxt, {}).get("data", {}).get("title", "")
                if title in banned_titles or any(bad in title for bad in banned_titles):
                    continue
                stack.append(nxt)
        return False

    def edge_to(src: dict, handle: str, dst: dict) -> bool:
        return any(edge.get("source") == src.get("id") and edge.get("sourceHandle") == handle and edge.get("target") == dst.get("id") for edge in edges)

    ifreport = node("IF_output_type是否为报告类")
    report_input = node("代码执行_准备报告LLM输入")
    final = node("代码执行_合并最终回答")
    local_llm = node("LLM_生成竞分对比报告_本地版")
    switch = node("IF_满血版LLM开关")
    token_http = node("HTTP请求_获取满血版LLM Token")
    full_http = node("HTTP请求_调用满血版LLM接口")
    token_if = node("IF_满血版Token是否成功")
    full_if = node("IF_满血版LLM是否成功")

    if not edge_to(ifreport, "report", report_input):
        fail("IF_output_type是否为报告类 true/report branch must enter 代码执行_准备报告LLM输入")
    if not edge_to(ifreport, "false", final):
        fail("IF_output_type是否为报告类 false branch must connect to final")
    if len([e for e in edges if e.get("source") == ifreport.get("id")]) < 2:
        fail("IF_output_type是否为报告类 must have true/false exits")
    banned = {"LLM_生成竞分对比报告_本地版", "HTTP请求_获取满血版LLM Token", "HTTP请求_调用满血版LLM接口"}
    if not reaches(ifreport.get("id"), final.get("id"), banned):
        fail("non-report branch must reach final without LLM/full HTTP nodes")
    if not reaches(switch.get("id"), local_llm.get("id")):
        fail("IF_满血版LLM开关=false must reach local LLM")
    if not reaches(switch.get("id"), token_http.get("id")):
        fail("IF_满血版LLM开关=true must reach token HTTP")
    if token_http.get("data", {}).get("url") != "{{#env.LLM_TOKEN_URL#}}":
        fail("token HTTP URL must use {{#env.LLM_TOKEN_URL#}}")
    if full_http.get("data", {}).get("url") != "{{#env.FULL_LLM_API_URL#}}":
        fail("full LLM HTTP URL must use {{#env.FULL_LLM_API_URL#}}")
    if not reaches(token_if.get("id"), local_llm.get("id")):
        fail("IF_满血版Token是否成功=false must reach local LLM fallback")
    if not reaches(full_if.get("id"), local_llm.get("id")):
        fail("IF_满血版LLM是否成功=false must reach local LLM fallback")
    if not reaches(full_if.get("id"), final.get("id")):
        fail("full LLM success path must reach final")
    if not reaches(local_llm.get("id"), final.get("id")):
        fail("local fallback path must reach final")
    full_llm_titles = [n for n in nodes if n.get("data", {}).get("title") == "LLM_生成竞分对比报告_满血版"]
    if full_llm_titles:
        fail("满血版 must not be a Dify built-in LLM node")
    if edge_to(switch, "enabled", local_llm):
        fail("full-enabled branch must not directly execute local LLM in parallel")

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
