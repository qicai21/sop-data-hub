"""data_agent 单元测试 — SQLite 集成测试"""
import json
from pathlib import Path

import pytest

from ops_hub.data_agent.agent import (
    BusinessDataAgent,
    normalize_chinese_date,
    parse_destination_station,
    parse_remarks,
    hash_text,
)


class TestNormalizeChinese:
    def test_standard_format(self):
        assert normalize_chinese_date("2026年4月23日") == "2026-04-23"

    def test_single_digit_month_day(self):
        assert normalize_chinese_date("2026年1月5日") == "2026-01-05"

    def test_none_input(self):
        assert normalize_chinese_date(None) is None

    def test_non_chinese_format(self):
        assert normalize_chinese_date("2026-04-23") == "2026-04-23"


class TestParseDestinationStation:
    def test_standard(self):
        assert parse_destination_station("到站：汐子") == "汐子"

    def test_colon_variant(self):
        assert parse_destination_station("到站:马林") == "马林"

    def test_no_match(self):
        assert parse_destination_station("没有到站信息") is None


class TestParseRemarks:
    def test_basic_remark(self):
        remarks = [{"date": "4月23日", "sequence": "1", "plan": "100吨（铁路 汐子）", "raw_line": ""}]
        parsed = parse_remarks(remarks, "2026-04-20")
        assert len(parsed) == 1
        assert parsed[0]["quantity"] == 100.0
        assert parsed[0]["transport_mode"] == "铁路"

    def test_remaining_qty(self):
        remarks = [{"date": "", "sequence": "", "plan": "200吨，剩余50吨", "raw_line": ""}]
        parsed = parse_remarks(remarks, "2026-04-20")
        assert parsed[0]["remaining_qty"] == 50.0

    def test_destination_from_to_station_phrase(self):
        remarks = [
            {
                "date": "4月20日",
                "sequence": "",
                "plan": "货主通知：汽运返库计划有14500吨改为铁路返库，火运敞车出港，到站：朝阳西",
                "raw_line": "",
            }
        ]
        parsed = parse_remarks(remarks, "2026-04-12")
        assert parsed[0]["transport_mode"] == "铁路"
        assert parsed[0]["destination"] == "朝阳西"
        assert parsed[0]["quantity"] == 14500.0


class TestBusinessDataAgent:
    def test_ingest_and_list(self, tmp_db):
        """Test basic ingest -> list cycle"""
        agent = BusinessDataAgent()

        sample_payload = {
            "is_target": True,
            "title": "锦州港货物出港计划通知单",
            "header_info": {"通知日期": "2026年4月20日", "内、外贸": "外贸"},
            "business_info": {
                "发货单位": "测试公司",
                "船名": "金泰68",
                "收货单位": "收货方",
                "联系人": "", "电话": "", "到达港": "", "接货库场": "",
                "承运单位": "", "船期": ""
            },
            "cargo_info": {
                "货物名称": "铁矿粉",
                "包装": "散装", "单件重": "", "总件数": "",
                "总重里": "5000吨", "日进货量": "", "运输方式": "铁路",
                "进货时间": "", "发货站(地)": ""
            },
            "special_matter": "到站：汐子",
            "remarks": [
                {"date": "4月21日", "sequence": "1", "plan": "500吨（铁路 汐子）", "raw_line": ""},
                {"date": "4月22日", "sequence": "2", "plan": "600吨", "raw_line": ""},
            ],
            "footer_ignored": True
        }

        records = agent.ingest_release_batch(sample_payload, source_file_name="test.json")
        assert len(records) == 2
        assert records[0].ship_name == "金泰68"
        assert records[0].cargo_name == "铁矿粉"

        all_records = agent.list_release_batches()
        assert len(all_records) == 2

    def test_upsert_dedup(self, tmp_db):
        """Same payload ingested twice should not create duplicates"""
        agent = BusinessDataAgent()
        payload = {
            "is_target": True,
            "header_info": {"通知日期": "2026年4月20日"},
            "business_info": {"船名": "测试船", "发货单位": "", "收货单位": "",
                              "联系人": "", "电话": "", "到达港": "", "接货库场": "",
                              "承运单位": "", "船期": ""},
            "cargo_info": {"货物名称": "煤炭", "包装": "", "单件重": "",
                           "总件数": "", "总重里": "", "日进货量": "",
                           "运输方式": "", "进货时间": "", "发货站(地)": ""},
            "special_matter": "",
            "remarks": [{"date": "4月21日", "sequence": "1", "plan": "100吨", "raw_line": ""}],
        }
        agent.ingest_release_batch(payload)
        agent.ingest_release_batch(payload)

        records = agent.list_release_batches()
        assert len(records) == 1  # upsert, not duplicate


class TestHashText:
    def test_deterministic(self):
        assert hash_text("test") == hash_text("test")

    def test_different_inputs(self):
        assert hash_text("a") != hash_text("b")
