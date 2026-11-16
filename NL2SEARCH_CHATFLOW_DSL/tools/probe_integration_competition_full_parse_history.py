#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inspect a previously completed Dify Chatflow's node traces (read-only).
Uses CONSOLE authentication, NOT a Dify application API key.
No POST, mutation, replay, or provider invocation; never stores raw node bodies.
"""
import argparse
import getpass
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

APP = "INTEGRATION_COMPETITION_CHATFLOW"
RUN = "059dfd89-945e-406e-9e01-6a74b98da62e"
DEFAULT_CONSOLE = "http://7.222.95.114:8013/console/api"
ROOT = Path(r"E:\work\tmp\20261008")
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}$")
SAFE_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
BODY_CAP = 48 * 1024 * 1024

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("REDIRECT_BLOCKED")

def safe_error_code(value):
    value = str(value or "")
    return value if SAFE_CODE.fullmatch(value) else "REDACTED"

def category(value):
    text = str(value or "").lower()
    if not text:
        return "NONE"
    if any(x in text for x in ("concurren", "too many", "429", "busy", "resource_exhausted", "capacity", "max request", "rate limit")):
        return "BUSY_OR_CONCURRENCY_INDICATION"
    if "code_not_200" in text:
        return "JAVA_BUSINESS_CODE_NOT_200"
    if "http_error" in text:
        return "JAVA_HTTP_STATUS_ERROR"
    if "choices_empty" in text:
        return "JAVA_CHOICES_EMPTY"
    if "content_empty" in text:
        return "JAVA_CONTENT_EMPTY"
    if "data_missing" in text:
        return "JAVA_DATA_MISSING"
    if "json_invalid" in text:
        return "JAVA_JSON_INVALID"
    if "body_empty" in text:
        return "JAVA_BODY_EMPTY"
    if "timeout" in text or "timed out" in text:
        return "TIMEOUT"
    if any(x in text for x in ("unsupported", "not support", "model not found")):
        return "MODEL_UNSUPPORTED_INDICATION"
    return "OTHER_REDACTED"

def sha(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest() if value else ""

def parse_json(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            result = json.loads(value)
            return result if isinstance(result, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}

def node_outputs(entry):
    out = entry.get("outputs")
    return parse_json(out)

def select_node(items, node_id):
    matches = [n for n in items if isinstance(n, dict) and str(n.get("node_id")) == node_id]
    return matches[-1] if matches else {}

def analyze(items):
    full_http = select_node(items, "http_full")
    full_parse = select_node(items, "full_parse")
    local = select_node(items, "llmlocal")
    http_outputs = node_outputs(full_http)
    parse_outputs = node_outputs(full_parse)
    body = parse_json(http_outputs.get("body"))
    java_result = parse_json(parse_outputs.get("full_llm_result_json"))
    choices = (body.get("data") or {}).get("choices") if isinstance(body.get("data"), dict) else None
    error = str(java_result.get("error") or full_parse.get("error") or "")
    business_code = body.get("code")
    report = {
        "run_id": RUN,
        "http_full_seen": bool(full_http),
        "http_full_node_status": str(full_http.get("status") or "")[:32],
        "http_full_status_code": http_outputs.get("status_code") if isinstance(http_outputs.get("status_code"), int) else None,
        "java_business_code": business_code if isinstance(business_code, int) and not isinstance(business_code, bool) else None,
        "java_error_code": safe_error_code(body.get("errorCode")),
        "java_message_category": category(body.get("msg")),
        "java_choices_present": isinstance(choices, list) and len(choices) > 0,
        "java_content_present": bool(choices and isinstance(choices[0], dict) and isinstance(choices[0].get("message"), dict)
                                     and isinstance(choices[0]["message"].get("content"), str)
                                     and choices[0]["message"]["content"].strip()),
        "full_parse_seen": bool(full_parse),
        "full_parse_node_status": str(full_parse.get("status") or "")[:32],
        "full_parse_llm_status": str(parse_outputs.get("full_llm_status") or "")[:32],
        "full_parse_result_llm_status": str(java_result.get("llm_status") or "")[:32],
        "full_parse_error_prefix": next((x for x in ("JAVA_FULL_LLM_HTTP_ERROR", "JAVA_FULL_LLM_CODE_NOT_200",
                                    "JAVA_FULL_LLM_DATA_MISSING", "JAVA_FULL_LLM_CHOICES_EMPTY",
                                    "JAVA_FULL_LLM_CONTENT_EMPTY", "JAVA_FULL_LLM_BODY_EMPTY",
                                    "JAVA_FULL_LLM_BODY_JSON_INVALID", "JAVA_FULL_LLM_PARSE_FAILED")
                                      if x in error), "NONE_OR_OTHER"),
        "full_parse_error_category": category(error),
        "full_parse_error_sha256": sha(error),
        "llmlocal_seen": bool(local),
        "raw_data_saved": False,
    }
    if not full_parse:
        report["outcome"] = "FULL_PARSE_HISTORY_UNAVAILABLE"
    elif report["full_parse_result_llm_status"] == "error" or report["full_parse_llm_status"] == "error":
        report["outcome"] = "FULL_PARSE_ERROR_OBSERVED"
    elif report["full_parse_result_llm_status"] == "ok":
        report["outcome"] = "FULL_PARSE_OK_OBSERVED"
    else:
        report["outcome"] = "FULL_PARSE_OUTPUT_UNAVAILABLE"
    return report

def api_get(opener, base, token, path, cap=BODY_CAP):
    req = Request(base + path, method="GET",
                  headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    with opener.open(req, timeout=35) as resp:
        data = resp.read(cap + 1)
        if len(data) > cap:
            raise RuntimeError("RESPONSE_TOO_LARGE")
        return json.loads(data)

def extract_app_id(payload):
    apps = payload.get("data") if isinstance(payload, dict) else []
    if isinstance(apps, dict):
        apps = apps.get("data") or []
    hits = [x for x in apps if isinstance(x, dict) and x.get("name") == APP and x.get("mode") == "advanced-chat"]
    return str(hits[0].get("id") or "") if len(hits) == 1 else "", len(hits)

def selftest():
    sample = [
        {"node_id": "http_full", "status": "succeeded",
         "outputs": {"status_code": 200, "body": json.dumps(
             {"code": 503, "msg": "max concurrency reached", "errorCode": "BUSY", "data": None})}},
        {"node_id": "full_parse", "status": "succeeded", "outputs": {"full_llm_status": "error",
         "full_llm_result_json": json.dumps(
             {"llm_status": "error", "error": "JAVA_FULL_LLM_CODE_NOT_200; msg=max concurrency reached"})}},
        {"node_id": "llmlocal", "status": "succeeded"},
    ]
    r = analyze(sample)
    assert r["outcome"] == "FULL_PARSE_ERROR_OBSERVED"
    assert r["java_business_code"] == 503
    assert r["full_parse_error_prefix"] == "JAVA_FULL_LLM_CODE_NOT_200"
    assert r["full_parse_error_category"] == "BUSY_OR_CONCURRENCY_INDICATION"
    assert r["raw_data_saved"] is False
    print("SELFTEST=PASS")

def main():
    parser = argparse.ArgumentParser(description="Read existing node executions. Never re-run the LLM.")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--execute-readonly", action="store_true")
    parser.add_argument("--allow-http", action="store_true")
    parser.add_argument("--console-base", default=DEFAULT_CONSOLE)
    opts = parser.parse_args()
    if opts.selftest:
        selftest()
        return 0
    if not opts.execute_readonly:
        parser.error("--execute-readonly required")
    base = opts.console_base.rstrip("/")
    url = urlsplit(base)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or url.path != "/console/api":
        parser.error("Expected a trusted Dify console URL ending in /console/api")
    if url.scheme == "http" and not opts.allow_http:
        parser.error("HTTP requires explicit --allow-http")
    print("MODE=READ_ONLY_EXISTING_RUN")
    print("RUN_ID=" + RUN)
    print("CONSOLE_HOST=" + url.hostname)
    print("CONSOLE_PORT=" + str(url.port or "DEFAULT"))
    print("MUTATIONS=NONE")
    token = getpass.getpass("Dify Console access token (hidden; NOT the app API key): ").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise SystemExit("CONSOLE_TOKEN_EMPTY")
    opener = build_opener(ProxyHandler({}), NoRedirect)
    try:
        apps = api_get(opener, base, token, "/apps?" + urlencode({
            "page": 1, "limit": 100, "mode": "all", "name": APP}), cap=3*1024*1024)
        app_id, count = extract_app_id(apps)
        if not UUID.fullmatch(app_id) or count != 1:
            print("APP_RESOLVE=FAIL EXACT_MATCHES=" + str(count))
            return 2
        print("APP_RESOLVE=PASS")
        result = api_get(opener, base, token,
            "/apps/" + app_id + "/workflow-runs/" + RUN + "/node-executions")
    except HTTPError as exc:
        print("CONSOLE_HTTP_STATUS=" + str(exc.code))
        print("CONSOLE_ACCESS=FAIL_OR_URL_INCORRECT")
        return 2
    except (URLError, OSError, ValueError, RuntimeError) as exc:
        print("CONSOLE_ACCESS_ERROR=" + type(exc).__name__)
        return 2
    finally:
        token = ""
    nodes = result.get("data") if isinstance(result, dict) else []
    if isinstance(nodes, dict):
        nodes = nodes.get("data") or []
    if not isinstance(nodes, list):
        print("NODE_TRACE_SHAPE_UNEXPECTED")
        return 2
    report = analyze(nodes)
    report["node_entries"] = len(nodes)
    ROOT.mkdir(parents=True, exist_ok=True)
    p = ROOT / ("IC_FULL_PARSE_HISTORY_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    p.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for k,v in report.items():
        if k != "raw_data_saved":
            print(k.upper() + "=" + str(v))
    print("SENSITIVE_FIELDS_LOGGED=NO")
    print("REDACTED_LOG=" + str(p))
    return 0 if report["outcome"] == "FULL_PARSE_ERROR_OBSERVED" else 3

if __name__ == "__main__":
    raise SystemExit(main())
