#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure MySQL value lengths using the approved CIS ETL v1.0.2 transform.

Read-only: MongoDB ping/find only. Never connects to MySQL and never writes data.
Reports contain field names and aggregate lengths only; no source values or identities.
"""
from __future__ import annotations

import argparse
import csv
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

BUCKETS = (16, 24, 32, 48, 64, 96, 128, 192, 255, 384, 512, 768, 1024, 2048)
ROW_LIMIT = 8126
ROW_WARNING_LIMIT = 7000


def load_etl(path: Path):
    if not path.is_file():
        raise RuntimeError("ETL_SCRIPT_NOT_FOUND")

    # The ETL creates its own file/console logger on import. A NullHandler keeps
    # this diagnostic from producing a second log containing source diagnostics.
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
    if len(module.EXPECTED_COLUMNS) != 285 or len(module.AREA_COLUMNS) != 267:
        raise RuntimeError("ETL_SCHEMA_MISMATCH")
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


def varchar_recommendation(max_chars, minimum, charset, max_varchar=512):
    target = max(minimum, math.ceil(max_chars * 1.2))
    length = next((n for n in BUCKETS if n >= target), None)
    if length is None or length > max_varchar:
        return f"TEXT CHARACTER SET {charset}", charset, 20
    bytes_per_char = 1 if charset == "ascii" else 4
    prefix = 1 if length * bytes_per_char <= 255 else 2
    return (
        f"VARCHAR({length}) CHARACTER SET {charset}",
        charset,
        length * bytes_per_char + prefix,
    )


def recommend(field, max_chars, etl):
    if field in etl.JSON_COLUMNS:
        return "JSON", "n/a", 20
    if field == "release_date":
        return "DATE", "n/a", 3

    # Preserve the ETL's existing hard limits for identity/source fields.
    fixed = {
        "manufacturer": ("VARCHAR(64) CHARACTER SET utf8mb4", "utf8mb4", 257),
        "platform": ("VARCHAR(128) CHARACTER SET utf8mb4", "utf8mb4", 514),
        "device_type": ("VARCHAR(32) CHARACTER SET utf8mb4", "utf8mb4", 129),
        "device_model": ("VARCHAR(128) CHARACTER SET utf8mb4", "utf8mb4", 514),
        "source_document_id": ("CHAR(24) CHARACTER SET ascii", "ascii", 24),
        "release_date_text": ("VARCHAR(64) CHARACTER SET utf8mb4", "utf8mb4", 258),
    }
    if field in fixed:
        return fixed[field]

    if field in etl.AREA_COLUMNS:
        return varchar_recommendation(max_chars, 16, "ascii", max_varchar=512)
    if field == "device_pic_url":
        # ETL accepts up to 1024 chars; TEXT avoids reserving 1024*4 inline bytes.
        return "TEXT CHARACTER SET utf8mb4", "utf8mb4", 20
    if field == "area_unit":
        return varchar_recommendation(max_chars, 16, "utf8mb4")
    if field == "semantic_version":
        return varchar_recommendation(max_chars, 32, "utf8mb4")
    return varchar_recommendation(max_chars, 32, "utf8mb4")


def measure(rows, etl):
    result = []
    for field in etl.WRITE_COLUMNS:
        values = [
            mysql_text(field, row[field], etl)
            for row in rows
            if row.get(field) is not None
        ]
        char_lengths = [len(value) for value in values]
        byte_lengths = [len(value.encode("utf-8")) for value in values]
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
    declared = sum(column["declared_row_bytes"] for column in columns)
    null_bitmap = math.ceil(len(etl.WRITE_COLUMNS) / 8)
    record_overhead = 32
    total = declared + null_bitmap + record_overhead
    status = (
        "PASS_WITH_MARGIN" if total <= ROW_WARNING_LIMIT
        else "NEAR_LIMIT" if total <= ROW_LIMIT
        else "OVER_LIMIT"
    )
    return total, null_bitmap, record_overhead, status


def write_csv(path: Path, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "field", "category", "non_null_count", "max_char_length",
        "p95_char_length", "p99_char_length", "max_utf8_bytes",
        "recommended_mysql_type", "charset", "declared_row_bytes",
    ]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(columns)
    os.replace(temporary, path)


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
    by_name = {column["field"]: column for column in columns}
    assert by_name["integration_total_area_mm2"]["max_char_length"] == 11
    assert by_name["integration_total_area_mm2"]["recommended_mysql_type"].startswith("VARCHAR(")
    assert by_name["device_design_attributes"]["recommended_mysql_type"] == "JSON"
    assert by_name["release_date"]["recommended_mysql_type"] == "DATE"

    with tempfile.TemporaryDirectory(prefix="cis_length_selftest_") as directory:
        path = Path(directory) / "audit.csv"
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
        output_path = Path(args.output).expanduser().resolve()
        write_csv(output_path, columns)

        area_count = sum(column["category"] == "AREA" for column in columns)
        json_count = sum(column["category"] == "JSON" for column in columns)
        text_count = sum(column["recommended_mysql_type"].startswith("TEXT") for column in columns)
        varchar_count = sum(column["recommended_mysql_type"].startswith("VARCHAR") for column in columns)

        print(
            f"AUDIT=PASS documents={stats['scanned_documents']} "
            f"device_rows={len(rows)} columns={len(columns)} "
            f"area_columns={area_count} json_columns={json_count} "
            f"varchar={varchar_count} text={text_count}"
        )
        print(
            f"ROW_SIZE_ESTIMATE_BYTES={row_bytes} NULL_BITMAP_BYTES={null_bytes} "
            f"RECORD_OVERHEAD_BYTES={overhead_bytes} "
            f"INNODB_LIMIT_BYTES={ROW_LIMIT} STATUS={row_status}"
        )
        print(f"CSV_REPORT={output_path.name}")
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
