#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read two existing Dify node records (no workflow execution, no writes)."""
import hashlib
import json
import os
import re
import sys

RUN_ID = "e8afe2d3-0c66-44dc-a5cb-81f400fcf544"
SQL = ("SELECT DISTINCT manufacturer, platform, device_type, device_model "
       "FROM sn_cis_integration_competition_wide "
       "WHERE manufacturer IS NOT NULL AND platform IS NOT NULL "
       "AND device_type IS NOT NULL AND device_model IS NOT NULL "
       "ORDER BY manufacturer, platform, device_type, device_model")
URL = "http://7.215.224.91:9201/simulation/datacenter/mysql/query/simple"
NODES = ("integration_competition_identity_catalog_prepare", "http_identity_catalog")

def decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value

def find_field(value, field, level=0):
    value = decode(value)
    if level > 7:
        return []
    if isinstance(value, dict):
        values = []
        for k, v in value.items():
            if str(k).lower() == field:
                values.append(v)
            if isinstance(v, (dict, list)) or (isinstance(v, str) and v[:1] in "{["):
                values.extend(find_field(v, field, level + 1))
        return values
    if isinstance(value, list):
        result = []
        for v in value[:30]:
            result.extend(find_field(v, field, level + 1))
        return result
    return []

def sql_status(value):
    vals = find_field(value, "sql")
    if not vals:
        return "NOT_VISIBLE"
    return "MATCH" if any(isinstance(v, str) and v.strip() == SQL for v in vals) else "MISMATCH"

def params_status(value):
    vals = find_field(value, "params")
    if not vals:
        return "NOT_VISIBLE"
    return "EMPTY_ARRAY" if any(v == [] for v in vals) else "MISMATCH"

def url_status(value):
    vals = find_field(value, "url")
    if not vals:
        return "NOT_VISIBLE"
    return "MATCH" if any(v == URL for v in vals) else "MISMATCH"

def response_info(value):
    for body in find_field(value, "body"):
        obj = decode(body)
        if not isinstance(obj, dict) or "code" not in obj:
            continue
        code = str(obj.get("code"))
        if not re.fullmatch(r"[0-9]{1,4}", code):
            code = "UNSAFE"
        msg = str(obj.get("msg") or obj.get("message") or "")
        return code, hashlib.sha256(msg.encode()).hexdigest()[:16] if msg else "NONE"
    return "NOT_VISIBLE", "NONE"

def selftest():
    assert sql_status({"body": json.dumps({"sql": SQL, "params": []})}) == "MATCH"
    assert params_status({"body": json.dumps({"sql": SQL, "params": []})}) == "EMPTY_ARRAY"
    assert sql_status({"sql": "SELECT 1"}) == "MISMATCH"
    assert response_info({"body": json.dumps({"code": 500, "msg": "secret"})})[0] == "500"
    print("SELFTEST=PASS READ_ONLY=YES SECRETS_REDACTED=YES")

def main():
    selftest()
    if os.getenv("DB_TYPE", "postgresql").lower() != "postgresql":
        print("DIFY_DB_TYPE=UNSUPPORTED_NO_QUERY")
        return 2
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import URL
        url = URL.create(
            "postgresql+psycopg2",
            username=os.environ["DB_USERNAME"],
            password=os.environ["DB_PASSWORD"],
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", "5432")),
            database=os.environ["DB_DATABASE"],
        )
        engine = create_engine(url, connect_args={"connect_timeout": 8}, pool_pre_ping=False)
        statement = text(
            "SELECT node_id, status, inputs, process_data, outputs "
            "FROM workflow_node_executions "
            "WHERE workflow_run_id = :run_id "
            "AND node_id IN ('integration_competition_identity_catalog_prepare', 'http_identity_catalog') "
            "ORDER BY created_at DESC LIMIT 6"
        )
        with engine.connect() as conn:
            rows = conn.execute(statement, {"run_id": RUN_ID}).mappings().all()
        engine.dispose()
    except Exception as exc:
        print("DB_READ=FAIL ERROR_TYPE=" + type(exc).__name__)
        return 2
    print("DB_READ=PASS MATCHED_NODES=" + str(len(rows)))
    if not rows:
        print("RECORDS=NOT_FOUND")
        return 3
    for row in rows:
        node = row["node_id"]
        print("NODE=" + node + " EXEC_STATUS=" + str(row["status"])[:24])
        inputs, processing, outputs = row["inputs"], row["process_data"], row["outputs"]
        if node == NODES[0]:
            print("BUILD_SQL=" + sql_status(outputs) + " BUILD_PARAMS=" + params_status(outputs))
        elif node == NODES[1]:
            print("INPUT_SQL=" + sql_status(inputs) + " INPUT_PARAMS=" + params_status(inputs) +
                  " INPUT_URL=" + url_status(inputs))
            print("PROCESS_SQL=" + sql_status(processing) + " PROCESS_PARAMS=" + params_status(processing) +
                  " PROCESS_URL=" + url_status(processing))
            code, msg_sha = response_info(outputs)
            print("RESPONSE_CODE=" + code + " RESPONSE_MESSAGE_SHA16=" + msg_sha)
    print("PRODUCTION_MUTATIONS=NONE")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
