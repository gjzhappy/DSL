#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Read-only CIS value-length audit based directly on ETL v1.0.2.
Source: MongoDB kirin_shennong_datacenter_dev.integration_device.
This audit-only copy reuses the original ETL's transform_document(), build_targets()
and WRITE_COLUMNS. It never connects to MySQL and never writes to either database.

Design:
- One MongoDB device document -> one transformed ETL row for measurement.
- Unique identity: manufacturer + platform + device_type + device_model.
- MongoDB is read-only. MySQL is the only write target.
- Does NOT create/drop/truncate tables and does NOT delete stale rows.
- Uses exact module_name + sub_module_name mapping derived from the approved CIS Tree.
- Repeated values for the same logical leaf are ordered comma-separated text, never summed.
- Stores repeated numeric area observations as ordered comma-separated TEXT values; never SUMs them.
- Redacts device identities, source IDs, raw values, credentials and connection accounts from logs.
- Preserves non-numeric area values, non-area metrics, chip models, design attributes,
  and unknown integration nodes in the designated JSON columns.
- Never sums detail areas to invent parent totals. Source totals are preserved.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import logging
import math
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:
    pymysql = None
    DictCursor = None

ETL_VERSION = "1.0.2"
TABLE = "sn_cis_integration_competition_wide"
SEMANTIC_VERSION = "cis-integration-v4"
MONGO_DATABASE = "kirin_shennong_datacenter_dev"
MONGO_COLLECTION = "integration_device"
MONGO_REPLICA_SET = "snrs"
MONGO_SEEDS = (
    "100.102.192.209:52003",
    "100.102.192.209:52004",
    "100.102.192.209:52005",
)
MONGO_DEFAULT_USER = "root"

MYSQL_DEFAULT = {
    "host": "node3",
    "port": 3306,
    "database": "kirin_shennong-dev",
    "charset": "utf8mb4",
    "autocommit": False,
}

IDENTITY_COLUMNS = ("manufacturer", "platform", "device_type", "device_model")
MANAGED_COLUMNS = {"id", "created_at", "updated_at"}
JSON_COLUMNS = ('device_design_attributes', 'raw_value_annotations', 'additional_metrics', 'module_chip_models', 'unmapped_integration_values')
AREA_COLUMNS = ('integration_total_area_mm2', 'small_system_total_area_mm2', 'peripheral_total_area_mm2', 'wireless_total_area_mm2', 'wireless_rf_total_area_mm2', 'wireless_nc_total_area_mm2', 'camera_total_area_mm2', 'fpc_total_area_mm2', 'io_expansion_total_area_mm2', 'wireless_nc_nfc_total_area_mm2', 'pmu_total_area_mm2', 'scharger_total_area_mm2', 'sensor_lowercase_group_total_area_mm2', 'sensor_uppercase_group_total_area_mm2', 'sim_total_area_mm2', 'test_point_total_area_mm2', 'storage_total_area_mm2', 'battery_total_area_mm2', 'analog_phy_total_area_mm2', 'wireless_rf_frontend_total_area_mm2', 'wireless_antenna_total_area_mm2', 'temperature_detection_total_area_mm2', 'display_total_area_mm2', 'audio_total_area_mm2', 'small_system_modem_area_mm2', 'small_system_modem_pmu_area_mm2', 'small_system_pmk_area_mm2', 'small_system_pmu1_area_mm2', 'small_system_pmu2_area_mm2', 'small_system_rfic_pmu_area_mm2', 'small_system_soc_total_area_mm2', 'small_system_ufs_total_area_mm2', 'peripheral_composite_area_mm2', 'power_charger_area_mm2', 'audio_codec_area_mm2', 'display_lcd_driver_power_area_mm2', 'power_scharger_area_mm2', 'power_wireless_charger_area_mm2', 'wireless_nc_uwb_area_mm2', 'wireless_nc_wifi_bt_baseband_area_mm2', 'wireless_rf_et_power_area_mm2', 'wireless_rf_mmw_smr_frontend_area_mm2', 'wireless_rf_rfic_mmw_peripherals_area_mm2', 'wireless_rf_cellular_rf_frontend_area_mm2', 'wireless_rf_tiantong_beidou_area_mm2', 'wireless_rf_satcom_tiantong_beidou_xingwang_area_mm2', 'wireless_rf_xingwang_1_0_area_mm2', 'power_5v_boost_area_mm2', 'camera_pwr_area_mm2', 'camera_front_cam_area_mm2', 'camera_front_camera_area_mm2', 'camera_points_sub_cam_lga_area_mm2', 'camera_rear_cam1_area_mm2', 'camera_rear_cam2_area_mm2', 'camera_rear_camera1_area_mm2', 'camera_rear_camera2_area_mm2', 'interface_fpc_interface_area_mm2', 'interface_fpc_interface_1_area_mm2', 'interface_fpc_interface_2_area_mm2', 'interface_fpc_interface_main_area_mm2', 'interface_fpc_interface_main_rf_area_mm2', 'interface_fpc_ntc_fpc_interface_main_area_mm2', 'interface_fpc_interface_typec_area_mm2', 'interface_io_expansion_iodie_area_mm2', 'wireless_nc_nfc_nfc_area_mm2', 'wireless_nc_nfc_110t_area_mm2', 'wireless_nc_nfc_fe_area_mm2', 'wireless_nc_nfc_fe_2_area_mm2', 'power_pmu_pmu0_buck1_area_mm2', 'power_pmu_pmu0_buck2_area_mm2', 'power_pmu_pmu0_digital_clk_area_mm2', 'power_pmu_pmu0_ldo_area_mm2', 'power_pmu_pmu1_buck1_area_mm2', 'power_pmu_pmu1_buck2_area_mm2', 'power_pmu_pmu2_buck1_area_mm2', 'power_pmu_pmu2_buck2_area_mm2', 'power_scharger_charger_res_uppercase_area_mm2', 'power_scharger_charger2_area_mm2', 'power_scharger_buck_charger_area_mm2', 'power_scharger_charger_res_titlecase_area_mm2', 'power_scharger_charger1_v700_area_mm2', 'power_scharger_sc1_area_mm2', 'power_scharger_sc2_area_mm2', 'power_scharger_scharger_v510_area_mm2', 'power_scharger_wl_charger_area_mm2', 'sensor_lowercase_group_sensor_area_mm2', 'sensor_uppercase_group_sensor_area_mm2', 'sensor_ambient_light_and_proximity_area_mm2', 'sensor_ambientandproximity_area_mm2', 'sensor_sar_1_area_mm2', 'sensor_sar_sensor_area_mm2', 'interface_sim_main_area_mm2', 'interface_sim_sd_area_mm2', 'interface_sim_sd_ioexp_area_mm2', 'small_system_soc_gnd_area_mm2', 'small_system_soc_gpio_interface_area_mm2', 'small_system_soc_hs_interface_area_mm2', 'small_system_soc_pwr1_area_mm2', 'small_system_soc_pwr2_area_mm2', 'small_system_soc_pwr3_area_mm2', 'small_system_soc_pwr4_area_mm2', 'small_system_ufs_memory_area_mm2', 'test_point_sim_lga_point_area_mm2', 'test_point_test_point_lowercase_area_mm2', 'test_point_test_point_titlecase_area_mm2', 'test_point_test_point_shields_area_mm2', 'storage_memory_area_mm2', 'power_battery_battery_area_mm2', 'power_battery_battery_1_protection_area_mm2', 'power_battery_battery_bjeh_protection_area_mm2', 'power_battery_battery_main_area_mm2', 'power_battery_bjeh_area_mm2', 'wireless_nc_gnss_bds_baseband_area_mm2', 'wireless_rf_satellite_bds_trx_area_mm2', 'wireless_rf_satellite_bds_trx_pa_area_mm2', 'wireless_rf_satellite_bds_trx1_rf_end_area_mm2', 'wireless_rf_satellite_bds_trx2_rf_end_area_mm2', 'wireless_nc_connectivity_1106_control_area_mm2', 'wireless_nc_connectivity_1106_power1_area_mm2', 'wireless_nc_connectivity_1106_power2_area_mm2', 'wireless_nc_connectivity_1107_control_area_mm2', 'wireless_nc_connectivity_1107_power1_area_mm2', 'wireless_nc_connectivity_1107_power2_area_mm2', 'wireless_nc_connectivity_control_1_area_mm2', 'wireless_nc_connectivity_control_bb_area_mm2', 'wireless_nc_connectivity_control_1107_area_mm2', 'wireless_nc_connectivity_power_1107_area_mm2', 'wireless_nc_connectivity_power_6506_area_mm2', 'wireless_nc_connectivity_power1_area_mm2', 'wireless_nc_connectivity_power1_1_area_mm2', 'wireless_nc_connectivity_power2_area_mm2', 'wireless_nc_connectivity_power2_1_area_mm2', 'wireless_nc_satellite_fxz_bbic_power_area_mm2', 'wireless_rf_satellite_fxz_bbic_rf_area_mm2', 'wireless_rf_satellite_fxz_rf_area_mm2', 'wireless_rf_satellite_fxz_rf_1_area_mm2', 'wireless_rf_satellite_fxz_rf_2_area_mm2', 'wireless_nc_gnss_gps_l1_l5_area_mm2', 'wireless_rf_band_path_lb_main_area_mm2', 'wireless_antenna_lds_ant_area_mm2', 'wireless_rf_composite_mhb_main_gpsl1_wifi_c0_nr1_area_mm2', 'wireless_rf_satellite_pgy_nsv_drx_area_mm2', 'wireless_nc_satellite_pgy_bb_main_area_mm2', 'wireless_rf_satellite_pgy_drx_area_mm2', 'wireless_rf_satellite_pgy_drx_1_area_mm2', 'wireless_rf_satellite_pgy_trx_area_mm2', 'wireless_rf_satellite_pgy_trx_1_area_mm2', 'wireless_rf_satellite_pgy_trx_pa_area_mm2', 'wireless_satellite_pgy212r_unclassified_area_mm2', 'wireless_rf_satellite_pgy21tx_candidate_area_mm2', 'wireless_nc_rf_path_rf0_2g4_c0_area_mm2', 'wireless_nc_rf_path_rf1_5g_c0_area_mm2', 'wireless_nc_rf_path_rf2_2g4_c1_area_mm2', 'wireless_nc_rf_path_rf3_5g_c1_area_mm2', 'wireless_antenna_side_ant_area_mm2', 'wireless_nc_wifi_2g4_c0_area_mm2', 'wireless_nc_wifi_2g4_c1_area_mm2', 'wireless_nc_wifi_5g_c0_area_mm2', 'wireless_nc_wifi_5g_c1_area_mm2', 'wireless_rf_shared_xrz_gg_fxz_1t3r_area_mm2', 'wireless_rf_satellite_xrz_prx_drx_area_mm2', 'wireless_rf_satellite_xrz_trx_pa_area_mm2', 'interface_analog_phy_usb_interface_area_mm2', 'interface_analog_phy_usb_protection_titlecase_area_mm2', 'interface_analog_phy_usb_protection_lowercase_area_mm2', 'wireless_rf_frontend_afl_3_lb_trx_area_mm2', 'wireless_rf_frontend_afl_3_mhb_trx_area_mm2', 'wireless_rf_frontend_afl_6_tx0_prx_area_mm2', 'wireless_rf_frontend_afl_6_tx1_mimo_p_area_mm2', 'wireless_rf_frontend_boost_bypass_area_mm2', 'wireless_rf_frontend_gsm_pa_area_mm2', 'wireless_rf_frontend_gsm_pa_1_area_mm2', 'wireless_rf_frontend_lb_l_pamid_area_mm2', 'wireless_rf_frontend_lb_lpamid_area_mm2', 'wireless_rf_frontend_lb_lpamid_1_area_mm2', 'wireless_rf_frontend_lb_rf_switch_area_mm2', 'wireless_rf_frontend_lb_rf_switch_1_area_mm2', 'wireless_rf_frontend_lmhb_ldifem_area_mm2', 'wireless_rf_frontend_lmhb_l_difem_area_mm2', 'wireless_rf_frontend_lmhb_ldifem_1_area_mm2', 'wireless_rf_frontend_mhb_l_pamid_area_mm2', 'wireless_rf_frontend_mhb_l_pamid_1_area_mm2', 'wireless_rf_frontend_mhb_rf_switch_area_mm2', 'wireless_rf_frontend_mhb_rf_switch_1_area_mm2', 'wireless_rf_frontend_mhb_trx_area_mm2', 'wireless_rf_frontend_mimo_drx_area_mm2', 'wireless_rf_frontend_mimo_drx_2_area_mm2', 'wireless_rf_frontend_mimo_drx_lna_area_mm2', 'wireless_rf_frontend_mimo_lna_area_mm2', 'wireless_rf_frontend_mimo_prx_lna_area_mm2', 'wireless_rf_frontend_mimo_prx_lna_1_area_mm2', 'wireless_rf_frontend_rf_bypass_area_mm2', 'wireless_rf_frontend_rf_pa_pmu_0_area_mm2', 'wireless_rf_frontend_rf_pa_pmu_1_area_mm2', 'wireless_rf_frontend_rf_pa_pmu1_apt_1_area_mm2', 'wireless_rf_frontend_rf_pa_pmu1_et_area_mm2', 'wireless_rf_frontend_rf_pmu0_et_area_mm2', 'wireless_rf_frontend_rf_pmu0_et_1_area_mm2', 'wireless_rf_frontend_rf_pwr_area_mm2', 'wireless_rf_frontend_rfic_pmu_area_mm2', 'wireless_rf_frontend_rfic_port_area_mm2', 'wireless_rf_frontend_rfic_port_1_area_mm2', 'wireless_rf_frontend_rfic_power_area_mm2', 'wireless_rf_frontend_rfic_power_1_area_mm2', 'wireless_rf_frontend_sub_3g_mhb_trx_area_mm2', 'wireless_rf_frontend_sub_3g_mhb_trx_1_area_mm2', 'wireless_rf_frontend_sub3g_lb_trx_area_mm2', 'wireless_rf_frontend_sub6g_tx0_prx_area_mm2', 'wireless_rf_frontend_sub6g_tx0_prx_1_area_mm2', 'wireless_rf_frontend_sub6g_tx1_mimo_p_area_mm2', 'wireless_rf_frontend_sub6g_tx1_mimo_p_1_area_mm2', 'wireless_antenna_ant_lb_area_mm2', 'wireless_antenna_ant_lb1_area_mm2', 'wireless_antenna_ant_mhb_1_afl_3_1_m77_2_m79_2_area_mm2', 'wireless_antenna_ant_mhb_1_m77_1_m79_1_m77_6_m79_3_area_mm2', 'wireless_antenna_ant_mhb_3_afl_3_3_m77_1_n79_1_pgy_1_area_mm2', 'wireless_antenna_ant_mhb_3_afl_3_3_m77_1_n79_1_pgy_1_1_area_mm2', 'wireless_antenna_ant_mhb_3_m77_2_m79_2_pgy_area_mm2', 'wireless_antenna_ant_mhb_4_m77_3_5g_abf1_area_mm2', 'wireless_antenna_ant_mhb_4_m77_3_uwb_area_mm2', 'wireless_antenna_ant_mhb_4_m77_3_uwb_1_area_mm2', 'wireless_antenna_ant_nc1_area_mm2', 'wireless_antenna_ant_nc1_1_area_mm2', 'wireless_antenna_ant_nc1_l1_m7879_3_area_mm2', 'wireless_antenna_ant_nc2_area_mm2', 'wireless_antenna_ant_nc2_lb2_area_mm2', 'wireless_antenna_ant_pgy_2_77_4_77_5_77_6_79_3_79_4_area_mm2', 'wireless_antenna_ant_pgy_2_77_4_77_5_77_6_79_3_79_4_1_area_mm2', 'wireless_antenna_ant_xrz_drx_79_4_77_5_core2_uwb_area_mm2', 'wireless_antenna_ant0_mhb_prx_mr7879_2_xrz_fxz_trx_r1_area_mm2', 'wireless_antenna_ant0_mhb_1_afl_3_1_m77_4_area_mm2', 'wireless_antenna_ant0_mhb_1_m78_5_m79_3_l5_m7879_2_area_mm2', 'wireless_antenna_ant10_ant_11_area_mm2', 'wireless_antenna_ant11_pgy_trx_mhb_up_nc_parasitic_area_mm2', 'wireless_antenna_ant2_mhb_3_afl_3_3_m77_3_n79_3_pgy_1_area_mm2', 'wireless_antenna_ant2_mhb_3_m7879_1_area_mm2', 'wireless_antenna_ant2_switch_area_mm2', 'wireless_antenna_ant3_mhb_mimod_mr7879_3_area_mm2', 'wireless_antenna_ant3_mhb_prx_mr7879_2_xrz_fxz_trx_sw_area_mm2', 'wireless_antenna_ant3_mhb_4_m77_2_m79_2_lb_3_area_mm2', 'wireless_antenna_ant3_mhb_4_m7879_4_abf1_area_mm2', 'wireless_antenna_ant4_lb_prx_area_mm2', 'wireless_antenna_ant6_gpsl1_wifi2_4g_c0_gg_trx_area_mm2', 'wireless_antenna_ant7_wifi_5g_c0_area_mm2', 'wireless_antenna_ant8_wifi2_4g_c1_gps_l5_area_mm2', 'wireless_antenna_ant9_wifi5g_c1_area_mm2', 'sensor_temperature_key_titlecase_area_mm2', 'sensor_temperature_key_uppercase_area_mm2', 'display_display_interface_area_mm2', 'display_display_pwr_area_mm2', 'display_display_pwr_solor_area_mm2', 'display_lcd_area_mm2', 'display_lcd_bypass_area_mm2', 'display_lcd_power_area_mm2', 'display_mlcd_fp_area_mm2', 'display_mlcd_pwr_area_mm2', 'display_mslcd_fp_area_mm2', 'audio_audio_bypass_area_mm2', 'audio_headphone_mic_vib_area_mm2', 'audio_mic_headphone_vib_area_mm2', 'audio_prosmart_pa_area_mm2', 'audio_smart_pa_3_area_mm2', 'audio_smartpa_area_mm2', 'audio_smartpa_tdd_area_mm2', 'audio_smartpa_tdd_noise_area_mm2', 'audio_motor_driver_audio_halfwidth_area_mm2', 'audio_motor_driver_audio_fullwidth_area_mm2')
EXPECTED_COLUMNS = ('id', 'manufacturer', 'platform', 'device_type', 'device_model', 'release_date', 'release_date_text', 'device_pic_url', 'source_document_id', 'area_unit', 'semantic_version', 'integration_total_area_mm2', 'small_system_total_area_mm2', 'peripheral_total_area_mm2', 'wireless_total_area_mm2', 'wireless_rf_total_area_mm2', 'wireless_nc_total_area_mm2', 'camera_total_area_mm2', 'fpc_total_area_mm2', 'io_expansion_total_area_mm2', 'wireless_nc_nfc_total_area_mm2', 'pmu_total_area_mm2', 'scharger_total_area_mm2', 'sensor_lowercase_group_total_area_mm2', 'sensor_uppercase_group_total_area_mm2', 'sim_total_area_mm2', 'test_point_total_area_mm2', 'storage_total_area_mm2', 'battery_total_area_mm2', 'analog_phy_total_area_mm2', 'wireless_rf_frontend_total_area_mm2', 'wireless_antenna_total_area_mm2', 'temperature_detection_total_area_mm2', 'display_total_area_mm2', 'audio_total_area_mm2', 'small_system_modem_area_mm2', 'small_system_modem_pmu_area_mm2', 'small_system_pmk_area_mm2', 'small_system_pmu1_area_mm2', 'small_system_pmu2_area_mm2', 'small_system_rfic_pmu_area_mm2', 'small_system_soc_total_area_mm2', 'small_system_ufs_total_area_mm2', 'peripheral_composite_area_mm2', 'power_charger_area_mm2', 'audio_codec_area_mm2', 'display_lcd_driver_power_area_mm2', 'power_scharger_area_mm2', 'power_wireless_charger_area_mm2', 'wireless_nc_uwb_area_mm2', 'wireless_nc_wifi_bt_baseband_area_mm2', 'wireless_rf_et_power_area_mm2', 'wireless_rf_mmw_smr_frontend_area_mm2', 'wireless_rf_rfic_mmw_peripherals_area_mm2', 'wireless_rf_cellular_rf_frontend_area_mm2', 'wireless_rf_tiantong_beidou_area_mm2', 'wireless_rf_satcom_tiantong_beidou_xingwang_area_mm2', 'wireless_rf_xingwang_1_0_area_mm2', 'power_5v_boost_area_mm2', 'camera_pwr_area_mm2', 'camera_front_cam_area_mm2', 'camera_front_camera_area_mm2', 'camera_points_sub_cam_lga_area_mm2', 'camera_rear_cam1_area_mm2', 'camera_rear_cam2_area_mm2', 'camera_rear_camera1_area_mm2', 'camera_rear_camera2_area_mm2', 'interface_fpc_interface_area_mm2', 'interface_fpc_interface_1_area_mm2', 'interface_fpc_interface_2_area_mm2', 'interface_fpc_interface_main_area_mm2', 'interface_fpc_interface_main_rf_area_mm2', 'interface_fpc_ntc_fpc_interface_main_area_mm2', 'interface_fpc_interface_typec_area_mm2', 'interface_io_expansion_iodie_area_mm2', 'wireless_nc_nfc_nfc_area_mm2', 'wireless_nc_nfc_110t_area_mm2', 'wireless_nc_nfc_fe_area_mm2', 'wireless_nc_nfc_fe_2_area_mm2', 'power_pmu_pmu0_buck1_area_mm2', 'power_pmu_pmu0_buck2_area_mm2', 'power_pmu_pmu0_digital_clk_area_mm2', 'power_pmu_pmu0_ldo_area_mm2', 'power_pmu_pmu1_buck1_area_mm2', 'power_pmu_pmu1_buck2_area_mm2', 'power_pmu_pmu2_buck1_area_mm2', 'power_pmu_pmu2_buck2_area_mm2', 'power_scharger_charger_res_uppercase_area_mm2', 'power_scharger_charger2_area_mm2', 'power_scharger_buck_charger_area_mm2', 'power_scharger_charger_res_titlecase_area_mm2', 'power_scharger_charger1_v700_area_mm2', 'power_scharger_sc1_area_mm2', 'power_scharger_sc2_area_mm2', 'power_scharger_scharger_v510_area_mm2', 'power_scharger_wl_charger_area_mm2', 'sensor_lowercase_group_sensor_area_mm2', 'sensor_uppercase_group_sensor_area_mm2', 'sensor_ambient_light_and_proximity_area_mm2', 'sensor_ambientandproximity_area_mm2', 'sensor_sar_1_area_mm2', 'sensor_sar_sensor_area_mm2', 'interface_sim_main_area_mm2', 'interface_sim_sd_area_mm2', 'interface_sim_sd_ioexp_area_mm2', 'small_system_soc_gnd_area_mm2', 'small_system_soc_gpio_interface_area_mm2', 'small_system_soc_hs_interface_area_mm2', 'small_system_soc_pwr1_area_mm2', 'small_system_soc_pwr2_area_mm2', 'small_system_soc_pwr3_area_mm2', 'small_system_soc_pwr4_area_mm2', 'small_system_ufs_memory_area_mm2', 'test_point_sim_lga_point_area_mm2', 'test_point_test_point_lowercase_area_mm2', 'test_point_test_point_titlecase_area_mm2', 'test_point_test_point_shields_area_mm2', 'storage_memory_area_mm2', 'power_battery_battery_area_mm2', 'power_battery_battery_1_protection_area_mm2', 'power_battery_battery_bjeh_protection_area_mm2', 'power_battery_battery_main_area_mm2', 'power_battery_bjeh_area_mm2', 'wireless_nc_gnss_bds_baseband_area_mm2', 'wireless_rf_satellite_bds_trx_area_mm2', 'wireless_rf_satellite_bds_trx_pa_area_mm2', 'wireless_rf_satellite_bds_trx1_rf_end_area_mm2', 'wireless_rf_satellite_bds_trx2_rf_end_area_mm2', 'wireless_nc_connectivity_1106_control_area_mm2', 'wireless_nc_connectivity_1106_power1_area_mm2', 'wireless_nc_connectivity_1106_power2_area_mm2', 'wireless_nc_connectivity_1107_control_area_mm2', 'wireless_nc_connectivity_1107_power1_area_mm2', 'wireless_nc_connectivity_1107_power2_area_mm2', 'wireless_nc_connectivity_control_1_area_mm2', 'wireless_nc_connectivity_control_bb_area_mm2', 'wireless_nc_connectivity_control_1107_area_mm2', 'wireless_nc_connectivity_power_1107_area_mm2', 'wireless_nc_connectivity_power_6506_area_mm2', 'wireless_nc_connectivity_power1_area_mm2', 'wireless_nc_connectivity_power1_1_area_mm2', 'wireless_nc_connectivity_power2_area_mm2', 'wireless_nc_connectivity_power2_1_area_mm2', 'wireless_nc_satellite_fxz_bbic_power_area_mm2', 'wireless_rf_satellite_fxz_bbic_rf_area_mm2', 'wireless_rf_satellite_fxz_rf_area_mm2', 'wireless_rf_satellite_fxz_rf_1_area_mm2', 'wireless_rf_satellite_fxz_rf_2_area_mm2', 'wireless_nc_gnss_gps_l1_l5_area_mm2', 'wireless_rf_band_path_lb_main_area_mm2', 'wireless_antenna_lds_ant_area_mm2', 'wireless_rf_composite_mhb_main_gpsl1_wifi_c0_nr1_area_mm2', 'wireless_rf_satellite_pgy_nsv_drx_area_mm2', 'wireless_nc_satellite_pgy_bb_main_area_mm2', 'wireless_rf_satellite_pgy_drx_area_mm2', 'wireless_rf_satellite_pgy_drx_1_area_mm2', 'wireless_rf_satellite_pgy_trx_area_mm2', 'wireless_rf_satellite_pgy_trx_1_area_mm2', 'wireless_rf_satellite_pgy_trx_pa_area_mm2', 'wireless_satellite_pgy212r_unclassified_area_mm2', 'wireless_rf_satellite_pgy21tx_candidate_area_mm2', 'wireless_nc_rf_path_rf0_2g4_c0_area_mm2', 'wireless_nc_rf_path_rf1_5g_c0_area_mm2', 'wireless_nc_rf_path_rf2_2g4_c1_area_mm2', 'wireless_nc_rf_path_rf3_5g_c1_area_mm2', 'wireless_antenna_side_ant_area_mm2', 'wireless_nc_wifi_2g4_c0_area_mm2', 'wireless_nc_wifi_2g4_c1_area_mm2', 'wireless_nc_wifi_5g_c0_area_mm2', 'wireless_nc_wifi_5g_c1_area_mm2', 'wireless_rf_shared_xrz_gg_fxz_1t3r_area_mm2', 'wireless_rf_satellite_xrz_prx_drx_area_mm2', 'wireless_rf_satellite_xrz_trx_pa_area_mm2', 'interface_analog_phy_usb_interface_area_mm2', 'interface_analog_phy_usb_protection_titlecase_area_mm2', 'interface_analog_phy_usb_protection_lowercase_area_mm2', 'wireless_rf_frontend_afl_3_lb_trx_area_mm2', 'wireless_rf_frontend_afl_3_mhb_trx_area_mm2', 'wireless_rf_frontend_afl_6_tx0_prx_area_mm2', 'wireless_rf_frontend_afl_6_tx1_mimo_p_area_mm2', 'wireless_rf_frontend_boost_bypass_area_mm2', 'wireless_rf_frontend_gsm_pa_area_mm2', 'wireless_rf_frontend_gsm_pa_1_area_mm2', 'wireless_rf_frontend_lb_l_pamid_area_mm2', 'wireless_rf_frontend_lb_lpamid_area_mm2', 'wireless_rf_frontend_lb_lpamid_1_area_mm2', 'wireless_rf_frontend_lb_rf_switch_area_mm2', 'wireless_rf_frontend_lb_rf_switch_1_area_mm2', 'wireless_rf_frontend_lmhb_ldifem_area_mm2', 'wireless_rf_frontend_lmhb_l_difem_area_mm2', 'wireless_rf_frontend_lmhb_ldifem_1_area_mm2', 'wireless_rf_frontend_mhb_l_pamid_area_mm2', 'wireless_rf_frontend_mhb_l_pamid_1_area_mm2', 'wireless_rf_frontend_mhb_rf_switch_area_mm2', 'wireless_rf_frontend_mhb_rf_switch_1_area_mm2', 'wireless_rf_frontend_mhb_trx_area_mm2', 'wireless_rf_frontend_mimo_drx_area_mm2', 'wireless_rf_frontend_mimo_drx_2_area_mm2', 'wireless_rf_frontend_mimo_drx_lna_area_mm2', 'wireless_rf_frontend_mimo_lna_area_mm2', 'wireless_rf_frontend_mimo_prx_lna_area_mm2', 'wireless_rf_frontend_mimo_prx_lna_1_area_mm2', 'wireless_rf_frontend_rf_bypass_area_mm2', 'wireless_rf_frontend_rf_pa_pmu_0_area_mm2', 'wireless_rf_frontend_rf_pa_pmu_1_area_mm2', 'wireless_rf_frontend_rf_pa_pmu1_apt_1_area_mm2', 'wireless_rf_frontend_rf_pa_pmu1_et_area_mm2', 'wireless_rf_frontend_rf_pmu0_et_area_mm2', 'wireless_rf_frontend_rf_pmu0_et_1_area_mm2', 'wireless_rf_frontend_rf_pwr_area_mm2', 'wireless_rf_frontend_rfic_pmu_area_mm2', 'wireless_rf_frontend_rfic_port_area_mm2', 'wireless_rf_frontend_rfic_port_1_area_mm2', 'wireless_rf_frontend_rfic_power_area_mm2', 'wireless_rf_frontend_rfic_power_1_area_mm2', 'wireless_rf_frontend_sub_3g_mhb_trx_area_mm2', 'wireless_rf_frontend_sub_3g_mhb_trx_1_area_mm2', 'wireless_rf_frontend_sub3g_lb_trx_area_mm2', 'wireless_rf_frontend_sub6g_tx0_prx_area_mm2', 'wireless_rf_frontend_sub6g_tx0_prx_1_area_mm2', 'wireless_rf_frontend_sub6g_tx1_mimo_p_area_mm2', 'wireless_rf_frontend_sub6g_tx1_mimo_p_1_area_mm2', 'wireless_antenna_ant_lb_area_mm2', 'wireless_antenna_ant_lb1_area_mm2', 'wireless_antenna_ant_mhb_1_afl_3_1_m77_2_m79_2_area_mm2', 'wireless_antenna_ant_mhb_1_m77_1_m79_1_m77_6_m79_3_area_mm2', 'wireless_antenna_ant_mhb_3_afl_3_3_m77_1_n79_1_pgy_1_area_mm2', 'wireless_antenna_ant_mhb_3_afl_3_3_m77_1_n79_1_pgy_1_1_area_mm2', 'wireless_antenna_ant_mhb_3_m77_2_m79_2_pgy_area_mm2', 'wireless_antenna_ant_mhb_4_m77_3_5g_abf1_area_mm2', 'wireless_antenna_ant_mhb_4_m77_3_uwb_area_mm2', 'wireless_antenna_ant_mhb_4_m77_3_uwb_1_area_mm2', 'wireless_antenna_ant_nc1_area_mm2', 'wireless_antenna_ant_nc1_1_area_mm2', 'wireless_antenna_ant_nc1_l1_m7879_3_area_mm2', 'wireless_antenna_ant_nc2_area_mm2', 'wireless_antenna_ant_nc2_lb2_area_mm2', 'wireless_antenna_ant_pgy_2_77_4_77_5_77_6_79_3_79_4_area_mm2', 'wireless_antenna_ant_pgy_2_77_4_77_5_77_6_79_3_79_4_1_area_mm2', 'wireless_antenna_ant_xrz_drx_79_4_77_5_core2_uwb_area_mm2', 'wireless_antenna_ant0_mhb_prx_mr7879_2_xrz_fxz_trx_r1_area_mm2', 'wireless_antenna_ant0_mhb_1_afl_3_1_m77_4_area_mm2', 'wireless_antenna_ant0_mhb_1_m78_5_m79_3_l5_m7879_2_area_mm2', 'wireless_antenna_ant10_ant_11_area_mm2', 'wireless_antenna_ant11_pgy_trx_mhb_up_nc_parasitic_area_mm2', 'wireless_antenna_ant2_mhb_3_afl_3_3_m77_3_n79_3_pgy_1_area_mm2', 'wireless_antenna_ant2_mhb_3_m7879_1_area_mm2', 'wireless_antenna_ant2_switch_area_mm2', 'wireless_antenna_ant3_mhb_mimod_mr7879_3_area_mm2', 'wireless_antenna_ant3_mhb_prx_mr7879_2_xrz_fxz_trx_sw_area_mm2', 'wireless_antenna_ant3_mhb_4_m77_2_m79_2_lb_3_area_mm2', 'wireless_antenna_ant3_mhb_4_m7879_4_abf1_area_mm2', 'wireless_antenna_ant4_lb_prx_area_mm2', 'wireless_antenna_ant6_gpsl1_wifi2_4g_c0_gg_trx_area_mm2', 'wireless_antenna_ant7_wifi_5g_c0_area_mm2', 'wireless_antenna_ant8_wifi2_4g_c1_gps_l5_area_mm2', 'wireless_antenna_ant9_wifi5g_c1_area_mm2', 'sensor_temperature_key_titlecase_area_mm2', 'sensor_temperature_key_uppercase_area_mm2', 'display_display_interface_area_mm2', 'display_display_pwr_area_mm2', 'display_display_pwr_solor_area_mm2', 'display_lcd_area_mm2', 'display_lcd_bypass_area_mm2', 'display_lcd_power_area_mm2', 'display_mlcd_fp_area_mm2', 'display_mlcd_pwr_area_mm2', 'display_mslcd_fp_area_mm2', 'audio_audio_bypass_area_mm2', 'audio_headphone_mic_vib_area_mm2', 'audio_mic_headphone_vib_area_mm2', 'audio_prosmart_pa_area_mm2', 'audio_smart_pa_3_area_mm2', 'audio_smartpa_area_mm2', 'audio_smartpa_tdd_area_mm2', 'audio_smartpa_tdd_noise_area_mm2', 'audio_motor_driver_audio_halfwidth_area_mm2', 'audio_motor_driver_audio_fullwidth_area_mm2', 'device_design_attributes', 'raw_value_annotations', 'additional_metrics', 'module_chip_models', 'unmapped_integration_values', 'created_at', 'updated_at')
WRITE_COLUMNS = tuple(c for c in EXPECTED_COLUMNS if c not in MANAGED_COLUMNS)

# Exact mapping: (source detail[].module_name, source integration[].sub_module_name) -> target area column.
FIELD_MAP = {('小系统', 'Modem'): 'small_system_modem_area_mm2', ('小系统', 'Modem PMU'): 'small_system_modem_pmu_area_mm2', ('小系统', 'PMK'): 'small_system_pmk_area_mm2', ('小系统', 'PMU1'): 'small_system_pmu1_area_mm2', ('小系统', 'PMU2'): 'small_system_pmu2_area_mm2', ('小系统', 'RFIC PMU'): 'small_system_rfic_pmu_area_mm2', ('小系统', 'SOC'): 'small_system_soc_total_area_mm2', ('小系统', 'UFS'): 'small_system_ufs_total_area_mm2', ('外设', 'CAM_PWR/闪光灯/MIC_VIB/SmartPA/NFC/sensor/接口/低压特性等'): 'peripheral_composite_area_mm2', ('外设', 'Charger'): 'power_charger_area_mm2', ('外设', 'codec'): 'audio_codec_area_mm2', ('外设', 'LCD 驱动/电源'): 'display_lcd_driver_power_area_mm2', ('外设', 'Scharge'): 'power_scharger_area_mm2', ('外设', 'Wcharge'): 'power_wireless_charger_area_mm2', ('NC', 'UWB'): 'wireless_nc_uwb_area_mm2', ('无线 / NC', 'UWB'): 'wireless_nc_uwb_area_mm2', ('NC', 'Wi-Fi/BT BB'): 'wireless_nc_wifi_bt_baseband_area_mm2', ('无线 / NC', 'Wi-Fi/BT BB'): 'wireless_nc_wifi_bt_baseband_area_mm2', ('RF', 'ET_POWER'): 'wireless_rf_et_power_area_mm2', ('无线 / RF', 'ET_POWER'): 'wireless_rf_et_power_area_mm2', ('RF', 'MMW（SMR+前端）'): 'wireless_rf_mmw_smr_frontend_area_mm2', ('无线 / RF', 'MMW（SMR+前端）'): 'wireless_rf_mmw_smr_frontend_area_mm2', ('RF', 'RFIC（含mmw RFIC）+ 外围'): 'wireless_rf_rfic_mmw_peripherals_area_mm2', ('无线 / RF', 'RFIC（含mmw RFIC）+ 外围'): 'wireless_rf_rfic_mmw_peripherals_area_mm2', ('RF', '射频蜂窝前端'): 'wireless_rf_cellular_rf_frontend_area_mm2', ('无线 / RF', '射频蜂窝前端'): 'wireless_rf_cellular_rf_frontend_area_mm2', ('RF', '天通/北斗'): 'wireless_rf_tiantong_beidou_area_mm2', ('无线 / RF', '天通/北斗'): 'wireless_rf_tiantong_beidou_area_mm2', ('RF', '卫星通信（天通/北斗/星网）'): 'wireless_rf_satcom_tiantong_beidou_xingwang_area_mm2', ('无线 / RF', '卫星通信（天通/北斗/星网）'): 'wireless_rf_satcom_tiantong_beidou_xingwang_area_mm2', ('RF', '星网1.0'): 'wireless_rf_xingwang_1_0_area_mm2', ('无线 / RF', '星网1.0'): 'wireless_rf_xingwang_1_0_area_mm2', ('5V boost', '5V boost'): 'power_5v_boost_area_mm2', ('CAM', 'CAM_PWR'): 'camera_pwr_area_mm2', ('CAM', 'FRONT_CAM'): 'camera_front_cam_area_mm2', ('CAM', 'FRONT_CAMERA'): 'camera_front_camera_area_mm2', ('CAM', 'Points_Sub_Cam_LGA'): 'camera_points_sub_cam_lga_area_mm2', ('CAM', 'REAR_CAM1'): 'camera_rear_cam1_area_mm2', ('CAM', 'REAR_CAM2'): 'camera_rear_cam2_area_mm2', ('CAM', 'REAR_CAMERA1'): 'camera_rear_camera1_area_mm2', ('CAM', 'REAR_CAMERA2'): 'camera_rear_camera2_area_mm2', ('FPC', 'FPC_Interface'): 'interface_fpc_interface_area_mm2', ('FPC', 'FPC_Interface_1'): 'interface_fpc_interface_1_area_mm2', ('FPC', 'FPC_Interface_2'): 'interface_fpc_interface_2_area_mm2', ('FPC', 'FPC_Interface_Main'): 'interface_fpc_interface_main_area_mm2', ('FPC', 'FPC_Interface_Main_RF'): 'interface_fpc_interface_main_rf_area_mm2', ('FPC', 'NTC_FPC_Interface_Main'): 'interface_fpc_ntc_fpc_interface_main_area_mm2', ('FPC', 'FPC_Interface_Typec'): 'interface_fpc_interface_typec_area_mm2', ('IO扩展', 'IODIE'): 'interface_io_expansion_iodie_area_mm2', ('NFC', 'NFC'): 'wireless_nc_nfc_nfc_area_mm2', ('NFC', 'NFC_110T'): 'wireless_nc_nfc_110t_area_mm2', ('NFC', 'NFC_FE'): 'wireless_nc_nfc_fe_area_mm2', ('NFC', 'NFC_FE_2'): 'wireless_nc_nfc_fe_2_area_mm2', ('PMU', 'PMU0_BUCK1'): 'power_pmu_pmu0_buck1_area_mm2', ('PMU', 'PMU0_BUCK2'): 'power_pmu_pmu0_buck2_area_mm2', ('PMU', 'PMU0_DIGITAL_CLK'): 'power_pmu_pmu0_digital_clk_area_mm2', ('PMU', 'PMU0_LDO'): 'power_pmu_pmu0_ldo_area_mm2', ('PMU', 'PMU1_BUCK1'): 'power_pmu_pmu1_buck1_area_mm2', ('PMU', 'PMU1_BUCK2'): 'power_pmu_pmu1_buck2_area_mm2', ('PMU', 'PMU2_BUCK1'): 'power_pmu_pmu2_buck1_area_mm2', ('PMU', 'PMU2_BUCK2'): 'power_pmu_pmu2_buck2_area_mm2', ('SC', 'CHARGER_RES'): 'power_scharger_charger_res_uppercase_area_mm2', ('Scharger', 'CHARGER_RES'): 'power_scharger_charger_res_uppercase_area_mm2', ('Scharger（原始模块：SC / Scharger）', 'CHARGER_RES'): 'power_scharger_charger_res_uppercase_area_mm2', ('SC', 'CHARGER2'): 'power_scharger_charger2_area_mm2', ('Scharger', 'CHARGER2'): 'power_scharger_charger2_area_mm2', ('Scharger（原始模块：SC / Scharger）', 'CHARGER2'): 'power_scharger_charger2_area_mm2', ('SC', 'Buck_Charger'): 'power_scharger_buck_charger_area_mm2', ('Scharger', 'Buck_Charger'): 'power_scharger_buck_charger_area_mm2', ('Scharger（原始模块：SC / Scharger）', 'Buck_Charger'): 'power_scharger_buck_charger_area_mm2', ('SC', 'Charger_RES'): 'power_scharger_charger_res_titlecase_area_mm2', ('Scharger', 'Charger_RES'): 'power_scharger_charger_res_titlecase_area_mm2', ('Scharger（原始模块：SC / Scharger）', 'Charger_RES'): 'power_scharger_charger_res_titlecase_area_mm2', ('SC', 'Charger1_V700'): 'power_scharger_charger1_v700_area_mm2', ('Scharger', 'Charger1_V700'): 'power_scharger_charger1_v700_area_mm2', ('Scharger（原始模块：SC / Scharger）', 'Charger1_V700'): 'power_scharger_charger1_v700_area_mm2', ('SC', 'SC1'): 'power_scharger_sc1_area_mm2', ('Scharger', 'SC1'): 'power_scharger_sc1_area_mm2', ('Scharger（原始模块：SC / Scharger）', 'SC1'): 'power_scharger_sc1_area_mm2', ('SC', 'SC2'): 'power_scharger_sc2_area_mm2', ('Scharger', 'SC2'): 'power_scharger_sc2_area_mm2', ('Scharger（原始模块：SC / Scharger）', 'SC2'): 'power_scharger_sc2_area_mm2', ('SC', 'Scharger_V510'): 'power_scharger_scharger_v510_area_mm2', ('Scharger', 'Scharger_V510'): 'power_scharger_scharger_v510_area_mm2', ('Scharger（原始模块：SC / Scharger）', 'Scharger_V510'): 'power_scharger_scharger_v510_area_mm2', ('SC', 'WL_Charger'): 'power_scharger_wl_charger_area_mm2', ('Scharger', 'WL_Charger'): 'power_scharger_wl_charger_area_mm2', ('Scharger（原始模块：SC / Scharger）', 'WL_Charger'): 'power_scharger_wl_charger_area_mm2', ('sensor', 'SENSOR'): 'sensor_lowercase_group_sensor_area_mm2', ('SENSOR', 'SENSOR'): 'sensor_uppercase_group_sensor_area_mm2', ('SENSOR', 'Ambient_Light_and_Proximity'): 'sensor_ambient_light_and_proximity_area_mm2', ('SENSOR', 'AmbientandProximity'): 'sensor_ambientandproximity_area_mm2', ('SENSOR', 'Sar_1'): 'sensor_sar_1_area_mm2', ('SENSOR', 'SAR_SENSOR'): 'sensor_sar_sensor_area_mm2', ('SIM', 'SIM_main'): 'interface_sim_main_area_mm2', ('SIM', 'SIM_SD'): 'interface_sim_sd_area_mm2', ('SIM', 'SIM_SD_IOEXP'): 'interface_sim_sd_ioexp_area_mm2', ('SOC', 'SOC_GND'): 'small_system_soc_gnd_area_mm2', ('SOC', 'SOC_GPIO_interface'): 'small_system_soc_gpio_interface_area_mm2', ('SOC', 'SOC_HS_interface'): 'small_system_soc_hs_interface_area_mm2', ('SOC', 'SOC_PWR1'): 'small_system_soc_pwr1_area_mm2', ('SOC', 'SOC_PWR2'): 'small_system_soc_pwr2_area_mm2', ('SOC', 'SOC_PWR3'): 'small_system_soc_pwr3_area_mm2', ('SOC', 'SOC_PWR4'): 'small_system_soc_pwr4_area_mm2', ('UFS', 'Memory'): 'small_system_ufs_memory_area_mm2', ('测试点', 'SIM_LGA_Point'): 'test_point_sim_lga_point_area_mm2', ('测试点', 'Test_point'): 'test_point_test_point_lowercase_area_mm2', ('测试点', 'Test_Point'): 'test_point_test_point_titlecase_area_mm2', ('测试点', 'Test_point_shields'): 'test_point_test_point_shields_area_mm2', ('存储', 'memory'): 'storage_memory_area_mm2', ('电池', 'Battery'): 'power_battery_battery_area_mm2', ('电池', 'Battery_1(电池保护)'): 'power_battery_battery_1_protection_area_mm2', ('电池', 'Battery_BJEH(电池保护)'): 'power_battery_battery_bjeh_protection_area_mm2', ('电池', 'Battery_Main'): 'power_battery_battery_main_area_mm2', ('电池', 'BJEH'): 'power_battery_bjeh_area_mm2', ('短距', 'BDS_BB'): 'wireless_nc_gnss_bds_baseband_area_mm2', ('短距', 'BDS_TRX'): 'wireless_rf_satellite_bds_trx_area_mm2', ('短距', 'BDS_TRX_PA'): 'wireless_rf_satellite_bds_trx_pa_area_mm2', ('短距', 'BDS_TRX1_RF_END'): 'wireless_rf_satellite_bds_trx1_rf_end_area_mm2', ('短距', 'BDS_TRX2_RF_END'): 'wireless_rf_satellite_bds_trx2_rf_end_area_mm2', ('短距', 'CONNECTIVITY_1106_Control'): 'wireless_nc_connectivity_1106_control_area_mm2', ('短距', 'CONNECTIVITY_1106_Power1'): 'wireless_nc_connectivity_1106_power1_area_mm2', ('短距', 'CONNECTIVITY_1106_Power2'): 'wireless_nc_connectivity_1106_power2_area_mm2', ('短距', 'CONNECTIVITY_1107_Control'): 'wireless_nc_connectivity_1107_control_area_mm2', ('短距', 'CONNECTIVITY_1107_Power1'): 'wireless_nc_connectivity_1107_power1_area_mm2', ('短距', 'CONNECTIVITY_1107_Power2'): 'wireless_nc_connectivity_1107_power2_area_mm2', ('短距', 'CONNECTIVITY_Contral_1'): 'wireless_nc_connectivity_control_1_area_mm2', ('短距', 'CONNECTIVITY_Contral_BB'): 'wireless_nc_connectivity_control_bb_area_mm2', ('短距', 'CONNECTIVITY_Ctrl_1107'): 'wireless_nc_connectivity_control_1107_area_mm2', ('短距', 'CONNECTIVITY_Power_1107'): 'wireless_nc_connectivity_power_1107_area_mm2', ('短距', 'CONNECTIVITY_Power_6506'): 'wireless_nc_connectivity_power_6506_area_mm2', ('短距', 'CONNECTIVITY_Power1'): 'wireless_nc_connectivity_power1_area_mm2', ('短距', 'CONNECTIVITY_Power1_1'): 'wireless_nc_connectivity_power1_1_area_mm2', ('短距', 'CONNECTIVITY_Power2'): 'wireless_nc_connectivity_power2_area_mm2', ('短距', 'CONNECTIVITY_Power2_1'): 'wireless_nc_connectivity_power2_1_area_mm2', ('短距', 'FXZ_BBIC_PWR'): 'wireless_nc_satellite_fxz_bbic_power_area_mm2', ('短距', 'FXZ_BBIC_RF'): 'wireless_rf_satellite_fxz_bbic_rf_area_mm2', ('短距', 'FXZ_RF'): 'wireless_rf_satellite_fxz_rf_area_mm2', ('短距', 'FXZ_RF_1'): 'wireless_rf_satellite_fxz_rf_1_area_mm2', ('短距', 'FXZ_RF_2'): 'wireless_rf_satellite_fxz_rf_2_area_mm2', ('短距', 'GPS_L1_L5'): 'wireless_nc_gnss_gps_l1_l5_area_mm2', ('短距', 'LB_Main'): 'wireless_rf_band_path_lb_main_area_mm2', ('短距', 'LDS_ant'): 'wireless_antenna_lds_ant_area_mm2', ('短距', 'MHB_main_GPSL1_wifi_C0_NR1'): 'wireless_rf_composite_mhb_main_gpsl1_wifi_c0_nr1_area_mm2', ('短距', 'NSV_PGY_DRX'): 'wireless_rf_satellite_pgy_nsv_drx_area_mm2', ('短距', 'PGY_BB_main'): 'wireless_nc_satellite_pgy_bb_main_area_mm2', ('短距', 'PGY_DRX'): 'wireless_rf_satellite_pgy_drx_area_mm2', ('短距', 'PGY_DRX_1'): 'wireless_rf_satellite_pgy_drx_1_area_mm2', ('短距', 'PGY_TRX'): 'wireless_rf_satellite_pgy_trx_area_mm2', ('短距', 'PGY_TRX_1'): 'wireless_rf_satellite_pgy_trx_1_area_mm2', ('短距', 'PGY_TRX_PA'): 'wireless_rf_satellite_pgy_trx_pa_area_mm2', ('短距', 'PGY212R'): 'wireless_satellite_pgy212r_unclassified_area_mm2', ('短距', 'PGY21TX'): 'wireless_rf_satellite_pgy21tx_candidate_area_mm2', ('短距', 'RF0_2G4_C0'): 'wireless_nc_rf_path_rf0_2g4_c0_area_mm2', ('短距', 'RF1_5G_C0'): 'wireless_nc_rf_path_rf1_5g_c0_area_mm2', ('短距', 'RF2_2G4_C1'): 'wireless_nc_rf_path_rf2_2g4_c1_area_mm2', ('短距', 'RF3_5G_C1'): 'wireless_nc_rf_path_rf3_5g_c1_area_mm2', ('短距', 'side_ANT'): 'wireless_antenna_side_ant_area_mm2', ('短距', 'WIFI_2G4_C0'): 'wireless_nc_wifi_2g4_c0_area_mm2', ('短距', 'WIFI_2G4_C1'): 'wireless_nc_wifi_2g4_c1_area_mm2', ('短距', 'WIFI_5G_C0'): 'wireless_nc_wifi_5g_c0_area_mm2', ('短距', 'WIFI_5G_C1'): 'wireless_nc_wifi_5g_c1_area_mm2', ('短距', 'XRZ_GG_FXZ_1T3R'): 'wireless_rf_shared_xrz_gg_fxz_1t3r_area_mm2', ('短距', 'XRZ_PRX_DRX'): 'wireless_rf_satellite_xrz_prx_drx_area_mm2', ('短距', 'XRZ_TRX_PA'): 'wireless_rf_satellite_xrz_trx_pa_area_mm2', ('模拟PHY', 'USB_Interface'): 'interface_analog_phy_usb_interface_area_mm2', ('模拟PHY', 'USB_Protection'): 'interface_analog_phy_usb_protection_titlecase_area_mm2', ('模拟PHY', 'USB_protection'): 'interface_analog_phy_usb_protection_lowercase_area_mm2', ('射频', 'AFL-3_LB_TRX'): 'wireless_rf_frontend_afl_3_lb_trx_area_mm2', ('射频', 'AFL-3_MHB_TRX'): 'wireless_rf_frontend_afl_3_mhb_trx_area_mm2', ('射频', 'AFL-6_Tx0_Prx'): 'wireless_rf_frontend_afl_6_tx0_prx_area_mm2', ('射频', 'AFL-6_Tx1_MIMO_P'): 'wireless_rf_frontend_afl_6_tx1_mimo_p_area_mm2', ('射频', 'Boost_Bypass'): 'wireless_rf_frontend_boost_bypass_area_mm2', ('射频', 'GSM_PA'): 'wireless_rf_frontend_gsm_pa_area_mm2', ('射频', 'GSM_PA_1'): 'wireless_rf_frontend_gsm_pa_1_area_mm2', ('射频', 'LB_L_PAMID'): 'wireless_rf_frontend_lb_l_pamid_area_mm2', ('射频', 'LB_LPAMID'): 'wireless_rf_frontend_lb_lpamid_area_mm2', ('射频', 'LB_LPAMID_1'): 'wireless_rf_frontend_lb_lpamid_1_area_mm2', ('射频', 'LB_RF_SWITCH'): 'wireless_rf_frontend_lb_rf_switch_area_mm2', ('射频', 'LB_RF_SWITCH_1'): 'wireless_rf_frontend_lb_rf_switch_1_area_mm2', ('射频', 'LMHB_LDIFEM'): 'wireless_rf_frontend_lmhb_ldifem_area_mm2', ('射频', 'LMHB_L-DIFEM'): 'wireless_rf_frontend_lmhb_l_difem_area_mm2', ('射频', 'LMHB_LDIFEM_1'): 'wireless_rf_frontend_lmhb_ldifem_1_area_mm2', ('射频', 'MHB_L_PAMID'): 'wireless_rf_frontend_mhb_l_pamid_area_mm2', ('射频', 'MHB_L_PAMID_1'): 'wireless_rf_frontend_mhb_l_pamid_1_area_mm2', ('射频', 'MHB_RF_SWITCH'): 'wireless_rf_frontend_mhb_rf_switch_area_mm2', ('射频', 'MHB_RF_SWITCH_1'): 'wireless_rf_frontend_mhb_rf_switch_1_area_mm2', ('射频', 'MHB_TRX'): 'wireless_rf_frontend_mhb_trx_area_mm2', ('射频', 'MIMO_DRX'): 'wireless_rf_frontend_mimo_drx_area_mm2', ('射频', 'MIMO_DRX_2'): 'wireless_rf_frontend_mimo_drx_2_area_mm2', ('射频', 'MIMO_DRX_LNA'): 'wireless_rf_frontend_mimo_drx_lna_area_mm2', ('射频', 'MIMO_LNA'): 'wireless_rf_frontend_mimo_lna_area_mm2', ('射频', 'MIMO_PRX_LNA'): 'wireless_rf_frontend_mimo_prx_lna_area_mm2', ('射频', 'MIMO_PRX_LNA_1'): 'wireless_rf_frontend_mimo_prx_lna_1_area_mm2', ('射频', 'RF_Bypass'): 'wireless_rf_frontend_rf_bypass_area_mm2', ('射频', 'RF_PA_PMU_0'): 'wireless_rf_frontend_rf_pa_pmu_0_area_mm2', ('射频', 'RF_PA_PMU_1'): 'wireless_rf_frontend_rf_pa_pmu_1_area_mm2', ('射频', 'RF_PA_PMU1_APT_1'): 'wireless_rf_frontend_rf_pa_pmu1_apt_1_area_mm2', ('射频', 'RF_PA_PMU1_ET'): 'wireless_rf_frontend_rf_pa_pmu1_et_area_mm2', ('射频', 'RF_PMU0_ET'): 'wireless_rf_frontend_rf_pmu0_et_area_mm2', ('射频', 'RF_PMU0_ET_1'): 'wireless_rf_frontend_rf_pmu0_et_1_area_mm2', ('射频', 'RF_PWR'): 'wireless_rf_frontend_rf_pwr_area_mm2', ('射频', 'RFIC_PMU'): 'wireless_rf_frontend_rfic_pmu_area_mm2', ('射频', 'RFIC_PORT'): 'wireless_rf_frontend_rfic_port_area_mm2', ('射频', 'RFIC_PORT_1'): 'wireless_rf_frontend_rfic_port_1_area_mm2', ('射频', 'RFIC_POWER'): 'wireless_rf_frontend_rfic_power_area_mm2', ('射频', 'RFIC_POWER_1'): 'wireless_rf_frontend_rfic_power_1_area_mm2', ('射频', 'Sub_3G_MHB_TRX'): 'wireless_rf_frontend_sub_3g_mhb_trx_area_mm2', ('射频', 'Sub_3G_MHB_TRX_1'): 'wireless_rf_frontend_sub_3g_mhb_trx_1_area_mm2', ('射频', 'Sub3G_LB_TRX'): 'wireless_rf_frontend_sub3g_lb_trx_area_mm2', ('射频', 'Sub6G_TX0_PRX'): 'wireless_rf_frontend_sub6g_tx0_prx_area_mm2', ('射频', 'Sub6G_TX0_PRX_1'): 'wireless_rf_frontend_sub6g_tx0_prx_1_area_mm2', ('射频', 'Sub6G_Tx1_MIMO_P'): 'wireless_rf_frontend_sub6g_tx1_mimo_p_area_mm2', ('射频', 'Sub6G_Tx1_MIMO_P_1'): 'wireless_rf_frontend_sub6g_tx1_mimo_p_1_area_mm2', ('天线', 'ANT_LB'): 'wireless_antenna_ant_lb_area_mm2', ('天线', 'ANT_LB1'): 'wireless_antenna_ant_lb1_area_mm2', ('天线', 'ANT_MHB-1_AFL-3-1_M77-2_M79-2'): 'wireless_antenna_ant_mhb_1_afl_3_1_m77_2_m79_2_area_mm2', ('天线', 'ANT_MHB-1_M77-1_M79-1_M77-6_M79-3'): 'wireless_antenna_ant_mhb_1_m77_1_m79_1_m77_6_m79_3_area_mm2', ('天线', 'ANT_MHB-3_AFL-3-3_M77-1_N79-1_PGY-1'): 'wireless_antenna_ant_mhb_3_afl_3_3_m77_1_n79_1_pgy_1_area_mm2', ('天线', 'ANT_MHB-3_AFL-3-3_M77-1_N79-1_PGY-1_1'): 'wireless_antenna_ant_mhb_3_afl_3_3_m77_1_n79_1_pgy_1_1_area_mm2', ('天线', 'ANT_MHB-3_M77-2_M79-2_PGY'): 'wireless_antenna_ant_mhb_3_m77_2_m79_2_pgy_area_mm2', ('天线', 'ANT_MHB-4_M77-3_5G_ABF1'): 'wireless_antenna_ant_mhb_4_m77_3_5g_abf1_area_mm2', ('天线', 'ANT_MHB-4_M77-3_UWB'): 'wireless_antenna_ant_mhb_4_m77_3_uwb_area_mm2', ('天线', 'ANT_MHB-4_M77-3_UWB_1'): 'wireless_antenna_ant_mhb_4_m77_3_uwb_1_area_mm2', ('天线', 'ANT_NC1'): 'wireless_antenna_ant_nc1_area_mm2', ('天线', 'ANT_NC1_1'): 'wireless_antenna_ant_nc1_1_area_mm2', ('天线', 'ANT_NC1_L1_M7879-3'): 'wireless_antenna_ant_nc1_l1_m7879_3_area_mm2', ('天线', 'ANT_NC2'): 'wireless_antenna_ant_nc2_area_mm2', ('天线', 'ANT_NC2_LB2'): 'wireless_antenna_ant_nc2_lb2_area_mm2', ('天线', 'ANT_PGY-2_77-4_77-5_77-6_79-3_79-4'): 'wireless_antenna_ant_pgy_2_77_4_77_5_77_6_79_3_79_4_area_mm2', ('天线', 'ANT_PGY-2_77-4_77-5_77-6_79-3_79-4_1'): 'wireless_antenna_ant_pgy_2_77_4_77_5_77_6_79_3_79_4_1_area_mm2', ('天线', 'ANT_XRZ_DRX_79-4_77-5_CORE2_UWB'): 'wireless_antenna_ant_xrz_drx_79_4_77_5_core2_uwb_area_mm2', ('天线', 'ANT0_MHB_PRX+MR7879_2_XRZ_TRX+FXZ_TRX_R1'): 'wireless_antenna_ant0_mhb_prx_mr7879_2_xrz_fxz_trx_r1_area_mm2', ('天线', 'ANT0_MHB-1_AFL-3-1_M77-4'): 'wireless_antenna_ant0_mhb_1_afl_3_1_m77_4_area_mm2', ('天线', 'ANT0_MHB-1_M78-5_M79-3_L5_M7879-2'): 'wireless_antenna_ant0_mhb_1_m78_5_m79_3_l5_m7879_2_area_mm2', ('天线', 'ANT10_ANT_11'): 'wireless_antenna_ant10_ant_11_area_mm2', ('天线', 'ANT11_PGY_TRX_MHB_UP_NC_parasitic'): 'wireless_antenna_ant11_pgy_trx_mhb_up_nc_parasitic_area_mm2', ('天线', 'ANT2_MHB-3_AFL-3-3_M77-3_N79-3_PGY-1'): 'wireless_antenna_ant2_mhb_3_afl_3_3_m77_3_n79_3_pgy_1_area_mm2', ('天线', 'ANT2_MHB-3_M7879-1'): 'wireless_antenna_ant2_mhb_3_m7879_1_area_mm2', ('天线', 'ANT2_SWITCH'): 'wireless_antenna_ant2_switch_area_mm2', ('天线', 'ANT3_MHB_MIMOD+MR7879_3'): 'wireless_antenna_ant3_mhb_mimod_mr7879_3_area_mm2', ('天线', 'ANT3_MHB_PRX+MR7879_2_XRZ_TRX+FXZ_TRX_SW'): 'wireless_antenna_ant3_mhb_prx_mr7879_2_xrz_fxz_trx_sw_area_mm2', ('天线', 'ANT3_MHB-4_M77_2_m79_2_LB_3'): 'wireless_antenna_ant3_mhb_4_m77_2_m79_2_lb_3_area_mm2', ('天线', 'ANT3_MHB-4_M7879-4_ABF1'): 'wireless_antenna_ant3_mhb_4_m7879_4_abf1_area_mm2', ('天线', 'ANT4_LB_PRX'): 'wireless_antenna_ant4_lb_prx_area_mm2', ('天线', 'ANT6_GPSL1_WIFI2_4G_C0_GG_TRX'): 'wireless_antenna_ant6_gpsl1_wifi2_4g_c0_gg_trx_area_mm2', ('天线', 'ANT7_WIFI_5G_C0'): 'wireless_antenna_ant7_wifi_5g_c0_area_mm2', ('天线', 'ANT8_WIFI2_4G_C1_GPS_L5'): 'wireless_antenna_ant8_wifi2_4g_c1_gps_l5_area_mm2', ('天线', 'ANT9_WIFI5G_C1'): 'wireless_antenna_ant9_wifi5g_c1_area_mm2', ('温度检测', 'Key'): 'sensor_temperature_key_titlecase_area_mm2', ('温度检测', 'KEY'): 'sensor_temperature_key_uppercase_area_mm2', ('显示', 'Display_INTERFACE'): 'display_display_interface_area_mm2', ('显示', 'Display_PWR'): 'display_display_pwr_area_mm2', ('显示', 'Display_PWR_SOLOR'): 'display_display_pwr_solor_area_mm2', ('显示', 'LCD'): 'display_lcd_area_mm2', ('显示', 'LCD Bypass'): 'display_lcd_bypass_area_mm2', ('显示', 'LCD_Power'): 'display_lcd_power_area_mm2', ('显示', 'MLCD_FP'): 'display_mlcd_fp_area_mm2', ('显示', 'MLCD_PWR'): 'display_mlcd_pwr_area_mm2', ('显示', 'MSLCD_FP'): 'display_mslcd_fp_area_mm2', ('音频', 'Audio bypass'): 'audio_audio_bypass_area_mm2', ('音频', 'headphone_MIC_VIB'): 'audio_headphone_mic_vib_area_mm2', ('音频', 'MIC_Headphone_VIB'): 'audio_mic_headphone_vib_area_mm2', ('音频', 'ProSmart_PA'): 'audio_prosmart_pa_area_mm2', ('音频', 'Smart_PA_3'): 'audio_smart_pa_3_area_mm2', ('音频', 'SmartPA'): 'audio_smartpa_area_mm2', ('音频', 'SmartPA_TDD'): 'audio_smartpa_tdd_area_mm2', ('音频', 'SmartPA_TDD_Noise'): 'audio_smartpa_tdd_noise_area_mm2', ('音频', '马达驱动(音频)'): 'audio_motor_driver_audio_halfwidth_area_mm2', ('音频', '马达驱动（音频）'): 'audio_motor_driver_audio_fullwidth_area_mm2'}

# Source detail[].total -> target module-level total field.
# "SC" and "Scharger" are both accepted as the same business classification.
# "5V boost" has one leaf only; its parent total is a fallback for the same leaf field.
MODULE_TOTAL_MAP = {'小系统': 'small_system_total_area_mm2', '小系统合计': 'small_system_total_area_mm2', '外设': 'peripheral_total_area_mm2', '外设合计': 'peripheral_total_area_mm2', 'RF': 'wireless_rf_total_area_mm2', 'NC': 'wireless_nc_total_area_mm2', '无线合计': 'wireless_total_area_mm2', 'CAM': 'camera_total_area_mm2', 'FPC': 'fpc_total_area_mm2', 'IO扩展': 'io_expansion_total_area_mm2', 'NFC': 'wireless_nc_nfc_total_area_mm2', 'PMU': 'pmu_total_area_mm2', 'SC': 'scharger_total_area_mm2', 'Scharger': 'scharger_total_area_mm2', 'sensor': 'sensor_lowercase_group_total_area_mm2', 'SENSOR': 'sensor_uppercase_group_total_area_mm2', 'SIM': 'sim_total_area_mm2', 'SOC': 'small_system_soc_total_area_mm2', 'UFS': 'small_system_ufs_total_area_mm2', '测试点': 'test_point_total_area_mm2', '存储': 'storage_total_area_mm2', '电池': 'battery_total_area_mm2', '模拟PHY': 'analog_phy_total_area_mm2', '射频': 'wireless_rf_frontend_total_area_mm2', '天线': 'wireless_antenna_total_area_mm2', '温度检测': 'temperature_detection_total_area_mm2', '显示': 'display_total_area_mm2', '音频': 'audio_total_area_mm2', '5V boost': 'power_5v_boost_area_mm2'}

MODULE_BUSINESS_PATH = {'小系统': '小系统', '外设': '外设', 'NC': '无线 → NC', '无线 / NC': '无线 → NC', 'RF': '无线 → RF', '无线 / RF': '无线 → RF', '5V boost': '电源管理 → 5V boost', 'CAM': '摄像头 → CAM', 'FPC': '连接与接口 → FPC', 'IO扩展': '连接与接口 → IO扩展', 'NFC': '无线 → NC → NFC', 'PMU': '电源管理 → PMU', 'SC': '电源管理 → Scharger', 'Scharger': '电源管理 → Scharger', 'Scharger（原始模块：SC / Scharger）': '电源管理 → Scharger', 'sensor': '传感器 → sensor（小写模块标识）', 'SENSOR': '传感器 → SENSOR（大写模块标识）', 'SIM': '连接与接口 → SIM', 'SOC': '小系统 → SOC', 'UFS': '小系统 → UFS', '测试点': '测试与调试 → 测试点', '存储': '存储', '电池': '电源管理 → 电池', '短距': '无线 → 短距（跨NC/RF/卫星/天线的来源分组）', '模拟PHY': '连接与接口 → 模拟PHY', '射频': '无线 → RF → 射频前端', '天线': '无线 → 天线', '温度检测': '传感器 → 温度检测（原始分组，业务归属待确认）', '显示': '显示', '音频': '音频'}

# Workbook group labels -> actual source module_name values.
SOURCE_GROUP_ALIASES = {'Scharger（原始模块：SC / Scharger）': ['SC', 'Scharger', 'Scharger（原始模块：SC / Scharger）'], '无线 / NC': ['NC', '无线 / NC'], '无线 / RF': ['RF', '无线 / RF']}

TOTAL_RAW_NOTE_MAP = {
    "小系统合计": "small_system_total_area_mm2",
    "外设合计": "peripheral_total_area_mm2",
    "无线合计": "wireless_total_area_mm2",
}

SPECIAL_ATTRIBUTE_MAP = {
    ("短距", "HL1PNHSHA"): "board_variant",
}

NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
ANNOTATED_NUMERIC_RE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*[（(].*[）)]$"
)
MAX_DECIMAL = Decimal("999999999999.999999")
SIX_PLACES = Decimal("0.000001")

class EtlError(RuntimeError):
    pass


def init_logging():
    logger = logging.getLogger("cis_integration_mongo_to_mysql")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        try:
            log_path = Path(__file__).resolve().with_name(
                f"{Path(__file__).stem}.log"
            )
        except Exception:
            log_path = Path.cwd() / f"{Path(__file__).stem}.log"
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)
    return logger


LOG = init_logging()


def log(message, level="info"):
    getattr(LOG, level.lower(), LOG.info)(message)


def identity_ref(identity):
    """Return a stable, non-reversible reference for log correlation."""
    if isinstance(identity, dict):
        values = [identity.get(k, "") for k in IDENTITY_COLUMNS]
    else:
        values = list(identity)
    payload = "\\x1f".join("" if v is None else str(v) for v in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def source_document_ref(value):
    if value is None:
        return "none"
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def area_text(number):
    """Canonical numeric text; strips insignificant zeroes but preserves precision."""
    normalized = number.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def canonical_json(value):
    return json.dumps(
        json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def json_safe(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if hasattr(value, "to_decimal"):
        try:
            return str(value.to_decimal())
        except Exception:
            return str(value)
    return str(value)


def as_list(value, field_name):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    raise EtlError(f"{field_name}必须是数组或对象")


def scalar_text(value, field_name, max_len):
    if value is None or isinstance(value, (dict, list, bool)):
        raise EtlError(f"{field_name}缺失或类型错误")
    text = str(value).strip()
    if not text:
        raise EtlError(f"{field_name}为空")
    if len(text) > max_len:
        raise EtlError(f"{field_name}长度超过目标列限制({max_len})")
    if "\x00" in text:
        raise EtlError(f"{field_name}包含NUL字符")
    return text


def decimal_from_value(value):
    """Return (Decimal|None, raw_annotation|None). Preserve '/' / Na / '1（低轨）'."""
    if value is None:
        return None, None

    if isinstance(value, bool):
        return None, str(value)

    if hasattr(value, "to_decimal"):
        try:
            value = value.to_decimal()
        except Exception:
            return None, str(value)

    if isinstance(value, Decimal):
        number = value
        raw_note = None
    elif isinstance(value, int):
        number = Decimal(value)
        raw_note = None
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise EtlError("面积值不是有限数值")
        number = Decimal(str(value))
        raw_note = None
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None, None
        if NUMERIC_RE.fullmatch(raw):
            number = Decimal(raw)
            raw_note = None
        else:
            annotated = ANNOTATED_NUMERIC_RE.fullmatch(raw)
            if annotated:
                number = Decimal(annotated.group(1))
                raw_note = raw
            else:
                return None, raw
    elif isinstance(value, dict) and len(value) == 1:
        for key in ("$numberInt", "$numberLong", "$numberDouble", "$numberDecimal"):
            if key in value:
                return decimal_from_value(value[key])
        return None, canonical_json(value)
    else:
        return None, canonical_json(value)

    if not number.is_finite():
        raise EtlError("面积值不是有限数值")

    with localcontext() as ctx:
        ctx.prec = 40
        quantized = number.quantize(SIX_PLACES, rounding=ROUND_HALF_UP)

    if quantized != number:
        raw_note = raw_note or str(value)

    if abs(quantized) > MAX_DECIMAL:
        raise EtlError("单条面积数值超出支持范围（最多12位整数、6位小数）")

    return quantized, raw_note


def append_json_value(container, key, value):
    """Merge values without silently discarding a different source value."""
    value = json_safe(value)
    if key not in container:
        container[key] = value
        return
    old = container[key]
    if canonical_json(old) == canonical_json(value):
        return
    if not isinstance(old, list):
        container[key] = [old]
    if canonical_json(value) not in {canonical_json(x) for x in container[key]}:
        container[key].append(value)


def append_deduplicated(items, value):
    signature = canonical_json(value)
    if all(canonical_json(x) != signature for x in items):
        items.append(json_safe(value))


def add_raw_annotation(raw_annotations, field, raw_value, source_path):
    if raw_value is None:
        return
    value = json_safe(raw_value)
    existing = raw_annotations.get(field)
    entry = {"raw_value": value, "source_path": source_path}
    if existing is None:
        raw_annotations[field] = value
    elif canonical_json(existing) == canonical_json(value):
        return
    else:
        if not isinstance(existing, list):
            raw_annotations[field] = [{"raw_value": existing, "source_path": "existing_source"}]
        append_deduplicated(raw_annotations[field], entry)


def add_unmapped(unmapped, module_name, sub_module_name, raw_value, unit, reason, source_path):
    if raw_value is None:
        return
    append_deduplicated(unmapped, {
        "module_name": module_name,
        "sub_module_name": sub_module_name,
        "raw_value": json_safe(raw_value),
        "unit": unit,
        "reason": reason,
        "source_path": source_path,
    })


def set_area_value(
    row, assigned, field, raw_value, source_path, raw_annotations,
    source_kind="other", source_key=None,
):
    """
    Store area observations without aggregation.

    Repeated detail_leaf observations from the same logical (module, submodule)
    are kept in original source order as comma-separated text.
    Different semantic source kinds that collide on one target column are not
    merged; identical single-value aliases are deduplicated, differing values fail.
    """
    if field not in AREA_COLUMNS:
        raise EtlError(f"映射目标不是面积字段：{field}")

    number, raw_note = decimal_from_value(raw_value)
    if number is not None:
        value_text = area_text(number)
        state = assigned.get(field)

        if state is None:
            row[field] = value_text
            assigned[field] = {
                "kind": source_kind,
                "key": source_key,
                "values": [value_text],
                "paths": [source_path],
            }
        elif (
            source_kind == "detail_leaf"
            and state["kind"] == "detail_leaf"
            and state["key"] == source_key
        ):
            candidate = row[field] + "," + value_text
            if len(candidate.encode("utf-8")) > 65535:
                raise EtlError(f"面积多值字段超过TEXT容量 field={field}")
            row[field] = candidate
            state["values"].append(value_text)
            state["paths"].append(source_path)
        elif len(state["values"]) == 1 and state["values"][0] == value_text:
            # Preserve existing source alias/fallback semantics when two paths
            # describe the exact same scalar fact; do not duplicate it.
            pass
        else:
            raise EtlError(
                f"MAPPING_CONFLICT field={field} "
                f"old_source_kind={state['kind']} new_source_kind={source_kind} "
                f"old_source_path={state['paths'][0]} new_source_path={source_path} "
                f"device_ref=REDACTED"
            )

    if raw_note is not None:
        add_raw_annotation(raw_annotations, field, raw_note, source_path)


def normalize_unit(value):
    if value is None or str(value).strip() == "":
        return "平方毫米"
    unit = str(value).strip().lower().replace(" ", "")
    accepted = {
        "平方毫米", "mm2", "mm²", "mm^2", "sq.mm", "sqmm", "squaremillimeter",
        "squaremillimeters",
    }
    if unit not in accepted:
        raise EtlError(
            "源数据面积单位不受支持（原始值已脱敏）；目标表统一为平方毫米，拒绝未经换算直接写入"
        )
    return "平方毫米"


def parse_release_date(value):
    if value is None or value == "":
        return None, None
    dt = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None, None
        normalized = raw.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d",
            ):
                try:
                    dt = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None, raw
    else:
        return None, str(value)

    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    # Target column is DATETIME(3).
    dt = dt.replace(microsecond=(dt.microsecond // 1000) * 1000)
    if dt.year < 1000 or dt.year > 9999:
        return None, str(value)
    return dt, None


def module_path(module_name):
    return MODULE_BUSINESS_PATH.get(module_name, f"来源模块：{module_name}")


def raw_note_target(module_name, metric_name):
    suffix = "原始值"
    if not isinstance(metric_name, str) or not metric_name.endswith(suffix):
        return None
    raw_name = metric_name[:-len(suffix)].strip()
    if raw_name in TOTAL_RAW_NOTE_MAP:
        return TOTAL_RAW_NOTE_MAP[raw_name]
    if raw_name in MODULE_TOTAL_MAP:
        return MODULE_TOTAL_MAP[raw_name]
    return FIELD_MAP.get((module_name, raw_name))


def add_metric(additional, module_name, business_path, metric_name, metric_value,
               group_key=None, label=None, source_path=None):
    append_deduplicated(additional, {
        "business_path": business_path,
        "module_name": module_name,
        "metric_name": metric_name,
        "metric_value": json_safe(metric_value),
        "group_key": group_key,
        "label": label,
        "source_path": source_path,
    })


def process_other(other, module_name, business_path, unit, row, assigned,
                  raw_annotations, additional):
    if other is None:
        return
    if not isinstance(other, dict):
        add_metric(additional, module_name, business_path, "other_raw", other,
                   source_path="integration_level.detail[].other")
        return

    other_type = other.get("type")
    info = other.get("info")
    info_items = as_list(info, "detail.other.info") if info is not None else []

    def process_metric(metric, group_key=None, label=None):
        if not isinstance(metric, dict):
            add_metric(additional, module_name, business_path, "raw_metric",
                       metric, group_key, label,
                       "integration_level.detail[].other.info")
            return
        metric_name = metric.get("metric_name")
        metric_value = metric.get("metric_value")
        metric_name_text = str(metric_name).strip() if metric_name is not None else ""
        target = raw_note_target(module_name, metric_name_text)
        if target:
            add_raw_annotation(
                raw_annotations, target, metric_value,
                f"integration_level.detail[{module_name}].other.info[{metric_name_text}]"
            )
            # Some historical documents preserve the raw value only in other.info.
            # Recover its numeric prefix when the mapped area field has no numeric value.
            if row.get(target) is None:
                set_area_value(
                    row, assigned, target, metric_value,
                    "integration_level.detail[].other.info",
                    raw_annotations,
                    source_kind="other_metric",
                    source_key=(module_name, metric_name_text),
                )
        else:
            add_metric(
                additional, module_name, business_path,
                metric_name_text or "unnamed_metric", metric_value,
                group_key, label,
                f"integration_level.detail[{module_name}].other.info",
            )

    if other_type == "multi":
        for group in info_items:
            if not isinstance(group, dict):
                process_metric(group)
                continue
            group_key = group.get("key")
            group_label = group.get("label")
            values = group.get("value")
            if isinstance(values, list):
                for metric in values:
                    process_metric(metric, group_key, group_label)
            elif values is not None:
                process_metric(
                    {"metric_name": "group_value", "metric_value": values},
                    group_key, group_label,
                )
            else:
                add_metric(
                    additional, module_name, business_path, "multi_group",
                    group, group_key, group_label,
                    f"integration_level.detail[{module_name}].other.info",
                )
    else:
        for metric in info_items:
            process_metric(metric)


def merge_existing_json(row, doc, column, expected_type):
    value = doc.get(column)
    if value is None:
        return
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            raise EtlError(f"MongoDB顶层{column}是字符串但不是合法JSON")
    if not isinstance(value, expected_type):
        raise EtlError(f"MongoDB顶层{column}类型不符合目标JSON列")
    row[column] = json_safe(value)


def transform_document(doc):
    if not isinstance(doc, dict):
        raise EtlError("MongoDB文档不是对象")

    row = {column: None for column in WRITE_COLUMNS}
    assigned = {}
    identity = {
        "manufacturer": scalar_text(doc.get("manufacturer"), "manufacturer", 64),
        "platform": scalar_text(doc.get("platform"), "platform", 128),
        "device_type": scalar_text(doc.get("device_type"), "device_type", 32),
        "device_model": scalar_text(doc.get("device_model"), "device_model", 128),
    }
    row.update(identity)

    object_id = doc.get("_id")
    if object_id is not None:
        source_id = str(object_id)
        if len(source_id) > 24:
            raise EtlError("MongoDB _id转字符串后超过source_document_id CHAR(24)")
        row["source_document_id"] = source_id

    release_date, release_text = parse_release_date(doc.get("release_date"))
    row["release_date"] = release_date
    release_text_value = (
        str(doc.get("release_date_text")).strip()
        if doc.get("release_date_text") not in (None, "")
        else release_text
    )
    if release_text_value is not None and len(release_text_value) > 64:
        raise EtlError("release_date_text超过VARCHAR(64)限制")
    row["release_date_text"] = release_text_value

    pic = doc.get("device_pic_url")
    if pic not in (None, ""):
        row["device_pic_url"] = scalar_text(pic, "device_pic_url", 1024)

    level = doc.get("integration_level")
    if not isinstance(level, dict):
        raise EtlError("integration_level不是对象")

    unit = normalize_unit(level.get("unit"))
    row["area_unit"] = unit
    row["semantic_version"] = SEMANTIC_VERSION

    raw_annotations = {}
    additional = []
    unmapped = []
    design_attributes = {}
    chip_models = {}

    # Preserve any existing table-shaped JSON fields if present in the source.
    merge_existing_json(row, doc, "device_design_attributes", dict)
    if row["device_design_attributes"]:
        design_attributes.update(row["device_design_attributes"])
    merge_existing_json(row, doc, "raw_value_annotations", dict)
    if row["raw_value_annotations"]:
        raw_annotations.update(row["raw_value_annotations"])
    merge_existing_json(row, doc, "additional_metrics", list)
    if row["additional_metrics"]:
        additional.extend(row["additional_metrics"])
    merge_existing_json(row, doc, "module_chip_models", dict)
    if row["module_chip_models"]:
        chip_models.update(row["module_chip_models"])
    merge_existing_json(row, doc, "unmapped_integration_values", list)
    if row["unmapped_integration_values"]:
        unmapped.extend(row["unmapped_integration_values"])

    # Direct top-level total is authoritative when numeric.
    direct_total, direct_total_raw = decimal_from_value(level.get("total"))
    if direct_total is not None:
        set_area_value(
            row, assigned, "integration_total_area_mm2", level.get("total"),
            "integration_level.total", raw_annotations,
            source_kind="root_total", source_key="integration_level.total",
        )
    elif direct_total_raw is not None:
        add_raw_annotation(
            raw_annotations, "integration_total_area_mm2", direct_total_raw,
            "integration_level.total",
        )

    detail_items = as_list(level.get("detail"), "integration_level.detail")
    for detail_index, detail in enumerate(detail_items):
        if not isinstance(detail, dict):
            add_unmapped(
                unmapped, None, None, detail, unit,
                "detail节点不是对象", f"integration_level.detail[{detail_index}]",
            )
            continue

        module_name_raw = detail.get("module_name")
        module_name = str(module_name_raw).strip() if module_name_raw is not None else ""
        if not module_name:
            add_unmapped(
                unmapped, None, None, detail, unit,
                "缺少module_name", f"integration_level.detail[{detail_index}]",
            )
            continue

        business_path = module_path(module_name)

        # Module-level source total. No detail-area SUM is ever generated here.
        if "total" in detail and detail.get("total") is not None:
            total_field = MODULE_TOTAL_MAP.get(module_name)
            if total_field:
                set_area_value(
                    row, assigned, total_field, detail.get("total"),
                    f"integration_level.detail[{detail_index}].total", raw_annotations,
                    source_kind="module_total", source_key=module_name,
                )
            else:
                add_unmapped(
                    unmapped, module_name, None, detail.get("total"), unit,
                    "该来源模块total在DDL中没有独立汇总列；不自动并入其他汇总",
                    f"integration_level.detail[{detail_index}].total",
                )

        # Preserve module -> chip model relationship by business path.
        module_model = detail.get("module_model")
        if module_model in (None, ""):
            module_model = detail.get("chip_model")
        if module_model not in (None, ""):
            append_json_value(chip_models, business_path, module_model)

        integrations = as_list(
            detail.get("integration"), 
            f"integration_level.detail[{detail_index}].integration"
        )
        for child_index, child in enumerate(integrations):
            child_path = (
                f"integration_level.detail[{detail_index}].integration[{child_index}]"
            )
            if not isinstance(child, dict):
                add_unmapped(
                    unmapped, module_name, None, child, unit,
                    "integration子项不是对象", child_path,
                )
                continue

            sub_raw = child.get("sub_module_name")
            sub_name = str(sub_raw).strip() if sub_raw is not None else ""
            if not sub_name:
                add_unmapped(
                    unmapped, module_name, None, child, unit,
                    "integration子项缺少sub_module_name", child_path,
                )
                continue

            special_attr = SPECIAL_ATTRIBUTE_MAP.get((module_name, sub_name))
            if special_attr:
                raw_value = child.get("area")
                if raw_value is None:
                    raw_value = child.get("value")
                attr_value = {
                    "source_module": module_name,
                    "source_sub_module": sub_name,
                    "raw_value": json_safe(raw_value if raw_value is not None else sub_name),
                }
                append_json_value(design_attributes, special_attr, attr_value)
                continue

            target_field = FIELD_MAP.get((module_name, sub_name))
            if target_field:
                set_area_value(
                    row, assigned, target_field, child.get("area"),
                    child_path + ".area", raw_annotations,
                    source_kind="detail_leaf", source_key=(module_name, sub_name),
                )
                # Preserve unexpected child properties rather than silently dropping them.
                extras = {
                    k: v for k, v in child.items()
                    if k not in {"sub_module_name", "area"}
                }
                if extras:
                    add_unmapped(
                        unmapped, module_name, sub_name, extras, unit,
                        "已映射面积节点包含额外来源属性",
                        child_path,
                    )
            else:
                add_unmapped(
                    unmapped, module_name, sub_name,
                    child.get("area") if "area" in child else child,
                    unit, "Tree映射中不存在该module_name/sub_module_name组合",
                    child_path,
                )

        process_other(
            detail.get("other"), module_name, business_path, unit,
            row, assigned, raw_annotations, additional,
        )

        known_detail_keys = {
            "module_name", "total", "integration", "other",
            "module_model", "chip_model",
        }
        extra_detail = {
            k: v for k, v in detail.items() if k not in known_detail_keys
        }
        if extra_detail:
            add_metric(
                additional, module_name, business_path,
                "detail_extra_fields", extra_detail,
                source_path=f"integration_level.detail[{detail_index}]",
            )

    # Legacy / future summary collections are preserved as non-area metrics.
    summary_items = level.get("summary")
    if summary_items is not None:
        for idx, item in enumerate(as_list(summary_items, "integration_level.summary")):
            add_metric(
                additional, "integration_level", "整机",
                "summary_item", item,
                source_path=f"integration_level.summary[{idx}]",
            )

    # Preserve unknown integration_level root fields.
    known_level_keys = {"total", "unit", "detail", "summary"}
    for key, value in level.items():
        if key not in known_level_keys:
            add_metric(
                additional, "integration_level", "整机",
                f"root_extra:{key}", value,
                source_path=f"integration_level.{key}",
            )

    # Derive integration_total only if source total is absent/non-numeric and at least
    # one of the three top-level source domain totals is available. Missing domains
    # count as 0 only for this explicit derivation; stored NULLs remain NULL.
    domain_fields = (
        "small_system_total_area_mm2",
        "peripheral_total_area_mm2",
        "wireless_total_area_mm2",
    )
    domain_values = [row.get(c) for c in domain_fields]
    multi_value_domains = [
        field for field in domain_fields
        if row.get(field) is not None and "," in str(row.get(field))
    ]
    scalar_domain_values = [
        Decimal(str(value)) if value is not None else Decimal("0")
        for value in domain_values
        if value is None or "," not in str(value)
    ]

    if direct_total is None and any(v is not None for v in domain_values):
        if multi_value_domains:
            add_metric(
                additional, "integration_level", "整机",
                "integration_total_derivation_skipped_multivalue_domain",
                {"fields": multi_value_domains, "reason": "domain contains comma-separated source observations"},
                source_path="ETL validation",
            )
            log(
                f"INTEGRATION_TOTAL_DERIVATION=SKIP reason=MULTIVALUE_DOMAIN "
                f"domain_count={len(multi_value_domains)}",
                "warning",
            )
        else:
            derived = sum(scalar_domain_values, Decimal("0"))
            set_area_value(
                row, assigned, "integration_total_area_mm2", derived,
                "derived:small_system+peripheral+wireless (missing domains=0)",
                raw_annotations,
                source_kind="derived",
                source_key="approved_top_level_formula",
            )
            log("INTEGRATION_TOTAL_DERIVED=PASS source=APPROVED_TOP_LEVEL_FORMULA")
    elif direct_total is not None and all(v is not None for v in domain_values):
        if multi_value_domains:
            log(
                f"INTEGRATION_TOTAL_CONSISTENCY=SKIP reason=MULTIVALUE_DOMAIN "
                f"domain_count={len(multi_value_domains)}",
                "warning",
            )
        else:
            derived = sum(scalar_domain_values, Decimal("0"))
            if derived != direct_total:
                log(
                    "INTEGRATION_TOTAL_SOURCE_MISMATCH "
                    "values=REDACTED policy=KEEP_SOURCE_TOTAL",
                    "warning",
                )
                add_metric(
                    additional, "integration_level", "整机",
                    "integration_total_consistency_check",
                    {"source_total": str(direct_total), "domain_sum": str(derived)},
                    source_path="ETL validation",
                )

    # Existing top-level design attributes can be supplemented by known raw properties.
    for key in ("board_variant", "design_variant", "pcb_variant", "board_version"):
        if doc.get(key) not in (None, ""):
            append_json_value(design_attributes, key, doc.get(key))

    row["device_design_attributes"] = design_attributes or None
    row["raw_value_annotations"] = raw_annotations or None
    row["additional_metrics"] = additional or None
    row["module_chip_models"] = chip_models or None
    row["unmapped_integration_values"] = unmapped or None

    return row


def redacted_diagnostic_payload(doc, target_field, entries):
    identity = {key: doc.get(key) for key in IDENTITY_COLUMNS}
    source_key_count = len({
        tuple(entry["source_key"]) for entry in entries
    })
    return {
        "device_ref": identity_ref(identity),
        "source_document_ref": source_document_ref(doc.get("_id")),
        "target_field": target_field,
        "classification": (
            "SAME_LOGICAL_LEAF_MULTIVALUE"
            if source_key_count == 1 else "CROSS_SOURCE_COLLISION_REQUIRES_REVIEW"
        ),
        "occurrence_count": len(entries),
        "source_key_count": source_key_count,
        "source_paths": [entry["source_path"] for entry in entries],
        "raw_values": "REDACTED",
        "device_identity": "REDACTED",
    }


def diagnose_mapping_conflicts(documents):
    """
    Read-only diagnostic for fields with multiple numeric source observations.
    All device identifiers and source values are redacted in console and file logs.
    """
    multivalue_field_count = 0
    cross_source_collision_count = 0
    scanned = 0

    for doc in documents:
        if not isinstance(doc, dict):
            continue
        level = doc.get("integration_level")
        if not isinstance(level, dict):
            continue

        scanned += 1
        grouped = defaultdict(list)

        details = as_list(level.get("detail"), "integration_level.detail")
        for detail_index, detail in enumerate(details):
            if not isinstance(detail, dict):
                continue
            module_name = str(detail.get("module_name") or "").strip()
            if not module_name:
                continue
            integrations = as_list(
                detail.get("integration"),
                f"integration_level.detail[{detail_index}].integration",
            )
            for child_index, child in enumerate(integrations):
                if not isinstance(child, dict):
                    continue
                sub_name = str(child.get("sub_module_name") or "").strip()
                if not sub_name:
                    continue
                target_field = FIELD_MAP.get((module_name, sub_name))
                if not target_field:
                    continue
                number, _ = decimal_from_value(child.get("area"))
                if number is None:
                    continue
                grouped[target_field].append({
                    "source_key": (module_name, sub_name),
                    "source_path": (
                        f"integration_level.detail[{detail_index}]"
                        f".integration[{child_index}].area"
                    ),
                })

        for target_field, entries in grouped.items():
            if len(entries) <= 1:
                continue
            multivalue_field_count += 1
            if len({tuple(entry["source_key"]) for entry in entries}) > 1:
                cross_source_collision_count += 1
            payload = redacted_diagnostic_payload(doc, target_field, entries)
            print(
                "MULTIVALUE_AREA_DETAIL="
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )

    log(
        f"MULTIVALUE_AREA_DIAGNOSIS=PASS scanned_documents={scanned} "
        f"multivalue_fields={multivalue_field_count} "
        f"cross_source_collisions={cross_source_collision_count} raw_values=REDACTED",
    )
    log("MONGODB_WRITE=NONE MYSQL_CONNECT=NO MYSQL_WRITE=NONE")
    return multivalue_field_count


def build_targets(documents):
    rows = []
    seen = {}
    scanned = 0
    skipped_no_level = 0
    for doc in documents:
        scanned += 1
        if not isinstance(doc, dict):
            raise EtlError("MongoDB游标返回非对象文档")
        if "integration_level" not in doc or doc.get("integration_level") is None:
            skipped_no_level += 1
            continue

        row = transform_document(doc)
        identity = tuple(row[k] for k in IDENTITY_COLUMNS)
        if identity in seen:
            raise EtlError(
                "MongoDB源集合存在重复设备唯一键，拒绝继续："
                f"device_ref={identity_ref(identity)}（身份字段已脱敏）"
            )
        seen[identity] = row.get("source_document_id")
        rows.append(row)

    if not rows:
        raise EtlError("没有找到可迁移的integration_level设备文档")

    multivalue_area_cells = 0
    multivalue_extra_observations = 0
    for row in rows:
        for field in AREA_COLUMNS:
            value = row.get(field)
            if value is not None and "," in str(value):
                multivalue_area_cells += 1
                multivalue_extra_observations += str(value).count(",")

    return rows, {
        "scanned_documents": scanned,
        "documents_without_integration_level": skipped_no_level,
        "target_devices": len(rows),
        "multivalue_area_cells": multivalue_area_cells,
        "multivalue_extra_observations": multivalue_extra_observations,
    }


def uri_details(uri):
    parts = urlsplit(uri)
    if parts.scheme not in ("mongodb", "mongodb+srv") or not parts.netloc:
        raise EtlError("MongoDB URI格式无效")
    db_name = unquote((parts.path or "").lstrip("/"))
    if db_name != MONGO_DATABASE:
        raise EtlError("MongoDB数据库环境不匹配；实际数据库名已脱敏")
    query = dict((k.lower(), v) for k, v in parse_qsl(parts.query, keep_blank_values=True))
    replica_set = query.get("replicaset")
    if replica_set != MONGO_REPLICA_SET:
        raise EtlError("MongoDB副本集不匹配；实际副本集名称已脱敏")
    auth_source = query.get("authsource")
    if auth_source != "admin":
        raise EtlError("MongoDB authSource必须为admin；实际值已脱敏")
    return parts, db_name, replica_set


def acquire_mongo_uri(args):
    uri = args.mongo_uri or os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URI")
    if uri and str(uri).strip():
        log("MONGODB_URI_SOURCE=ENVIRONMENT_OR_ARGUMENT (REDACTED)")
        return str(uri).strip()

    user = os.environ.get("MONGODB_USER", MONGO_DEFAULT_USER).strip() or MONGO_DEFAULT_USER
    password = getpass.getpass("MongoDB root 密码（仅密码，隐藏输入）: ")
    if not password:
        raise EtlError("MongoDB密码不能为空")

    credentials = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    uri = (
        f"mongodb://{credentials}{','.join(MONGO_SEEDS)}/{MONGO_DATABASE}"
        f"?authSource=admin&replicaSet={MONGO_REPLICA_SET}"
    )
    log("MONGODB_URI_CONSTRUCTION=PASS source=HIDDEN_PASSWORD credentials=REDACTED")
    return uri


def single_seed_uri(parts, seed):
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, _ = netloc.rsplit("@", 1)
        new_netloc = userinfo + "@" + seed
    else:
        new_netloc = seed
    options = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() != "directconnection"
    ]
    return urlunsplit(parts._replace(netloc=new_netloc, query=urlencode(options)))


def hello_info(client):
    try:
        return client.admin.command("hello")
    except Exception:
        return client.admin.command("isMaster")


def make_mongo_client(uri, args, direct=False):
    try:
        from pymongo import MongoClient, ReadPreference
    except ImportError as exc:
        raise EtlError("缺少pymongo，请执行：python -m pip install pymongo") from exc

    return MongoClient(
        uri,
        directConnection=direct,
        serverSelectionTimeoutMS=args.mongo_server_selection_timeout_ms,
        connectTimeoutMS=args.mongo_connect_timeout_ms,
        socketTimeoutMS=args.mongo_socket_timeout_ms,
        maxPoolSize=args.mongo_max_pool_size,
        minPoolSize=0,
        retryReads=True,
        read_preference=ReadPreference.PRIMARY,
        appname="cis-integration-wide-etl",
    )


def connect_mongo_primary(uri, args):
    parts, db_name, expected_rs = uri_details(uri)
    log(
        "MONGODB_URI_PREFLIGHT=PASS database=EXPECTED replica_set=EXPECTED "
        f"seeds={len(parts.netloc.rsplit('@', 1)[-1].split(','))}"
    )

    # First attempt normal replica-set discovery.
    discovery_options = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() != "directconnection"
    ]
    discovery_uri = urlunsplit(parts._replace(query=urlencode(discovery_options)))
    client = None
    try:
        client = make_mongo_client(discovery_uri, args, direct=False)
        client.admin.command("ping")
        hello = hello_info(client)
        if hello.get("setName") != expected_rs:
            raise EtlError("MongoDB实际副本集名称与URI不一致")
        if not (hello.get("isWritablePrimary") or hello.get("ismaster")):
            raise EtlError("MongoDB连接目标不是PRIMARY，拒绝读取")
        log("MONGODB_CONNECT=PASS mode=REPLICA_SET_DISCOVERY role=PRIMARY")
        return client, db_name
    except Exception as exc:
        # Only selection timeouts are eligible for the known seed-by-seed fallback.
        try:
            from pymongo.errors import ServerSelectionTimeoutError
            timeout_error = isinstance(exc, ServerSelectionTimeoutError)
        except Exception:
            timeout_error = False

        if client is not None:
            client.close()
            client = None
        if not timeout_error or parts.scheme == "mongodb+srv":
            if isinstance(exc, EtlError):
                raise
            raise EtlError(
                f"MongoDB连接失败：{type(exc).__name__}；未输出异常全文以保护凭据"
            ) from exc

        log("REPLICA_SET_DISCOVERY=TIMEOUT; DIRECT_PRIMARY_FALLBACK=START", "warning")

    if parts.scheme == "mongodb+srv":
        raise EtlError("mongodb+srv不支持当前多Seed直连PRIMARY回退")

    seeds_raw = parts.netloc.rsplit("@", 1)[-1]
    seeds = [s.strip() for s in seeds_raw.split(",") if s.strip()]
    for index, seed in enumerate(seeds, start=1):
        candidate = None
        try:
            candidate_uri = single_seed_uri(parts, seed)
            candidate = make_mongo_client(candidate_uri, args, direct=True)
            hello = hello_info(candidate)
            if hello.get("setName") != expected_rs:
                log(f"MONGO_SEED_{index}=REPLICA_SET_MISMATCH", "warning")
                continue
            if hello.get("arbiterOnly"):
                log(f"MONGO_SEED_{index}=ARBITER_SKIP", "warning")
                continue
            if not (hello.get("isWritablePrimary") or hello.get("ismaster")):
                log(f"MONGO_SEED_{index}=NOT_PRIMARY_SKIP", "warning")
                continue
            candidate.admin.command("ping")
            log(f"MONGO_SEED_{index}=PRIMARY_PASS")
            log("MONGODB_CONNECT=PASS mode=DIRECT_PRIMARY role=PRIMARY")
            selected = candidate
            candidate = None
            return selected, db_name
        except Exception as exc:
            log(f"MONGO_SEED_{index}=FAILED type={type(exc).__name__}", "warning")
        finally:
            if candidate is not None:
                candidate.close()

    raise EtlError("所有URI Seed均未找到可访问的PRIMARY")


def read_mongo_documents(collection, args):
    projection = {
        "_id": 1,
        "manufacturer": 1,
        "platform": 1,
        "device_type": 1,
        "device_model": 1,
        "release_date": 1,
        "release_date_text": 1,
        "device_pic_url": 1,
        "integration_level": 1,
        "device_design_attributes": 1,
        "raw_value_annotations": 1,
        "additional_metrics": 1,
        "module_chip_models": 1,
        "unmapped_integration_values": 1,
        "board_variant": 1,
        "design_variant": 1,
        "pcb_variant": 1,
        "board_version": 1,
    }
    cursor = None
    documents = []
    try:
        cursor = collection.find({}, projection=projection, no_cursor_timeout=False)
        cursor = cursor.batch_size(args.mongo_batch_size)
        if args.mongo_max_time_ms:
            cursor = cursor.max_time_ms(args.mongo_max_time_ms)
        if args.limit:
            cursor = cursor.limit(args.limit)

        log(
            f"MONGODB_QUERY=START collection={MONGO_COLLECTION} "
            f"projection=target_columns_only batch_size={args.mongo_batch_size}"
        )
        for doc in cursor:
            documents.append(doc)
        log(f"MONGODB_QUERY=PASS documents={len(documents)}")
        return documents
    except Exception as exc:
        raise EtlError(
            f"MongoDB读取失败：{type(exc).__name__}；未输出异常全文"
        ) from exc
    finally:
        if cursor is not None:
            cursor.close()


def resolve_mysql_config(args):
    host = args.mysql_host or os.environ.get("MYSQL_HOST") or MYSQL_DEFAULT["host"]
    port = args.mysql_port or int(os.environ.get("MYSQL_PORT", MYSQL_DEFAULT["port"]))
    database = args.mysql_database or os.environ.get("MYSQL_DATABASE") or MYSQL_DEFAULT["database"]
    user = args.mysql_user or os.environ.get("MYSQL_USER") or ""
    if not user:
        user = input("MySQL username [root]: ").strip() or "root"

    password = os.environ.get("MYSQL_PASSWORD")
    if not password:
        password = getpass.getpass("MySQL password (hidden input): ")
    if not password:
        raise EtlError("MySQL密码不能为空")
    if not host or not database or not user:
        raise EtlError("MySQL连接配置不完整")
    if not 1 <= int(port) <= 65535:
        raise EtlError("MySQL端口无效")

    return {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "database": database,
        "charset": MYSQL_DEFAULT["charset"],
        "autocommit": False,
        "connect_timeout": 10,
        "read_timeout": 120,
        "write_timeout": 120,
        "cursorclass": DictCursor,
    }


def verify_mysql_schema(conn):
    with conn.cursor() as cursor:
        cursor.execute(f"SHOW COLUMNS FROM `{TABLE}`")
        column_rows = cursor.fetchall()
        actual_columns = {r["Field"] for r in column_rows}
        expected = set(EXPECTED_COLUMNS)
        missing = sorted(expected - actual_columns)
        extra = sorted(actual_columns - expected)
        if missing or extra:
            raise EtlError(
                f"目标表字段不匹配 missing={missing[:20]} extra={extra[:20]}"
            )

        types = {r["Field"]: str(r["Type"]).lower() for r in column_rows}
        for field in AREA_COLUMNS:
            if types.get(field) != "text":
                raise EtlError(
                    f"面积列类型不匹配 field={field} actual={types.get(field)} "
                    "expected=text；请先执行area_columns_multivalue_alter.sql"
                )
        for field in JSON_COLUMNS:
            if types.get(field) != "json":
                raise EtlError(
                    f"JSON列类型不匹配 field={field} actual={types.get(field)}"
                )
        for field in IDENTITY_COLUMNS:
            expected_type = {
                "manufacturer": "varchar(64)",
                "platform": "varchar(128)",
                "device_type": "varchar(32)",
                "device_model": "varchar(128)",
            }[field]
            if types.get(field) != expected_type:
                raise EtlError(
                    f"唯一键字段类型不匹配：{field} actual={types.get(field)} expected={expected_type}"
                )
        nullable = {r["Field"]: r["Null"] for r in column_rows}
        for field in IDENTITY_COLUMNS:
            if nullable.get(field) != "NO":
                raise EtlError(f"唯一键字段必须NOT NULL：{field}")

        cursor.execute(f"SHOW INDEX FROM `{TABLE}`")
        index_rows = cursor.fetchall()
        indexes = defaultdict(list)
        unique_flags = {}
        for r in index_rows:
            key_name = r["Key_name"]
            indexes[key_name].append((int(r["Seq_in_index"]), r["Column_name"]))
            unique_flags[key_name] = int(r["Non_unique"]) == 0

        unique_cols = tuple(
            col for _, col in sorted(indexes.get("uk_device_identity", []))
        )
        if (
            not unique_flags.get("uk_device_identity", False)
            or unique_cols != IDENTITY_COLUMNS
        ):
            raise EtlError(
                "目标表缺少正确的uk_device_identity唯一索引；"
                f"actual={unique_cols}"
            )

        cursor.execute(
            "SELECT ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
            (conn.db.decode() if isinstance(conn.db, bytes) else conn.db, TABLE),
        )
        engine_row = cursor.fetchone()
        if not engine_row or str(engine_row["ENGINE"]).upper() != "INNODB":
            raise EtlError("目标表必须为InnoDB，才能保证事务回滚")

    log(
        f"MYSQL_SCHEMA=PASS table={TABLE} columns={len(actual_columns)} "
        f"area_columns={len(AREA_COLUMNS)} area_type=TEXT "
        f"json_columns={len(JSON_COLUMNS)} "
        "unique_key=uk_device_identity engine=InnoDB"
    )


def upsert_sql():
    columns = WRITE_COLUMNS
    placeholders = ",".join(["%s"] * len(columns))
    column_sql = ",".join(f"`{c}`" for c in columns)
    update_columns = [c for c in columns if c not in IDENTITY_COLUMNS]
    update_sql = ",".join(
        [f"`{c}`=VALUES(`{c}`)" for c in update_columns]
        + ["`updated_at`=CURRENT_TIMESTAMP(3)"]
    )
    return (
        f"INSERT INTO `{TABLE}` ({column_sql}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_sql}"
    )


def db_value(field, value):
    if field in JSON_COLUMNS and value is not None:
        return canonical_json(value)
    return value


def values_for_row(row):
    return tuple(db_value(c, row.get(c)) for c in WRITE_COLUMNS)


def normalize_db_value(field, value):
    if value is None:
        return None
    if field in JSON_COLUMNS:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception as exc:
                raise EtlError(f"数据库JSON回读解析失败 field={field}") from exc
        return value
    if field in AREA_COLUMNS:
        return str(value)
    if field == "release_date" and isinstance(value, datetime):
        return value.replace(microsecond=(value.microsecond // 1000) * 1000)
    return value


def verify_readback(conn, rows):
    select_columns = ",".join(f"`{c}`" for c in WRITE_COLUMNS)
    with conn.cursor() as cursor:
        for expected in rows:
            identity_values = tuple(expected[k] for k in IDENTITY_COLUMNS)
            where = " AND ".join(f"`{k}`=%s" for k in IDENTITY_COLUMNS)
            cursor.execute(
                f"SELECT {select_columns} FROM `{TABLE}` WHERE {where}",
                identity_values,
            )
            fetched = cursor.fetchall()
            if len(fetched) != 1:
                raise EtlError(
                    "MySQL回读行数不为1，事务回滚："
                    f"device_ref={identity_ref(identity_values)} count={len(fetched)}"
                )
            actual = fetched[0]
            for field in WRITE_COLUMNS:
                exp = expected.get(field)
                got = normalize_db_value(field, actual.get(field))
                if field in JSON_COLUMNS:
                    if canonical_json(exp) != canonical_json(got):
                        raise EtlError(
                            f"MySQL JSON回读不一致 field={field} device_ref={identity_ref(identity_values)}"
                        )
                elif field in AREA_COLUMNS:
                    if exp is None:
                        if got is not None:
                            raise EtlError(f"MySQL面积回读不一致 field={field}")
                    elif got is None or str(exp) != got:
                        raise EtlError(f"MySQL面积回读不一致 field={field}")
                elif field == "release_date":
                    if exp != got:
                        raise EtlError("MySQL release_date回读不一致")
                elif exp != got:
                    raise EtlError(
                        f"MySQL回读不一致 field={field} device_ref={identity_ref(identity_values)}"
                    )


def write_mysql(conn, rows, batch_size):
    sql = upsert_sql()
    total = len(rows)
    conn.begin()
    try:
        with conn.cursor() as cursor:
            for start in range(0, total, batch_size):
                batch = rows[start:start + batch_size]
                cursor.executemany(sql, [values_for_row(r) for r in batch])
                log(
                    f"MYSQL_BATCH=PASS batch={start // batch_size + 1} "
                    f"rows={len(batch)}"
                )

        # Verify the persisted values before COMMIT; any mismatch rolls back all rows.
        verify_readback(conn, rows)
        log(f"MYSQL_READBACK=PASS rows={total}")
        conn.commit()
        log(
            f"MYSQL_COMMIT=PASS table={TABLE} rows={total} "
            "stale_delete=NO mongo_write=NO"
        )
    except Exception:
        conn.rollback()
        log("MYSQL_ROLLBACK=PASS", "exception")
        raise


def write_report(summary, status):
    try:
        path = Path(__file__).resolve().with_name(
            f"{Path(__file__).stem}_report.json"
        )
        report = {
            "etl_version": ETL_VERSION,
            "target_table": TABLE,
            "semantic_version": SEMANTIC_VERSION,
            "status": status,
            "summary": summary,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=json_safe) + "\n",
            encoding="utf-8",
        )
        os.replace(str(tmp), str(path))
        log(f"JSON_REPORT={path.name}")
    except Exception as exc:
        log(f"JSON_REPORT=SKIP type={type(exc).__name__}", "warning")




AUDIT_VARCHAR_BUCKETS = (16, 24, 32, 48, 64, 96, 128, 192, 255, 384, 512)
AUDIT_INNODB_ROW_LIMIT = 8126
AUDIT_ROW_WARNING_LIMIT = 7000


def audit_percentile(values, pct):
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * pct / 100) - 1)
    return ordered[index]


def audit_recommend_type(field, max_chars):
    if field in JSON_COLUMNS:
        return "JSON", 20
    if field == "release_date":
        return "DATE", 3
    if field == "source_document_id":
        return "CHAR(24) CHARACTER SET ascii COLLATE ascii_bin", 24

    fixed = {
        "manufacturer": ("VARCHAR(64) CHARACTER SET utf8mb4", 64 * 4 + 2),
        "platform": ("VARCHAR(128) CHARACTER SET utf8mb4", 128 * 4 + 2),
        "device_type": ("VARCHAR(32) CHARACTER SET utf8mb4", 32 * 4 + 1),
        "device_model": ("VARCHAR(128) CHARACTER SET utf8mb4", 128 * 4 + 2),
        "release_date_text": ("VARCHAR(64) CHARACTER SET utf8mb4", 64 * 4 + 2),
        "area_unit": ("VARCHAR(16) CHARACTER SET utf8mb4", 16 * 4 + 1),
        "semantic_version": ("VARCHAR(32) CHARACTER SET utf8mb4", 32 * 4 + 1),
    }
    if field in fixed:
        return fixed[field][0], fixed[field][1]

    is_area = field in AREA_COLUMNS
    charset = "ascii" if is_area else "utf8mb4"
    bytes_per_char = 1 if is_area else 4
    minimum = 16 if is_area else 32
    target = max(minimum, math.ceil(max_chars * 1.25))
    length = next((n for n in AUDIT_VARCHAR_BUCKETS if n >= target), None)

    if length is None:
        return f"TEXT CHARACTER SET {charset}", 20

    prefix_bytes = 1 if length * bytes_per_char <= 255 else 2
    declared_bytes = length * bytes_per_char + prefix_bytes
    return f"VARCHAR({length}) CHARACTER SET {charset}", declared_bytes


def measure_value_lengths(rows):
    """Measure exactly the values passed by the ETL to WRITE_COLUMNS."""
    field_values = {field: [] for field in WRITE_COLUMNS}

    for row in rows:
        for field in WRITE_COLUMNS:
            value = db_value(field, row.get(field))
            if value is None:
                continue
            if isinstance(value, (datetime, date)):
                text_value = value.isoformat()
            elif isinstance(value, bytes):
                # No binary business columns are expected; report byte length only.
                text_value = None
            else:
                text_value = str(value)

            if text_value is None:
                field_values[field].append((len(value), len(value)))
            else:
                field_values[field].append(
                    (len(text_value), len(text_value.encode("utf-8")))
                )

    result = []
    total_rows = len(rows)
    for field in WRITE_COLUMNS:
        measurements = field_values[field]
        char_lengths = [item[0] for item in measurements]
        byte_lengths = [item[1] for item in measurements]
        max_chars = max(char_lengths, default=0)
        mysql_type, declared_bytes = audit_recommend_type(field, max_chars)

        if field in AREA_COLUMNS:
            category = "AREA"
        elif field in JSON_COLUMNS:
            category = "JSON"
        elif field == "release_date":
            category = "DATE"
        elif field in IDENTITY_COLUMNS:
            category = "IDENTITY"
        elif field == "source_document_id":
            category = "SOURCE_ID"
        else:
            category = "STRING"

        result.append({
            "field": field,
            "category": category,
            "row_count": total_rows,
            "non_null_count": len(measurements),
            "null_count": total_rows - len(measurements),
            "max_char_length": max_chars,
            "p95_char_length": audit_percentile(char_lengths, 95),
            "p99_char_length": audit_percentile(char_lengths, 99),
            "max_utf8_bytes": max(byte_lengths, default=0),
            "recommended_mysql_type": mysql_type,
            "declared_row_bytes": declared_bytes,
        })
    return result


def print_value_length_report(columns, rows):
    # Print only field names and length statistics; never print source values
    # or device identities. PowerShell transcript captures the same output.
    log(
        "VALUE_LENGTH_HEADER field category rows non_null null "
        "max_chars p95_chars p99_chars max_utf8_bytes recommended_mysql_type"
    )
    for item in columns:
        log(
            "VALUE_LENGTH "
            f"field={item['field']} category={item['category']} "
            f"rows={item['row_count']} non_null={item['non_null_count']} "
            f"null={item['null_count']} max_chars={item['max_char_length']} "
            f"p95_chars={item['p95_char_length']} "
            f"p99_chars={item['p99_char_length']} "
            f"max_utf8_bytes={item['max_utf8_bytes']} "
            f"mysql_type={item['recommended_mysql_type']}"
        )

    # The three ETL-managed columns are not supplied by WRITE_COLUMNS.
    declared_bytes = sum(item["declared_row_bytes"] for item in columns)
    declared_bytes += 8  # id BIGINT UNSIGNED
    declared_bytes += 7 + 7  # created_at / updated_at DATETIME(3)
    null_bitmap_bytes = math.ceil(len(EXPECTED_COLUMNS) / 8)
    record_overhead_bytes = 32
    estimated_row_bytes = declared_bytes + null_bitmap_bytes + record_overhead_bytes

    if estimated_row_bytes <= AUDIT_ROW_WARNING_LIMIT:
        row_status = "PASS_WITH_MARGIN"
    elif estimated_row_bytes <= AUDIT_INNODB_ROW_LIMIT:
        row_status = "NEAR_LIMIT"
    else:
        row_status = "OVER_LIMIT"

    counts = {
        "varchar": sum(item["recommended_mysql_type"].startswith("VARCHAR") for item in columns),
        "text": sum(item["recommended_mysql_type"].startswith("TEXT") for item in columns),
        "json": sum(item["recommended_mysql_type"] == "JSON" for item in columns),
        "char": sum(item["recommended_mysql_type"].startswith("CHAR") for item in columns),
    }
    return {
        "field_count": len(columns),
        "row_count": len(rows),
        "area_columns": len(AREA_COLUMNS),
        "area_multivalue_cells": sum(
            1 for row in rows for field in AREA_COLUMNS
            if row.get(field) is not None and "," in str(row.get(field))
        ),
        "estimated_row_bytes": estimated_row_bytes,
        "null_bitmap_bytes": null_bitmap_bytes,
        "record_overhead_bytes": record_overhead_bytes,
        "row_status": row_status,
        **counts,
    }


def run_self_test():
    sample = {
        "_id": "0123456789abcdef01234567",
        "manufacturer": "Apple",
        "platform": "A19",
        "device_type": "phone",
        "device_model": "iPhone17 Pro Max",
        "release_date": "2026-09-20T12:30:15.123Z",
        "device_pic_url": "https://example.invalid/device.png",
        "integration_level": {
            "total": 2466,
            "unit": "平方毫米",
            "detail": [
                {
                    "module_name": "小系统",
                    "total": 956,
                    "integration": [
                        {"sub_module_name": "SOC", "area": 565},
                        {"sub_module_name": "PMK", "area": 55},
                        {"sub_module_name": "UFS", "area": 562},
                    ],
                },
                {
                    "module_name": "SOC",
                    "total": 565,
                    "integration": [
                        {"sub_module_name": "SOC_GND", "area": 1},
                        {"sub_module_name": "SOC_GPIO_interface", "area": 2},
                    ],
                    "module_model": "SoC-X",
                },
                {
                    "module_name": "UFS",
                    "total": 562,
                    "integration": [{"sub_module_name": "Memory", "area": 562}],
                },
                {
                    "module_name": "外设",
                    "total": 755,
                    "integration": [{
                        "sub_module_name": "CAM_PWR/闪光灯/MIC_VIB/SmartPA/NFC/sensor/接口/低压特性等",
                        "area": 458,
                    }],
                },
                {
                    "module_name": "RF",
                    "total": None,
                    "integration": [{
                        "sub_module_name": "卫星通信（天通/北斗/星网）",
                        "area": 1,
                    }],
                    "other": {
                        "type": "single",
                        "info": [{
                            "metric_name": "卫星通信（天通/北斗/星网）原始值",
                            "metric_value": "1（低轨）",
                        }],
                    },
                    "module_model": "RFIC-X",
                },
                {
                    "module_name": "NC",
                    "total": None,
                    "integration": [{
                        "sub_module_name": "Wi-Fi/BT BB",
                        "area": 546,
                    }],
                    "other": {
                        "type": "single",
                        "info": [{
                            "metric_name": "Wi-Fi/BT BB原始值",
                            "metric_value": "546（模组）",
                        }],
                    },
                },
                {
                    "module_name": "无线合计",
                    "total": 755,
                    "integration": [],
                },
                {
                    "module_name": "短距",
                    "total": 88,
                    "integration": [
                        {"sub_module_name": "HL1PNHSHA", "area": "Ver A"},
                        {"sub_module_name": "NEW_NODE", "area": "/"},
                    ],
                    "other": {
                        "type": "multi",
                        "info": [{
                            "key": "PMU0",
                            "label": "PMU0",
                            "value": [{"metric_name": "集成度", "metric_value": 12}],
                        }],
                    },
                },
                {
                    "module_name": "SC",
                    "total": 10,
                    "integration": [{"sub_module_name": "CHARGER2", "area": 10}],
                },
                {
                    "module_name": "5V boost",
                    "total": 3,
                    "integration": [{"sub_module_name": "5V boost", "area": 3}],
                },
            ],
        },
    }

    row = transform_document(sample)
    assert row["integration_total_area_mm2"] == "2466"
    assert row["small_system_total_area_mm2"] == "956"
    assert row["peripheral_total_area_mm2"] == "755"
    assert row["wireless_total_area_mm2"] == "755"
    assert row["wireless_rf_satcom_tiantong_beidou_xingwang_area_mm2"] == "1"
    assert row["wireless_nc_wifi_bt_baseband_area_mm2"] == "546"
    assert row["small_system_soc_total_area_mm2"] == "565"
    assert row["small_system_ufs_total_area_mm2"] == "562"
    assert row["raw_value_annotations"]["wireless_rf_satcom_tiantong_beidou_xingwang_area_mm2"] == "1（低轨）"
    assert row["raw_value_annotations"]["wireless_nc_wifi_bt_baseband_area_mm2"] == "546（模组）"
    assert row["device_design_attributes"]["board_variant"]["raw_value"] == "Ver A"
    assert row["module_chip_models"]["无线 → RF"] == "RFIC-X"
    assert row["power_scharger_charger2_area_mm2"] == "10"
    assert row["power_5v_boost_area_mm2"] == "3"
    assert row["unmapped_integration_values"] is not None
    assert row["additional_metrics"] is not None

    # Log correlation uses non-reversible fingerprints, never raw identity values.
    secret_identity = {
        "manufacturer": "SENSITIVE_MANUFACTURER",
        "platform": "SENSITIVE_PLATFORM",
        "device_type": "phone",
        "device_model": "SENSITIVE_MODEL",
    }
    token = identity_ref(secret_identity)
    assert len(token) == 12
    assert "SENSITIVE" not in token
    redacted_payload = redacted_diagnostic_payload(
        {
            **secret_identity,
            "_id": "SENSITIVE_OBJECT_ID",
        },
        "example_area_mm2",
        [{"source_key": ("SECRET_MODULE", "SECRET_SUBMODULE"),
          "source_path": "integration_level.detail[0].integration[0].area",
          "value": "SENSITIVE_AREA_VALUE"}],
    )
    redacted_text = json.dumps(redacted_payload, ensure_ascii=False)
    assert "SENSITIVE" not in redacted_text
    assert redacted_payload["raw_values"] == "REDACTED"

    # Zero is a valid numeric area; annotated and non-numeric text is preserved.
    z, zraw = decimal_from_value("0（低轨）")
    assert z == Decimal("0.000000") and zraw == "0（低轨）"
    slash, slashraw = decimal_from_value("/")
    assert slash is None and slashraw == "/"

    # Repeated observations of the same logical leaf are comma-joined in source order.
    multi = dict(sample)
    multi["integration_level"] = dict(sample["integration_level"])
    multi["integration_level"]["detail"] = list(sample["integration_level"]["detail"]) + [{
        "module_name": "FPC",
        "integration": [
            {"sub_module_name": "FPC_Interface", "area": 9.38},
            {"sub_module_name": "FPC_Interface", "area": 17.54},
            {"sub_module_name": "FPC_Interface", "area": 9.38},
        ],
    }, {
        "module_name": "天线",
        "integration": [
            {"sub_module_name": "ANT10_ANT_11", "area": 42.77},
            {"sub_module_name": "ANT10_ANT_11", "area": 16.59},
        ],
    }]
    multi_row = transform_document(multi)
    assert multi_row["interface_fpc_interface_area_mm2"] == "9.38,17.54,9.38"
    assert multi_row["wireless_antenna_ant10_ant_11_area_mm2"] == "42.77,16.59"

    # Different semantic source kinds targeting the same column still fail on differing values.
    bad = dict(sample)
    bad["integration_level"] = dict(sample["integration_level"])
    bad["integration_level"]["detail"] = list(sample["integration_level"]["detail"]) + [{
        "module_name": "5V boost",
        "total": 4,
        "integration": [{"sub_module_name": "5V boost", "area": 3}],
    }]
    try:
        transform_document(bad)
    except EtlError as exc:
        assert "MAPPING_CONFLICT" in str(exc)
        assert "REDACTED" in str(exc)
    else:
        raise AssertionError("cross-source mapping conflict was not rejected")

    assert len(EXPECTED_COLUMNS) == 285
    assert len(set(EXPECTED_COLUMNS)) == 285
    assert len(AREA_COLUMNS) == 267
    assert len(JSON_COLUMNS) == 5
    assert len(FIELD_MAP) == 270
    assert len(set(FIELD_MAP.values())) == 243
    assert set(FIELD_MAP.values()).issubset(set(AREA_COLUMNS))
    all_area_targets = (
        set(FIELD_MAP.values())
        | set(MODULE_TOTAL_MAP.values())
        | {"integration_total_area_mm2"}
    )
    assert all_area_targets == set(AREA_COLUMNS), (
        f"area mapping coverage mismatch missing={set(AREA_COLUMNS)-all_area_targets} "
        f"extra={all_area_targets-set(AREA_COLUMNS)}"
    )
    assert upsert_sql().count("%s") == len(WRITE_COLUMNS)

    duplicate_test = [sample, dict(sample)]
    try:
        build_targets(duplicate_test)
    except EtlError as exc:
        assert "重复设备唯一键" in str(exc)
    else:
        raise AssertionError("duplicate source identity was not rejected")

    no_level = {
        "_id": "1123456789abcdef01234567",
        "manufacturer": "Apple",
        "platform": "A19",
        "device_type": "phone",
        "device_model": "NoIntegrationLevel",
    }
    built, stats = build_targets([sample, no_level])
    assert len(built) == 1 and stats["documents_without_integration_level"] == 1
    built_multi, multi_stats = build_targets([multi])
    assert len(built_multi) == 1
    assert multi_stats["multivalue_area_cells"] == 2
    assert multi_stats["multivalue_extra_observations"] == 3
    assert normalize_db_value(
        "interface_fpc_interface_area_mm2", "9.38,17.54"
    ) == "9.38,17.54"

    # Length audit uses the exact ETL-produced row and WRITE_COLUMNS contract.
    audit_probe_row = dict(multi_row)
    audit_probe_row["device_model"] = "SENSITIVE_MODEL"
    audit_columns = measure_value_lengths([audit_probe_row])
    audit_by_field = {item["field"]: item for item in audit_columns}
    assert len(audit_columns) == len(WRITE_COLUMNS)
    assert audit_by_field["wireless_antenna_ant10_ant_11_area_mm2"]["max_char_length"] == len("42.77,16.59")
    assert audit_by_field["wireless_antenna_ant10_ant_11_area_mm2"]["recommended_mysql_type"].startswith("VARCHAR(")
    assert "SENSITIVE_MODEL" not in json.dumps(audit_columns, ensure_ascii=False)

    print(
        "SELFTEST=PASS "
        "columns=285 area_columns=267 area_type=TEXT json_columns=5 "
        "tree_mapping=270 exact_pairs area_target_coverage=267/267 "
        "numeric_zero=PASS comma_multivalue_order=PASS no_sum=PASS "
        "annotated_raw_preserved=PASS unknown_nodes_preserved=PASS "
        "design_attribute=PASS chip_models=PASS cross_source_conflict=REJECT "
        "duplicate_identity=REJECT log_redaction=PASS value_length_audit=PASS "
        "audit_redaction=PASS mongo_write=NONE mysql_write=NONE"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only MongoDB CIS ETL v1.0.2 value-length audit"
    )
    parser.add_argument("--mongo-uri", default="", help="完整MongoDB URI；不建议将凭据直接写在命令行")
    parser.add_argument("--mongo-server-selection-timeout-ms", type=int, default=10000)
    parser.add_argument("--mongo-connect-timeout-ms", type=int, default=10000)
    parser.add_argument("--mongo-socket-timeout-ms", type=int, default=60000)
    parser.add_argument("--mongo-max-pool-size", type=int, default=10)
    parser.add_argument("--mongo-batch-size", type=int, default=300)
    parser.add_argument("--mongo-max-time-ms", type=int, default=120000)

    parser.add_argument("--mysql-host", default=None)
    parser.add_argument("--mysql-port", type=int, default=None)
    parser.add_argument("--mysql-database", default=None)
    parser.add_argument("--mysql-user", default=None)
    parser.add_argument("--mysql-password-env", action="store_true")

    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="只读取MongoDB并转换预检，不连接MySQL、不写入")
    parser.add_argument("--mysql-test-only", action="store_true",
                        help="只连接MySQL并验证已存在目标表结构（267个面积列必须为TEXT），不读取MongoDB、不写入")
    parser.add_argument("--diagnose-mapping-conflicts", action="store_true",
                        help="只读MongoDB，只读诊断多条来源记录映射到同一面积列；设备身份与数值脱敏")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--audit-value-lengths",
        action="store_true",
        help="只读MongoDB，按ETL转换结果统计WRITE_COLUMNS字段长度；绝不连接MySQL",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        run_self_test()
        return 0

    # This checked-in copy is deliberately audit-only. Never fall through to
    # the original ETL's MySQL migration path.
    if not args.audit_value_lengths:
        raise EtlError("AUDIT_ONLY_MODE_REQUIRED：必须指定--audit-value-lengths")
    if args.dry_run or args.mysql_test_only or args.diagnose_mapping_conflicts:
        raise EtlError("--audit-value-lengths不能与其他运行模式组合")

    if args.diagnose_mapping_conflicts and (args.dry_run or args.mysql_test_only):
        raise EtlError(
            "--diagnose-mapping-conflicts不能与--dry-run或--mysql-test-only同时使用"
        )

    positive = {
        "batch_size": args.batch_size,
        "mongo_batch_size": args.mongo_batch_size,
        "mongo_server_selection_timeout_ms": args.mongo_server_selection_timeout_ms,
        "mongo_connect_timeout_ms": args.mongo_connect_timeout_ms,
        "mongo_socket_timeout_ms": args.mongo_socket_timeout_ms,
        "mongo_max_pool_size": args.mongo_max_pool_size,
    }
    for name, value in positive.items():
        if value <= 0:
            raise EtlError(f"{name}必须大于0")
    if args.limit is not None and args.limit <= 0:
        raise EtlError("--limit必须大于0")

    if pymysql is None and args.mysql_test_only:
        raise EtlError("缺少PyMySQL；长度审计模式不需要PyMySQL")

    if args.mysql_password_env and not args.audit_value_lengths and not os.environ.get("MYSQL_PASSWORD"):
        raise EtlError("--mysql-password-env已指定，但MYSQL_PASSWORD未设置")

    conn = None
    mongo_client = None
    try:
        # MySQL-only check is intentionally independent of MongoDB.
        if args.mysql_test_only:
            config = resolve_mysql_config(args)
            conn = pymysql.connect(**config)
            with conn.cursor() as cursor:
                cursor.execute("SELECT DATABASE() AS db, VERSION() AS version, CURRENT_USER() AS account")
                info = cursor.fetchone()
            log(
                f"MYSQL_CONNECTION_TEST=PASS database=TARGET version={info['version']} "
                "account=REDACTED"
            )
            if info["db"] != MYSQL_DEFAULT["database"] and not args.mysql_database:
                raise EtlError("MySQL目标库不匹配；实际数据库名已脱敏")
            verify_mysql_schema(conn)
            log("MYSQL_CREATE_TABLE=NO MYSQL_WRITE=NO")
            return 0

        mongo_uri = acquire_mongo_uri(args)
        mongo_client, db_name = connect_mongo_primary(mongo_uri, args)
        collection = mongo_client[db_name][MONGO_COLLECTION]

        documents = read_mongo_documents(collection, args)
        if args.diagnose_mapping_conflicts:
            diagnose_mapping_conflicts(documents)
            return 0

        rows, stats = build_targets(documents)

        log(
            f"SOURCE_SUMMARY collection={MONGO_COLLECTION} "
            f"scanned={stats['scanned_documents']} "
            f"without_integration_level={stats['documents_without_integration_level']} "
            f"target_devices={stats['target_devices']} "
            f"multivalue_area_cells={stats['multivalue_area_cells']} "
            f"multivalue_extra_observations={stats['multivalue_extra_observations']}"
        )
        log(
            f"TRANSFORM_SUMMARY target_table={TABLE} "
            f"area_columns={len(AREA_COLUMNS)} json_columns={len(JSON_COLUMNS)} "
            "unmapped_values=preserved raw_annotations=preserved"
        )
        log("MONGODB_WRITE=NONE")

        if args.audit_value_lengths:
            audit_columns = measure_value_lengths(rows)
            audit = print_value_length_report(audit_columns, rows)
            log(
                f"AUDIT_VALUE_LENGTHS=PASS rows={audit['row_count']} "
                f"fields={audit['field_count']} area_columns={audit['area_columns']} "
                f"varchar={audit['varchar']} text={audit['text']} json={audit['json']} "
                f"area_multivalue_cells={audit['area_multivalue_cells']}"
            )
            log(
                f"ROW_SIZE_ESTIMATE_BYTES={audit['estimated_row_bytes']} "
                f"NULL_BITMAP_BYTES={audit['null_bitmap_bytes']} "
                f"RECORD_OVERHEAD_BYTES={audit['record_overhead_bytes']} "
                f"INNODB_LIMIT_BYTES={AUDIT_INNODB_ROW_LIMIT} "
                f"STATUS={audit['row_status']}"
            )
            log(
                "AUDIT_OUTPUT=CONSOLE "
                "MONGODB_WRITE=NONE MYSQL_CONNECT=NO MYSQL_WRITE=NONE "
                "SENSITIVE_VALUES=REDACTED"
            )
            return 0

        if args.dry_run:
            log("DRY_RUN=PASS MYSQL_CONNECT=NO MYSQL_WRITE=NO")
            write_report(stats, "DRY_RUN_PASS")
            return 0

        config = resolve_mysql_config(args)
        conn = pymysql.connect(**config)
        with conn.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS db, VERSION() AS version, CURRENT_USER() AS account")
            info = cursor.fetchone()
        log(
            f"MYSQL_CONNECTION=PASS database=TARGET version={info['version']} "
            "account=REDACTED"
        )
        if info["db"] != MYSQL_DEFAULT["database"] and not args.mysql_database:
            raise EtlError("MySQL目标库不匹配；实际数据库名已脱敏")

        verify_mysql_schema(conn)
        write_mysql(conn, rows, args.batch_size)
        write_report(stats, "MIGRATION_PASS")
        log(
            f"FINAL=PASS source_devices={len(rows)} target_table={TABLE} "
            "MYSQL_READBACK=PASS MYSQL_COMMIT=PASS MONGODB_WRITE=NONE"
        )
        return 0

    finally:
        if conn is not None:
            conn.close()
            log("MYSQL_CONNECTION_CLOSED=PASS")
        if mongo_client is not None:
            mongo_client.close()
            log("MONGODB_CLIENT_CLOSED=PASS")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EtlError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        # Avoid printing driver exception text: it may contain connection details.
        print(f"ERROR: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(99)