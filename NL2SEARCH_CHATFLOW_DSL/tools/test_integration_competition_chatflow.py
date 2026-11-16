#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for INTEGRATION_COMPETITION_CHATFLOW."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))
import merge_business_chatflow as merger  # noqa: E402

KEY = "INTEGRATION_COMPETITION"


def run_code_node(doc: dict, node_id: str, **kwargs) -> dict:
    node = next(item for item in doc["workflow"]["graph"]["nodes"] if item["id"] == node_id)
    namespace = {}
    exec(compile(node["data"]["code"], "<%s>" % node_id, "exec"), namespace)
    return namespace["main"](**kwargs)


class IntegrationCompetitionChatflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc, cls.shard_text = merger.load_doc(KEY)
        cls.summary = merger.validate_doc(KEY, cls.doc)
        cls.envs = {item["name"]: item for item in cls.doc["workflow"]["environment_variables"]}

    def load_runtime_registry(self):
        result = run_code_node(
            self.doc,
            "integration_competition_registry_load",
            integration_competition_query_registry_json=self.envs["INTEGRATION_COMPETITION_QUERY_REGISTRY_JSON"]["value"],
            integration_competition_query_registry_fields_00_json=self.envs["INTEGRATION_COMPETITION_QUERY_REGISTRY_FIELDS_00_JSON"]["value"],
            integration_competition_query_registry_fields_01_json=self.envs["INTEGRATION_COMPETITION_QUERY_REGISTRY_FIELDS_01_JSON"]["value"],
            integration_competition_query_registry_fields_02_json=self.envs["INTEGRATION_COMPETITION_QUERY_REGISTRY_FIELDS_02_JSON"]["value"],
        )
        self.assertEqual(result["integration_competition_registry_load_status"], "ok")
        self.assertEqual(result["integration_competition_registry_load_error"], "")
        return json.loads(result["integration_competition_registry_raw_json"])

    def catalog_fixture(self, rows=None):
        if rows is None:
            rows = [
                {"manufacturer": "Apple", "platform": "A18", "device_type": "phone", "device_model": "iPhone16 Pro Max"},
                {"manufacturer": "Apple", "platform": "A19", "device_type": "phone", "device_model": "iPhone17 Pro Max"},
                {"manufacturer": "Xiaomi", "platform": "Snapdragon 8 Elite", "device_type": "phone", "device_model": "mi15pro"},
                {"manufacturer": "Xiaomi", "platform": "Snapdragon 8 Gen5", "device_type": "phone", "device_model": "mi17Promax"},
            ]
        return {"code": 200, "data": {"rows": rows, "rowCount": len(rows)}}

    def build_runtime_registry(self, rows=None):
        base = self.load_runtime_registry()
        merged = run_code_node(
            self.doc,
            "integration_competition_identity_catalog_merge",
            registry_raw_json=json.dumps(base, ensure_ascii=False),
            body=json.dumps(self.catalog_fixture(rows), ensure_ascii=False),
            status_code=200,
            request_status="ok",
        )
        self.assertEqual(merged["integration_competition_registry_load_status"], "ok")
        self.assertEqual(merged["integration_competition_registry_load_error"], "")
        enriched = json.loads(merged["integration_competition_registry_enriched_json"])
        parsed = run_code_node(
            self.doc,
            "integration_competition_registry_parse",
            integration_competition_registry_raw_json=json.dumps(enriched, ensure_ascii=False),
            integration_competition_registry_load_status="ok",
        )
        self.assertEqual(parsed["integration_competition_registry_parse_status"], "ok")
        return enriched, parsed

    def plan(self, question: str, parsed: dict, memory: str = "{}") -> dict:
        result = run_code_node(
            self.doc,
            "integration_competition_sql_plan",
            context_json=json.dumps({"question": question, "user": "regression-test"}, ensure_ascii=False),
            integration_competition_registry_json=parsed["integration_competition_registry_json"],
            integration_competition_alias_index_json=parsed["integration_competition_alias_index_json"],
            integration_competition_registry_parse_status=parsed["integration_competition_registry_parse_status"],
            integration_competition_registry_error=parsed["integration_competition_registry_error"],
            integration_competition_query_memory_json=memory,
        )
        self.assertEqual(result["integration_competition_sql_compile_status"], "ok")
        return result

    def test_01_schema_inventory_and_top_level_scope(self):
        registry = self.load_runtime_registry()
        self.assertEqual(len(registry["fields"]), 282)
        self.assertEqual(sum(1 for name in registry["fields"] if name.endswith("_area_mm2")), 267)
        self.assertEqual(set(registry["value_aliases"]), {"manufacturer", "platform", "device_type", "device_model"})
        self.assertTrue(all(not values for values in registry["value_aliases"].values()))
        self.assertEqual(
            registry["composite_fields"]["integration_overview"]["expand_to"],
            ["integration_total_area_mm2", "small_system_total_area_mm2", "peripheral_total_area_mm2", "wireless_total_area_mm2"],
        )
        self.assertEqual(
            registry["composite_fields"]["top_level_domains"]["expand_to"],
            ["small_system_total_area_mm2", "peripheral_total_area_mm2", "wireless_total_area_mm2"],
        )
        self.assertEqual(len(registry["composite_fields"]), 28)
        self.assertEqual(len(registry["composite_fields"]["small_system"]["expand_to"]), 17)
        self.assertEqual(len(registry["composite_fields"]["wireless"]["expand_to"]), 150)
        self.assertEqual(registry["composite_fields"]["peripheral"]["expand_to"], ["peripheral_total_area_mm2", "peripheral_composite_area_mm2"])
        self.assertEqual(registry["composite_fields"]["release_information"]["expand_to"], ["release_date", "release_date_text"])
        self.assertIn("device_pic_url", registry["composite_fields"]["identity"]["expand_to"])
        self.assertIn("整机总面积", registry["fields"]["integration_total_area_mm2"]["aliases"])
        base_registry = json.loads(self.envs["INTEGRATION_COMPETITION_QUERY_REGISTRY_JSON"]["value"])
        self.assertTrue(all(isinstance(item.get("expand_to"), list) and item["expand_to"] for item in base_registry["composite_fields"].values()))
        self.assertFalse(any(key.startswith("_expand_") for item in base_registry["composite_fields"].values() for key in item))

    def test_02_all_area_fields_have_ddl_semantics(self):
        registry = self.load_runtime_registry()
        area = [field for name, field in registry["fields"].items() if name.endswith("_area_mm2")]
        self.assertEqual(len(area), 267)
        self.assertTrue(all("业务定义" in field["description"] and "数据层级" in field["description"] for field in area))

    def test_03_identity_catalog_query_is_read_only(self):
        result = run_code_node(
            self.doc,
            "integration_competition_identity_catalog_prepare",
            context_json=json.dumps({"user": "regression-test"}),
        )
        request = json.loads(result["integration_competition_identity_catalog_request_body_json"])
        sql = request["sql"]
        self.assertIn("SELECT DISTINCT", sql)
        self.assertIn("sn_cis_integration_competition_wide", sql)
        for field in ("manufacturer", "platform", "device_type", "device_model"):
            self.assertIn(field, sql)
        for word in ("UPDATE ", "DELETE ", "INSERT ", "DROP ", "TRUNCATE "):
            self.assertNotIn(word, sql.upper())

    def test_04_runtime_aliases_are_database_derived(self):
        enriched, _ = self.build_runtime_registry()
        self.assertEqual(set(enriched["value_aliases"]["manufacturer"]), {"Apple", "Xiaomi"})
        self.assertEqual(set(enriched["value_aliases"]["device_model"]), {"iPhone16 Pro Max", "iPhone17 Pro Max", "mi15pro", "mi17Promax"})
        self.assertEqual(enriched["value_aliases"]["manufacturer"]["Apple"]["aliases"], ["Apple", "苹果"])
        self.assertIn("苹果A18", enriched["value_aliases"]["platform"]["A18"]["aliases"])
        self.assertEqual(enriched["value_aliases"]["device_type"]["phone"]["aliases"], ["phone", "手机", "智能手机"])
        self.assertEqual(enriched["value_aliases"]["platform"]["Snapdragon 8 Elite"]["aliases"], ["Snapdragon 8 Elite"])
        self.assertNotIn("Apple A19", enriched["value_aliases"]["platform"]["Snapdragon 8 Gen5"]["aliases"])
        self.assertNotIn("Apple", enriched["value_aliases"]["manufacturer"].get("Unknown", {}))

    def test_05_normalization_collision_is_not_guessed(self):
        payload = {"code": 200, "data": {"rows": [
            {"manufacturer": "Maker", "platform": "P1", "device_type": "phone", "device_model": "Model-A"},
            {"manufacturer": "Maker", "platform": "P1", "device_type": "phone", "device_model": "model a"},
        ]}}
        result = run_code_node(
            self.doc,
            "integration_competition_identity_catalog_merge",
            registry_raw_json=json.dumps(self.load_runtime_registry(), ensure_ascii=False),
            body=json.dumps(payload, ensure_ascii=False),
            status_code=200,
            request_status="ok",
        )
        enriched = json.loads(result["integration_competition_registry_enriched_json"])
        self.assertEqual(enriched["value_aliases"]["device_model"], {})
        self.assertGreater(int(result["integration_competition_identity_catalog_warning_count"]), 0)
        self.assertNotIn("Apple", enriched["value_aliases"]["manufacturer"])
        self.assertNotIn("A19", enriched["value_aliases"]["platform"])

    def test_06_catalog_failure_fails_closed(self):
        result = run_code_node(
            self.doc,
            "integration_competition_identity_catalog_merge",
            registry_raw_json=json.dumps(self.load_runtime_registry(), ensure_ascii=False),
            body="{}",
            status_code=503,
            request_status="ok",
        )
        self.assertEqual(result["integration_competition_registry_load_status"], "error")
        parsed = run_code_node(
            self.doc,
            "integration_competition_registry_parse",
            integration_competition_registry_raw_json=result["integration_competition_registry_enriched_json"],
            integration_competition_registry_load_status=result["integration_competition_registry_load_status"],
        )
        self.assertNotEqual(parsed["integration_competition_registry_parse_status"], "ok")

    def test_07_total_alias_and_no_sum_planning(self):
        _, parsed = self.build_runtime_registry()
        result = self.plan("查询手机 iPhone17 Pro Max 的整机总面积", parsed)
        plan = json.loads(result["integration_competition_sql_plan_json"])
        self.assertEqual(plan["where_filters"]["device_model"], ["iPhone17 Pro Max"])
        self.assertEqual(plan["where_filters"]["device_type"], ["phone"])
        self.assertIn("integration_total_area_mm2", plan["select_fields"])
        self.assertTrue(plan["sql"].startswith("SELECT "))
        self.assertNotIn("SUM(", plan["sql"].upper())

    def test_08_composite_expansion_uses_real_fields(self):
        _, parsed = self.build_runtime_registry()
        result = self.plan("查询 iPhone17 Pro Max 的小系统面积构成", parsed)
        plan = json.loads(result["integration_competition_sql_plan_json"])
        self.assertIn("small_system", plan["selected_composites"])
        self.assertIn("small_system_total_area_mm2", plan["select_fields"])
        self.assertIn("small_system_soc_total_area_mm2", plan["select_fields"])
        self.assertIn("small_system_pmk_area_mm2", plan["select_fields"])
        self.assertIn("small_system_pmu1_area_mm2", plan["select_fields"])
        self.assertIn("small_system_pmu2_area_mm2", plan["select_fields"])

    def test_09_followup_inherits_device_scope(self):
        _, parsed = self.build_runtime_registry()
        first = self.plan("查询 iPhone17 Pro Max 的整机总面积", parsed)
        second = self.plan("这些机型的无线总面积", parsed, first["integration_competition_current_execution_context_json"])
        plan = json.loads(second["integration_competition_sql_plan_json"])
        self.assertEqual(plan["where_filters"]["device_model"], ["iPhone17 Pro Max"])
        self.assertIn("wireless_total_area_mm2", plan["select_fields"])

    def test_10_area_rules_are_present_in_prompts(self):
        prompt = self.envs["INTEGRATION_COMPETITION_ANALYSIS_SYSTEM_PROMPT"]["value"] + "\n" + self.envs["INTEGRATION_COMPETITION_ANALYSIS_USER_PROMPT"]["value"]
        for term in ("42.77,16.59", "不强制等于RF+NC", "不是无线下第三个可加总业务域", "天线不是第三个加数", "NULL", "GG", "SUM"):
            self.assertIn(term, prompt)


    def test_11_other_words_do_not_inherit_previous_device_scope(self):
        _, parsed = self.build_runtime_registry()
        first = self.plan("查询 iPhone17 Pro Max 的整机总面积", parsed)
        memory = first["integration_competition_current_execution_context_json"]
        for question in ("查询其他模块的无线总面积", "查询其它模块的无线总面积", "查询其余模块的无线总面积"):
            result = self.plan(question, parsed, memory)
            plan = json.loads(result["integration_competition_sql_plan_json"])
            self.assertEqual(plan["query_intent_meta"]["query_scope_behavior"], "reset")
            self.assertNotIn("device_model", plan["where_filters"])

    def test_12_previous_result_is_only_attached_for_reference_queries(self):
        _, parsed = self.build_runtime_registry()
        first = self.plan("查询 iPhone17 Pro Max 的整机总面积", parsed)
        previous = {"sql_plan": json.loads(first["integration_competition_sql_plan_json"]), "rows": [{"device_model": "iPhone17 Pro Max", "integration_total_area_mm2": "42.77"}], "row_count": 1}
        for question, expected_count in (("查询其他模块的无线总面积", 0), ("查询这些机型的无线总面积", 1)):
            current = self.plan(question, parsed, first["integration_competition_current_execution_context_json"])
            result = run_code_node(
                self.doc, "llm_input",
                context_json=json.dumps({"question": question, "user": "regression-test"}, ensure_ascii=False),
                mysql_query_result_json=json.dumps({"rows": [{"wireless_total_area_mm2": "10.5"}], "sql_plan": json.loads(current["integration_competition_sql_plan_json"])}, ensure_ascii=False),
                integration_competition_selected_field_meta_json=current["integration_competition_selected_field_meta_json"],
                integration_competition_alias_index_json=parsed["integration_competition_alias_index_json"],
                analysis_system_prompt=self.envs["INTEGRATION_COMPETITION_ANALYSIS_SYSTEM_PROMPT"]["value"],
                analysis_user_prompt=self.envs["INTEGRATION_COMPETITION_ANALYSIS_USER_PROMPT"]["value"],
                integration_competition_current_execution_context_json=current["integration_competition_current_execution_context_json"],
                integration_competition_previous_query_result_json=json.dumps(previous, ensure_ascii=False),
                local_context_tokens="32768", local_max_predict_tokens="4096", prompt_token_safety_margin="1024",
            )
            package = json.loads(result["llm_handler_input_json"])
            self.assertEqual(len(package["previous_query_result"]["rows"]), expected_count)

    def test_13_oversized_prompt_guard_does_not_forward_large_prompt(self):
        _, parsed = self.build_runtime_registry()
        plan_result = self.plan("查询 iPhone17 Pro Max 的整机总面积", parsed)
        result = run_code_node(
            self.doc, "llm_input",
            context_json=json.dumps({"question": "查询整机面积", "user": "regression-test"}, ensure_ascii=False),
            mysql_query_result_json=json.dumps({"rows": [{"large_value": "x" * 12000}], "sql_plan": json.loads(plan_result["integration_competition_sql_plan_json"])}, ensure_ascii=False),
            integration_competition_selected_field_meta_json="{}",
            integration_competition_alias_index_json=parsed["integration_competition_alias_index_json"],
            analysis_system_prompt="system", analysis_user_prompt="rules",
            integration_competition_current_execution_context_json="{}",
            integration_competition_previous_query_result_json="{}",
            local_context_tokens="32", local_max_predict_tokens="16", prompt_token_safety_margin="8",
        )
        payload = json.loads(result["llm_handler_input_json"])
        self.assertEqual(payload["status"], "error")
        self.assertEqual(result["llm_system_prompt"], "")
        self.assertTrue(result["llm_user_prompt"].startswith("LLM_INPUT_TOO_LARGE"))
        self.assertLess(len(result["llm_user_prompt"]), 200)

    def test_14_ambiguous_fuzzy_device_model_fails_closed(self):
        rows = [
            {"manufacturer": "Apple", "platform": "A19", "device_type": "phone", "device_model": "iPhone17 Pro Max A"},
            {"manufacturer": "Apple", "platform": "A19", "device_type": "phone", "device_model": "iPhone17 Pro Max B"},
        ]
        _, parsed = self.build_runtime_registry(rows)
        result = run_code_node(
            self.doc, "integration_competition_sql_plan",
            context_json=json.dumps({"question": "查询 iPhone17 Pro Max 的整机总面积", "user": "regression-test"}, ensure_ascii=False),
            integration_competition_registry_json=parsed["integration_competition_registry_json"],
            integration_competition_alias_index_json=parsed["integration_competition_alias_index_json"],
            integration_competition_registry_parse_status=parsed["integration_competition_registry_parse_status"],
            integration_competition_registry_error=parsed["integration_competition_registry_error"],
            integration_competition_query_memory_json="{}",
        )
        self.assertEqual(result["integration_competition_sql_compile_status"], "error")
        self.assertIn("多个候选值", result["integration_competition_sql_compile_error_answer"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
