"""Phase 9 runtime schema context helper for semantic_plan_validator.

Builds a transient schema_runtime_context_contract immediately before validation.
It never persists full schema metadata or alias index into compact conversation context.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

CONTRACT_VERSION = "schema_runtime_context_contract"


def _loads(v: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except Exception:
            return default
    return default


def _dumps(v: Any) -> str:
    return json.dumps(v if v is not None else {}, ensure_ascii=False, separators=(",", ":"))


def _str(v: Any) -> str:
    return str(v or "").strip()


def _list(v: Any) -> List[Any]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [x for x in v if x is not None and _str(x)]
    if isinstance(v, str):
        parsed = _loads(v, None)
        if isinstance(parsed, list):
            return [x for x in parsed if x is not None and _str(x)]
        return [x.strip() for x in v.split(",") if x.strip()]
    return [v]


def _add(xs: List[str], value: Any) -> None:
    s = _str(value)
    if s and s not in xs:
        xs.append(s)


def _collections_from_plan(plan: Dict[str, Any]) -> Tuple[List[str], str]:
    out: List[str] = []
    primary = _str(plan.get("primary_collection"))
    _add(out, primary)
    for rc in _list(plan.get("related_collections")):
        _add(out, rc)
    stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    for stage in stages:
        if isinstance(stage, dict):
            _add(out, stage.get("collection"))
            _add(out, stage.get("primary_collection"))
            for rc in _list(stage.get("related_collections")):
                _add(out, rc)
    return out, primary


def _collections_from_selection(selection: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    _add(out, selection.get("selected_primary_collection"))
    for key in ("selected_related_collections", "related_collections", "candidate_collections"):
        for c in _list(selection.get(key)):
            if isinstance(c, dict):
                _add(out, c.get("collection") or c.get("name"))
            else:
                _add(out, c)
    return out


def _collections_from_compact(compact: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    ref = compact.get("schema_context_ref") if isinstance(compact.get("schema_context_ref"), dict) else {}
    for c in _list(ref.get("collections")):
        _add(out, c)
    _add(out, compact.get("last_primary_collection"))
    for c in _list(compact.get("last_related_collections")):
        _add(out, c)
    return out


def _schema_info(schema_raw: Any) -> Tuple[Dict[str, Any], bool, str]:
    schema = _loads(schema_raw, {})
    if not isinstance(schema, dict) or not schema:
        return {}, False, "empty_schema_metadata"
    collections = schema.get("collections")
    if not isinstance(collections, dict) or not collections:
        return schema, False, "missing_schema_metadata_collections"
    return schema, True, ""


def _alias_ready(alias_raw: Any) -> Tuple[Dict[str, Any], bool, str]:
    alias_index = _loads(alias_raw, {})
    if not isinstance(alias_index, dict) or not alias_index:
        return {}, False, "empty_schema_alias_index"
    return alias_index, True, ""


def _primary_in_schema(schema: Dict[str, Any], primary: str) -> bool:
    if not primary:
        return True
    collections = schema.get("collections") if isinstance(schema, dict) else {}
    return isinstance(collections, dict) and primary in collections


def _ref(compact: Dict[str, Any]) -> Dict[str, Any]:
    return compact.get("schema_context_ref") if isinstance(compact.get("schema_context_ref"), dict) else {}


def _result(**kwargs: Any) -> Dict[str, Any]:
    base = {
        "contract_version": CONTRACT_VERSION,
        "schema_context_ready": False,
        "schema_hydration_needed": False,
        "schema_hydration_reason": "",
        "schema_hydration_collections": [],
        "schema_metadata_json": "{}",
        "schema_alias_index_json": "{}",
        "schema_digest": "",
        "schema_version": "",
        "catalog_digest": "",
        "schema_source": "missing",
        "schema_context_ref": {"collections": [], "schema_digest": "", "schema_version": "", "catalog_digest": ""},
        "warnings": [],
        "errors": [],
    }
    base.update(kwargs)
    if not isinstance(base.get("schema_context_ref"), dict):
        base["schema_context_ref"] = {
            "collections": base.get("schema_hydration_collections") or [],
            "schema_digest": base.get("schema_digest") or "",
            "schema_version": base.get("schema_version") or "",
            "catalog_digest": base.get("catalog_digest") or "",
        }
    base["validator_schema_context_ready"] = bool(base["schema_context_ready"])
    base["schema_runtime_context_json"] = _dumps({k: v for k, v in base.items() if k not in {"schema_runtime_context_json"}})
    return base


def prepare_validator_schema_context(
    semantic_plan_json: Any = "{}",
    schema_metadata_json: Any = "{}",
    schema_alias_index_json: Any = "{}",
    collection_selection_json: Any = "{}",
    compact_context_json: Any = "{}",
    legacy_schema_metadata_json: Any = "{}",
    legacy_schema_alias_index_json: Any = "{}",
) -> Dict[str, Any]:
    warnings: List[str] = []
    errors: List[str] = []
    plan = _loads(semantic_plan_json, {})
    if not isinstance(plan, dict):
        plan = {}
    selection = _loads(collection_selection_json, {})
    if not isinstance(selection, dict):
        selection = {}
    compact = _loads(compact_context_json, {})
    if not isinstance(compact, dict):
        compact = {}
    ref = _ref(compact)

    plan_cols, primary = _collections_from_plan(plan)
    collections: List[str] = []
    for source in (plan_cols, _collections_from_selection(selection), _collections_from_compact(compact)):
        for c in source:
            _add(collections, c)

    schema, schema_ok, schema_reason = _schema_info(schema_metadata_json)
    alias_index, alias_ok, alias_reason = _alias_ready(schema_alias_index_json)
    digest = _str(schema.get("schema_digest")) if isinstance(schema, dict) else ""
    version = _str(schema.get("schema_version")) if isinstance(schema, dict) else ""
    catalog_digest = _str(schema.get("catalog_digest") or ref.get("catalog_digest")) if isinstance(schema, dict) else _str(ref.get("catalog_digest"))
    ref_digest = _str(ref.get("schema_digest"))
    ref_version = _str(ref.get("schema_version"))

    if ref_digest and digest and ref_digest != digest:
        warnings.append("schema_digest_mismatch_between_runtime_and_compact_ref")
    if ref_version and version and ref_version != version:
        warnings.append("schema_version_mismatch_between_runtime_and_compact_ref")

    needs_reasons: List[str] = []
    if not schema_ok:
        needs_reasons.append(schema_reason)
    if not alias_ok:
        needs_reasons.append(alias_reason)
    if schema_ok and primary and not _primary_in_schema(schema, primary):
        needs_reasons.append("schema_metadata_missing_plan_primary_collection")
    if ref_digest and not digest:
        needs_reasons.append("compact_ref_has_schema_digest_but_runtime_digest_missing")

    if schema_ok and alias_ok and _primary_in_schema(schema, primary):
        return _result(
            schema_context_ready=True,
            schema_hydration_needed=False,
            schema_hydration_reason="",
            schema_hydration_collections=[],
            schema_metadata_json=_dumps(schema),
            schema_alias_index_json=_dumps(alias_index),
            schema_digest=digest,
            schema_version=version,
            catalog_digest=catalog_digest,
            schema_source="current_runtime",
            schema_context_ref={"collections": collections, "schema_digest": digest, "schema_version": version, "catalog_digest": catalog_digest},
            warnings=warnings,
            errors=[],
        )

    legacy_schema, legacy_schema_ok, _ = _schema_info(legacy_schema_metadata_json)
    legacy_alias, legacy_alias_ok, _ = _alias_ready(legacy_schema_alias_index_json)
    if legacy_schema_ok and legacy_alias_ok and _primary_in_schema(legacy_schema, primary):
        warnings.append("legacy_conversation_schema_fallback_used_runtime_only")
        return _result(
            schema_context_ready=True,
            schema_hydration_needed=False,
            schema_hydration_reason="legacy_conversation_compat",
            schema_hydration_collections=[],
            schema_metadata_json=_dumps(legacy_schema),
            schema_alias_index_json=_dumps(legacy_alias),
            schema_digest=_str(legacy_schema.get("schema_digest") or digest or ref_digest),
            schema_version=_str(legacy_schema.get("schema_version") or version or ref_version),
            catalog_digest=_str(legacy_schema.get("catalog_digest") or catalog_digest),
            schema_source="legacy_conversation_compat",
            schema_context_ref={"collections": collections, "schema_digest": _str(legacy_schema.get("schema_digest") or digest or ref_digest), "schema_version": _str(legacy_schema.get("schema_version") or version or ref_version), "catalog_digest": _str(legacy_schema.get("catalog_digest") or catalog_digest)},
            warnings=warnings,
            errors=[],
        )

    if not collections:
        errors.append("missing_collections_for_schema_hydration")
        return _result(
            schema_context_ready=False,
            schema_hydration_needed=False,
            schema_hydration_reason="missing_collections_for_schema_hydration",
            schema_hydration_collections=[],
            schema_digest=digest or ref_digest,
            schema_version=version or ref_version,
            catalog_digest=catalog_digest,
            schema_source="invalid" if schema_metadata_json and schema_metadata_json != "{}" else "missing",
            schema_context_ref={"collections": [], "schema_digest": digest or ref_digest, "schema_version": version or ref_version, "catalog_digest": catalog_digest},
            warnings=warnings,
            errors=errors,
        )

    reason = ";".join(dict.fromkeys([r for r in needs_reasons if r])) or "runtime_schema_context_not_ready"
    return _result(
        schema_context_ready=False,
        schema_hydration_needed=True,
        schema_hydration_reason=reason,
        schema_hydration_collections=collections,
        schema_digest=digest or ref_digest,
        schema_version=version or ref_version,
        catalog_digest=catalog_digest,
        schema_source="invalid" if schema_metadata_json and schema_metadata_json != "{}" else "missing",
        schema_context_ref={"collections": collections, "schema_digest": digest or ref_digest, "schema_version": version or ref_version, "catalog_digest": catalog_digest},
        warnings=warnings,
        errors=errors,
    )


def main(**kwargs: Any) -> Dict[str, Any]:
    return prepare_validator_schema_context(**kwargs)


if __name__ == "__main__":
    schema = {"collections": {"orders": {}}, "schema_digest": "d1", "schema_version": "v1"}
    alias = {"field_aliases": {"orders": {}}}
    plan = {"primary_collection": "orders", "related_collections": [], "stages": [{"collection": "orders"}]}
    print(json.dumps(prepare_validator_schema_context(json.dumps(plan), json.dumps(schema), json.dumps(alias)), ensure_ascii=False, indent=2))
