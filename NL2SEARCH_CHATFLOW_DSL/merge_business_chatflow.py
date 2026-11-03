#!/usr/bin/env python3
from __future__ import annotations
import argparse
import ast
import json
import re
from collections import defaultdict, deque
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

CONFIGS = {
    "SI_DESIGN_CONSTRAINT": {
        "stem": "SI_DESIGN_CONSTRAINT_CHATFLOW",
        "app_name": "SI_DESIGN_CONSTRAINT_CHATFLOW",
        "prefix": "si_design_constraint_",
        "registry_env": "SI_DESIGN_CONSTRAINT_QUERY_REGISTRY_JSON",
        "system_env": "SI_DESIGN_CONSTRAINT_ANALYSIS_SYSTEM_PROMPT",
        "user_env": "SI_DESIGN_CONSTRAINT_ANALYSIS_USER_PROMPT",
        "table": "sn_si_design_constraints_wide",
        "fields": ["manufacturer", "platform", "chip", "module", "level", "insertion_loss", "return_loss", "sdc", "crosstalk", "isolation", "parasitic_constraint", "remark"],
        "filter_fields": ["manufacturer", "platform", "chip", "module", "level"],
        "prompt_terms": ["SI设计约束", "插入损耗", "回波损耗", "约束级别"],
        "forbidden_terms": ["PHONE_MODULE_JF_CHATFLOW", "摄像模组", "HDR模式", "这些手机", "这些机型"]
    },
    "JF_CAPACITOR": {
        "stem": "JF_CAPACITOR_CHATFLOW",
        "app_name": "JF_CAPACITOR_CHATFLOW",
        "prefix": "jf_capacitor_",
        "registry_env": "JF_CAPACITOR_QUERY_REGISTRY_JSON",
        "system_env": "JF_CAPACITOR_ANALYSIS_SYSTEM_PROMPT",
        "user_env": "JF_CAPACITOR_ANALYSIS_USER_PROMPT",
        "table": "sn_pisi_jf_capacitor_comparisons_wide",
        "fields": ["manufacturer", "platform", "power_domain", "pin_net_name", "inductor", "near_capacitor", "far_capacitor", "far_total_capacitance", "near_cap_count", "far_cap_count", "far_0402_regular", "far_0402_three_terminal", "far_0201_regular", "far_cap_area_mm2", "total_cap_area_mm2", "remark"],
        "filter_fields": ["manufacturer", "platform", "power_domain", "pin_net_name", "inductor"],
        "prompt_terms": ["电源域", "Pin/Net", "近端电容", "远端电容", "far_total_capacitance"],
        "forbidden_terms": ["PHONE_MODULE_JF_CHATFLOW", "摄像模组", "HDR模式", "这些手机", "这些机型"]
    }
}

ENV_TEMPLATE_RE = re.compile(r"\{\{#env\.([A-Za-z_][A-Za-z0-9_]*)#\}\}")
NODE_TEMPLATE_RE = re.compile(r"\{\{#([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)#\}\}")

def fail(message: str) -> None:
    raise SystemExit("FAIL: " + message)

def discover_parts(cfg: dict) -> list[Path]:
    pattern = re.compile(re.escape(cfg["stem"]) + r"_(\d+)\.yml$")
    found = []
    for path in BASE_DIR.glob(cfg["stem"] + "_*.yml"):
        match = pattern.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    found.sort(key=lambda item: item[0])
    numbers = [number for number, _ in found]
    if not found:
        fail("no shards found for " + cfg["stem"])
    if numbers != list(range(len(numbers))):
        fail("shard numbering must be continuous from 00: " + repr(numbers))
    return [path for _, path in found]

def load_doc(key: str) -> tuple[dict, str]:
    if key not in CONFIGS:
        fail("unknown workflow key: " + key)
    text = "".join(path.read_text(encoding="utf-8") for path in discover_parts(CONFIGS[key]))
    if not text.strip():
        fail(key + ": merged DSL is empty")
    try:
        doc = json.loads(text)
    except Exception as exc:
        fail(key + ": merged DSL is not valid JSON-compatible YAML: " + str(exc))
    if not isinstance(doc, dict):
        fail(key + ": DSL root must be an object")
    return doc, text

def main_args(code: str) -> list[str]:
    tree = ast.parse(code)
    fn = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"), None)
    if fn is None:
        raise ValueError("code node has no main()")
    return [arg.arg for arg in fn.args.args]

def validate_doc(key: str, doc: dict) -> dict:
    cfg = CONFIGS[key]
    if doc.get("version") != "0.6.0" or doc.get("kind") != "app":
        fail(key + ": Dify DSL version/kind mismatch")
    app = doc.get("app") or {}
    if app.get("mode") != "advanced-chat" or app.get("name") != cfg["app_name"]:
        fail(key + ": app metadata mismatch")

    workflow = doc.get("workflow") or {}
    envs = workflow.get("environment_variables")
    if not isinstance(envs, list):
        fail(key + ": environment_variables must be a list")
    env_defs = {}
    env_ids = set()
    for item in envs:
        name = item.get("name") if isinstance(item, dict) else None
        env_id = item.get("id") if isinstance(item, dict) else None
        if not name or name in env_defs or not env_id or env_id in env_ids:
            fail(key + ": duplicate or missing environment variable identity: " + repr(name))
        if item.get("selector") != ["env", name] or item.get("value_type") != "string":
            fail(key + ": environment selector/type mismatch: " + name)
        env_defs[name] = item
        env_ids.add(env_id)

    required_envs = [
        cfg["registry_env"], cfg["system_env"], cfg["user_env"],
        "ENABLE_FULL_LLM", "SN_FULL_LLM_CHAT_URLS", "SN_MYSQL_QUERY_URLS",
        "LOCAL_CONTEXT_TOKENS", "LOCAL_MAX_PREDICT_TOKENS", "PROMPT_TOKEN_SAFETY_MARGIN"
    ]
    for name in required_envs:
        if name not in env_defs or not str(env_defs[name].get("value") or "").strip():
            fail(key + ": required environment variable missing or empty: " + name)

    try:
        registry = json.loads(env_defs[cfg["registry_env"]]["value"])
    except Exception as exc:
        fail(key + ": Registry JSON invalid: " + str(exc))
    if registry.get("table") != cfg["table"]:
        fail(key + ": Registry table mismatch")
    fields = registry.get("fields") or {}
    if set(fields) != set(cfg["fields"]):
        fail(key + ": Registry field set mismatch: " + repr(sorted(set(cfg["fields"]) ^ set(fields))))
    roles = registry.get("query_roles") or {}
    if roles.get("sql_select_fields") != "all_fields":
        fail(key + ": sql_select_fields must be all_fields")
    if set(roles.get("sql_filter_fields") or []) != set(cfg["filter_fields"]):
        fail(key + ": sql_filter_fields mismatch")
    if not {"manufacturer", "platform"} <= set(registry.get("value_aliases") or {}):
        fail(key + ": confirmed manufacturer/platform value_aliases missing")
    for name, composite in (registry.get("composite_fields") or {}).items():
        missing = sorted(set(composite.get("expand_to") or []) - set(fields))
        if missing:
            fail(key + ": composite " + name + " references unknown fields: " + repr(missing))
    for field_name, values in (registry.get("value_aliases") or {}).items():
        if field_name not in fields or field_name not in cfg["filter_fields"] or not isinstance(values, dict):
            fail(key + ": invalid value_aliases field: " + field_name)

    conversations = workflow.get("conversation_variables") or []
    conv_names = [item.get("name") for item in conversations]
    expected_memory = {cfg["prefix"] + "query_memory_json", cfg["prefix"] + "previous_query_result_json"}
    if len(conv_names) != len(set(conv_names)) or not expected_memory <= set(conv_names):
        fail(key + ": conversation memory variables missing or duplicated")
    for item in conversations:
        if item.get("selector") != ["conversation", item.get("name")] or item.get("value_type") != "string" or not isinstance(item.get("value"), str):
            fail(key + ": invalid conversation variable: " + repr(item.get("name")))

    graph = workflow.get("graph") or {}
    nodes, edges = graph.get("nodes"), graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list) or not graph.get("viewport"):
        fail(key + ": graph nodes/edges/viewport required")
    by_id = {node.get("id"): node for node in nodes}
    if None in by_id or len(by_id) != len(nodes):
        fail(key + ": duplicate or missing node id")
    edge_ids = [edge.get("id") for edge in edges]
    if None in edge_ids or len(edge_ids) != len(set(edge_ids)):
        fail(key + ": duplicate or missing edge id")
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for edge in edges:
        if edge.get("source") not in by_id or edge.get("target") not in by_id:
            fail(key + ": dangling edge: " + repr(edge))
        if edge.get("targetHandle") != "target":
            fail(key + ": invalid targetHandle")
        outgoing[edge["source"]].append(edge)
        incoming[edge["target"]].append(edge)

    starts = [node_id for node_id, node in by_id.items() if node.get("data", {}).get("type") == "start"]
    answers = [node_id for node_id, node in by_id.items() if node.get("data", {}).get("type") == "answer"]
    if len(starts) != 1 or not answers:
        fail(key + ": exactly one Start and at least one Answer required")

    producers = {
        "env": set(env_defs),
        "conversation": set(conv_names),
        "sys": {"query", "user_id", "conversation_id", "app_id", "workflow_id"}
    }
    for node_id, node in by_id.items():
        data = node.get("data") or {}
        kind = data.get("type")
        if kind != "answer" and not outgoing[node_id]:
            fail(key + ": non-Answer node has no outgoing edge: " + node_id)
        if node.get("position") != node.get("positionAbsolute"):
            fail(key + ": position/positionAbsolute mismatch: " + node_id)
        if "width" not in node or "height" not in node:
            fail(key + ": node size missing: " + node_id)
        if kind == "start":
            if not isinstance(data.get("variables"), list):
                fail(key + ": Start variables must be a list")
            producers[node_id] = {item.get("variable") for item in data["variables"]}
        elif kind == "code":
            if not isinstance(data.get("variables"), list) or not isinstance(data.get("outputs"), dict):
                fail(key + ": Code node schema invalid: " + node_id)
            if data.get("source_code") != data.get("code"):
                fail(key + ": source_code/code mismatch: " + node_id)
            try:
                args = main_args(data.get("code") or "")
            except Exception as exc:
                fail(key + ": Python code syntax/main signature invalid: " + node_id + " " + str(exc))
            variables = [item.get("variable") for item in data.get("variables") or []]
            if args != variables:
                fail(key + ": code arguments differ from Dify variables: " + node_id)
            for output_name in data["outputs"]:
                if output_name not in data["code"]:
                    fail(key + ": declared code output not present in code: " + node_id + "." + output_name)
            producers[node_id] = set(data["outputs"])
        elif kind == "http-request":
            producers[node_id] = {"status_code", "body", "headers"}
        elif kind == "llm":
            producers[node_id] = {"text", "structured_output"}
        elif kind == "knowledge-retrieval":
            producers[node_id] = {"result"}
        else:
            producers[node_id] = set()
        if kind == "if-else":
            cases = data.get("cases")
            if not isinstance(cases, list):
                fail(key + ": IF cases must be a list: " + node_id)
            case_ids = set()
            for case in cases:
                if not case.get("case_id") or not isinstance(case.get("conditions"), list):
                    fail(key + ": invalid IF case schema: " + node_id)
                case_ids.add(case["case_id"])
            used = {edge.get("sourceHandle") for edge in outgoing[node_id]}
            if not used <= (case_ids | {"false"}):
                fail(key + ": IF branch handles mismatch: " + node_id)

    # Reachability from Start and to an Answer.
    seen, queue = set(starts), deque(starts)
    while queue:
        current = queue.popleft()
        for edge in outgoing[current]:
            if edge["target"] not in seen:
                seen.add(edge["target"])
                queue.append(edge["target"])
    if set(by_id) - seen:
        fail(key + ": unreachable nodes: " + repr(sorted(set(by_id) - seen)))
    reverse = defaultdict(list)
    for edge in edges:
        reverse[edge["target"]].append(edge["source"])
    can_answer, queue = set(answers), deque(answers)
    while queue:
        current = queue.popleft()
        for parent in reverse[current]:
            if parent not in can_answer:
                can_answer.add(parent)
                queue.append(parent)
    if set(by_id) - can_answer:
        fail(key + ": nodes cannot reach Answer: " + repr(sorted(set(by_id) - can_answer)))

    def check_selector(selector, where):
        if not isinstance(selector, list) or len(selector) < 2:
            return
        scope, variable = selector[0], selector[1]
        if scope in ("sys", "env", "conversation"):
            valid = variable in producers.get(scope, set())
        else:
            valid = scope in producers and variable in producers[scope]
        if not valid:
            fail(key + ": invalid variable selector at " + where + ": " + repr(selector))

    def scan(value, where):
        if isinstance(value, dict):
            for field, child in value.items():
                if field in ("value_selector", "variable_selector", "selector") and isinstance(child, list):
                    check_selector(child, where + "." + field)
                scan(child, where + "." + field)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                scan(child, where + "[" + str(index) + "]")
        elif isinstance(value, str):
            for match in NODE_TEMPLATE_RE.finditer(value):
                scope, variable = match.groups()
                if scope in ("sys", "env", "conversation"):
                    valid = variable in producers.get(scope, set())
                else:
                    valid = scope in producers and variable in producers[scope]
                if not valid:
                    fail(key + ": invalid template reference at " + where + ": " + scope + "." + variable)
    scan(doc, "dsl")

    for required in required_envs:
        if required not in producers["env"]:
            fail(key + ": environment producer missing: " + required)
    if "{{#env.SN_MYSQL_QUERY_URLS#}}" != by_id["http_mysql_query"]["data"].get("url"):
        fail(key + ": MySQL HTTP endpoint changed")
    if "{{#env.SN_FULL_LLM_CHAT_URLS#}}" != by_id["http_full"]["data"].get("url"):
        fail(key + ": Full LLM HTTP endpoint changed")

    for node_id in (cfg["prefix"] + "registry_load", cfg["prefix"] + "registry_parse", cfg["prefix"] + "sql_plan"):
        if node_id not in by_id:
            fail(key + ": business node missing: " + node_id)
        outputs = by_id[node_id]["data"].get("outputs") or {}
        if not outputs or any(not name.startswith(cfg["prefix"]) for name in outputs):
            fail(key + ": business node output namespace mismatch: " + node_id)

    all_text = json.dumps(doc, ensure_ascii=False)
    for forbidden in cfg["forbidden_terms"]:
        if forbidden.casefold() in all_text.casefold():
            fail(key + ": legacy PHONE term remains: " + forbidden)
    for term in cfg["prompt_terms"]:
        if term.casefold() not in all_text.casefold():
            fail(key + ": business prompt term missing: " + term)
    if "JF_QUERY_REGISTRY_JSON" in all_text or "JF_ANALYSIS_SYSTEM_PROMPT" in all_text:
        fail(key + ": old JF environment variable names remain")

    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "environment_variables": len(env_defs),
        "conversation_variables": len(conv_names),
        "registry_fields": len(fields),
        "table": cfg["table"]
    }

def main() -> int:
    parser = argparse.ArgumentParser(description="Merge and validate business-specific Dify Chatflow shards.")
    parser.add_argument("workflow", choices=sorted(CONFIGS) + ["ALL"])
    parser.add_argument("--write", action="store_true", help="write the local merged Dify import artifact")
    args = parser.parse_args()
    keys = sorted(CONFIGS) if args.workflow == "ALL" else [args.workflow]
    for key in keys:
        cfg = CONFIGS[key]
        doc, text = load_doc(key)
        summary = validate_doc(key, doc)
        if args.write:
            output = BASE_DIR / (cfg["stem"] + ".yml")
            output.write_text(text, encoding="utf-8")
            print("WROTE " + str(output))
        print("PASS " + key + " " + json.dumps(summary, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
