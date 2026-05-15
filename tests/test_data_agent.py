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

    def test_live_ocr_confusion_zhongtang_station(self):
        assert parse_destination_station("10000吨（铁路 沱子）") == "汐子"


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

    def test_live_ocr_confusion_in_remark_destination(self):
        remarks = [{"date": "5月10日", "sequence": "第一次下达计划", "plan": "10000吨（铁路 沱子）", "raw_line": ""}]
        parsed = parse_remarks(remarks, "2026-05-10")
        assert parsed[0]["destination"] == "汐子"
        assert parsed[0]["sequence"] == "lot01"

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
    def test_inspection_with_ship_anchor_matches_active_release_dispatch_rule(self, tmp_db):
        agent = BusinessDataAgent()
        bella_id = "844d4859bf906decebd6440fb9dead0c200a39c3"
        agent.db.execute(
            """
            INSERT INTO release_batches (
              id, batch_key, ship_name, cargo_name, destination_station,
              notice_date, batch_date, batch_sequence, batch_quantity,
              batch_count, source_json, searchable_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '{}', ?)
            """,
            (bella_id, "bella|xizi|lot03", "贝拉", "铁矿", "汐子", "2026-04-30", "2026-04-30", "lot03", 10000, "贝拉 汐子 铁矿"),
        )
        for ship in ["合远9", "卡迪", "康瑞", "丰收散运", "鞍子河"]:
            agent.db.execute(
                """
                INSERT INTO release_batches (
                  id, batch_key, ship_name, cargo_name, destination_station,
                  notice_date, batch_count, source_json, searchable_text, updated_at
                ) VALUES (?, ?, ?, '铁矿', '汐子', '2026-05-12', 1, '{}', ?, CURRENT_TIMESTAMP)
                """,
                (hash_text(ship), f"{ship}|xizi", ship, f"{ship} 汐子 铁矿"),
            )
        agent.db.commit()
        agent.refresh_release_dispatch_match_rules()

        result = agent.ingest_inspection_payload(
            {
                "project": "中唐特钢铁矿发运项目",
                "rows": [
                    {"seq": 1, "car_no": "4975136", "cargo_info_effective": "汐子铁矿粉"},
                    {"seq": 2, "car_no": "1682323", "cargo_info_effective": "汐子铁矿粉/贝拉"},
                ],
                "cargo_summary": {"汐子铁矿粉/贝拉": ["1682323"]},
                "meta": {"date": "2026年5月2日"},
            },
            source_file_name="bella_inspection.jpg",
        )

        assert result["status"] == "candidate"
        assert result["release_batch_ids"] == [bella_id]

    def test_inspection_without_ship_anchor_stays_pending_when_same_station_cargo_rules_exist(self, tmp_db):
        agent = BusinessDataAgent()
        for ship in ["贝拉", "丰收散运"]:
            agent.db.execute(
                """
                INSERT INTO release_batches (
                  id, batch_key, ship_name, cargo_name, destination_station,
                  notice_date, batch_count, source_json, searchable_text
                ) VALUES (?, ?, ?, '铁矿', '汐子', '2026-05-01', 1, '{}', ?)
                """,
                (hash_text(ship), f"{ship}|xizi", ship, f"{ship} 汐子 铁矿"),
            )
        agent.db.commit()
        agent.refresh_release_dispatch_match_rules()

        result = agent.ingest_inspection_payload(
            {
                "rows": [{"seq": 1, "car_no": "4975136", "cargo_info_effective": "汐子铁矿粉"}],
                "cargo_summary": {"汐子铁矿粉": ["4975136"]},
            },
            source_file_name="no_ship.jpg",
        )

        assert result["status"] == "pending"
        assert result["release_batch_ids"] == []

    def test_completed_release_dispatch_rule_no_longer_auto_matches(self, tmp_db):
        agent = BusinessDataAgent()
        bella_id = "844d4859bf906decebd6440fb9dead0c200a39c3"
        agent.db.execute(
            """
            INSERT INTO release_batches (
              id, batch_key, ship_name, cargo_name, destination_station,
              notice_date, batch_count, source_json, searchable_text
            ) VALUES (?, 'bella|xizi|lot03', '贝拉', '铁矿', '汐子', '2026-04-30', 1, '{}', '贝拉 汐子 铁矿')
            """,
            (bella_id,),
        )
        agent.db.commit()
        agent.refresh_release_dispatch_match_rules()
        agent.complete_release_dispatch_match_rule(bella_id, manual_note="本批已下表")

        result = agent.ingest_inspection_payload(
            {
                "rows": [{"seq": 1, "car_no": "1682323", "cargo_info_effective": "汐子铁矿粉/贝拉"}],
                "cargo_summary": {"汐子铁矿粉/贝拉": ["1682323"]},
            },
            source_file_name="bella_after_completed.jpg",
        )

        assert result["status"] == "pending"
        assert result["release_batch_ids"] == []
        release = agent.get(bella_id)
        rule = agent.db.execute(
            "SELECT status FROM release_dispatch_match_rules WHERE release_batch_id=?",
            (bella_id,),
        ).fetchone()
        assert release is not None
        assert release.dispatch_status == "completed"
        assert rule["status"] == "completed"

        forced = agent.ingest_inspection_payload(
            {
                "rows": [{"seq": 1, "car_no": "1682323", "cargo_info_effective": "汐子铁矿粉/贝拉"}],
                "cargo_summary": {"汐子铁矿粉/贝拉": ["1682323"]},
            },
            source_file_name="bella_after_completed_force_supplement.jpg",
            include_completed_release_batches=True,
        )
        assert forced["status"] == "candidate"
        assert forced["release_batch_ids"] == [bella_id]

    def test_suspended_release_dispatch_rule_no_longer_auto_matches_until_reopened(self, tmp_db):
        agent = BusinessDataAgent()
        bella_id = "batch-suspended-bella"
        agent.db.execute(
            """
            INSERT INTO release_batches (
              id, batch_key, ship_name, cargo_name, destination_station,
              notice_date, batch_count, source_json, searchable_text
            ) VALUES (?, 'bella|xizi|suspended', '贝拉', '铁矿', '汐子', '2026-04-30', 1, '{}', '贝拉 汐子 铁矿')
            """,
            (bella_id,),
        )
        agent.db.commit()
        agent.refresh_release_dispatch_match_rules()
        assert agent.update_release_dispatch_status(bella_id, "suspended", manual_note="等人工确认") is True

        payload = {
            "rows": [{"seq": 1, "car_no": "1682323", "cargo_info_effective": "汐子铁矿粉/贝拉"}],
            "cargo_summary": {"汐子铁矿粉/贝拉": ["1682323"]},
        }
        suspended = agent.ingest_inspection_payload(payload, source_file_name="bella_suspended.jpg")
        assert suspended["status"] == "pending"
        assert suspended["release_batch_ids"] == []

        assert agent.force_reopen_release_dispatch_match_rule(bella_id, manual_note="恢复发运") is True
        reopened = agent.ingest_inspection_payload(payload, source_file_name="bella_reopened.jpg")
        rule = agent.db.execute(
            "SELECT status, manual_note FROM release_dispatch_match_rules WHERE release_batch_id=?",
            (bella_id,),
        ).fetchone()
        assert reopened["status"] == "candidate"
        assert reopened["release_batch_ids"] == [bella_id]
        assert rule["status"] == "active"
        assert rule["manual_note"] == "恢复发运"

    def test_multiple_active_lots_for_same_ship_station_cargo_become_ambiguous(self, tmp_db):
        agent = BusinessDataAgent()
        for seq in ["lot01", "lot04"]:
            batch_id = f"malan-{seq}"
            agent.db.execute(
                """
                INSERT INTO release_batches (
                  id, batch_key, project, ship_name, cargo_name, destination_station,
                  notice_date, batch_date, batch_sequence, batch_quantity,
                  batch_count, source_json, searchable_text
                ) VALUES (?, ?, '中唐特钢铁矿发运项目', '马兰探险', '铁矿', '汐子',
                          '2026-05-13', '2026-05-13', ?, 10000, 1, '{}', ?)
                """,
                (batch_id, f"malan|xizi|{seq}", seq, f"马兰探险 汐子 铁矿 {seq}"),
            )
        agent.db.commit()
        agent.refresh_release_dispatch_match_rules()

        result = agent.ingest_inspection_payload(
            {
                "rows": [{"seq": 1, "car_no": "300001", "cargo_info_effective": "汐子铁矿粉/马兰探险"}],
                "cargo_summary": {"汐子铁矿粉/马兰探险": ["300001"]},
            },
            source_file_name="malan_ambiguous.jpg",
        )

        assert result["status"] == "ambiguous"
        assert result["reason"] == "ambiguous_release_batch_candidate"
        assert sorted(result["release_batch_ids"]) == ["malan-lot01", "malan-lot04"]

    def test_manual_assign_inspection_candidate_sets_audited_candidate_without_formal_write(self, tmp_db):
        agent = BusinessDataAgent()
        release_batch_id = "manual-batch-1"
        agent.db.execute(
            """
            INSERT INTO release_batches (
              id, batch_key, project, ship_name, cargo_name, destination_station,
              notice_date, batch_count, source_json, searchable_text
            ) VALUES (?, 'manual|batch', '中唐特钢铁矿发运项目', '马兰探险', '铁矿', '汐子', '2026-05-13', 1, '{}', '马兰探险 汐子 铁矿')
            """,
            (release_batch_id,),
        )
        agent.db.execute(
            """
            INSERT INTO inspection_ingestion_candidates (
              id, source_file_name, status, reason, group_name, release_batch_id,
              wagon_count, car_numbers_json, payload_json
            ) VALUES ('cand-manual-1', 'manual_inspection.jpg', 'ambiguous', 'ambiguous_release_batch_candidate',
                      '数据单发群', NULL, 1, '["300001"]', ?)
            """,
            (json.dumps({"rows": [{"seq": 1, "car_no": "300001"}], "_agent_sop_authorized": True}, ensure_ascii=False),),
        )
        agent.db.commit()

        assigned = agent.assign_inspection_candidate(
            "cand-manual-1",
            release_batch_id,
            operator_note="人工指认马兰探险 lot01",
        )
        row = agent.db.execute("SELECT * FROM inspection_ingestion_candidates WHERE id='cand-manual-1'").fetchone()
        payload = json.loads(row["payload_json"])

        assert assigned is True
        assert row["status"] == "candidate"
        assert row["reason"] == "manual_assigned_to_release_batch"
        assert row["release_batch_id"] == release_batch_id
        assert payload["_manual_assignment"]["operator_note"] == "人工指认马兰探险 lot01"
        assert payload["_manual_assignment"]["release_batch_id"] == release_batch_id

    def test_ingest_release_batch_auto_generates_active_dispatch_match_rule(self, tmp_db):
        agent = BusinessDataAgent()
        payload = {
            "is_target": True,
            "header_info": {"通知日期": "2026年4月30日"},
            "business_info": {"船名": "贝拉", "发货单位": "", "收货单位": ""},
            "cargo_info": {"货物名称": "铁矿", "总重里": "10000", "运输方式": "铁路"},
            "special_matter": "到站：汐子",
            "remarks": [{"date": "4月30日", "sequence": "第三次下达计划", "plan": "10000吨（铁路 汐子）", "raw_line": ""}],
        }

        records = agent.ingest_release_batch(payload, source_file_name="bella_plan.json")
        rule = agent.db.execute(
            "SELECT * FROM release_dispatch_match_rules WHERE release_batch_id=?",
            (records[0].id,),
        ).fetchone()

        assert rule is not None
        assert rule["status"] == "active"
        assert rule["ship_name"] == "贝拉"
        assert "贝拉" in rule["matching_str"]

    def test_non_sop_release_batch_does_not_enter_dispatch_index(self, tmp_db):
        agent = BusinessDataAgent()
        payload = {
            "is_target": True,
            "header_info": {"通知日期": "2026年5月12日"},
            "business_info": {"船名": "卡迪", "发货单位": "", "收货单位": "五矿物流（营口）有限公司"},
            "cargo_info": {"货物名称": "铁矿", "总重里": "20000", "运输方式": "铁路"},
            "special_matter": "到站：凌源东（凌东）",
            "remarks": [{"date": "5月12日", "sequence": "第二次下达计划", "plan": "20000吨（铁路 凌源东）", "raw_line": ""}],
        }

        records = agent.ingest_release_batch(payload, source_file_name="kadi_non_sop.json")
        rule_count = agent.db.execute(
            "SELECT count(*) FROM release_dispatch_match_rules WHERE release_batch_id=?",
            (records[0].id,),
        ).fetchone()[0]

        assert records
        assert rule_count == 0

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

    def test_ingest_business_text_does_not_create_when_lot_is_ambiguous(self, tmp_db):
        agent = BusinessDataAgent()
        payload = {
            "is_target": True,
            "project": "中唐特钢铁矿发运项目",
            "header_info": {"通知日期": "2026年04月28日"},
            "business_info": {"船名": "马兰探险", "发货单位": "中国外运东北有限公司锦州分公司", "收货单位": "中国外运东北有限公司锦州分公司"},
            "cargo_info": {"货物名称": "铁矿", "货物品名": "铁矿", "运输方式": "铁路"},
            "special_matter": "到站:汐子",
            "remarks": [
                {"date": "2026-04-04", "sequence": "第一次下达计划", "quantity": 10000, "destination": "汐子", "raw_line": "lot01 10000吨 汐子"},
                {"date": "2026-04-17", "sequence": "第四次下达计划", "quantity": 10000, "destination": "汐子", "raw_line": "lot04 10000吨 汐子"},
            ],
        }
        agent.ingest_release_batch(payload, source_file_name="malan_departure.json")
        text = """供方: 中国外运东北有限公司锦州分公司
船名：马兰探险
货名：铁矿粉
港口：锦州港
数量：10000
计划号：90260500008
合同号：ZLZT-2026050801"""

        records = agent.ingest_business_text(text)

        assert records == []
        rows = agent.list_release_batches()
        assert len(rows) == 2
        assert all(row.plan_id is None for row in rows)
        audit = agent.db.execute("select * from image_ingestion_audit where message_type='text'").fetchone()
        assert audit["status"] == "pending"
        assert audit["reason"] == "ambiguous_release_batch_match"
        assert audit["requires_manual_review"] == 1

    def test_ingest_business_text_updates_unique_existing_lot(self, tmp_db):
        agent = BusinessDataAgent()
        payload = {
            "is_target": True,
            "project": "中唐特钢铁矿发运项目",
            "header_info": {"通知日期": "2026年04月28日"},
            "business_info": {"船名": "马兰探险", "发货单位": "中国外运东北有限公司锦州分公司", "收货单位": "中国外运东北有限公司锦州分公司"},
            "cargo_info": {"货物名称": "铁矿", "货物品名": "铁矿", "运输方式": "铁路"},
            "special_matter": "到站:汐子",
            "remarks": [
                {"date": "2026-04-23", "sequence": "第五次下达计划", "quantity": 8248, "destination": "乌兰浩特", "raw_line": "lot05 8248吨 乌兰浩特"},
                {"date": "2026-04-28", "sequence": "第六次下达计划", "quantity": 10000, "destination": "汐子", "raw_line": "lot06 10000吨 汐子"},
            ],
        }
        agent.ingest_release_batch(payload, source_file_name="malan_departure.json")
        text = """供方: 中国外运东北有限公司锦州分公司
船名：马兰探险
货名：纽曼粉
港口：锦州港
到站：乌兰浩特
数量：8248
计划号：90260500008
合同号：ZLZT-2026050801"""

        records = agent.ingest_business_text(text)

        assert len(records) == 1
        record = records[0]
        assert record.batch_sequence == "lot05"
        assert record.destination_station == "乌兰浩特"
        assert record.batch_quantity == 8248.0
        assert record.plan_id == "90260500008"
        assert record.contract_no == "ZLZT-2026050801"
        assert record.cargo_product_name == "纽曼粉"
        assert len(agent.list_release_batches()) == 2
        audit = agent.db.execute("select * from image_ingestion_audit where message_type='text'").fetchone()
        assert audit["status"] == "ingested"
        assert audit["db_action"] == "release_batch_update"


class TestHashText:
    def test_deterministic(self):
        assert hash_text("test") == hash_text("test")

    def test_different_inputs(self):
        assert hash_text("a") != hash_text("b")
