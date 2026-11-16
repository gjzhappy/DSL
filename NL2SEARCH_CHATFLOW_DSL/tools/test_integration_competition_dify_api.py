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
SOURCE_SHA = "d27191cb434654fd0f8823bde07371a57a1636b8"
LIMIT = 4 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("HTTP_REDIRECT_BLOCKED")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
        total_bytes = 0
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
            elif event == "error":
                failure = "DIFY_STREAM_ERROR"
            elif event == "workflow_finished":
                data = item.get("data")
                if isinstance(data, dict) and data.get("status") in ("failed", "stopped"):
                    failure = "WORKFLOW_FAILED"
            if item.get("conversation_id"):
                conversation_id = str(item["conversation_id"])
        answer = "".join(chunks)
        return {
            "ok": status == 200 and bool(answer) and not failure,
            "http_status": status,
            "answer": answer,
            "conversation_id": conversation_id,
            "error": failure,
        }


def run():
    parser = argparse.ArgumentParser(description="Read-only Dify app identity check; optional guarded Chatflow smoke tests.")
    parser.add_argument("--base-url", default=DEFAULT_URL)
    parser.add_argument("--allow-http", action="store_true", help="Explicitly accept an unencrypted internal HTTP connection.")
    parser.add_argument("--execute", action="store_true", help="Allow test chat messages; does not import/publish/alter applications.")
    opts = parser.parse_args()
    url = opts.base_url.rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path.rstrip("/") != "/v1":
        raise SystemExit("BASE_URL_INVALID: expected https://host/v1")
    if parsed.scheme == "http" and not opts.allow_http:
        raise SystemExit("HTTP_REQUIRES_EXPLICIT_ALLOW_HTTP: prefer HTTPS when available.")
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    logpath = LOG_ROOT / ("DIFY_IC_API_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(os.getpid()) + ".json")
    report = {
        "source_sha": SOURCE_SHA,
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
    print("MODE=" + ("CHAT_MESSAGES_EXPLICIT" if opts.execute else "READ_ONLY_INFO"))
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
        if not opts.execute:
            report["outcome"] = "READ_ONLY_PREFLIGHT_PASS"
            print("DIFY_READONLY_PREFLIGHT=PASS")
            return 0, report, logpath
        model_a = input("输入 MySQL 中真实存在的设备型号 A（不记录）: ").strip()
        model_b = input("第二个真实型号 B（可留空）: ").strip()
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
            success = bool(response.get("ok")) and "LLM_INPUT_TOO_LARGE" not in answer
            if continuity in ("first", "followup") and not response.get("conversation_id"):
                success = False
            record = {
                "case": name, "ok": success, "http_status": response.get("http_status", 0),
                "error_code": response.get("error") or "",
                "answer_chars": len(answer),
                "answer_sha256": digest(answer) if answer else "",
                "conversation_returned": bool(response.get("conversation_id")),
                "semantic_validation": "REQUIRES_MYSQL_ORACLE",
            }
            report["cases"].append(record)
            print("CASE=" + name + " TRANSPORT=" + ("PASS" if success else "FAIL") +
                  " ANSWER_CHARS=" + str(len(answer)))
            if not success:
                report["outcome"] = "API_SMOKE_FAILED"
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
