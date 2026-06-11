"""Phase 10 schema hydration helpers for validator runtime schema context.

These functions mirror the code embedded in the DSL Phase 10 hydration nodes:
- 代码执行_构建ValidatorSchemaHydration检索任务
- 代码执行_合并HydratedSchema上下文并解析metadata
- 代码执行_准备HydratedValidator运行时Schema上下文

They do not call an LLM and never persist full schema metadata/alias index into
compact conversation context.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from schema_metadata_c_mvp_lib import build_alias_index, extract_schema_metadata

MAX_COLLECTIONS = 3
UNKNOWN = {"", "unknown", "UNKNOWN", "null", "None", "未确定", "未知"}


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


def _list(v: Any) -> List[Any]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        parsed = _loads(v, None)
        if isinstance(parsed, list):
            return parsed
        return [x.strip() for x in v.split(",") if x.strip()]
    return [v]


def _add(out: List[str], value: Any) -> None:
    s = str(value or "").strip()
    if not s or s in UNKNOWN:
        return
    if s not in out:
        out.append(s)


def _plan_cols(plan: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    _add(out, plan.get("primary_collection"))
    for c in _list(plan.get("related_collections")):
        _add(out, c)
    for st in plan.get("stages") or []:
        if isinstance(st, dict):
            _add(out, st.get("collection"))
            _add(out, st.get("primary_collection"))
            for c in _list(st.get("related_collections")):
                _add(out, c)
    return out


def _selection_cols(sel: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    _add(out, sel.get("selected_primary_collection"))
    for k in ("selected_related_collections", "related_collections", "candidate_collections"):
        for c in _list(sel.get(k)):
            _add(out, (c or {}).get("collection") if isinstance(c, dict) else c)
    return out


def _compact_cols(ctx: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    ref = ctx.get("schema_context_ref") if isinstance(ctx.get("schema_context_ref"), dict) else {}
    for c in _list(ref.get("collections")):
        _add(out, c)
    _add(out, ctx.get("last_primary_collection"))
    for c in _list(ctx.get("last_related_collections")):
        _add(out, c)
    return out



def _parsed_collection_names(schema: Dict[str, Any]) -> List[str]:
    collections = schema.get("collections") if isinstance(schema, dict) else {}
    return [str(k).strip() for k in collections.keys() if str(k).strip()] if isinstance(collections, dict) else []


def _prune_ref_to_parsed(ref: Dict[str, Any], schema: Dict[str, Any], warnings: List[Any]) -> Dict[str, Any]:
    out = dict(ref or {})
    parsed = set(_parsed_collection_names(schema))
    requested = [str(c or "").strip() for c in _list(out.get("collections")) if str(c or "").strip()]
    if parsed and requested:
        kept = [c for c in requested if c in parsed]
        removed = [c for c in requested if c not in parsed]
        if removed:
            warnings.append({
                "type": "schema_context_ref_pruned_to_parsed_collections",
                "removed": removed,
                "reason": "requested collection was not parsed into schema_metadata.collections",
            })
        out["collections"] = kept
    return out

def build_hydration_retrieval_tasks(
    schema_runtime_context_json: Any = "{}",
    semantic_plan_json: Any = "{}",
    compact_context_json: Any = "{}",
    collection_selection_json: Any = "{}",
) -> Dict[str, Any]:
    runtime = _loads(schema_runtime_context_json, {}) or {}
    plan = _loads(semantic_plan_json, {}) or {}
    compact = _loads(compact_context_json, {}) or {}
    selection = _loads(collection_selection_json, {}) or {}
    warnings: List[str] = []
    sources = [
        ("schema_runtime_context.schema_hydration_collections", _list(runtime.get("schema_hydration_collections"))),
        ("semantic_plan", _plan_cols(plan)),
        ("collection_selection", _selection_cols(selection)),
        ("compact_context", _compact_cols(compact)),
    ]
    chosen: List[str] = []
    source_name = ""
    for name, cols in sources:
        tmp: List[str] = []
        for c in cols:
            _add(tmp, c)
        if tmp:
            chosen = tmp
            source_name = name
            break
    plan_cols = _plan_cols(plan)
    compact_cols = _compact_cols(compact)
    if plan_cols and compact_cols and set(plan_cols) != set(compact_cols):
        warnings.append("semantic_plan_collections_differ_from_compact_context_ref")
    if len(chosen) > MAX_COLLECTIONS:
        warnings.append("hydration_collections_truncated_to_3")
        chosen = chosen[:MAX_COLLECTIONS]
    tasks = [
        {
            "collection_name": c,
            "role": "validator_schema_hydration",
            "query_text": "schema metadata alias fields metrics relations for collection " + c,
            "retrieval_mode": "validator_schema_hydration",
        }
        for c in chosen
    ]
    valid = bool(tasks)
    reason = str(runtime.get("schema_hydration_reason") or ("derived_from_" + source_name if source_name else "missing_collections_for_schema_hydration"))
    if not valid:
        warnings.append("missing_collections_for_schema_hydration")
    return {
        "hydration_collection_tasks": tasks,
        "collection_tasks": tasks,
        "hydration_collections": chosen,
        "hydration_reason": reason,
        "hydration_task_valid": valid,
        "hydration_warnings": warnings,
        "hydration_warnings_json": _dumps(warnings),
        "retrieval_mode": "validator_schema_hydration",
    }


def merge_hydrated_schema_context(
    hydrated_schema_results: Any = None,
    hydration_collections: Any = None,
    compact_context_json: Any = "{}",
    hydration_warnings_json: Any = "[]",
) -> Dict[str, Any]:
    warnings = _loads(hydration_warnings_json, []) or []
    errors: List[str] = []
    sections = []
    for raw in hydrated_schema_results or []:
        item = _loads(raw, {}) or {}
        text = str(item.get("context_text") or "")
        if text:
            sections.append(text)
    meta = extract_schema_metadata("\n\n".join(sections))
    wanted = [str(c or "").strip() for c in _list(hydration_collections) if str(c or "").strip()]
    if wanted:
        meta["collections"] = {c: meta.get("collections", {}).get(c) for c in wanted if c in (meta.get("collections") or {})}
        meta["schema_digest"] = ""
        # Recompute digest through parser helper by passing normalized metadata.
        meta = extract_schema_metadata(meta)
    if not meta.get("collections"):
        errors.append("hydrated_schema_metadata_empty")
    alias = build_alias_index(meta)
    compact = _loads(compact_context_json, {}) or {}
    ref = compact.get("schema_context_ref") if isinstance(compact.get("schema_context_ref"), dict) else {}
    if ref.get("schema_digest") and ref.get("schema_digest") != meta.get("schema_digest"):
        warnings.append("hydrated_schema_digest_mismatch_with_compact_ref")
    version = str(meta.get("schema_version") or ref.get("schema_version") or "").strip()
    catalog_digest = str(ref.get("catalog_digest") or "").strip()
    context_ref = {
        "collections": wanted or list((meta.get("collections") or {}).keys()),
        "schema_digest": meta.get("schema_digest") or "",
        "schema_version": version,
        "catalog_digest": catalog_digest,
    }
    context_ref = _prune_ref_to_parsed(context_ref, meta, warnings)
    return {
        "hydrated_schema_metadata_json": _dumps(meta),
        "hydrated_schema_alias_index_json": _dumps(alias),
        "hydrated_schema_digest": meta.get("schema_digest") or "",
        "hydrated_schema_version": version,
        "hydrated_schema_context_ref_json": _dumps(context_ref),
        "hydration_success": bool(meta.get("collections")) and not errors,
        "hydration_warnings_json": _dumps(warnings),
        "hydration_errors_json": _dumps(errors),
    }


def prepare_hydrated_validator_runtime_schema_context(
    hydrated_schema_metadata_json: Any = "{}",
    hydrated_schema_alias_index_json: Any = "{}",
    hydrated_schema_digest: str = "",
    hydrated_schema_version: str = "",
    hydrated_schema_context_ref_json: Any = "{}",
    hydration_success: Any = False,
    hydration_warnings_json: Any = "[]",
    hydration_errors_json: Any = "[]",
) -> Dict[str, Any]:
    warnings = _loads(hydration_warnings_json, []) or []
    errors = _loads(hydration_errors_json, []) or []
    meta = _loads(hydrated_schema_metadata_json, {}) or {}
    alias = _loads(hydrated_schema_alias_index_json, {}) or {}
    ref = _loads(hydrated_schema_context_ref_json, {}) or {}
    ref = _prune_ref_to_parsed(ref, meta, warnings)
    ready = bool(hydration_success and meta.get("collections") and alias)
    ctx = {
        "contract_version": "schema_runtime_context_contract",
        "schema_context_ready": ready,
        "validator_schema_context_ready": ready,
        "schema_hydration_needed": False,
        "schema_hydration_reason": "",
        "schema_hydration_collections": [],
        "schema_metadata_json": _dumps(meta) if ready else "{}",
        "schema_alias_index_json": _dumps(alias) if ready else "{}",
        "schema_digest": str(hydrated_schema_digest or ref.get("schema_digest") or ""),
        "schema_version": str(hydrated_schema_version or ref.get("schema_version") or ""),
        "catalog_digest": str(ref.get("catalog_digest") or ""),
        "schema_source": "hydrated_runtime" if ready else "hydration_failed",
        "schema_context_ref": ref,
        "warnings": warnings,
        "errors": errors,
    }
    ctx["schema_runtime_context_json"] = _dumps(ctx)
    return ctx


def select_final_validator_runtime_schema_context(base_schema_runtime_context_json: Any = "{}", hydrated_schema_runtime_context_json: Any = "{}") -> Dict[str, Any]:
    base = _loads(base_schema_runtime_context_json, {}) or {}
    hydrated = _loads(hydrated_schema_runtime_context_json, {}) or {}
    ctx = hydrated if hydrated.get("schema_context_ready") else base
    warnings = ctx.get("warnings") if isinstance(ctx.get("warnings"), list) else []
    meta = _loads(ctx.get("schema_metadata_json"), {}) or {}
    if isinstance(ctx.get("schema_context_ref"), dict):
        ctx["schema_context_ref"] = _prune_ref_to_parsed(ctx.get("schema_context_ref"), meta, warnings)
        ctx["warnings"] = warnings
    ctx.setdefault("contract_version", "schema_runtime_context_contract")
    ctx["schema_runtime_context_json"] = _dumps(ctx)
    return ctx


def main(**kwargs: Any) -> Dict[str, Any]:
    return build_hydration_retrieval_tasks(**kwargs)


if __name__ == "__main__":
    plan = {"primary_collection": "orders", "stages": [{"collection": "orders"}]}
    runtime = {"schema_hydration_collections": ["orders"], "schema_hydration_reason": "empty_schema_metadata"}
    print(json.dumps(build_hydration_retrieval_tasks(_dumps(runtime), _dumps(plan)), ensure_ascii=False, indent=2))
