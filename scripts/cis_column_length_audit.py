#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure MySQL value lengths using the approved CIS ETL v1.0.2 transform.

Read-only: MongoDB ping/find only. Never connects to MySQL and never writes data.
Reports contain field names and aggregate lengths only; no source values or identities.
"""
from __future__ import annotations

import argparse
import csv
import getpass
import importlib.util
import json
import logging
import math
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

ETL_NAME = "sn_cis_integration_competition_wide_mongo_to_mysql_v1_0_2.py"
BUCKETS = (16, 24, 32, 48, 64, 96, 128, 192, 255, 384, 512, 768, 1024, 2048)
ROW_LIMIT = 8126
ROW_WARNING_LIMIT = 7000


def load_etl(path: Path):
    if not path.is_file():
        raise RuntimeError("ETL_SCRIPT_NOT_FOUND")

    # Prevent ETL v1.0.2 from creating its own log or writing source details.
    logger = logging.getLogger("cis_integration_mongo_to_mysql")
    logger.addHandler(logging.NullHandler())

    spec = importlib.util.spec_from_file_location("cis_etl_v102_length_probe", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError("ETL_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    if module.ETL_VERSION != "1.0.2":
        raise RuntimeError("ETL_VERSION_MISMATCH")
    return module


def mysql_text(field, value, etl):
    if field in etl.JSON_COLUMNS:
        return etl.canonical_json(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def percentile(values, pct):
    if not values:
        return 0
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * pct / 100) - 1)]


def recommend(field, max_chars, etl):
    if field in etl.JSON_COLUMNS:
        return "JSON", "n/a", 20
    if field == "release_date":
        return "DATE", "n/a", 3

    ascii_field = field in etl.AREA_COLUMNS or field == "source_document_id"
    charset = "ascii" if ascii_field else "utf8mb4"
    if field in etl.AREA_COLUMNS:
        minimum = 16
    elif field == "device_pic_url":
        minimum = 255
    elif field == "device_model":
        minimum = 64
    elif field in ("manufacturer", "device_type", "release_date_text", "semantic_version"):
        minimum = 32
    elif field == "platform":
        minimum = 64
    elif field == "source_document_id":
        minimum = 16
    else:
        minimum = 32

    target = max(minimum, math.ceil(max_chars * 1.2))
    length = next((n for n in BUCKETS if n >= target), None)
    if length is None:
        return f"TEXT CHARACTER SET {charset}", charset, 20

    bytes_per_char = 1 if ascii_field else 4
    prefix = 1 if length * bytes_per_char <= 255 else 2
    return f"VARCHAR({length}) CHARACTER SET {charset}", charset, length * bytes_per_char + prefix


def measure(rows, etl):
    result = []
    for field in etl.WRITE_COLUMNS:
        values = [
            mysql_text(field, row[field], etl)
            for row in rows
            if row.get(field) is not None
        ]
        char_lengths = [len(v) for v in values]
        byte_lengths = [len(v.encode("utf-8")) for v in values]
        mysql_type, charset, declared_bytes = recommend(
            field, max(char_lengths, default=0), etl
        )
        result.append({
            "field": field,
            "category": (
                "AREA" if field in etl.AREA_COLUMNS
                else "JSON" if field in etl.JSON_COLUMNS
                else "DATE" if field == "release_date"
                else "STRING"
            ),
            "non_null_count": len(values),
            "max_char_length": max(char_lengths, default=0),
            "p95_char_length": percentile(char_lengths, 95),
            "p99_char_length": percentile(char_lengths, 99),
            "max_utf8_bytes": max(byte_lengths, default=0),
            "recommended_mysql_type": mysql_type,
            "charset": charset,
            "declared_row_bytes": declared_bytes,
        })
    return result


def estimate_row_size(columns, etl):
    # Conservative DDL-width estimate: maximum VARCHAR bytes, plus NULL bitmap
    # and modest record overhead. JSON/TEXT use an external-value pointer estimate.
    nullable_bitmap = math.ceil(len(etl.WRITE_COLUMNS) / 8)
    overhead = 32
    declared = sum(x["declared_row_bytes"] for x in columns)
    total = declared + nullable_bitmap + overhead
    status = (
        "PASS_WITH_MARGIN" if total <= ROW_WARNING_LIMIT
        else "NEAR_LIMIT" if total <= ROW_LIMIT
        else "OVER_LIMIT"
    )
    return total, nullable_bitmap, overhead, status


def write_csv(path: Path, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "field", "category", "non_null_count", "max_char_length",
        "p95_char_length", "p99_char_length", "max_utf8_bytes",
        "recommended_mysql_type", "charset", "declared_row_bytes",
    ]
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(columns)
    os.replace(tmp, path)


def self_test():
    class FakeETL:
        WRITE_COLUMNS = (
            "integration_total_area_mm2",
            "device_design_attributes",
            "device_model",
            "release_date",
        )
        AREA_COLUMNS = ("integration_total_area_mm2",)
        JSON_COLUMNS = ("device_design_attributes",)

        @staticmethod
        def canonical_json(value):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    rows = [{
        "integration_total_area_mm2": "42.77,16.59",
        "device_design_attributes": {"private_value": "DO_NOT_EXPORT"},
        "device_model": "PRIVATE_DEVICE_MODEL",
        "release_date": date(2026, 11, 14),
    }]
    columns = measure(rows, FakeETL)
    by_name = {x["field"]: x for x in columns}
    assert by_name["integration_total_area_mm2"]["max_char_length"] == 11
    assert by_name["integration_total_area_mm2"]["recommended_mysql_type"].startswith("VARCHAR(")
    assert by_name["device_design_attributes"]["recommended_mysql_type"] == "JSON"
    assert by_name["release_date"]["recommended_mysql_type"] == "DATE"

    with tempfile.TemporaryDirectory(prefix="cis_length_selftest_") as tmp:
        path = Path(tmp) / "audit.csv"
        write_csv(path, columns)
        output = path.read_text(encoding="utf-8-sig")
        assert "DO_NOT_EXPORT" not in output
        assert "PRIVATE_DEVICE_MODEL" not in output
    print("SELF_TEST=PASS length=PASS json=PASS date=PASS redaction=PASS")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Read-only value-length measurement using CIS ETL v1.0.2"
    )
    parser.add_argument("--etl-script", required=True)
    parser.add_argument("--output", default="cis_column_length_audit.csv")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.limit is not None and args.limit <= 0:
        print("AUDIT=FAILED error=INVALID_LIMIT", file=sys.stderr)
        return 2
    if args.self_test:
        self_test()
        return 0

    client = None
    phase = "LOAD_ETL"
    try:
        etl = load_etl(Path(args.etl_script).expanduser().resolve())
        phase = "MONGODB_CONNECT"
        mongo_args = SimpleNamespace(
            mongo_uri="",
            mongo_server_selection_timeout_ms=10000,
            mongo_connect_timeout_ms=10000,
            mongo_socket_timeout_ms=60000,
            mongo_max_pool_size=10,
            mongo_batch_size=300,
            mongo_max_time_ms=120000,
            limit=args.limit,
        )
        uri = etl.acquire_mongo_uri(mongo_args)
        client, database_name = etl.connect_mongo_primary(uri, mongo_args)

        phase = "MONGODB_READ"
        collection = client[database_name][etl.MONGO_COLLECTION]
        documents = etl.read_mongo_documents(collection, mongo_args)

        phase = "ETL_TRANSFORM"
        rows, stats = etl.build_targets(documents)

        phase = "MEASURE_LENGTHS"
        columns = measure(rows, etl)
        row_bytes, null_bytes, overhead_bytes, row_status = estimate_row_size(columns, etl)
        write_csv(Path(args.output).expanduser().resolve(), columns)

        area_count = sum(1 for x in columns if x["category"] == "AREA")
        json_count = sum(1 for x in columns if x["category"] == "JSON")
        text_count = sum(1 for x in columns if x["recommended_mysql_type"].startswith("TEXT"))
        varchar_count = sum(1 for x in columns if x["recommended_mysql_type"].startswith("VARCHAR"))

        print(
            f"AUDIT=PASS documents={stats['scanned_documents']} "
            f"device_rows={len(rows)} columns={len(columns)} "
            f"area_columns={area_count} json_columns={json_count} "
            f"varchar={varchar_count} text={text_count}"
        )
        print(
            f"ROW_SIZE_ESTIMATE_BYTES={row_bytes} "
            f"NULL_BITMAP_BYTES={null_bytes} RECORD_OVERHEAD_BYTES={overhead_bytes} "
            f"INNODB_LIMIT_BYTES={ROW_LIMIT} STATUS={row_status}"
        )
        print(f"CSV_REPORT={Path(args.output).name}")
        print(
            "MONGODB_READ_ONLY=YES MONGODB_WRITE=NO "
            "MYSQL_CONNECT=NO MYSQL_WRITE=NO SENSITIVE_VALUES=REDACTED"
        )
        return 0
    except Exception as exc:
        print(
            f"AUDIT=FAILED phase={phase} error_type={type(exc).__name__} "
            "exception_details=REDACTED",
            file=sys.stderr,
        )
        return 2
    finally:
        if client is not None:
            client.close()
            print("MONGODB_CLIENT_CLOSED=PASS")


if __name__ == "__main__":
    raise SystemExit(main())
