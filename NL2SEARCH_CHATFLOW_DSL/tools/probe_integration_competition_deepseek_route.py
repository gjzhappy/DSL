#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-request, redacted Dify DeepSeek-path probe. No config/SQL mutations."""
import argparse
import getpass
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

BASE = "http://7.222.95.114:8013/v1"
APP = "INTEGRATION_COMPETITION_CHATFLOW"
ROOT = Path(r"E:\work\tmp\20261008") if os.name == "nt" else Path.home() / "dify-ic-regression"
LIMIT = 8 * 1024 * 1024

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("REDIRECT_BLOCKED")

def category(value):
    s = str(value or "").lower()
    if not s:
        return "NONE"
    if "concurren" in s or "too many" in s or "busy" in s or "429" in s or "capacity" in s or "resource_exhausted" in s:
        return "BUSY_OR_CONCURRENCY"
    if ("model" in s and ("not support" in s or "unsupported" in s)) or ("model" in s and "404" in s):
        return "MODEL_ROUTING_OR_UNSUPPORTED"
    if "404" in s or "not found" in s:
        return "HTTP_404"
    if "timeout" in s or "timed out" in s:
        return "TIMEOUT"
    if any(x in s for x in ("500", "502", "503", "504", "gateway")):
        return "HTTP_5XX"
    return "OTHER_REDACTED"

def evaluate(r):
    if r["http_status"] != 200:
        return "DIFY_HTTP_FAILED"
    if r["workflow_status"] != "succeeded":
        return "WORKFLOW_NOT_SUCCEEDED"
    if not r["http_full_seen"]:
        return "FULL_LLM_NOT_SELECTED"
    if r["llmlocal_seen"]:
        return "LOCAL_FALLBACK_USED"
    if r["http_full_status"] != 200:
        return "FULL_LLM_HTTP_NOT_200_OR_UNOBSERVED"
    if r["full_parse_status"] != "ok":
        return "FULL_LLM_PARSE_NOT_OK_OR_UNOBSERVED"
    if r["answer_chars"] <= 0:
        return "EMPTY_ANSWER"
    return "DEEPSEEK_ROUTE_PASS"

def probe(api_key):
    opener = build_opener(ProxyHandler({}), NoRedirect)
    def open_request(path, method="GET", payload=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(BASE + path, data=body, method=method, headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        return opener.open(req, timeout=240)

    with open_request("/info") as res:
        info = json.loads(res.read(65536))
        if res.status != 200 or info.get("name") != APP or info.get("mode") != "advanced-chat":
            raise ValueError("APP_IDENTITY_MISMATCH")
    print("APP_IDENTITY=PASS")
    report = dict(http_status=0, workflow_status="", http_full_seen=False,
                  http_full_status=None, full_parse_status="", llmlocal_seen=False,
                  answer_chars=0, workflow_run_id="", error_category="NONE",
                  error_fingerprint="", events=0)
    query = ("查询IPhone16 Pro Max的小系统总面积，以及SOC、PMK、PMU1、PMU2的面积；"
             "只根据实际数据库记录回答。")
    payload = {"inputs": {}, "query": query, "response_mode": "streaming",
               "conversation_id": "", "user": "ic-deepseek-route-" + uuid.uuid4().hex[:12]}
    answer_parts = []
    start = time.monotonic()
    try:
        with open_request("/chat-messages", "POST", payload) as stream:
            report["http_status"] = stream.status
            count = 0
            for raw in stream:
                count += len(raw)
                if count > LIMIT:
                    report["error_category"] = "RESPONSE_LIMIT"
                    break
                if not raw.startswith(b"data:"):
                    continue
                try:
                    event = json.loads(raw[5:].strip().decode("utf-8"))
                except (UnicodeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                report["events"] += 1
                name = event.get("event")
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                if name in ("message", "agent_message"):
                    answer_parts.append(str(event.get("answer") or ""))
                elif name == "message_replace":
                    answer_parts = [str(event.get("answer") or "")]
                elif name in ("node_started", "node_finished"):
                    node = str(data.get("node_id") or "")
                    if node == "http_full":
                        report["http_full_seen"] = True
                        if name == "node_finished":
                            out = data.get("outputs") or {}
                            if isinstance(out, dict):
                                try:
                                    report["http_full_status"] = int(out.get("status_code"))
                                except (ValueError, TypeError):
                                    pass
                    elif node == "full_parse" and name == "node_finished":
                        out = data.get("outputs") or {}
                        if isinstance(out, dict):
                            report["full_parse_status"] = str(out.get("full_llm_status") or "")[:20]
                    elif node == "llmlocal":
                        report["llmlocal_seen"] = True
                    if name == "node_finished" and data.get("status") in ("failed", "exception"):
                        err = str(data.get("error") or "")
                        report["error_category"] = category(err)
                        report["error_fingerprint"] = hashlib.sha256(err.encode()).hexdigest() if err else ""
                elif name == "workflow_finished":
                    report["workflow_status"] = str(data.get("status") or "")
                    report["workflow_run_id"] = str(event.get("workflow_run_id") or data.get("id") or "")[:80]
                    if data.get("error"):
                        err = str(data["error"])
                        report["error_category"] = category(err)
                        report["error_fingerprint"] = hashlib.sha256(err.encode()).hexdigest()
                elif name == "error":
                    err = str(event.get("message") or event.get("error") or "")
                    report["error_category"] = category(err)
                    report["error_fingerprint"] = hashlib.sha256(err.encode()).hexdigest() if err else ""
    except HTTPError as exc:
        report["http_status"] = exc.code
        report["error_category"] = "DIFY_HTTP_" + str(exc.code)
    except (URLError, OSError) as exc:
        report["error_category"] = type(exc).__name__
    report["elapsed_sec"] = round(time.monotonic() - start, 2)
    report["answer_chars"] = len("".join(answer_parts))
    report["outcome"] = evaluate(report)
    return report

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--allow-http", action="store_true")
    a = p.parse_args()
    if a.selftest:
        x = dict(http_status=200, workflow_status="succeeded", http_full_seen=True,
                 llmlocal_seen=False, http_full_status=200, full_parse_status="ok", answer_chars=100)
        assert evaluate(x) == "DEEPSEEK_ROUTE_PASS"
        x["llmlocal_seen"] = True
        assert evaluate(x) == "LOCAL_FALLBACK_USED"
        x["llmlocal_seen"] = False
        x["full_parse_status"] = "error"
        assert evaluate(x) == "FULL_LLM_PARSE_NOT_OK_OR_UNOBSERVED"
        print("SELFTEST=PASS")
        return 0
    if not a.execute or not a.allow_http:
        p.error("Requires --execute --allow-http; sends one Dify test chat, may create a test conversation.")
    ROOT.mkdir(parents=True, exist_ok=True)
    key = getpass.getpass("Dify API Key (hidden, not logged): ").strip()
    if not key:
        raise SystemExit("API_KEY_EMPTY")
    try:
        result = probe(key)
    except HTTPError as exc:
        print("PREFLIGHT_HTTP=" + str(exc.code))
        return 2
    except Exception as exc:
        print("PREFLIGHT_ERROR=" + type(exc).__name__)
        return 2
    finally:
        key = ""
    report_file = ROOT / ("IC_DEEPSEEK_ROUTE_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    report_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for label in ("outcome", "http_status", "workflow_status", "http_full_seen",
                  "http_full_status", "full_parse_status", "llmlocal_seen",
                  "answer_chars", "workflow_run_id", "error_category", "elapsed_sec"):
        print(label.upper() + "=" + str(result[label]))
    print("MODEL_ID_PROOF=REQUIRES_JAVA_BACKEND_OR_PROVIDER_LOGS")
    print("REDACTED_LOG=" + str(report_file))
    return 0 if result["outcome"] == "DEEPSEEK_ROUTE_PASS" else 3

if __name__ == "__main__":
    raise SystemExit(main())
