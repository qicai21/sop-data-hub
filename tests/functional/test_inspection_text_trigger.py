"""#143:检装车文本触发器 — 复合/单船文本拆 task + 与通知单候选 rendezvous。

业务定位:微信文本("汐子铁鞍子河5节")只当触发器 + 预期车数;车号顺序 /
lot 归属仍归检装车通知单。本测试覆盖:
  1. 提取器:复合多船文本只抽出明确属于检验类项目的船段,歧义段不猜。
  2. 路由:复合文本 → inspection_text_trigger,吉林四平不被吞。
  3. 扇出:一条复合文本 inbox → N 个 task(每船一个,带预期车数)。
  4. rendezvous:无候选 → pending 等待;有候选 → 委托既有检验链并附预期车数。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from sop_hub.sop.message_inbox import ensure_message_inbox_schema
from sop_hub.sop.monitoring_plan_matcher import MessageEvent
from sop_hub.sop.text_router import (
    classify_text_message,
    extract_inspection_text_triggers,
)
from sop_hub.sop.workflow_task_store import (
    create_task_from_message_inbox,
    ensure_workflow_task_db_schema,
)

WX_754 = "十四道瓢屯镍，丰收20节，乌兰浩特镍长航滨海1节，汐子铁鞍子河5节，朝阳西铁宝腾海15节"


# ── 1. 提取器 ────────────────────────────────────────────────────────────
def test_extract_composite_only_inspection_ships():
    trs = extract_inspection_text_triggers(WX_754)
    by_ship = {t["ship"]: t for t in trs}
    # 鞍子河(中唐)/宝腾海(朝阳)有明确 yaml known_ships 归属 → 抽出
    assert "鞍子河" in by_ship
    assert by_ship["鞍子河"]["project_id"] == "zhongtang_special_steel"
    assert by_ship["鞍子河"]["destination"] == "汐子"
    assert by_ship["鞍子河"]["expected_count"] == 5
    assert "宝腾海" in by_ship
    assert by_ship["宝腾海"]["project_id"] == "chaoyang_steel"
    assert by_ship["宝腾海"]["expected_count"] == 15
    # 长航滨海 歧义(可能走四平镍/jilin)→ 不在检验类 yaml → 保守不抽
    assert "长航滨海" not in by_ship


def test_extract_count_prefix_form():
    trs = extract_inspection_text_triggers("十四道 15节宝腾海 朝阳西铁")
    assert len(trs) == 1
    assert trs[0]["ship"] == "宝腾海"
    assert trs[0]["expected_count"] == 15


def test_extract_count_far_before_ship():
    # wx_931 形态:车数前置 + 中间夹"朝阳西铁",船名在最后(超 8 字符回找窗)
    trs = extract_inspection_text_triggers("14道52节  朝阳西铁  中联发")
    assert len(trs) == 1
    assert trs[0]["ship"] == "中联发"
    assert trs[0]["expected_count"] == 52
    assert trs[0]["project_id"] == "chaoyang_steel"


def test_extract_jilin_not_captured():
    # 吉林四平蓝鳍属 jilin,不是检验类 → 不抽
    assert extract_inspection_text_triggers("煤六 四平铁 蓝鳍 53节") == []


# ── 2. 路由 ──────────────────────────────────────────────────────────────
def _event(text: str) -> MessageEvent:
    return MessageEvent(
        message_id="m1", channel="wechat", group_id="g",
        text=text, received_at="2026-06-13 10:00:00",
    )


def test_route_composite_to_trigger():
    r = classify_text_message(_event(WX_754))
    assert r.sop_flow == "inspection_text_trigger_flow"
    assert r.sop_node == "inspection_text_trigger"
    assert r.sop_project_id == ""  # 多项目混合 → 留空


def test_route_single_zhongtang_to_trigger():
    r = classify_text_message(_event("十四道汐子铁鞍子河5节"))
    assert r.sop_node == "inspection_text_trigger"
    assert r.sop_project_id == "zhongtang_special_steel"


def test_route_huanqiu_xinren_to_trigger():
    r = classify_text_message(_event("煤五，汐子铁，环球信任，实装33节"))
    assert r.sop_node == "inspection_text_trigger"
    assert r.sop_flow == "inspection_text_trigger_flow"
    assert r.sop_project_id == "zhongtang_special_steel"


def test_route_baoli_to_trigger():
    r = classify_text_message(_event("汐子铁，宝丽，实装40节"))
    assert r.sop_node == "inspection_text_trigger"
    assert r.sop_flow == "inspection_text_trigger_flow"
    assert r.sop_project_id == "zhongtang_special_steel"


def test_zhongtang_freight_only_group_ignores_inspection_text():
    r = classify_text_message(
        MessageEvent(
            message_id="m2",
            channel="wechat",
            group_id="中唐特钢发运群",
            text="煤四 汐子铁 宝丽 53节",
            metadata={"group_name": "中唐特钢发运群"},
        )
    )
    assert r.processing_status == "ignored"
    assert r.sop_flow == ""
    assert "仅跟踪货运信息" in r.summary


def test_zhongtang_freight_only_group_keeps_freight_detail():
    r = classify_text_message(
        MessageEvent(
            message_id="m3",
            channel="wechat",
            group_id="中唐特钢发运群",
            text="船名：宝丽 货名：混合粉 合同号：ZLZT-2026070901 计划号：90260700027",
            metadata={"group_name": "中唐特钢发运群"},
        )
    )
    assert r.processing_status == "matched_sop"
    assert r.sop_flow == "freight_detail_flow"


def test_route_jilin_still_departure():
    r = classify_text_message(_event("煤六 四平铁 蓝鳍 53节"))
    assert r.sop_node == "detect_departure_message"
    assert r.sop_project_id == "jilin_jingang_jinzhou"


# ── 3 + 4. 扇出 + rendezvous(需 tmp DB)──────────────────────────────────
@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "sop.db"
    ensure_message_inbox_schema(db_path=p)
    ensure_workflow_task_db_schema(db_path=p)
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE inspection_ingestion_candidates ("
        " id TEXT PRIMARY KEY, message_id TEXT, ship_name TEXT,"
        " destination TEXT, candidate_status TEXT, created_at TEXT,"
        " reason TEXT, updated_at TEXT, wagon_count INTEGER)"  # Fix A reason/updated_at;e30a067 wagon_count(车数闸)
    )
    conn.commit()
    conn.close()
    return p


def _insert_inbox(db, text: str, *, inbox_id: int = 100) -> int:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO message_inbox (id, message_id, group_name, received_datetime, "
        " text_content, sop_flow, sop_node, is_sop_msg, processing_status) "
        "VALUES (?,?,?,?,?,?,?,1,'matched_sop')",
        (inbox_id, f"wx_{inbox_id}", "铁晟业务工作群", "2026-06-13 10:00:00",
         text, "inspection_text_trigger_flow", "inspection_text_trigger"),
    )
    conn.commit()
    conn.close()
    return inbox_id


def test_fanout_creates_one_task_per_ship(db):
    inbox_id = _insert_inbox(db, WX_754)
    res = create_task_from_message_inbox(inbox_id, db_path=db)
    assert res["action"] == "created_multi"
    assert len(res["ids"]) == 2  # 鞍子河 + 宝腾海

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT task_type, project_id, input_json FROM workflow_task_db "
        "WHERE message_inbox_id=? ORDER BY id", (inbox_id,),
    ).fetchall()
    conn.close()
    assert len(rows) == 2
    ships = sorted(json.loads(r["input_json"])["trigger_ship"] for r in rows)
    assert ships == ["宝腾海", "鞍子河"]
    for r in rows:
        ij = json.loads(r["input_json"])
        assert ij["trigger_expected_count"] in (5, 15)
        # UNIQUE(inbox, task_type) → 每船 task_type 后缀船名
        assert r["task_type"].startswith("inspection_text_trigger:")


def test_fanout_idempotent(db):
    inbox_id = _insert_inbox(db, WX_754)
    create_task_from_message_inbox(inbox_id, db_path=db)
    res2 = create_task_from_message_inbox(inbox_id, db_path=db)
    # 第二次全是 duplicate → 不新建
    assert res2["ids"] == []
    conn = sqlite3.connect(str(db))
    n = conn.execute(
        "SELECT count(*) FROM workflow_task_db WHERE message_inbox_id=?",
        (inbox_id,)).fetchone()[0]
    conn.close()
    assert n == 2


def test_rendezvous_pending_when_no_candidate(db):
    from sop_hub.sop.workflow_task_executor import run_workflow_task
    inbox_id = _insert_inbox(db, "十四道汐子铁鞍子河5节")
    res = create_task_from_message_inbox(inbox_id, db_path=db)
    task_id = res["ids"][0]
    out = run_workflow_task(task_id, db_path=db)
    # 无候选 + 任务刚建 → pending 等待(stage waiting_inspection_notice)
    assert out["status"] == "pending"
    assert out["output_json"]["stage"] == "waiting_inspection_notice"
    assert out["output_json"]["expected_count"] == 5
    # 仍 pending → 下一轮可重新捞起
    conn = sqlite3.connect(str(db))
    st = conn.execute(
        "SELECT task_status FROM workflow_task_db WHERE id=?", (task_id,)
    ).fetchone()[0]
    conn.close()
    assert st == "pending"


def test_rendezvous_delegates_when_candidate_exists(db):
    """有匹配候选 → 委托既有检验链;结果带 text_trigger 交叉校验标注。"""
    from sop_hub.sop.workflow_task_executor import run_workflow_task

    # 放一个匹配候选 + 指向它的 inbox 行(图片侧)。
    # e30a067 起候选匹配加三道闸:未消费(candidate)+ 车数对齐(±4)+ 临近(24h内)。
    # 故 fixture 必须 wagon_count 对齐触发预期(5节)、created_at 用 datetime('now')。
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO inspection_ingestion_candidates "
        "(id, message_id, ship_name, destination, candidate_status, wagon_count, created_at) "
        "VALUES ('cand1','wx_img_1','鞍子河','汐子','candidate',5, datetime('now'))"
    )
    conn.execute(
        "INSERT INTO message_inbox (id, message_id, group_name, received_datetime, "
        " inspection_candidate_id, is_sop_msg, processing_status) "
        "VALUES (200,'wx_img_1','铁晟业务工作群','2026-06-13 09:00:00','cand1',1,'matched_sop')"
    )
    conn.commit()
    conn.close()

    inbox_id = _insert_inbox(db, "十四道汐子铁鞍子河5节", inbox_id=101)
    res = create_task_from_message_inbox(inbox_id, db_path=db)
    task_id = res["ids"][0]
    out = run_workflow_task(task_id, db_path=db)
    # 委托发生了:结果(无论链成功/失败)都带 text_trigger 标注,指向候选
    oj = out.get("output_json") or {}
    assert oj.get("text_trigger", {}).get("delegated_to_candidate") == "cand1"
    assert oj["text_trigger"]["expected_count"] == 5


def test_rendezvous_does_not_skip_matched_candidate_outside_event_bounds(db):
    from sop_hub.sop.workflow_task_executor import run_workflow_task

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO inspection_ingestion_candidates "
        "(id, message_id, ship_name, destination, candidate_status, wagon_count, created_at) "
        "VALUES ('cand_done','wx_img_done','环球信任','汐子','matched',35, datetime('now'))"
    )
    conn.commit()
    conn.close()

    inbox_id = _insert_inbox(db, "煤五，汐子铁，环球信任，实装33节", inbox_id=102)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE message_inbox SET received_datetime='2026-07-02 01:43:09' WHERE id=?",
        (inbox_id,),
    )
    conn.commit()
    conn.close()

    res = create_task_from_message_inbox(inbox_id, db_path=db)
    task_id = res["ids"][0]
    out = run_workflow_task(task_id, db_path=db)
    oj = out.get("output_json") or {}
    assert oj.get("stage") != "already_handled_by_notice_chain"
    assert out["status"] == "pending"
    assert oj["stage"] == "waiting_inspection_notice"
