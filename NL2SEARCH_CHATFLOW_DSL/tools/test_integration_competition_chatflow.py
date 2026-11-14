#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for INTEGRATION_COMPETITION_CHATFLOW.

Run from the DSL repository root:
    python NL2SEARCH_CHATFLOW_DSL/tools/test_integration_competition_chatflow.py
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
MERGER_PATH = TOOLS_DIR / "merge_business_chatflow.py"
SPEC = importlib.util.spec_from_file_location("merge_business_chatflow", MERGER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load merge_business_chatflow.py")
MERGER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MERGER
SPEC.loader.exec_module(MERGER)

KEY = "INTEGRATION_COMPETITION"
CFG = MERGER.CONFIGS[KEY]
DOC, _ = MERGER.load_doc(KEY)
SUMMARY = MERGER.validate_doc(KEY, DOC)
ENV = {item["name"]: item for item in DOC["workflow"]["environment_variables"]}
REGISTRY = json.loads(ENV[CFG["registry_env"]]["value"])
SYSTEM_PROMPT = ENV[CFG["system_env"]]["value"]
USER_PROMPT = ENV[CFG["user_env"]]["value"]


class IntegrationCompetitionChatflowTests(unittest.TestCase):
    def test_existing_merger_validation_passes(self):
        self.assertEqual(SUMMARY["table"], "sn_cis_integration_competition_wide")
        self.assertEqual(SUMMARY["registry_fields"], 282)
        self.assertEqual(SUMMARY["nodes"], 24)
        self.assertEqual(SUMMARY["edges"], 27)

    def test_registry_matches_complete_query_schema(self):
        self.assertEqual(len(REGISTRY["fields"]), 282)
        self.assertEqual(len(CFG["fields"]), 282)
        self.assertEqual(set(REGISTRY["fields"]), set(CFG["fields"]))
        self.assertEqual(
            len([name for name in REGISTRY["fields"] if name.endswith("_area_mm2")]),
            267,
        )
        self.assertEqual(
            sum(1 for name in REGISTRY["fields"] if name in {
                "device_design_attributes", "raw_value_annotations",
                "additional_metrics", "module_chip_models",
                "unmapped_integration_values",
            }),
            5,
        )

    def test_filter_fields_are_identity_only(self):
        self.assertEqual(
            set(REGISTRY["query_roles"]["sql_filter_fields"]),
            {"manufacturer", "platform", "device_type", "device_model"},
        )
        self.assertEqual(REGISTRY["query_roles"]["sql_select_fields"], "all_fields")

    def test_composite_fields_reference_real_columns(self):
        fields = set(REGISTRY["fields"])
        self.assertGreaterEqual(len(REGISTRY["composite_fields"]), 15)
        for name, item in REGISTRY["composite_fields"].items():
            self.assertTrue(item["expand_to"], name)
            self.assertTrue(set(item["expand_to"]) <= fields, name)

    def test_etl_module_names_are_searchable_aliases(self):
        # Every known Tree leaf mapped by ETL is represented by a field alias.
        alias_count = sum(
            1 for item in REGISTRY["fields"].values()
            if len(item.get("aliases") or []) >= 3
        )
        self.assertGreaterEqual(alias_count, 250)

    def test_multivalue_area_values_are_never_aggregated(self):
        required_phrases = (
            "逗号分隔", "保留原始顺序", "不得将它们求和",
            "不得通过明细字段推算合计",
        )
        combined = SYSTEM_PROMPT + "\n" + USER_PROMPT
        for phrase in required_phrases:
            self.assertIn(phrase, combined)
        self.assertIn("wireless_total_area_mm2", REGISTRY["fields"])
        self.assertIn("integration_total_area_mm2", REGISTRY["fields"])

    def test_independent_wireless_totals_are_not_forced_to_sum(self):
        for name in ("wireless_total_area_mm2", "wireless_rf_total_area_mm2",
                     "wireless_nc_total_area_mm2"):
            self.assertIn(name, REGISTRY["fields"])
        self.assertIn("不得擅自相加", SYSTEM_PROMPT)

    def test_legacy_jf_namespace_is_absent(self):
        serialized = json.dumps(DOC, ensure_ascii=False)
        for token in ("JF_CAPACITOR", "jf_capacitor_", "sn_pisi_jf_capacitor"):
            self.assertNotIn(token, serialized)

    def test_business_node_namespaces_are_isolated(self):
        prefix = CFG["prefix"]
        by_id = {node["id"]: node for node in DOC["workflow"]["graph"]["nodes"]}
        for suffix in ("registry_load", "registry_parse", "sql_plan"):
            node = by_id[prefix + suffix]
            outputs = node["data"].get("outputs") or {}
            self.assertTrue(outputs)
            self.assertTrue(all(name.startswith(prefix) for name in outputs))
        self.assertIn(prefix + "query_memory_json",
                      {item["name"] for item in DOC["workflow"]["conversation_variables"]})
        self.assertIn(prefix + "previous_query_result_json",
                      {item["name"] for item in DOC["workflow"]["conversation_variables"]})

    def test_code_node_contracts_remain_aligned(self):
        for node in DOC["workflow"]["graph"]["nodes"]:
            data = node.get("data") or {}
            if data.get("type") != "code":
                continue
            code = data.get("code") or ""
            self.assertEqual(data.get("source_code"), code, node["id"])
            match = re.search(r"^def main\((.*?)\):", code, re.S | re.M)
            self.assertIsNotNone(match, node["id"])
            args = []
            for part in match.group(1).split(","):
                name = part.strip().split("=", 1)[0].strip()
                if name:
                    args.append(name)
            variables = [item.get("variable") for item in data.get("variables") or []]
            self.assertEqual(args, variables, node["id"])
            for output in (data.get("outputs") or {}):
                self.assertIn(output, code, node["id"])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(IntegrationCompetitionChatflowTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
