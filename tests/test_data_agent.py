"""data_agent 单元测试 — SQLite 集成测试"""
import json
from pathlib import Path

import pytest

from sop_hub.data_agent.agent import (
    BusinessDataAgent,
    canonicalize_ship_text,
    normalize_chinese_date,
    parse_destination_station,
    parse_remarks,
    project_known_ships,
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

    def test_single_release_plan_without_explicit_sequence_defaults_to_lot01(self, tmp_db):
        agent = BusinessDataAgent()
        payload = {
            "is_target": True,
            "project": "朝阳钢铁铁矿发运项目",
            "header_info": {"通知日期": "2026年05月11日"},
            "business_info": {
                "船名": "宝腾海",
                "发货单位": "鞍钢汽车运输有限责任公司",
                "收货单位": "鞍钢汽车运输有限责任公司",
            },
            "cargo_info": {"货物名称": "铁矿", "运输方式": "铁路"},
            "special_matter": "发运“宝腾海”轮所卸货物，火运出港，到站：朝阳西。",
            "remarks": [{"date": "", "sequence": "", "plan": "", "raw_line": ""}],
        }

        records = agent.ingest_release_batch(payload, source_file_name="baotenghai_plan.jpg")

        assert len(records) == 1
        assert records[0].batch_sequence == "lot01"
        assert records[0].batch_key.endswith("|朝阳西|2026-05-11|lot01")
        assert records[0].destination_station == "朝阳西"

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

    def test_completed_batch_locked_against_notice_resend(self, tmp_db):
        """#jilin-resend 2026-06-18:已 confirmed_received 的 lot,通知单重发
        (数量漂移)绝不能被改写——锁死。

        旧 bug:累计出港通知单重发把历史 lot 又带回来,OCR 数量噪声触发
        date命中+qty不符 → review_needed → ON CONFLICT 重写已收货完成的批次
        (updated_at 翻新、群里误报"新建放货批次")。
        """
        agent = BusinessDataAgent()
        payload = {
            "is_target": True,
            "header_info": {"通知日期": "2026年6月1日"},
            "business_info": {"船名": "蓝鳍", "发货单位": "", "收货单位": ""},
            "cargo_info": {"货物名称": "铁矿粉", "运输方式": "铁路"},
            "special_matter": "到站:四平",
            "remarks": [
                {"date": "6月1日", "sequence": "1", "plan": "9000吨", "raw_line": "9000吨"},
                {"date": "6月2日", "sequence": "2", "plan": "9000吨", "raw_line": "9000吨"},
            ],
        }
        recs = agent.ingest_release_batch(payload, source_file_name="lanqi_v1.json")
        assert len(recs) == 2
        first = recs[0]
        assert first.batch_quantity == 9000

        # 第一条 lot 收货完成(终态)
        assert agent.update_release_dispatch_status(first.id, "confirmed_received")

        # 通知单重发:该 lot 数量漂移 9000→8888(date 命中 qty 不符,旧逻辑会重写)
        payload_resend = json.loads(json.dumps(payload))
        payload_resend["remarks"][0]["plan"] = "8888吨"
        payload_resend["remarks"][0]["raw_line"] = "8888吨"
        agent.ingest_release_batch(payload_resend, source_file_name="lanqi_resend.json")

        after = {r.id: r for r in agent.list_release_batches()}
        assert len(after) == 2                                   # 无重复行
        locked = after[first.id]
        assert locked.dispatch_status == "confirmed_received"    # 状态没被动
        assert locked.batch_quantity == 9000                     # 数量没被改成 8888(锁死)

    def test_in_progress_batch_not_locked_on_resend(self, tmp_db):
        """对照组:loading(进行中)批次不在锁定集,重发不会被 lock 跳过——避免误伤。
        (是否更新数量是既有 dedup 行为;这里只守"锁不误伤进行中批次、且不重复建"。)"""
        agent = BusinessDataAgent()
        payload = {
            "is_target": True,
            "header_info": {"通知日期": "2026年6月1日"},
            "business_info": {"船名": "马兰希望", "发货单位": "", "收货单位": ""},
            "cargo_info": {"货物名称": "铁矿粉", "运输方式": "铁路"},
            "special_matter": "到站:四平",
            "remarks": [{"date": "6月1日", "sequence": "1", "plan": "20000吨", "raw_line": "20000吨"}],
        }
        recs = agent.ingest_release_batch(payload, source_file_name="mlxw_v1.json")
        lot01 = recs[0]
        agent.update_release_dispatch_status(lot01.id, "loading")
        payload_resend = json.loads(json.dumps(payload))
        payload_resend["remarks"][0]["plan"] = "21000吨"
        payload_resend["remarks"][0]["raw_line"] = "21000吨"
        agent.ingest_release_batch(payload_resend, source_file_name="mlxw_resend.json")
        after = {r.id: r for r in agent.list_release_batches()}
        assert len(after) == 1                              # 不重复建
        assert after[lot01.id].dispatch_status == "loading"  # 进行中批次未被锁逻辑破坏

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




class TestCanonicalizeShip:
    def test_live_ocr_confusion_lanqi(self):
        # 2026-06-18 蓝鳍 被 VLM OCR 成 蓝嶂(嶂 生僻字)→ 归一回正名。
        assert canonicalize_ship_text("蓝嶂") == "蓝鳍"

    def test_strips_whitespace_before_mapping(self):
        assert canonicalize_ship_text("  蓝嶂 ") == "蓝鳍"

    def test_known_ship_unchanged(self):
        assert canonicalize_ship_text("蓝鳍") == "蓝鳍"

    def test_none_and_empty_passthrough(self):
        assert canonicalize_ship_text(None) is None
        assert canonicalize_ship_text("") == ""
        assert canonicalize_ship_text("   ") == ""


class TestProjectKnownShips:
    def test_jilin_known_ships_loaded_from_yaml(self):
        ships = project_known_ships("jilin_jingang_jinzhou")
        assert "蓝鳍" in ships
        assert "马兰希望" in ships

    def test_unknown_project_returns_empty(self):
        assert project_known_ships("不存在的项目") == set()
        assert project_known_ships(None) == set()


class TestShipNameOcrGuard:
    def test_ocr_misread_ship_name_is_canonicalized_then_ingested(self, tmp_db):
        # 蓝嶂(误读)归一为 蓝鳍 后,船名落在 jilin known_ships 内 → 正常建批次。
        agent = BusinessDataAgent()
        payload = {
            "is_target": True,
            "project": "jilin_jingang_jinzhou",
            "header_info": {"通知日期": "2026年6月18日"},
            "business_info": {"船名": "蓝嶂", "发货单位": "", "收货单位": ""},
            "cargo_info": {"货物名称": "铁矿粉", "总重里": "5000", "运输方式": "铁路"},
            "special_matter": "到站：四平",
            "remarks": [{"date": "6月18日", "sequence": "第一次下达计划", "plan": "5000吨（铁路 四平）", "raw_line": ""}],
        }

        records = agent.ingest_release_batch(payload, source_file_name="lanqi_ocr_lot01.json")

        assert len(records) == 1
        assert records[0].ship_name == "蓝鳍"

    def test_unknown_ship_name_skips_whole_group(self, tmp_db):
        # 归一后仍不在 jilin known_ships → 疑似没覆盖的误读/未登记新船,
        # 不建整组批次(避免整组空批次需人工删)。
        agent = BusinessDataAgent()
        payload = {
            "is_target": True,
            "project": "jilin_jingang_jinzhou",
            "header_info": {"通知日期": "2026年6月18日"},
            "business_info": {"船名": "幽灵号", "发货单位": "", "收货单位": ""},
            "cargo_info": {"货物名称": "铁矿粉", "总重里": "5000", "运输方式": "铁路"},
            "special_matter": "到站：四平",
            "remarks": [
                {"date": "6月18日", "sequence": "第一次下达计划", "plan": "5000吨（铁路 四平）", "raw_line": ""},
                {"date": "6月18日", "sequence": "第二次下达计划", "plan": "5000吨（铁路 四平）", "raw_line": ""},
            ],
        }

        records = agent.ingest_release_batch(payload, source_file_name="ghost_ship.json")

        assert records == []
        assert agent.list_release_batches() == []


class TestHashText:
    def test_deterministic(self):
        assert hash_text("test") == hash_text("test")

    def test_different_inputs(self):
        assert hash_text("a") != hash_text("b")
