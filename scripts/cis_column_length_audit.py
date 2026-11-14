#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only MongoDB field-length audit for CIS integration wide-table DDL.

Uses the approved ETL v1.0.2 transform and mapping. It only issues MongoDB
ping/find operations, never writes to MongoDB, and never connects to MySQL.
Reports contain aggregate lengths and field metadata only; no source values,
device identities, ObjectIds, host addresses, credentials, or connection URIs.
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
from typing import Any

ETL_DEFAULT = "sn_cis_integration_competition_wide_mongo_to_mysql_v1_0_2.py"
EXPECTED_ETL_VERSION = "1.0.2"
EXPECTED_LOGICAL_COLUMNS = 285
EXPECTED_AREA_COLUMNS = 267
INNODB_ROW_LIMIT = 8126
ROW_SIZE_SAFETY_LIMIT = 7000
TEXT_INLINE_THRESHOLD_BYTES = 40
TEXT_POINTER_BYTES = 20
VARCHAR_BUCKETS = (12, 16, 24, 32, 48, 64, 96, 128, 192, 255, 384, 512)

# Preserve established identity widths from the approved V4 DDL. The audit may
# recommend TEXT only if observed data plus headroom exceeds this safe cap.
IDENTITY_BASELINES = {
    "manufacturer": 64,
    "platform": 128,
    "device_type": 32,
    "device_model": 128,
}
SCALAR_BASELINES = {
    "release_date_text": 64,
    "area_unit": 16,
    "semantic_version": 32,
}
NON_NULL_COLUMNS = {
    "id", "manufacturer", "platform", "device_type", "device_model",
    "area_unit", "semantic_version",
}


def load_etl(path: Path):
    if not path.is_file():
        raise RuntimeError("ETL_SOURCE_NOT_FOUND")
    spec = importlib.util.spec_from_file_location("cis_etl_v102_for_audit", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError("ETL_IMPORT_FAILED")

    # The ETL module initializes file + console logging at import time. Replace
    # FileHandler during import, then detach all handlers: audit must not create
    # or append to the ETL log, and transform diagnostics stay silent here.
    original_file_handler = logging.FileHandler
    logging.FileHandler = lambda *args, **kwargs: logging.NullHandler()
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        logging.FileHandler = original_file_handler

    logger = getattr(module, "LOG", None)
    if logger is not None:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        logger.addHandler(logging.NullHandler())
        logger.propagate = False

    if getattr(module, "ETL_VERSION", None) != EXPECTED_ETL_VERSION:
        raise RuntimeError("ETL_VERSION_MISMATCH")
    if len(getattr(module, "EXPECTED_COLUMNS", ())) != EXPECTED_LOGICAL_COLUMNS:
        raise RuntimeError("ETL_SCHEMA_COLUMN_COUNT_MISMATCH")
    if len(getattr(module, "AREA_COLUMNS", ())) != EXPECTED_AREA_COLUMNS:
        raise RuntimeError("ETL_AREA_COLUMN_COUNT_MISMATCH")
    return module


def resolve_etl_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    candidates = [
        Path(__file__).resolve().with_name(ETL_DEFAULT),
        Path.cwd() / ETL_DEFAULT,
    ]
    # User's established local execution directory; harmless if absent.
    if os.name == "nt":
        candidates.append(Path(r"E:\work\tmp\20261008") / ETL_DEFAULT)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("ETL_SOURCE_NOT_FOUND; pass --etl-script with approved v1.0.2 path")


def value_to_text(value, etl) -> str:
    if isinstance(value, (dict, list, tuple)):
        return etl.canonical_json(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        # Preserve only the byte count; never decode or emit source bytes.
        return "\0" * len(value)
    return str(value)


def percentile(sorted_values: list[int], pct: float) -> int:
    if not sorted_values:
        return 0
    rank = max(1, math.ceil((pct / 100.0) * len(sorted_values)))
    return sorted_values[min(rank - 1, len(sorted_values) - 1)]


def choose_varchar_length(max_chars: int, *, minimum: int = 16, absolute_margin: int = 8) -> int | None:
    # At least 50% growth headroom, plus a configurable absolute margin.
    target = max(minimum, max_chars + max(absolute_margin, math.ceil(max_chars * 0.5)))
    for bucket in VARCHAR_BUCKETS:
        if bucket >= target:
            return bucket
    return None


def classify_column(field: str, etl) -> str:
    if field == "id":
        return "PRIMARY_KEY"
    if field in etl.MANAGED_COLUMNS:
        return "SCHEMA_MANAGED"
    if field in etl.IDENTITY_COLUMNS:
        return "DEVICE_IDENTITY"
    if field in etl.JSON_COLUMNS:
        return "JSON_EXTENSION"
    if field in etl.AREA_COLUMNS:
        return "AREA_VALUE"
    if field in {"release_date", "created_at", "updated_at"}:
        return "DATETIME"
    if field == "source_document_id":
        return "SOURCE_REFERENCE"
    return "SCALAR_TEXT"


def _varchar_recommendation(max_chars: int, *, minimum: int, charset: str,
                            max_varchar: int = 512, absolute_margin: int = 8) -> dict[str, Any]:
    length = choose_varchar_length(max_chars, minimum=minimum, absolute_margin=absolute_margin)
    if length is None or length > max_varchar:
        return {
            "recommended_type": f"TEXT CHARACTER SET {charset}",
            "charset": charset,
            "confidence": "OBSERVED_MAX_PLUS_HEADROOM_TEXT",
            "declared_max_bytes": TEXT_POINTER_BYTES,
        }
    bytes_per_char = 1 if charset == "ascii" else 4
    prefix = 1 if length * bytes_per_char <= 255 else 2
    collation = "ascii_bin" if charset == "ascii" else None
    suffix = f" COLLATE {collation}" if collation else ""
    return {
        "recommended_type": f"VARCHAR({length}) CHARACTER SET {charset}{suffix}",
        "charset": charset,
        "confidence": "OBSERVED_MAX_PLUS_HEADROOM",
        "declared_max_bytes": length * bytes_per_char + prefix,
    }


def recommend_column(field: str, stat: dict, etl) -> dict[str, Any]:
    role = stat["role"]
    max_chars = stat["max_char_length"]

    if role == "PRIMARY_KEY":
        return {"recommended_type": "BIGINT UNSIGNED", "charset": "n/a",
                "confidence": "SCHEMA_FIXED", "declared_max_bytes": 8}
    if role == "SCHEMA_MANAGED":
        return {"recommended_type": "DATETIME(3)", "charset": "n/a",
                "confidence": "SCHEMA_FIXED", "declared_max_bytes": 7}
    if role == "DATETIME":
        return {"recommended_type": "DATETIME(3)", "charset": "n/a",
                "confidence": "SCHEMA_FIXED", "declared_max_bytes": 7}
    if role == "JSON_EXTENSION":
        return {"recommended_type": "JSON", "charset": "utf8mb4 (JSON internal)",
                "confidence": "SEMANTIC_TYPE_FIXED", "declared_max_bytes": TEXT_POINTER_BYTES}
    if role == "SOURCE_REFERENCE":
        return {"recommended_type": "CHAR(24) CHARACTER SET ascii COLLATE ascii_bin",
                "charset": "ascii", "confidence": "SCHEMA_FIXED",
                "declared_max_bytes": 24}
    if role == "AREA_VALUE":
        # ETL v1.0.2 normalizes numeric area observations to ASCII digits,
        # decimal point, sign and comma; source annotations are stored elsewhere.
        return _varchar_recommendation(max_chars, minimum=12, charset="ascii", max_varchar=512, absolute_margin=4)
    if role == "DEVICE_IDENTITY":
        baseline = IDENTITY_BASELINES[field]
        recommendation = _varchar_recommendation(
            max(max_chars, baseline - max(8, math.ceil(baseline * 0.5))),
            minimum=baseline, charset="utf8mb4", max_varchar=512,
        )
        recommendation["confidence"] = "EXISTING_DDL_BASELINE_AND_OBSERVED"
        return recommendation

    minimum = SCALAR_BASELINES.get(field, 32)
    return _varchar_recommendation(max_chars, minimum=minimum,
                                   charset="utf8mb4", max_varchar=512)


def build_column_stats(rows: list[dict], etl) -> list[dict]:
    results = []
    total = len(rows)
    for field in etl.EXPECTED_COLUMNS:
        role = classify_column(field, etl)
        lengths_chars: list[int] = []
        lengths_bytes: list[int] = []
        multivalue_count = 0
        max_observations = 0

        # id/created_at/updated_at are generated/managed by MySQL and are not
        # populated by the source transform. Do not misreport them as source NULLs.
        if field not in etl.MANAGED_COLUMNS:
            for row in rows:
                value = row.get(field)
                if value is None:
                    continue
                text = value_to_text(value, etl)
                char_len = len(text)
                byte_len = len(text.encode("utf-8"))
                lengths_chars.append(char_len)
                lengths_bytes.append(byte_len)
                if field in etl.AREA_COLUMNS:
                    observations = text.count(",") + 1
                    if observations > 1:
                        multivalue_count += 1
                    max_observations = max(max_observations, observations)

        chars_sorted = sorted(lengths_chars)
        bytes_sorted = sorted(lengths_bytes)
        stat = {
            "field": field,
            "role": role,
            "row_count": total,
            "observed_in_source": field not in etl.MANAGED_COLUMNS,
            "non_null_count": len(lengths_chars) if field not in etl.MANAGED_COLUMNS else None,
            "null_count": total - len(lengths_chars) if field not in etl.MANAGED_COLUMNS else None,
            "max_char_length": max(chars_sorted, default=0),
            "p95_char_length": percentile(chars_sorted, 95),
            "p99_char_length": percentile(chars_sorted, 99),
            "max_utf8_bytes": max(bytes_sorted, default=0),
            "p95_utf8_bytes": percentile(bytes_sorted, 95),
            "p99_utf8_bytes": percentile(bytes_sorted, 99),
            "multivalue_count": multivalue_count if field in etl.AREA_COLUMNS else 0,
            "max_observations": max_observations if field in etl.AREA_COLUMNS else 0,
        }
        stat.update(recommend_column(field, stat, etl))
        results.append(stat)
    return results


def estimate_row_width(column_stats: list[dict], rows: list[dict], etl) -> dict:
    # InnoDB DDL row-size estimate: VARCHAR maximum bytes + length prefix;
    # TEXT/JSON are represented by a 20-byte external-value pointer.
    declared = sum(item["declared_max_bytes"] for item in column_stats)
    nullable_count = len(etl.EXPECTED_COLUMNS) - len(NON_NULL_COLUMNS)
    null_bitmap_bytes = math.ceil(nullable_count / 8)
    overhead_bytes = 32
    ddl_estimate = declared + null_bitmap_bytes + overhead_bytes

    # Observed DYNAMIC-row estimate: values <=40 bytes may remain inline;
    # longer TEXT/JSON values can be represented by a 20-byte external pointer.
    recommendation_by_field = {item["field"]: item for item in column_stats}
    observed_inline_sizes = []
    for row in rows:
        size = 0
        for field in etl.EXPECTED_COLUMNS:
            if field in etl.MANAGED_COLUMNS:
                continue
            value = row.get(field)
            if value is None:
                continue
            raw_size = len(value_to_text(value, etl).encode("utf-8"))
            rec_type = recommendation_by_field[field]["recommended_type"]
            if rec_type.startswith(("TEXT", "JSON")):
                size += raw_size if raw_size <= TEXT_INLINE_THRESHOLD_BYTES else TEXT_POINTER_BYTES
            elif rec_type.startswith("VARCHAR"):
                size += raw_size + (1 if raw_size <= 255 else 2)
            elif rec_type.startswith("CHAR"):
                size += recommendation_by_field[field]["declared_max_bytes"]
            elif rec_type.startswith("DATETIME"):
                size += 7
            elif rec_type.startswith("BIGINT"):
                size += 8
        observed_inline_sizes.append(size + null_bitmap_bytes + overhead_bytes)

    observed_sorted = sorted(observed_inline_sizes)
    if ddl_estimate <= ROW_SIZE_SAFETY_LIMIT and max(observed_inline_sizes, default=0) <= ROW_SIZE_SAFETY_LIMIT:
        status = "PASS_WITH_MARGIN"
    elif ddl_estimate <= INNODB_ROW_LIMIT and max(observed_inline_sizes, default=0) <= INNODB_ROW_LIMIT:
        status = "NEAR_LIMIT"
    else:
        status = "OVER_LIMIT"

    area_bytes = sum(item["declared_max_bytes"] for item in column_stats if item["role"] == "AREA_VALUE")
    return {
        "innodb_reference_row_limit_bytes": INNODB_ROW_LIMIT,
        "safety_target_bytes": ROW_SIZE_SAFETY_LIMIT,
        "estimated_ddl_max_row_bytes": ddl_estimate,
        "estimated_area_declared_bytes": area_bytes,
        "estimated_non_area_declared_bytes": declared - area_bytes,
        "nullable_bitmap_estimate_bytes": null_bitmap_bytes,
        "record_overhead_allowance_bytes": overhead_bytes,
        "max_observed_inline_row_bytes_estimate": max(observed_inline_sizes, default=0),
        "p95_observed_inline_row_bytes_estimate": percentile(observed_sorted, 95),
        "p99_observed_inline_row_bytes_estimate": percentile(observed_sorted, 99),
        "status": status,
        "note": "Estimate only; validate CREATE TABLE on MySQL 8.0.32 with ROW_FORMAT=DYNAMIC. TEXT/JSON use a 20-byte DDL pointer estimate; short observed values may remain inline.",
    }


def write_reports(output_dir: Path, column_stats: list[dict], summary: dict, etl) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "cis_column_length_audit.csv"
    json_path = output_dir / "cis_column_type_recommendation.json"
    columns = [
        "field", "role", "row_count", "observed_in_source", "non_null_count", "null_count",
        "max_char_length", "p95_char_length", "p99_char_length",
        "max_utf8_bytes", "p95_utf8_bytes", "p99_utf8_bytes",
        "multivalue_count", "max_observations", "recommended_type",
        "charset", "confidence", "declared_max_bytes",
    ]
    csv_tmp = csv_path.with_suffix(".csv.tmp")
    with csv_tmp.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(column_stats)
    os.replace(csv_tmp, csv_path)

    payload = {
        "audit_version": "1.1.0",
        "etl_version": etl.ETL_VERSION,
        "target_table": etl.TABLE,
        "source_collection": etl.MONGO_COLLECTION,
        "read_only": True,
        "mysql_connection": False,
        "mongo_write": False,
        "sensitive_values_included": False,
        "summary": summary,
        "columns": column_stats,
    }
    json_tmp = json_path.with_suffix(".json.tmp")
    json_tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(json_tmp, json_path)
    return csv_path, json_path


def run_self_test(etl):
    row = {field: None for field in etl.EXPECTED_COLUMNS}
    row.update({
        "manufacturer": "SENSITIVE_MANUFACTURER",
        "platform": "SENSITIVE_PLATFORM",
        "device_type": "phone",
        "device_model": "SENSITIVE_MODEL",
        "source_document_id": "0123456789abcdef01234567",
        "integration_total_area_mm2": "42.77,16.59",
        "wireless_antenna_ant10_ant_11_area_mm2": "42.77,16.59,9.38",
        "device_design_attributes": {"secret": "SENSITIVE_JSON_VALUE"},
    })
    stats = build_column_stats([row], etl)
    by_name = {item["field"]: item for item in stats}
    assert len(stats) == EXPECTED_LOGICAL_COLUMNS == len(etl.EXPECTED_COLUMNS)
    assert len(etl.AREA_COLUMNS) == EXPECTED_AREA_COLUMNS
    assert by_name["integration_total_area_mm2"]["multivalue_count"] == 1
    assert by_name["wireless_antenna_ant10_ant_11_area_mm2"]["max_observations"] == 3
    assert by_name["integration_total_area_mm2"]["recommended_type"].startswith("VARCHAR(")
    assert by_name["device_design_attributes"]["recommended_type"] == "JSON"
    assert by_name["id"]["observed_in_source"] is False

    long_row = dict(row)
    long_row["integration_total_area_mm2"] = "1" * 700
    long_stats = build_column_stats([long_row], etl)
    long_area = next(x for x in long_stats if x["field"] == "integration_total_area_mm2")
    assert long_area["recommended_type"].startswith("TEXT")

    width = estimate_row_width(stats, [row], etl)
    with tempfile.TemporaryDirectory(prefix="cis_audit_selftest_") as tmp:
        csv_path, json_path = write_reports(Path(tmp), stats, {"row_width_estimate": width}, etl)
        combined = csv_path.read_text(encoding="utf-8-sig") + json_path.read_text(encoding="utf-8")
        for sentinel in ("SENSITIVE_MANUFACTURER", "SENSITIVE_PLATFORM", "SENSITIVE_MODEL", "SENSITIVE_JSON_VALUE"):
            assert sentinel not in combined
        assert "0123456789abcdef01234567" not in combined
    print("SELF_TEST=PASS columns=285 area_columns=267 multivalue=PASS varchar_text_classification=PASS row_width_estimate=PASS redaction=PASS mysql_connect=NO mongo_write=NO")


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only CIS MongoDB field-length audit")
    parser.add_argument("--etl-script", default=None, help="approved ETL v1.0.2 source path")
    parser.add_argument("--auth-source", default="admin", help="MongoDB authentication database; default admin")
    parser.add_argument("--mongo-server-selection-timeout-ms", type=int, default=10000)
    parser.add_argument("--mongo-connect-timeout-ms", type=int, default=10000)
    parser.add_argument("--mongo-socket-timeout-ms", type=int, default=60000)
    parser.add_argument("--mongo-max-pool-size", type=int, default=10)
    parser.add_argument("--mongo-batch-size", type=int, default=300)
    parser.add_argument("--mongo-max-time-ms", type=int, default=120000)
    parser.add_argument("--limit", type=int, default=None, help="optional sample limit; omit for full scan")
    parser.add_argument("--output-dir", default=str(Path.cwd() / "cis_column_audit_output"))
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def open_mongo_read_only(etl, args):
    try:
        import pymongo
    except ImportError:
        raise RuntimeError("DEPENDENCY_MISSING_PYMONGO") from None

    password = getpass.getpass("MongoDB root 密码（仅密码，隐藏输入）: ")
    if not password:
        raise RuntimeError("EMPTY_MONGO_PASSWORD")

    client = pymongo.MongoClient(
        host=list(etl.MONGO_SEEDS),
        username=etl.MONGO_DEFAULT_USER,
        password=password,
        authSource=args.auth_source,
        replicaSet=etl.MONGO_REPLICA_SET,
        readPreference="primary",
        serverSelectionTimeoutMS=args.mongo_server_selection_timeout_ms,
        connectTimeoutMS=args.mongo_connect_timeout_ms,
        socketTimeoutMS=args.mongo_socket_timeout_ms,
        maxPoolSize=args.mongo_max_pool_size,
        appname="cis-column-length-audit-readonly",
        retryReads=True,
    )
    try:
        # Ping and find are the only MongoDB operations used by this program.
        client.admin.command("ping")
        return client
    except Exception:
        client.close()
        raise


def run_audit(args, etl):
    client = None
    cursor = None
    try:
        client = open_mongo_read_only(etl, args)
        database = client[etl.MONGO_DATABASE]
        collection = database[etl.MONGO_COLLECTION]
        cursor = collection.find(
            {},
            batch_size=args.mongo_batch_size,
            max_time_ms=args.mongo_max_time_ms,
        )
        if args.limit is not None:
            cursor = cursor.limit(args.limit)

        rows, transform_summary = etl.build_targets(cursor)
        column_stats = build_column_stats(rows, etl)
        row_width = estimate_row_width(column_stats, rows, etl)
        observed_rows = {
            "max_observed_source_payload_bytes": max(
                (sum(len(value_to_text(row.get(field), etl).encode("utf-8"))
                     for field in etl.WRITE_COLUMNS if row.get(field) is not None)
                 for row in rows), default=0
            ),
        }
        recommendation_counts = {
            "VARCHAR": sum(1 for item in column_stats if item["recommended_type"].startswith("VARCHAR")),
            "TEXT": sum(1 for item in column_stats if item["recommended_type"].startswith("TEXT")),
            "JSON": sum(1 for item in column_stats if item["recommended_type"] == "JSON"),
            "fixed_types": sum(1 for item in column_stats if item["confidence"] in {"SCHEMA_FIXED", "SEMANTIC_TYPE_FIXED"}),
        }
        summary = {
            "source_documents_scanned": transform_summary["scanned_documents"],
            "documents_without_integration_level": transform_summary["documents_without_integration_level"],
            "transformed_device_rows": transform_summary["target_devices"],
            "area_columns": len(etl.AREA_COLUMNS),
            "json_columns": len(etl.JSON_COLUMNS),
            "logical_columns": len(etl.EXPECTED_COLUMNS),
            "multivalue_area_cells": transform_summary["multivalue_area_cells"],
            "multivalue_extra_observations": transform_summary["multivalue_extra_observations"],
            "scan_limited": args.limit is not None,
            "row_width_estimate": row_width,
            "observed_payload": observed_rows,
            "recommendation_counts": recommendation_counts,
        }
        csv_path, json_path = write_reports(Path(args.output_dir).resolve(), column_stats, summary, etl)
        scope = "SAMPLE" if args.limit is not None else "FULL"
        print(
            f"AUDIT=PASS scope={scope} documents={summary['source_documents_scanned']} "
            f"device_rows={summary['transformed_device_rows']} columns={summary['logical_columns']} "
            f"area_columns={summary['area_columns']} varchar={recommendation_counts['VARCHAR']} "
            f"text={recommendation_counts['TEXT']} row_width_status={row_width['status']} "
            "SENSITIVE_VALUES=REDACTED CONNECTION_DETAILS=REDACTED MYSQL_CONNECT=NO MONGODB_WRITE=NO"
        )
        print(f"CSV_REPORT={csv_path.name}")
        print(f"JSON_REPORT={json_path.name}")
        return 0
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if client is not None:
            client.close()
            print("MONGODB_CLIENT_CLOSED=PASS")


def main():
    args = parse_args()
    if args.limit is not None and args.limit <= 0:
        print("AUDIT=FAILED error=INVALID_LIMIT", file=sys.stderr)
        return 2
    for name in (
        "mongo_server_selection_timeout_ms", "mongo_connect_timeout_ms",
        "mongo_socket_timeout_ms", "mongo_max_pool_size", "mongo_batch_size",
        "mongo_max_time_ms",
    ):
        if getattr(args, name) <= 0:
            print("AUDIT=FAILED error=INVALID_MONGO_OPTION", file=sys.stderr)
            return 2
    try:
        etl_path = resolve_etl_path(args.etl_script)
        etl = load_etl(etl_path)
        if args.self_test:
            run_self_test(etl)
            return 0
        return run_audit(args, etl)
    except Exception as exc:
        # Never print exception messages: driver exceptions may include hostnames,
        # URIs, authentication details, document fragments, or raw values.
        error_type = type(exc).__name__
        safe_codes = {
            "ETL_SOURCE_NOT_FOUND", "ETL_IMPORT_FAILED", "ETL_VERSION_MISMATCH",
            "ETL_SCHEMA_COLUMN_COUNT_MISMATCH", "ETL_AREA_COLUMN_COUNT_MISMATCH",
            "DEPENDENCY_MISSING_PYMONGO", "EMPTY_MONGO_PASSWORD",
        }
        error_code = str(exc) if str(exc) in safe_codes else "REDACTED"
        print(f"AUDIT=FAILED error_type={error_type} error_code={error_code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
