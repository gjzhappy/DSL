#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Two fixed read-only SQL requests to locate a backend failure; redact responses."""
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
import json
import re

URL = "http://7.215.224.91:9201/simulation/datacenter/mysql/query/simple"
LOG = Path(r"E:\work\tmp\20261008")
CATALOG_SQL = ("SELECT DISTINCT manufacturer, platform, device_type, device_model "
               "FROM sn_cis_integration_competition_wide "
               "WHERE manufacturer IS NOT NULL AND platform IS NOT NULL "
               "AND device_type IS NOT NULL AND device_model IS NOT NULL "
               "ORDER BY manufacturer, platform, device_type, device_model")
QUERIES = [("select1", "SELECT 1 AS probe_value"), ("catalog", CATALOG_SQL)]

class BlockRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("REDIRECT_BLOCKED")

def classify(message):
    m = str(message or "").lower()
    if re.search(r"user not login|not logged in|not login|未登录|请登录", m):
        return "USER_NOT_LOGIN"
    if re.search(r"access denied for user|authentication plugin|mysql.*1045|1045.*access denied", m):
        return "MYSQL_LOGIN_DENIED"
    if re.search(r"unauthoriz|invalid.token|token expired|鉴权失败|认证失败", m):
        return "SERVICE_AUTH"
    if re.search(r"permission|forbidden|access denied|权限", m):
        return "PERMISSION"
    if re.search(r"doesn.t exist|unknown table|1146|表不存在", m):
        return "TABLE_NOT_FOUND"
    if re.search(r"unknown column|1054|字段不存在", m):
        return "COLUMN_NOT_FOUND"
    if re.search(r"syntax|1064|语法", m):
        return "SQL_SYNTAX"
    if re.search(r"jdbc|database|mysql|communications link|数据库", m):
        return "DATABASE_OTHER"
    return "OTHER" if m else "NONE"

def probe(opener, label, sql):
    body = json.dumps({"sql": sql, "params": [], "user": "ic-readonly-probe"}, separators=(",", ":")).encode("utf-8")
    req = Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json"
    })
    try:
        with opener.open(req, timeout=25) as resp:
            http = resp.status
            raw = resp.read(262145)
    except HTTPError as exc:
        http = exc.code
        raw = exc.read(262145)
    except (URLError, OSError, RuntimeError) as exc:
        return {"case": label, "status": "NETWORK_ERROR", "error_type": type(exc).__name__}
    if len(raw) > 262144:
        return {"case": label, "status": "TOO_LARGE", "http": http}
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeError):
        return {"case": label, "status": "INVALID_JSON", "http": http}
    if not isinstance(parsed, dict):
        return {"case": label, "status": "INVALID_BODY", "http": http}
    code = parsed.get("code")
    code = str(code) if code is not None else "MISSING"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", code):
        code = "REDACTED"
    message = str(parsed.get("msg") or parsed.get("message") or "")
    return {
        "case": label, "status": "PASS" if http == 200 and code in ("0", "200") else "BACKEND_FAILURE",
        "http": http, "backend_code": code, "category": classify(message),
        "message_sha256": sha256(message.encode("utf-8")).hexdigest() if message else ""
    }

def main():
    assert all(sql.lower().startswith("select ") and ";" not in sql for _, sql in QUERIES)
    assert classify("User not login") == "USER_NOT_LOGIN"
    assert classify("Access denied for user 'x'") == "MYSQL_LOGIN_DENIED"
    print("SELFTEST=PASS READ_ONLY=YES")
    print("ENDPOINT=SN_MYSQL_QUERY_SIMPLE")
    opener = build_opener(ProxyHandler({}), BlockRedirect())
    results = []
    for label, sql in QUERIES:
        if label == "catalog" and results[0]["status"] != "PASS":
            print("CATALOG=SKIPPED_SELECT1_FAILED")
            break
        result = probe(opener, label, sql)
        results.append(result)
        print("CASE={case} STATUS={status} HTTP={http} CODE={backend_code} CATEGORY={category} MESSAGE_SHA256={message_sha256}".format(
            case=result["case"], status=result["status"], http=result.get("http", 0),
            backend_code=result.get("backend_code", ""), category=result.get("category", ""),
            message_sha256=result.get("message_sha256", "")))
    LOG.mkdir(parents=True, exist_ok=True)
    path = LOG / ("IC_MYSQL_BACKEND_PROBE_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    path.write_text(json.dumps({"read_only": True, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REDACTED_LOG=" + str(path))
    return 0 if len(results) == 2 and all(x["status"] == "PASS" for x in results) else 3

if __name__ == "__main__":
    raise SystemExit(main())
