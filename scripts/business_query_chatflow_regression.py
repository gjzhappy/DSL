#!/usr/bin/env python3
from __future__ import annotations
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "NL2SEARCH_CHATFLOW_DSL"))
from merge_business_chatflow import CONFIGS, load_doc, validate_doc

def must(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)

def nodes_by_id(doc: dict) -> dict:
    return {node["id"]: node["data"] for node in doc["workflow"]["graph"]["nodes"]}

def args_of(code: str) -> list[str]:
    tree = ast.parse(code)
    fn = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"), None)
    must(fn is not None, "code node main() missing")
    return [arg.arg for arg in fn.args.args]

def run_node(data: dict, kwargs: dict) -> dict:
    namespace = {}
    exec(compile(data["code"], "<dify-code-node>", "exec"), namespace)
    return namespace["main"](**kwargs)

def run_registry(cfg: dict, nodes: dict, doc: dict) -> tuple[dict, dict]:
    env = {item["name"]: item["value"] for item in doc["workflow"]["environment_variables"]}
    prefix = cfg["prefix"]
    load_node = nodes[prefix + "registry_load"]
    load_args = args_of(load_node["code"])
    loaded = run_node(load_node, {load_args[0]: env[cfg["registry_env"]]})
    must(loaded[prefix + "registry_load_status"] == "ok", "Registry load failed")
    parse_node = nodes[prefix + "registry_parse"]
    parse_args = args_of(parse_node["code"])
    parsed = run_node(parse_node, {
        parse_args[0]: loaded[prefix + "registry_raw_json"],
        parse_args[1]: loaded[prefix + "registry_load_status"]
    })
    must(parsed[prefix + "registry_parse_status"] == "ok", parsed.get(prefix + "registry_error", "Registry parse failed"))
    return parsed, env

def run_plan(cfg: dict, nodes: dict, parsed: dict, question: str, memory: str = "{}") -> dict:
    prefix = cfg["prefix"]
    node = nodes[prefix + "sql_plan"]
    args = args_of(node["code"])
    kwargs = {name: "" for name in args}
    kwargs["context_json"] = json.dumps({"question": question}, ensure_ascii=False)
    kwargs[prefix + "registry_json"] = parsed[prefix + "registry_json"]
    kwargs[prefix + "alias_index_json"] = parsed[prefix + "alias_index_json"]
    kwargs[prefix + "registry_parse_status"] = "ok"
    kwargs[prefix + "registry_error"] = ""
    kwargs[prefix + "query_memory_json"] = memory
    result = run_node(node, kwargs)
    must(result[prefix + "sql_compile_status"] == "ok", result.get(prefix + "sql_compile_error_answer", "SQL planning failed"))
    return result

def check_workflow(key: str) -> dict:
    cfg = CONFIGS[key]
    prefix = cfg["prefix"]
    doc, _ = load_doc(key)
    validate_doc(key, doc)
    nodes = nodes_by_id(doc)
    parsed, env = run_registry(cfg, nodes, doc)

    if key == "SI_DESIGN_CONSTRAINT":
        question = "查询华为长沙平台各芯片、模块的插损和回损"
        followup = "这些模块的串扰呢？"
        expected_params = ["Huawei", "Changsha"]
        expected_fields = {"manufacturer", "platform", "chip", "module", "level", "insertion_loss", "return_loss"}
    else:
        question = "查询苹果 A19 平台各电源域、Pin/Net 的远端总容值和远端电容面积"
        followup = "这些网络的远端电容数量呢？"
        expected_params = ["Apple", "A19"]
        expected_fields = {"manufacturer", "platform", "power_domain", "pin_net_name", "far_total_capacitance", "far_cap_area_mm2"}

    first = run_plan(cfg, nodes, parsed, question)
    plan = json.loads(first[prefix + "sql_plan_json"])
    must(plan["table"] == cfg["table"], key + ": SQL table mismatch")
    must(all(value in plan.get("params", []) for value in expected_params), key + ": confirmed identity filters not compiled")
    must(expected_fields <= set(plan.get("select_fields") or []), key + ": expected business fields not selected")
    must(str(plan.get("sql") or "").lstrip().lower().startswith("select "), key + ": generated SQL is not SELECT")
    must(first[prefix + "memory_write_ready"] is True, key + ": query scope not marked for memory write")

    memory = first[prefix + "current_execution_context_json"]
    second = run_plan(cfg, nodes, parsed, followup, memory)
    second_plan = json.loads(second[prefix + "sql_plan_json"])
    must(second_plan["query_intent_meta"]["query_scope_behavior"] == "inherit", key + ": follow-up did not inherit query scope")
    must(second_plan["where_filters"] == plan["where_filters"], key + ": follow-up changed inherited identity filters")

    # Exercise the real MySQL response normalizer and LLM input builder with a synthetic row.
    row = {field: "TEST_VALUE" for field in plan["select_fields"]}
    normalize_node = nodes["mysql_normalize"]
    normalize_args = args_of(normalize_node["code"])
    normalize_kwargs = {name: "" for name in normalize_args}
    normalize_kwargs["body"] = json.dumps({"code": 0, "data": {"rows": [row], "rowCount": 1}}, ensure_ascii=False)
    normalize_kwargs["status_code"] = 200
    normalize_kwargs[prefix + "sql_plan_json"] = first[prefix + "sql_plan_json"]
    normalized = run_node(normalize_node, normalize_kwargs)
    query_result = json.loads(normalized["mysql_query_result_json"])
    must(query_result["status"] == "ok" and query_result["row_count"] == 1, key + ": MySQL response normalization failed")

    llm_node = nodes["llm_input"]
    llm_args = args_of(llm_node["code"])
    llm_kwargs = {name: "" for name in llm_args}
    llm_kwargs.update({
        "context_json": json.dumps({"question": question}, ensure_ascii=False),
        "mysql_query_result_json": normalized["mysql_query_result_json"],
        prefix + "selected_field_meta_json": first[prefix + "selected_field_meta_json"],
        prefix + "alias_index_json": parsed[prefix + "alias_index_json"],
        "analysis_system_prompt": env[cfg["system_env"]],
        "analysis_user_prompt": env[cfg["user_env"]],
        prefix + "current_execution_context_json": memory,
        prefix + "previous_query_result_json": "{}",
        "local_context_tokens": env["LOCAL_CONTEXT_TOKENS"],
        "local_max_predict_tokens": env["LOCAL_MAX_PREDICT_TOKENS"],
        "prompt_token_safety_margin": env["PROMPT_TOKEN_SAFETY_MARGIN"]
    })
    llm_result = run_node(llm_node, llm_kwargs)
    payload = json.loads(llm_result["llm_handler_input_json"])
    must(payload["status"] == "ok", key + ": LLM input budget/config guard failed")
    must(question in llm_result["llm_user_prompt"], key + ": LLM prompt omitted user question")

    # The final node must append the real SQL rows, not ask the LLM to recreate them.
    query_result["sql_plan"]["selected_field_meta"] = json.loads(first[prefix + "selected_field_meta_json"])
    final_node = nodes["final"]
    final_result = run_node(final_node, {
        "llm_handler_result_json": json.dumps({"handler_status": "ok", "answer": "## 分析结论\n测试结论\n\n## 分析依据\n测试依据"}, ensure_ascii=False),
        "mysql_query_result_json": json.dumps(query_result, ensure_ascii=False)
    })
    must("原始查询数据" in final_result["final_answer"], key + ": original SQL rows not appended")

    return {
        "workflow": key,
        "table": cfg["table"],
        "sql_params": plan["params"],
        "selected_fields": plan["select_fields"],
        "followup_scope": second_plan["query_intent_meta"]["query_scope_behavior"],
        "mysql_normalizer": "PASS",
        "llm_input": "PASS",
        "raw_rows_append": "PASS"
    }

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Regression tests for the SI and JF capacitor Dify Chatflows.")
    parser.add_argument("workflow", choices=sorted(CONFIGS) + ["ALL"])
    args = parser.parse_args()
    keys = sorted(CONFIGS) if args.workflow == "ALL" else [args.workflow]
    results = [check_workflow(key) for key in keys]
    print(json.dumps({"result": "PASS", "workflows": results}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FAIL: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
