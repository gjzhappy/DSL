#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal, application-specific API smoke test for Dify integration competition.

No import, publish, settings, SQL writes, or conversation deletion is performed.
GET /info is read-only. --execute explicitly authorizes short Chatflow messages,
which can create test conversation records in Dify.
Raw answers, credentials, SQL and business records are never logged.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler, ProxyHandler, Request, build_opener
)

LOG_ROOT = Path(r"E:\work\tmp\20261008")
EXPECTED_APP = "INTEGRATION_COMPETITION_CHATFLOW"
DEFAULT_URL = "http://7.222.95.114:8013/v1"
DSL_BASELINE_SHA = "d5f99a3f71b18b8f79423ae0d2daecc085fd86c3"
LIMIT = 4 * 1024 * 1024
CATALOG_SQL = ("SELECT DISTINCT manufacturer, platform, device_type, device_model "
               "FROM sn_cis_integration_competition_wide "
               "WHERE manufacturer IS NOT NULL AND platform IS NOT NULL "
               "AND device_type IS NOT NULL AND device_model IS NOT NULL "
               "ORDER BY manufacturer, platform, device_type, device_model")
CATALOG_URL = "http://7.215.224.91:9201/simulation/datacenter/mysql/query/simple"
NODE_PROBES = {
    "integration_competition_registry_load": ("integration_competition_registry_load_status", "integration_competition_registry_load_error"),
    "integration_competition_identity_catalog_merge": ("integration_competition_registry_load_status", "integration_competition_registry_load_error"),
    "integration_competition_registry_parse": ("integration_competition_registry_parse_status", "integration_competition_registry_error"),
    "integration_competition_sql_plan": ("integration_competition_sql_compile_status", "integration_competition_sql_compile_error_answer"),
    "mysql_request": ("mysql_request_status", "mysql_request_error_answer"),
    "mysql_normalize": ("mysql_query_status", "mysql_query_error_answer"),
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("HTTP_REDIRECT_BLOCKED")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def diagnostic(error: object) -> dict:
    """Return a fixed category and hash only, never raw Dify errors."""
    if not error:
        return {"category": "UNSPECIFIED", "fingerprint": ""}
    raw = str(error)
    normalized = raw.lower()
    if any(x in normalized for x in ("length of output variable", "output variable length", "code_max_string_length", "must be less than 80000", "string length exceeds", "output is too long")):
        return {"category": "CODE_OUTPUT_LENGTH_LIMIT", "fingerprint": digest(raw)}
    for category, pattern in (
        ("AUTH", r"\b(401|403|unauthorized|forbidden|invalid.api.key)\b"),
        ("HTTP_5XX", r"\b(500|502|503|504|bad gateway|service unavailable)\b"),
        ("HTTP_4XX", r"\b(400|404|422|not found|bad request)\b"),
        ("TIMEOUT", r"timeout|timed out|deadline exceeded"),
        ("NETWORK", r"connection refused|connection reset|dns|name resolution|network unreachable"),
        ("MYSQL_BACKEND", r"mysql|sql|database|pymysql|unknown column"),
        ("REGISTRY", r"registry|alias|field_inventory"),
        ("VARIABLE", r"variable|parameter|not defined|nameerror|keyerror"),
        ("LLM", r"llm|model provider|token budget|context length"),
        ("CODE_NODE", r"code execution|codeexecution|sandbox|traceback"),
    ):
        if re.search(pattern, normalized):
            return {"category": category, "fingerprint": digest(raw)}
    match = re.search(r"\b([A-Za-z_]{3,65}(?:Error|Exception))\b", raw)
    return {"category": match.group(1) if match else "UNCLASSIFIED",
            "fingerprint": digest(raw)}


def classify_backend_message(message: str) -> str:
    """Classify without exposing any SQL, database identifiers or credentials."""
    lower = str(message or "").lower()
    if re.search(r"unknown column|1054|字段不存在", lower):
        return "COLUMN_NOT_FOUND"
    if re.search(r"doesn.t exist|unknown table|1146|表不存在", lower):
        return "TABLE_NOT_FOUND"
    if re.search(r"syntax|1064|语法错误", lower):
        return "SQL_SYNTAX"
    if re.search(r"access denied for user|authentication plugin|mysql.*1045", lower):
        return "MYSQL_AUTH"
    if re.search(r"user not login|not logged in|未登录|请先登录", lower):
        return "SERVICE_LOGIN"
    if re.search(r"unauthoriz|invalid.token|token expired|authentication failed|认证失败", lower):
        return "SERVICE_AUTH"
    if re.search(r"permission|forbidden|access denied|权限不足", lower):
        return "PERMISSION"
    if re.search(r"jdbc|database|mysql|connection|sql|数据库", lower):
        return "DATABASE_OTHER"
    return "OTHER" if lower else "EMPTY"


def catalog_contract(obj):
    """Read limited expected SQL fields from Dify SSE; never retain raw inputs."""
    if isinstance(obj, str):
        if len(obj) > 20000:
            return "NOT_VISIBLE"
        try:
            obj = json.loads(obj)
        except (TypeError, ValueError):
            return "NOT_VISIBLE"
    if not isinstance(obj, dict):
        return "NOT_VISIBLE"
    if "sql" in obj:
        if not isinstance(obj.get("sql"), str) or not isinstance(obj.get("params"), list):
            return "MISMATCH"
        return "MATCH" if obj["sql"].strip() == CATALOG_SQL and obj["params"] == [] else "MISMATCH"
    for value in obj.values():
        if isinstance(value, (dict, str)):
            outcome = catalog_contract(value) if not isinstance(value, str) or len(value) <= 20000 else "NOT_VISIBLE"
            if outcome != "NOT_VISIBLE":
                return outcome
    return "NOT_VISIBLE"


def safe_node_id(value: object) -> str:
    value = str(value or "")
    return value if re.fullmatch(r"[A-Za-z0-9_:-]{1,100}", value) else ""


def request_json(opener, url: str, api_key: str, method: str, payload=None):
    data = None if payload is None else json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    req = Request(
        url, method=method, data=data,
        headers={
            "Authorization": "Bearer " + api_key,
            "Accept": "text/event-stream, application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        response = opener.open(req, timeout=240)
    except HTTPError as exc:
        return {"ok": False, "http_status": exc.code, "error": "HTTP_ERROR"}
    except (URLError, OSError, RuntimeError) as exc:
        return {"ok": False, "http_status": 0, "error": type(exc).__name__}
    with response:
        status = response.status
        content_type = response.headers.get("Content-Type", "").lower()
        if method == "GET" or "text/event-stream" not in content_type:
            buf = response.read(LIMIT + 1)
            if len(buf) > LIMIT:
                return {"ok": False, "http_status": status, "error": "RESPONSE_TOO_LARGE"}
            try:
                obj = json.loads(buf)
            except (UnicodeDecodeError, ValueError):
                return {"ok": False, "http_status": status, "error": "JSON_INVALID"}
            if method == "GET":
                return {"ok": status == 200, "http_status": status, "body": obj}
            return {
                "ok": status == 200 and bool(obj.get("answer")),
                "http_status": status,
                "answer": str(obj.get("answer") or ""),
                "conversation_id": str(obj.get("conversation_id") or ""),
                "error": str(obj.get("error") or "")[:48] if obj.get("error") else "",
            }
        chunks = []
        conversation_id = ""
        failure = ""
        workflow_run_id = ""
        last_node_id = ""
        failed_node_id = ""
        failed_node_type = ""
        error_info = {"category": "UNSPECIFIED", "fingerprint": ""}
        total_bytes = 0
        node_probe = []
        catalog_build = "NOT_OBSERVED"
        catalog_http_input = "NOT_VISIBLE"
        catalog_http_url = "NOT_VISIBLE"
        while True:
            line = response.readline()
            if not line:
                break
            total_bytes += len(line)
            if total_bytes > LIMIT:
                failure = "RESPONSE_TOO_LARGE"
                break
            if not line.startswith(b"data:"):
                continue
            try:
                item = json.loads(line[5:].strip())
            except (UnicodeDecodeError, ValueError):
                continue
            if not isinstance(item, dict):
                continue
            event = item.get("event")
            if event in ("message", "agent_message"):
                chunks.append(str(item.get("answer") or ""))
            elif event == "message_replace":
                chunks = [str(item.get("answer") or "")]
            elif event in ("node_started", "node_finished"):
                node_data = item.get("data")
                if isinstance(node_data, dict):
                    last_node_id = safe_node_id(node_data.get("node_id")) or last_node_id
                    if event == "node_finished" and last_node_id == "integration_competition_identity_catalog_prepare":
                        outputs = node_data.get("outputs")
                        if isinstance(outputs, dict):
                            catalog_build = catalog_contract(outputs.get("integration_competition_identity_catalog_request_body_json"))
                    if last_node_id == "http_identity_catalog":
                        inputs = node_data.get("inputs")
                        if isinstance(inputs, dict):
                            found = catalog_contract(inputs)
                            if found != "NOT_VISIBLE":
                                catalog_http_input = found
                            request_url = inputs.get("url")
                            if isinstance(request_url, str):
                                catalog_http_url = "MATCH" if request_url == CATALOG_URL else "MISMATCH"
                    if event == "node_finished" and last_node_id == "http_identity_catalog":
                        outputs = node_data.get("outputs")
                        outputs = outputs if isinstance(outputs, dict) else {}
                        http_code = str(outputs.get("status_code") or "")
                        try:
                            payload = json.loads(outputs.get("body") or "{}")
                            payload = payload if isinstance(payload, dict) else {}
                        except (ValueError, TypeError):
                            payload = {}
                        raw_backend_code = str(payload.get("code") if payload.get("code") is not None else "")
                        backend_code = raw_backend_code if re.fullmatch(r"[A-Za-z0-9_-]{1,24}", raw_backend_code) else "UNAVAILABLE"
                        backend_msg = str(payload.get("msg") or payload.get("message") or "")
                        category = classify_backend_message(backend_msg)
                        node_probe.append({
                            "node": "http_identity_catalog",
                            "execution_status": str(node_data.get("status") or "")[:24],
                            "http_status": http_code if re.fullmatch(r"[0-9]{3}", http_code) else "UNAVAILABLE",
                            "backend_code": backend_code,
                            "backend_message_category": category,
                            "backend_message_fingerprint": digest(backend_msg) if backend_msg else "",
                        })
                    if event == "node_finished" and last_node_id in NODE_PROBES:
                        status_key, error_key = NODE_PROBES[last_node_id]
                        outputs = node_data.get("outputs")
                        outputs = outputs if isinstance(outputs, dict) else {}
                        raw_status = str(outputs.get(status_key) or "")
                        raw_error = str(outputs.get(error_key) or "")
                        safe_status = raw_status if raw_status in ("ok", "error", "missing") else "NOT_EMITTED"
                        safe_code = raw_error if re.fullmatch(r"(?:REGISTRY|IDENTITY_CATALOG|INTEGRATION)[A-Z0-9_]{3,80}", raw_error) else ""
                        node_probe.append({
                            "node": last_node_id,
                            "execution_status": str(node_data.get("status") or "")[:24],
                            "business_status": safe_status,
                            "error_code": safe_code,
                            "error_fingerprint": digest(raw_error) if raw_error else "",
                            "node_error_category": diagnostic(node_data.get("error"))["category"] if node_data.get("error") else "",
                        })
                    if event == "node_finished" and node_data.get("status") in ("failed", "stopped", "exception"):
                        failed_node_id = safe_node_id(node_data.get("node_id"))
                        failed_node_type = safe_node_id(node_data.get("node_type"))
                        error_info = diagnostic(node_data.get("error"))
            elif event == "error":
                failure = "DIFY_STREAM_ERROR"
                error_info = diagnostic(item.get("message") or item.get("error"))
            elif event == "workflow_finished":
                data = item.get("data")
                if isinstance(data, dict):
                    workflow_run_id = safe_node_id(item.get("workflow_run_id") or data.get("id") or "")
                    if data.get("status") in ("failed", "stopped"):
                        failure = "WORKFLOW_FAILED"
                        if data.get("error"):
                            error_info = diagnostic(data.get("error"))
            if item.get("conversation_id"):
                conversation_id = str(item["conversation_id"])
        answer = "".join(chunks)
        return {
            "ok": status == 200 and bool(answer) and not failure,
            "http_status": status,
            "answer": answer,
            "conversation_id": conversation_id,
            "workflow_run_id": workflow_run_id,
            "last_node_id": last_node_id,
            "failed_node_id": failed_node_id,
            "failed_node_type": failed_node_type,
            "error": failure,
            "diagnostic": error_info,
            "node_probe": node_probe,
            "catalog_build": catalog_build,
            "catalog_http_input": catalog_http_input,
            "catalog_http_url": catalog_http_url,
        }


def run():
    parser = argparse.ArgumentParser(description="Read-only Dify app identity check; optional guarded Chatflow smoke tests.")
    parser.add_argument("--base-url", default=DEFAULT_URL)
    parser.add_argument("--allow-http", action="store_true", help="Explicitly accept an unencrypted internal HTTP connection.")
    parser.add_argument("--execute", action="store_true", help="Allow test chat messages; does not import/publish/alter applications.")
    parser.add_argument("--diagnose-first", action="store_true", help="Send just the overview query and report redacted failure evidence.")
    parser.add_argument("--model-a", default="", help="Existing database device model for the smoke test, avoids repeat input.")
    parser.add_argument("--inspect-run-id", default="", help="Read one existing Dify workflow run without sending a chat message.")
    parser.add_argument("--selftest", action="store_true", help="Check local diagnostic logic without network or credentials.")
    opts = parser.parse_args()
    if opts.selftest:
        assert catalog_contract({"sql": CATALOG_SQL, "params": [], "user": "redacted"}) == "MATCH"
        assert catalog_contract({"body": json.dumps({"sql": CATALOG_SQL, "params": []})}) == "MATCH"
        assert catalog_contract({"sql": "SELECT 1", "params": []}) == "MISMATCH"
        assert classify_backend_message("Unknown column foo") == "COLUMN_NOT_FOUND"
        assert classify_backend_message("User not login") == "SERVICE_LOGIN"
        assert classify_backend_message("Access denied for user x") == "MYSQL_AUTH"
        print("DIAGNOSTIC_SELFTEST=PASS NO_NETWORK=YES")
        return 0, {}, None
    if opts.diagnose_first and not opts.execute:
        raise SystemExit("DIAGNOSE_FIRST_REQUIRES_EXECUTE")
    if opts.inspect_run_id and (opts.execute or opts.diagnose_first):
        raise SystemExit("RUN_INSPECTION_MUST_BE_READ_ONLY")
    if opts.inspect_run_id and not re.fullmatch(r"[0-9a-fA-F-]{36}", opts.inspect_run_id):
        raise SystemExit("WORKFLOW_RUN_ID_INVALID")
    url = opts.base_url.rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path.rstrip("/") != "/v1":
        raise SystemExit("BASE_URL_INVALID: expected https://host/v1")
    if parsed.scheme == "http" and not opts.allow_http:
        raise SystemExit("HTTP_REQUIRES_EXPLICIT_ALLOW_HTTP: prefer HTTPS when available.")
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    logpath = LOG_ROOT / ("DIFY_IC_API_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(os.getpid()) + ".json")
    report = {
        "dsl_baseline_sha": DSL_BASELINE_SHA,
        "app_expected": EXPECTED_APP,
        "api_host": parsed.hostname,
        "api_port": parsed.port,
        "transport": parsed.scheme,
        "app_verified": False,
        "cases": [],
        "outcome": "NOT_RUN",
    }
    opener = build_opener(ProxyHandler({}), NoRedirect)
    print("DIFY_HOST=" + str(parsed.hostname))
    print("APP_EXPECTED=" + EXPECTED_APP)
    print("MODE=" + ("READ_ONLY_WORKFLOW_RUN" if opts.inspect_run_id else "CHAT_MESSAGES_EXPLICIT" if opts.execute else "READ_ONLY_INFO"))
    if parsed.scheme == "http":
        print("TRANSPORT_WARNING=HTTP_UNENCRYPTED_INTERNAL_ONLY")
    api_key = getpass.getpass("Dify 集成度应用 API Key（隐藏输入，不写日志）: ").strip()
    if not api_key:
        raise SystemExit("API_KEY_EMPTY")
    try:
        info = request_json(opener, url + "/info", api_key, "GET")
        if not info.get("ok"):
            report["outcome"] = "PREFLIGHT_FAILED"
            report["error_code"] = info.get("error", "HTTP_ERROR")
            report["http_status"] = info.get("http_status", 0)
            print("APP_INFO_PREFLIGHT=FAIL HTTP=" + str(info.get("http_status", 0)))
            return 2, report, logpath
        app = info.get("body")
        if not isinstance(app, dict):
            report["outcome"] = "PREFLIGHT_FAILED"
            report["error_code"] = "APP_INFO_INVALID"
            return 2, report, logpath
        if app.get("name") != EXPECTED_APP or app.get("mode") != "advanced-chat":
            report["outcome"] = "PREFLIGHT_FAILED"
            report["error_code"] = "WRONG_APP_OR_MODE"
            print("APP_IDENTITY=FAIL_EXPECTED_INTEGRATION_COMPETITION")
            return 2, report, logpath
        report["app_verified"] = True
        print("APP_IDENTITY=PASS")
        if opts.inspect_run_id:
            run_info = request_json(opener, url + "/workflows/run/" + opts.inspect_run_id, api_key, "GET")
            if not run_info.get("ok") or not isinstance(run_info.get("body"), dict):
                report["outcome"] = "READ_ONLY_RUN_LOOKUP_FAILED"
                report["http_status"] = run_info.get("http_status", 0)
                print("WORKFLOW_RUN_LOOKUP=FAIL HTTP=" + str(report["http_status"]))
                return 2, report, logpath
            body = run_info["body"]
            error = diagnostic(body.get("error"))
            report["outcome"] = "READ_ONLY_RUN_LOOKUP_PASS"
            report["run"] = {
                "workflow_run_id": opts.inspect_run_id,
                "status": str(body.get("status") or "")[:24],
                "error_category": error["category"],
                "error_fingerprint": error["fingerprint"],
                "error_present": bool(body.get("error")),
            }
            print("WORKFLOW_RUN_LOOKUP=PASS")
            print("RUN_STATUS=" + report["run"]["status"])
            print("RUN_ERROR_CATEGORY=" + error["category"])
            print("RUN_ERROR_FINGERPRINT=" + error["fingerprint"])
            print("RUN_ERROR_PRESENT=" + str(report["run"]["error_present"]))
            return 0, report, logpath
        if not opts.execute:
            report["outcome"] = "READ_ONLY_PREFLIGHT_PASS"
            print("DIFY_READONLY_PREFLIGHT=PASS")
            return 0, report, logpath
        model_a = opts.model_a.strip() or input("输入 MySQL 中真实存在的设备型号 A（不记录）: ").strip()
        model_b = "" if opts.diagnose_first else input("第二个真实型号 B（可留空）: ").strip()
        if not model_a:
            report["outcome"] = "INPUT_REQUIRED"
            print("MODEL_A_REQUIRED")
            return 2, report, logpath
        user = "ic-regression-" + uuid.uuid4().hex[:16]
        questions = [
            ("overview", "查询" + model_a + "的整机总面积、小系统总面积、外设总面积和无线总面积。", "first"),
            ("small_system_followup", "这些机型的小系统中SOC、PMK、PMU1、PMU2的面积分别是多少？", "followup"),
            ("wireless_followup", "它们的无线RF、NC和天线面积分别是多少？", "followup"),
            ("unknown_model", "查询XYZ-NOT-EXIST-999的整机总面积，找不到就明确说明没有数据。", "fresh"),
        ]
        if model_b:
            questions.append(("two_model_comparison", "对比" + model_a + "和" + model_b + "的整机总面积和小系统总面积。", "fresh"))
        if opts.diagnose_first:
            questions = questions[:1]
        conversation_id = ""
        for name, query, continuity in questions:
            if continuity == "fresh":
                conversation_id = ""
            response = request_json(opener, url + "/chat-messages", api_key, "POST", {
                "inputs": {},
                "query": query,
                "response_mode": "streaming",
                "conversation_id": conversation_id,
                "user": user,
            })
            answer = str(response.get("answer") or "")
            # A returned answer can be the dedicated SQL-error branch: it is not a successful query.
            error_branch = response.get("last_node_id") == "ans_sql_error" or answer.startswith(
                ("MySQL 查询请求构建失败", "MySQL 查询失败")
            )
            success = bool(response.get("ok")) and not error_branch and "LLM_INPUT_TOO_LARGE" not in answer
            if continuity in ("first", "followup") and not response.get("conversation_id"):
                success = False
            record = {
                "case": name, "ok": success, "http_status": response.get("http_status", 0),
                "error_code": response.get("error") or "",
                "answer_chars": len(answer),
                "answer_sha256": digest(answer) if answer else "",
                "conversation_returned": bool(response.get("conversation_id")),
                "workflow_run_id": response.get("workflow_run_id") or "",
                "last_node_id": response.get("last_node_id") or "",
                "failed_node_id": response.get("failed_node_id") or "",
                "failed_node_type": response.get("failed_node_type") or "",
                "error_category": (response.get("diagnostic") or {}).get("category") or "UNSPECIFIED",
                "error_fingerprint": (response.get("diagnostic") or {}).get("fingerprint") or "",
                "semantic_validation": "REQUIRES_MYSQL_ORACLE",
                "node_probe": response.get("node_probe") or [],
                "error_branch": bool(error_branch),
                "catalog_build": response.get("catalog_build", "NOT_OBSERVED"),
                "catalog_http_input": response.get("catalog_http_input", "NOT_VISIBLE"),
                "catalog_http_url": response.get("catalog_http_url", "NOT_VISIBLE"),
            }
            report["cases"].append(record)
            print("CASE=" + name + " TRANSPORT=" + ("PASS" if success else "FAIL") +
                  " ANSWER_CHARS=" + str(len(answer)))
            if not success:
                report["outcome"] = "API_SMOKE_FAILED"
                print("WORKFLOW_FAILED_NODE=" + record["failed_node_id"])
                print("WORKFLOW_LAST_NODE=" + record["last_node_id"])
                print("WORKFLOW_ERROR_CATEGORY=" + record["error_category"])
                print("WORKFLOW_ERROR_FINGERPRINT=" + record["error_fingerprint"])
                print("WORKFLOW_RUN_ID=" + record["workflow_run_id"])
                print("CATALOG_BUILD=" + record["catalog_build"] +
                      " HTTP_INPUT=" + record["catalog_http_input"] +
                      " HTTP_URL=" + record["catalog_http_url"])
                for probe in record["node_probe"]:
                    if probe["node"] == "http_identity_catalog":
                        print("CATALOG_HTTP=" + probe["http_status"] +
                              " BACKEND_CODE=" + probe["backend_code"] +
                              " BACKEND_CATEGORY=" + probe["backend_message_category"] +
                              " MSG_SHA256=" + probe["backend_message_fingerprint"])
                    else:
                        print("NODE_PROBE=" + probe["node"] +
                              " EXEC=" + probe["execution_status"] +
                              " STATUS=" + probe["business_status"] +
                              " CODE=" + probe["error_code"] +
                              " ERROR_SHA256=" + probe["error_fingerprint"] +
                              " NODE_ERROR=" + probe["node_error_category"])
                break
            conversation_id = str(response["conversation_id"])
            time.sleep(0.25)
        else:
            report["outcome"] = "API_SMOKE_PASS_SEMANTICS_NOT_VERIFIED"
        print("DIFY_API_SMOKE=" + report["outcome"])
        return (0 if report["outcome"].startswith("API_SMOKE_PASS") else 3), report, logpath
    finally:
        api_key = ""


if __name__ == "__main__":
    result = 4
    output = {}
    path = None
    try:
        result, output, path = run()
    except (KeyboardInterrupt, EOFError):
        print("TEST_CANCELLED")
        result = 130
    except Exception as exc:
        print("TEST_EXCEPTION=" + type(exc).__name__)
        result = 4
    if path is not None:
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("REDACTED_LOG=" + str(path))
    raise SystemExit(result)
