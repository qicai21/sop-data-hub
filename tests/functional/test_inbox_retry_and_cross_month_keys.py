"""回归测试:#117 #118

bug 2 (#117):upsert/text_router 失败时不推 cursor + 抛 InboxWriteRetryNeeded
              → 防止 SOP 链因瞬态 db lock 永久丢消息(蓝鳍 wx_367 案例)
bug 3 (#118):message_inbox 唯一键加 source_file 区分跨月 jsonl
              → wx_367 在 2026-05.jsonl 和 2026-06.jsonl 各能进 inbox 一行
"""
from __future__ import annotations

import importlib
import logging
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


# ── helper: 构造一个最小可用 MessageEvent ──────────────────────────
def _make_event(
    message_id: str = "wx_367",
    source_file: str = "/repo/铁晟业务工作群/2026-06.jsonl",
    text: str = "煤六 50节 四平铁 蓝鳍",
    received_at: str = "2026-06-06 21:28:40",
    local_id: int = 367,
) -> Any:
    raw_bundle = SimpleNamespace(
        message_id=message_id,
        group_id="铁晟业务工作群",
        source_agent="wx-ops-agent",
        received_at=received_at,
        raw_image_path=None,
        ocr_json_path=None,
        message_metadata_path=source_file,
        text=text,
        extraction_kind="text",
        registration_status="complete",
        warnings=[],
    )
    return SimpleNamespace(
        message_id=message_id,
        channel="wechat",
        group_id="铁晟业务工作群",
        source_agent="wx-ops-agent",
        received_at=received_at,
        message_type="text",
        text=text,
        raw_asset_bundle=raw_bundle,
        metadata={
            "local_id": local_id,
            "server_id": None,
            "message_key": "",
            "image_md5": "",
            "group_name": "铁晟业务工作群",
            "group_wxid": "",
            "sender": "A静候佳音",
            "sender_wxid": "wxid_xxx",
            "source_file": source_file,
            "processing_status": "ready",
            "media_status": "complete",
        },
    )


@pytest.fixture()
def temp_db(monkeypatch):
    """临时 sop_agent.db,所有相关模块都走它。"""
    tmp = tempfile.NamedTemporaryFile(suffix="_inbox.db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(db_path))
    yield db_path
    if db_path.exists():
        db_path.unlink()


# ── #118: 跨月 jsonl 同 message_id 都能进 inbox ────────────────────
def test_cross_month_jsonl_same_message_id_both_insert(temp_db):
    from sop_hub.sop.message_inbox import ensure_message_inbox_schema, upsert_message_inbox_event

    ensure_message_inbox_schema(db_path=str(temp_db))

    # 5月版本 wx_367
    ev_may = _make_event(
        source_file="/wx/铁晟业务工作群/2026-05.jsonl",
        text="煤四 凌东铁 康瑞 工人清车皮铲车装车作业",
        received_at="2026-05-06 14:51:15",
    )
    r1 = upsert_message_inbox_event(ev_may, db_path=str(temp_db))
    assert r1["action"] == "inserted"

    # 6月版本 wx_367 — 同 message_id 但 source_file 不同
    ev_jun = _make_event(
        source_file="/wx/铁晟业务工作群/2026-06.jsonl",
        text="煤六 50节 四平铁 蓝鳍",
        received_at="2026-06-06 21:28:40",
    )
    r2 = upsert_message_inbox_event(ev_jun, db_path=str(temp_db))
    assert r2["action"] == "inserted", "6月版本应该作为新行 INSERT,不能跟 5 月行冲突"
    assert r1["id"] != r2["id"]

    conn = sqlite3.connect(str(temp_db))
    rows = conn.execute(
        "SELECT message_id, source_file, text_content, received_datetime "
        "FROM message_inbox WHERE message_id='wx_367' ORDER BY received_datetime"
    ).fetchall()
    conn.close()
    assert len(rows) == 2
    assert rows[0][1].endswith("2026-05.jsonl")
    assert rows[1][1].endswith("2026-06.jsonl")
    assert "蓝鳍" in rows[1][2]


# ── 2026-06-16: 内容去重(朝阳中联发刷屏回归)──────────────────────
def test_same_group_same_text_content_deduped(temp_db):
    """同群同文本 12h 内重读(新 message_id)→ 内容去重拦截,不新建。

    回归:wechat-ops-agent 把同一条消息每轮重复 append(每次新 wx_<seq>),
    按 message_id 去重失效 → 反复建 inbox/task → excel 发送刷屏。
    """
    from sop_hub.sop.message_inbox import (
        ensure_message_inbox_schema,
        upsert_message_inbox_event,
    )

    ensure_message_inbox_schema(db_path=str(temp_db))
    # 真实重发:wx-ops 每轮重 append 同消息会拿**新 seq**(= 新 local_id),故 message_id
    # 与 local_id 都变 → 主键(local_id)不命中 → 落内容去重(12h 同文本)兜底。
    ev1 = _make_event(message_id="wx_189", local_id=189, text="中联发 第二列 53车")
    assert upsert_message_inbox_event(ev1, db_path=str(temp_db))["action"] == "inserted"

    ev2 = _make_event(message_id="wx_191", local_id=191, text="中联发 第二列 53车")
    r2 = upsert_message_inbox_event(ev2, db_path=str(temp_db))
    assert r2["action"] == "duplicate_content", "同群同文本重读应被内容去重拦"

    # 不同文本不受影响,正常新建
    ev3 = _make_event(message_id="wx_193", local_id=193, text="中联发 工人清车皮")
    assert upsert_message_inbox_event(ev3, db_path=str(temp_db))["action"] == "inserted"

    conn = sqlite3.connect(str(temp_db))
    n = conn.execute(
        "SELECT count(*) FROM message_inbox WHERE text_content='中联发 第二列 53车'"
    ).fetchone()[0]
    conn.close()
    assert n == 1, "同文本只应有 1 行"


# ── #118 idempotency: 同 source_file 再 upsert 仍是 UPDATE ────────
def test_same_source_file_upsert_is_update(temp_db):
    from sop_hub.sop.message_inbox import ensure_message_inbox_schema, upsert_message_inbox_event

    ensure_message_inbox_schema(db_path=str(temp_db))
    ev = _make_event()
    r1 = upsert_message_inbox_event(ev, db_path=str(temp_db))
    r2 = upsert_message_inbox_event(ev, db_path=str(temp_db))
    assert r1["action"] == "inserted"
    assert r2["action"] == "updated"
    assert r1["id"] == r2["id"]


# ── #117: upsert 失败时 process_event_once 抛 InboxWriteRetryNeeded ──
def test_process_event_once_raises_on_upsert_failure(temp_db, monkeypatch, tmp_path):
    """模拟 upsert 抛异常(daemon 锁场景),验证:
    - process_event_once 抛 InboxWriteRetryNeeded
    - cursor 没被推进
    """
    # 需要 path 才能 import scripts.run_live_service
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    if "run_live_service" in sys.modules:
        del sys.modules["run_live_service"]

    rls = importlib.import_module("scripts.run_live_service")

    # patch upsert_message_inbox_event 让它抛"database is locked"
    def _fail_upsert(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(
        "sop_hub.sop.message_inbox.upsert_message_inbox_event", _fail_upsert,
    )

    cursor: dict[str, Any] = {"sources": {}}
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    logger = logging.getLogger("test")
    plan: dict[str, Any] = {}
    ev = _make_event()

    with pytest.raises(rls.InboxWriteRetryNeeded):
        rls.process_event_once(
            event=ev, monitoring_plan=plan, runtime_root=runtime_root,
            logger=logger, apply_mode=False, cursor=cursor,
            write_message_inbox=True,
        )

    # cursor 应仍是空 — 没被这条失败消息推进
    assert cursor["sources"] == {}, "cursor 不应在 upsert 失败时前进"


# ── #119 split-row excel: container_numbers_json 空时 fallback ─────
def test_split_row_excel_falls_back_to_container_no(temp_db, tmp_path):
    """split 车 container_batch_map 有 2 lot,但 container_numbers_json
    没填(95306 来源只填 container_no="A/B" raw 字符串)→ 老代码会输出空
    rows,fallback "/"-split 修了它。"""
    import json
    import sqlite3
    from sop_hub.sop.message_inbox import ensure_message_inbox_schema
    ensure_message_inbox_schema(db_path=str(temp_db))

    conn = sqlite3.connect(str(temp_db))
    # 最小 schema(只放 generate_dispatch_event_excel 用到的字段)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS release_batches (
        id TEXT PRIMARY KEY, ship_name TEXT, project TEXT,
        destination_station TEXT, cargo_name TEXT, cargo_product_name TEXT,
        contract_no TEXT, order_identifier TEXT, batch_sequence TEXT,
        notice_date TEXT
    );
    CREATE TABLE IF NOT EXISTS wagon_shipments (
        id TEXT PRIMARY KEY, car_no TEXT, batch_id TEXT,
        ticketed_at TEXT, container_no TEXT, container_numbers_json TEXT,
        container_batch_map TEXT, marked_weight REAL, car_model TEXT,
        origin_name TEXT, destination_name TEXT
    );
    """)
    conn.execute(
        "INSERT INTO release_batches (id, ship_name, project, destination_station, "
        "cargo_name, contract_no, batch_sequence, notice_date) VALUES "
        "('lotA','蓝鳍','jilin_jingang_jinzhou','四平','铁矿粉','HNMC-A','lot03','2026-05-29'),"
        "('lotB','蓝鳍','jilin_jingang_jinzhou','四平','铁矿粉','HNMC-B','lot05','2026-06-03')"
    )
    cbm = json.dumps({"BOX1": "lotA", "BOX2": "lotB"})
    # 关键:container_no 有 raw 但 container_numbers_json 为空
    conn.execute(
        "INSERT INTO wagon_shipments (id, car_no, batch_id, ticketed_at, "
        "container_no, container_numbers_json, container_batch_map) VALUES "
        "('w1','1886696','lotA','2026-06-06 21:24:00','BOX1/BOX2','',?)", (cbm,)
    )
    # 第二辆 wagon batch_id=lotB,让 _fetch_event 把 lotB 也带进 batches dict
    # (chain 真场景里 lotB 必然至少有一辆别的整车指它)
    conn.execute(
        "INSERT INTO wagon_shipments (id, car_no, batch_id, ticketed_at, "
        "container_no, container_numbers_json, container_batch_map) VALUES "
        "('w2','1886697','lotB','2026-06-06 21:25:00','BOX3/BOX4','',NULL)"
    )
    conn.commit()
    conn.close()

    # 注入 yaml 配置查询 — 用最简模板,直接调内部 row builder
    from sop_hub.sop.departure_excel import _fetch_event_wagons_and_batches, _build_event_row
    import json as _json
    wagons, batches, _ = _fetch_event_wagons_and_batches(["w1", "w2"], db_path=str(temp_db))
    assert len(wagons) == 2
    # 拿 split 的那辆
    w = next(w for w in wagons if w["car_no"] == "1886696")
    assert w["container_batch_map"]
    cbm_dict = _json.loads(w["container_batch_map"])
    try:
        ordered = [b for b in _json.loads(w.get("container_numbers_json") or "[]") if b]
    except Exception:
        ordered = []
    if not ordered:
        raw = w.get("container_no") or ""
        ordered = [b.strip() for b in str(raw).split("/") if b.strip()]
    assert ordered == ["BOX1", "BOX2"], "fallback 必须从 container_no '/' 拆"

    rows = []
    for box in ordered:
        b = batches[cbm_dict[box]]
        rows.append(_build_event_row(
            wagon=w, batch=b, seq=len(rows)+1,
            earliest_ticketed_compact="", container_override=box,
        ))
    assert len(rows) == 2
    assert rows[0]["container_no_1"] == "BOX1" and rows[0]["entry_contract_no"] == "HNMC-A"
    assert rows[1]["container_no_1"] == "BOX2" and rows[1]["entry_contract_no"] == "HNMC-B"
    assert rows[0]["container_no_2"] == "" and rows[1]["container_no_2"] == ""


# ── #120 factory upload split 车 per-box order_id ─────────────────
def test_factory_upload_split_wagon_uses_cbm_per_box_order(temp_db):
    """split 车 box2 必须用 container_batch_map[box2] 对应 lot 的
    order_identifier/contract_no,而不是 wagon.batch_id 那个主 lot 的。
    历史 bug 2026-06-07:这层没拆 → 工厂端 box 错位,verify miss/extra
    成跨 lot 错位三角链。"""
    import json
    import sqlite3
    from sop_hub.sop.message_inbox import ensure_message_inbox_schema
    from sop_hub.sop.factory_upload import _build_event_upload_payloads, FactoryUploadConfig
    from dataclasses import dataclass

    ensure_message_inbox_schema(db_path=str(temp_db))
    conn = sqlite3.connect(str(temp_db))
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS release_batches (
        id TEXT PRIMARY KEY, ship_name TEXT, project TEXT,
        destination_station TEXT, cargo_name TEXT, cargo_product_name TEXT,
        contract_no TEXT, order_identifier TEXT, batch_sequence TEXT,
        notice_date TEXT
    );
    CREATE TABLE IF NOT EXISTS wagon_shipments (
        id TEXT PRIMARY KEY, car_no TEXT, batch_id TEXT,
        ticketed_at TEXT, container_no TEXT, container_numbers_json TEXT,
        container_batch_map TEXT, marked_weight REAL, car_model TEXT,
        origin_name TEXT, destination_name TEXT
    );
    """)
    conn.execute(
        "INSERT INTO release_batches (id, ship_name, project, destination_station, "
        "cargo_name, contract_no, order_identifier, batch_sequence, notice_date) VALUES "
        "('lotA','蓝鳍','jilin_jingang_jinzhou','四平','铁矿粉','HNMC-A','ORDER-A','lot03','2026-05-29'),"
        "('lotB','蓝鳍','jilin_jingang_jinzhou','四平','铁矿粉','HNMC-B','ORDER-B','lot05','2026-06-03')"
    )
    cbm = json.dumps({"BOX1": "lotA", "BOX2": "lotB"})
    conn.execute(
        "INSERT INTO wagon_shipments (id, car_no, batch_id, ticketed_at, "
        "container_no, container_numbers_json, container_batch_map) VALUES "
        "('w1','1886696','lotA','2026-06-06 21:24:00','BOX1/BOX2','',?)", (cbm,)
    )
    conn.commit()
    conn.close()

    @dataclass
    class _Field:
        key: str
        source: str
        value: str = ""

    # 模拟 jilin yaml factory_upload_config.payload_fields
    fields = [
        _Field(key="wagonNumber", source="95306_confirm"),
        _Field(key="boxNumber", source="95306_confirm"),
        _Field(key="contractNumber", source="release_batch.contract_no"),
        _Field(key="orderId", source="release_batch.order_identifier"),
    ]
    config = FactoryUploadConfig(
        endpoint="", login_path="", upload_path="", username="",
        token_header="", fields=fields, excel_to_factory={},
    )
    payloads, bids, err = _build_event_upload_payloads(["w1"], config, db_path=str(temp_db))
    assert err == ""
    assert {b for b in bids} == {"lotA", "lotB"}, "cbm 指向的 lotB 必须进 batches dict"
    assert len(payloads) == 2

    by_box = {p.container_no: p.payload for p in payloads}
    assert by_box["BOX1"]["orderId"] == "ORDER-A"
    assert by_box["BOX1"]["contractNumber"] == "HNMC-A"
    assert by_box["BOX2"]["orderId"] == "ORDER-B", "split box2 必须走 cbm 指向 lot 的 order"
    assert by_box["BOX2"]["contractNumber"] == "HNMC-B"


# ── #121: yaml has_inspection_slip 守门 ────────────────────────────
def test_resolve_task_type_skips_when_yaml_has_no_inspection_slip(monkeypatch):
    """jilin yaml 写 has_inspection_slip: false → router 不建 inspection_*_flow
    task,直接返回 SKIP_TASK_TYPE_SENTINEL,上游 create_task_from_message_inbox
    看到就跳过。防止 OCR 误归属导致的 generic_sop_task 永久 pending 孤儿。"""
    from sop_hub.sop import workflow_task_store as wts
    # mock has_inspection_slip → jilin False / 中唐 True
    def _mock(pid):
        return pid != "jilin_jingang_jinzhou"
    monkeypatch.setattr(wts, "_project_has_inspection_slip", _mock)

    # jilin + inspection_notice_flow → 跳过
    assert wts._resolve_task_type(
        "jilin_jingang_jinzhou", "inspection_notice_flow", "create_inspection_candidate",
    ) == wts.SKIP_TASK_TYPE_SENTINEL

    # jilin + departure_flow → 正常归 jljg_departure_text_chain
    assert wts._resolve_task_type(
        "jilin_jingang_jinzhou", "departure_flow", "detect_departure_message",
    ) == "jljg_departure_text_chain"

    # 中唐 + inspection_notice_flow → 正常归 zhongtang_inspection_chain
    assert wts._resolve_task_type(
        "zhongtang_special_steel", "inspection_notice_flow", "create_inspection_candidate",
    ) == "zhongtang_inspection_chain"


# ── #125: lifecycle 状态机常量 + migration map ─────────────────────
def test_lifecycle_phases_complete_and_migration_covers_legacy():
    from sop_hub.sop import lifecycle as lc
    assert len(lc.ALL_PHASES) == 8
    assert set(lc.ALL_PHASES) == {
        "pending_freight", "enriched", "loading", "all_loaded",
        "tracking", "delivered", "confirmed_received", "closed",
    }
    # 行为门控分组合理
    assert "loading" in lc.PHASES_OPEN_TO_DEPARTURE_MATCH
    assert "enriched" in lc.PHASES_OPEN_TO_DEPARTURE_MATCH
    assert "all_loaded" not in lc.PHASES_OPEN_TO_DEPARTURE_MATCH, "all_loaded 应拒绝新装车通知"
    assert "loading" in lc.PHASES_ACTIVE_TRACKING
    assert "all_loaded" in lc.PHASES_ACTIVE_TRACKING
    assert "closed" not in lc.PHASES_DASHBOARD_ACTIVE
    assert "confirmed_received" in lc.PHASES_TERMINAL
    # 老 5 值都在 migration map
    legacy = {"in_progress", "completed", "pending_completion", "suspended", "cancelled"}
    assert legacy.issubset(set(lc.LEGACY_MIGRATION_MAP.keys()))
    # 映射目标都是合法新枚举
    for new in lc.LEGACY_MIGRATION_MAP.values():
        assert new in lc.ALL_PHASES


# ── #128: 状态机推进器 ─────────────────────────────────────────────
def test_advance_lifecycle_legal_transition_and_idempotency(temp_db):
    """合法跳转写入,同 phase 再调是 noop,非法回退被拒绝。"""
    import sqlite3
    from sop_hub.sop.lifecycle_transition import advance_lifecycle

    conn = sqlite3.connect(str(temp_db))
    conn.execute("""CREATE TABLE release_batches (
        id TEXT PRIMARY KEY, dispatch_status TEXT NOT NULL,
        dispatch_status_note TEXT, dispatch_status_updated_at TEXT,
        updated_at TEXT
    )""")
    conn.execute(
        "INSERT INTO release_batches (id, dispatch_status) VALUES ('b1', 'pending_freight')"
    )
    conn.commit()
    conn.close()

    # 1) 合法:pending_freight → enriched
    r = advance_lifecycle("b1", "enriched", reason="freight ok", db_path=str(temp_db))
    assert r["action"] == "advanced"
    assert r["from_phase"] == "pending_freight" and r["to_phase"] == "enriched"

    # 2) 幂等:enriched → enriched = noop
    r = advance_lifecycle("b1", "enriched", reason="re-trigger", db_path=str(temp_db))
    assert r["action"] == "noop"

    # 3) 合法跳级:enriched → all_loaded (跳过 loading 也允许,plan 一次性满)
    r = advance_lifecycle("b1", "all_loaded", reason="50车一次装满", db_path=str(temp_db))
    assert r["action"] == "advanced"

    # 4) 非法回退:all_loaded → loading 拒绝
    r = advance_lifecycle("b1", "loading", reason="bug", db_path=str(temp_db))
    assert r["action"] == "rejected"
    assert "illegal transition" in r["reason"]

    # 5) 越规:all_loaded → enriched 拒绝
    r = advance_lifecycle("b1", "enriched", reason="bug", db_path=str(temp_db))
    assert r["action"] == "rejected"

    # transition_log 应有 2 条 advanced(1,3),没有 noop/rejected
    conn = sqlite3.connect(str(temp_db))
    log = conn.execute("SELECT from_phase, to_phase, reason FROM lifecycle_transition_log ORDER BY id").fetchall()
    conn.close()
    assert len(log) == 2
    assert log[0] == ("pending_freight", "enriched", "freight ok")
    assert log[1] == ("enriched", "all_loaded", "50车一次装满")


def test_advance_lifecycle_unknown_batch_or_phase(temp_db):
    """不存在的 batch 或非法 phase 名 → rejected,不破。"""
    import sqlite3
    from sop_hub.sop.lifecycle_transition import advance_lifecycle

    conn = sqlite3.connect(str(temp_db))
    conn.execute("""CREATE TABLE release_batches (
        id TEXT PRIMARY KEY, dispatch_status TEXT NOT NULL,
        dispatch_status_note TEXT, dispatch_status_updated_at TEXT, updated_at TEXT
    )""")
    conn.commit()
    conn.close()

    r1 = advance_lifecycle("nonexistent", "loading", reason="x", db_path=str(temp_db))
    assert r1["action"] == "rejected" and "not found" in r1["reason"]

    r2 = advance_lifecycle("any", "garbage_phase", reason="x", db_path=str(temp_db))
    assert r2["action"] == "rejected" and "unknown" in r2["reason"]


# ── #126: phase-aware match + pending_match 兜底 ──────────────────
def test_find_release_batch_phase_filter(temp_db):
    """ship 有 3 batch:enriched/loading/all_loaded — 应优先 loading,跳过 all_loaded。
    全 all_loaded 时返回 reason='all_loaded_full'。"""
    import sqlite3
    from sop_hub.sop.executor_runner import find_release_batch_with_reason

    conn = sqlite3.connect(str(temp_db))
    conn.execute("""CREATE TABLE release_batches (
        id TEXT PRIMARY KEY, ship_name TEXT, destination_station TEXT,
        dispatch_status TEXT NOT NULL, project TEXT, batch_sequence TEXT
    )""")
    conn.executemany(
        "INSERT INTO release_batches (id, ship_name, destination_station, dispatch_status, project) VALUES (?,?,?,?,?)",
        [
            ("lotA", "蓝鳍", "四平", "loading", "jilin_jingang_jinzhou"),
            ("lotB", "蓝鳍", "四平", "enriched", "jilin_jingang_jinzhou"),
            ("lotC", "蓝鳍", "四平", "all_loaded", "jilin_jingang_jinzhou"),
        ],
    )
    conn.commit()
    conn.close()

    # 应优先选 loading
    rb, reason, candidates = find_release_batch_with_reason("蓝鳍", "四平", db_path=str(temp_db))
    assert rb is not None
    assert rb["id"] == "lotA"
    assert reason == "ok"

    # 把 lotA 也推到 all_loaded → enriched(lotB) 应被选
    conn = sqlite3.connect(str(temp_db))
    conn.execute("UPDATE release_batches SET dispatch_status='all_loaded' WHERE id='lotA'")
    conn.commit()
    conn.close()
    rb, reason, _ = find_release_batch_with_reason("蓝鳍", "四平", db_path=str(temp_db))
    assert rb["id"] == "lotB", "应回退到 enriched"
    assert reason == "ok"

    # 全部 all_loaded → 没 open lot,reason='all_loaded_full'
    conn = sqlite3.connect(str(temp_db))
    conn.execute("UPDATE release_batches SET dispatch_status='all_loaded' WHERE id='lotB'")
    conn.commit()
    conn.close()
    rb, reason, candidates = find_release_batch_with_reason("蓝鳍", "四平", db_path=str(temp_db))
    assert rb is None
    assert reason == "all_loaded_full"
    assert set(candidates) == {"lotA", "lotB", "lotC"}


def test_release_batch_pending_match_lifecycle(temp_db):
    from sop_hub.sop.release_batch_pending_match import (
        ensure_schema, create_pending, list_pending, mark_resolved, mark_rejected,
    )
    ensure_schema(db_path=str(temp_db))

    # 创建 2 条 pending
    r1 = create_pending(
        message_id="wx_500", reason="all_loaded_full",
        ship_name="蓝鳍", destination="四平", car_count=20,
        text_content="煤六 20节 四平铁 蓝鳍", group_id="铁晟业务工作群",
        candidate_batch_ids=["lotA", "lotB"], db_path=str(temp_db),
    )
    assert r1["action"] == "created"

    # 同 message_id+group_id 再 create → already_exists
    r1b = create_pending(
        message_id="wx_500", reason="any",
        group_id="铁晟业务工作群", db_path=str(temp_db),
    )
    assert r1b["action"] == "already_exists"

    r2 = create_pending(
        message_id="wx_501", reason="ship_not_found",
        ship_name="幽灵船", destination="四平", db_path=str(temp_db),
    )
    assert r2["action"] == "created"

    pending = list_pending(db_path=str(temp_db))
    assert len(pending) == 2

    # 标 resolved
    assert mark_resolved(r1["id"], "lotC", manual_note="新 lot07 来了挂这里",
                         db_path=str(temp_db))
    # 标 rejected
    assert mark_rejected(r2["id"], manual_note="OCR 误识别,不是真船",
                         db_path=str(temp_db))

    pending = list_pending(db_path=str(temp_db))
    assert len(pending) == 0, "resolved/rejected 不该再列在 pending 里"


# ── #127: lifecycle closeout ──────────────────────────────────────
def test_lifecycle_closeout_advances_when_all_wagons_delivered(temp_db, monkeypatch):
    """吉林金钢以箱级表为事实源：全部 box latest_stage_key 已收货
    → batch 推到 confirmed_received。lotB 仍有 ticketed → 不推。"""
    import sqlite3
    conn = sqlite3.connect(str(temp_db))
    conn.execute("""CREATE TABLE release_batches (
        id TEXT PRIMARY KEY, project TEXT, ship_name TEXT, batch_sequence TEXT,
        dispatch_status TEXT NOT NULL, dispatch_status_note TEXT,
        dispatch_status_updated_at TEXT, updated_at TEXT,
        remaining_weight_tons REAL, batch_quantity REAL
    )""")  # cc1922f:lifecycle_closeout SELECT 加了这两列(散粮吨位闸)
    # 吉林 closeout 只读 wagon_container_shipments（车级表为历史快照）
    conn.execute("""CREATE TABLE wagon_container_shipments (
        id TEXT PRIMARY KEY, batch_id TEXT, latest_stage_key TEXT,
        ticketed_at TEXT, loading_line TEXT, status_name TEXT
    )""")
    conn.execute("""CREATE TABLE wagon_shipments (
        id TEXT PRIMARY KEY, batch_id TEXT, latest_stage_key TEXT
    )""")
    # 2 batches: lotA 全 delivered → 应推; lotB 一半 ticketed → 不推
    conn.executemany(
        "INSERT INTO release_batches (id, project, ship_name, batch_sequence, dispatch_status, remaining_weight_tons) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("lotA", "jilin_jingang_jinzhou", "蓝鳍", "lot1", "all_loaded", 0),
            ("lotB", "jilin_jingang_jinzhou", "蓝鳍", "lot2", "loading", 0),
        ],
    )
    for i in range(5):
        conn.execute(
            "INSERT INTO wagon_container_shipments "
            "(id, batch_id, latest_stage_key, ticketed_at, loading_line) VALUES (?,?,?,?,?)",
            (f"wA{i}", "lotA",
             "delivered" if i < 4 else "unloading_completed",
             "2026-01-01", "煤一"),
        )
    for i in range(5):
        conn.execute(
            "INSERT INTO wagon_container_shipments "
            "(id, batch_id, latest_stage_key, ticketed_at, loading_line) VALUES (?,?,?,?,?)",
            (f"wB{i}", "lotB",
             "delivered" if i < 3 else "ticketed",
             "2026-01-01", "煤一"),
        )
    conn.commit()
    conn.close()

    # mock yaml lifecycle.mode = full_track_to_received(默认)
    monkeypatch.setattr(
        "sop_hub.sop.lifecycle_closeout._yaml_lifecycle_mode",
        lambda pid: "full_track_to_received",
    )

    from sop_hub.sop.lifecycle_closeout import run_lifecycle_closeout
    res = run_lifecycle_closeout(db_path=str(temp_db))
    assert res["scanned"] == 2
    assert res["advanced"] == 1, "只 lotA 全 received,lotB 还有 ticketed"
    assert res["skipped_pending"] == 1
    a = res["advances"][0]
    assert a["batch_id"] == "lotA"
    assert a["to_phase"] == "confirmed_received"

    # 验证 DB 状态
    conn = sqlite3.connect(str(temp_db))
    statuses = dict(conn.execute("SELECT id, dispatch_status FROM release_batches").fetchall())
    conn.close()
    assert statuses["lotA"] == "confirmed_received"
    assert statuses["lotB"] == "loading"


def test_lifecycle_closeout_shipped_is_completed_mode_skips_to_closed(temp_db, monkeypatch):
    """yaml lifecycle.mode=shipped_is_completed (朝阳):all_loaded 直跳 closed,
    不等 wagon 到货。"""
    import sqlite3
    conn = sqlite3.connect(str(temp_db))
    conn.execute("""CREATE TABLE release_batches (
        id TEXT PRIMARY KEY, project TEXT, ship_name TEXT, batch_sequence TEXT,
        dispatch_status TEXT NOT NULL, dispatch_status_note TEXT,
        dispatch_status_updated_at TEXT, updated_at TEXT,
        remaining_weight_tons REAL, batch_quantity REAL
    )""")  # cc1922f:lifecycle_closeout SELECT 加了这两列(散粮吨位闸)
    conn.execute("CREATE TABLE wagon_shipments (id TEXT, batch_id TEXT, latest_stage_key TEXT)")
    conn.execute(
        "INSERT INTO release_batches (id, project, ship_name, batch_sequence, dispatch_status) "
        "VALUES ('lotCY', 'chaoyang_steel', '宝腾海', 'lot1', 'all_loaded')"
    )
    # 朝阳:wagons 不必到货,只要 all_loaded 就 closed
    for i in range(3):
        conn.execute(
            "INSERT INTO wagon_shipments (id, batch_id, latest_stage_key) VALUES (?,?,?)",
            (f"wCY{i}", "lotCY", "ticketed"),  # 故意不 delivered
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "sop_hub.sop.lifecycle_closeout._yaml_lifecycle_mode",
        lambda pid: "shipped_is_completed",
    )

    from sop_hub.sop.lifecycle_closeout import run_lifecycle_closeout
    res = run_lifecycle_closeout(db_path=str(temp_db))
    assert res["advanced"] == 1
    assert res["advances"][0]["to_phase"] == "closed"


# ── #130: 复合检车单 ship rule 多组拆分 ─────────────────────────
def test_split_inspection_payload_by_ship_rules(temp_db, monkeypatch):
    """一图 24 行含 2 船(长航滨海 14 + 宝腾海 10):
    - 朝阳宝腾海 rule 在 active → group 2 ship='宝腾海' candidate
    - 长航滨海无 rule(乌兰浩特项目未建)→ group 1 unmatched pending_review"""
    import sqlite3
    from sop_hub.data_agent.agent import BusinessDataAgent
    import json

    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(temp_db))

    conn = sqlite3.connect(str(temp_db))
    conn.executescript("""
    CREATE TABLE release_batches (
      id TEXT PRIMARY KEY, batch_key TEXT NOT NULL UNIQUE, project TEXT,
      ship_name TEXT NOT NULL, cargo_name TEXT NOT NULL,
      destination_station TEXT, notice_date TEXT NOT NULL,
      dispatch_status TEXT NOT NULL DEFAULT 'loading',
      source_json TEXT NOT NULL DEFAULT '{}', searchable_text TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE release_dispatch_match_rules (
      id TEXT PRIMARY KEY, release_batch_id TEXT NOT NULL UNIQUE,
      project TEXT, ship_name TEXT NOT NULL, destination_station TEXT,
      cargo_name TEXT NOT NULL, matching_str TEXT NOT NULL,
      matching_tokens_json TEXT NOT NULL,
      status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 100,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      completed_at TEXT, manual_note TEXT
    );
    CREATE TABLE inspection_ingestion_candidates (
      id TEXT PRIMARY KEY, source_file_name TEXT NOT NULL, status TEXT NOT NULL,
      reason TEXT, group_name TEXT, release_batch_id TEXT,
      wagon_count INTEGER DEFAULT 0, car_numbers_json TEXT NOT NULL DEFAULT '[]',
      payload_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      message_id TEXT, project_id TEXT, document_type TEXT,
      source_image_path TEXT, extraction_json_path TEXT, parsed_json TEXT,
      ship_name TEXT, destination TEXT, cargo_name TEXT,
      candidate_status TEXT DEFAULT 'pending_match'
    );
    """)
    conn.execute(
        "INSERT INTO release_batches (id, batch_key, project, ship_name, cargo_name, destination_station, notice_date, dispatch_status) "
        "VALUES ('lot_btb', 'k', 'chaoyang_steel', '宝腾海', '铁矿', '朝阳西', '2026-06-01', 'loading')"
    )
    conn.execute(
        "INSERT INTO release_dispatch_match_rules (id, release_batch_id, project, ship_name, destination_station, cargo_name, matching_str, matching_tokens_json, status) "
        "VALUES ('rule_btb', 'lot_btb', 'chaoyang_steel', '宝腾海', '朝阳西', '铁矿', '宝腾海 朝阳西 铁矿', ?, 'active')",
        (json.dumps({"ship": ["宝腾海"], "destination": ["朝阳西"], "cargo": ["铁矿"]}),)
    )
    conn.commit(); conn.close()

    agent = BusinessDataAgent()
    # 构造 OCR-like payload
    payload = {
        "rows": (
            [{"seq": i, "car_no": f"100{i:04d}", "cargo_info_raw": h} for i, h in enumerate([
                "乌兰浩特镍矿", "沈阳盛京颐昇代", "长航滨海", "14节",
            ], start=1)]
            + [{"seq": i, "car_no": f"200{i:04d}", "cargo_info_raw": ""} for i in range(5, 15)]  # 10 行真车(长航滨海)
            + [{"seq": 15, "car_no": "3000015", "cargo_info_raw": "朝阳西铁矿"},
               {"seq": 16, "car_no": "3000016", "cargo_info_raw": "鞍钢集团朝阳钢铁"},
               {"seq": 17, "car_no": "3000017", "cargo_info_raw": "宝腾海"}]
            + [{"seq": i, "car_no": f"400{i:04d}", "cargo_info_raw": ""} for i in range(18, 25)]  # 7 行真车(宝腾海)
        )
    }
    groups = agent._split_payload_by_ship_rules(payload)
    assert len(groups) == 2, f"expect 2 groups (unmatched + 宝腾海), got {len(groups)}"

    g_unmatched = next(g for g in groups if g.get("_split_group_unmatched"))
    g_btb = next(g for g in groups if g.get("_split_group_ship_name") == "宝腾海")
    # 算法:ship 锚点 seq 17(宝腾海)向前吸收 4 行表头(cargo 非空连续):
    # seq 16 鞍钢集团 / seq 15 朝阳西铁矿 都非空被吸收,seq 14 cargo='' 停。
    # 所以宝腾海段 = seq 15-24 = 10 行;unmatched = seq 1-14 = 14 行。
    assert len(g_unmatched["rows"]) == 14
    assert len(g_btb["rows"]) == 10
    assert g_btb["_split_group_rule_id"] == "rule_btb"


# ── #132 (2026-06-09): 同船多 anchor 应合并,不应拆 ─────────────────
def test_split_payload_dedupes_same_ship_multiple_anchors(temp_db, monkeypatch):
    """wx_17 (2026-06-08) 36 车一张鞍子河单,qwen3-vl 把 row[8] 空字段错读成
    '鞍子河' → 算法见 2 个 anchor 切成 28+8。修复后:相邻同 rule 的 anchor
    合并,返回单组 payload(不拆)。"""
    import sqlite3
    from sop_hub.data_agent.agent import BusinessDataAgent
    import json

    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(temp_db))
    conn = sqlite3.connect(str(temp_db))
    conn.executescript("""
    CREATE TABLE release_batches (
      id TEXT PRIMARY KEY, batch_key TEXT NOT NULL UNIQUE, project TEXT,
      ship_name TEXT NOT NULL, cargo_name TEXT NOT NULL,
      destination_station TEXT, notice_date TEXT NOT NULL,
      dispatch_status TEXT NOT NULL DEFAULT 'loading',
      source_json TEXT NOT NULL DEFAULT '{}', searchable_text TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE release_dispatch_match_rules (
      id TEXT PRIMARY KEY, release_batch_id TEXT NOT NULL UNIQUE,
      project TEXT, ship_name TEXT NOT NULL, destination_station TEXT,
      cargo_name TEXT NOT NULL, matching_str TEXT NOT NULL,
      matching_tokens_json TEXT NOT NULL,
      status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 100,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      completed_at TEXT, manual_note TEXT
    );
    """)
    conn.execute(
        "INSERT INTO release_batches (id, batch_key, project, ship_name, cargo_name, destination_station, notice_date, dispatch_status) "
        "VALUES ('lot_azh', 'k', 'zhongtang_special_steel', '鞍子河', '铁矿', '汐子', '2026-06-04', 'loading')"
    )
    conn.execute(
        "INSERT INTO release_dispatch_match_rules (id, release_batch_id, project, ship_name, destination_station, cargo_name, matching_str, matching_tokens_json, status) "
        "VALUES ('rule_azh', 'lot_azh', 'zhongtang_special_steel', '鞍子河', '汐子', '铁矿', '鞍子河 汐子 铁矿', ?, 'active')",
        (json.dumps({"ship": ["鞍子河"], "destination": ["汐子"], "cargo": ["铁矿"]}),)
    )
    conn.commit(); conn.close()

    agent = BusinessDataAgent()
    # wx_17 实际格局:前 4 行是表头(汐子铁矿粉 / 锦州新铁晟代 / 鞍子河 / 36节),
    # 之后 32 行真车 cargo_info_raw 应为空,但 qwen3-vl 把 row[8] 错读成 '鞍子河'。
    payload = {
        "rows": [
            {"seq": 1, "car_no": "1551953", "cargo_info_raw": "汐子铁矿粉"},
            {"seq": 2, "car_no": "4927890", "cargo_info_raw": "锦州新铁晟代"},
            {"seq": 3, "car_no": "1676634", "cargo_info_raw": "鞍子河"},   # 真锚点
            {"seq": 4, "car_no": "1827292", "cargo_info_raw": "36节"},
        ]
        + [{"seq": i, "car_no": f"40000{i:02d}", "cargo_info_raw": ""} for i in range(5, 9)]
        + [{"seq": 9, "car_no": "4936358", "cargo_info_raw": "鞍子河"}]      # OCR 错读锚点
        + [{"seq": i, "car_no": f"50000{i:02d}", "cargo_info_raw": ""} for i in range(10, 37)]
    }
    assert len(payload["rows"]) == 36

    groups = agent._split_payload_by_ship_rules(payload)
    assert len(groups) == 1, \
        f"same-ship multi-anchor 应合并为 1 段,实际 {len(groups)} 段(回归 wx_17 bug)"
    g = groups[0]
    # 单段时返回的就是原 payload,没有 _split_group_rule_id 标记
    assert len(g["rows"]) == 36


# ── #131: unmatched 组直接丢弃,不建 candidate ──────────────────
def test_ingest_drops_unmatched_split_groups(temp_db, monkeypatch):
    """multi_group 时 _split_group_unmatched=True 段不建 candidate,只 matched
    段建。dropped_unmatched_rows 计入返回值。"""
    import json
    import sqlite3
    from sop_hub.data_agent.agent import BusinessDataAgent

    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(temp_db))

    conn = sqlite3.connect(str(temp_db))
    conn.executescript("""
    CREATE TABLE release_batches (
      id TEXT PRIMARY KEY, batch_key TEXT NOT NULL UNIQUE, project TEXT,
      ship_name TEXT NOT NULL, cargo_name TEXT NOT NULL,
      destination_station TEXT, notice_date TEXT NOT NULL,
      dispatch_status TEXT NOT NULL DEFAULT 'loading',
      source_json TEXT NOT NULL DEFAULT '{}', searchable_text TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE release_dispatch_match_rules (
      id TEXT PRIMARY KEY, release_batch_id TEXT NOT NULL UNIQUE,
      project TEXT, ship_name TEXT NOT NULL, destination_station TEXT,
      cargo_name TEXT NOT NULL, matching_str TEXT NOT NULL,
      matching_tokens_json TEXT NOT NULL,
      status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 100,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      completed_at TEXT, manual_note TEXT
    );
    CREATE TABLE inspection_ingestion_candidates (
      id TEXT PRIMARY KEY, source_file_name TEXT NOT NULL, status TEXT NOT NULL,
      reason TEXT, group_name TEXT, release_batch_id TEXT,
      wagon_count INTEGER DEFAULT 0, car_numbers_json TEXT NOT NULL DEFAULT '[]',
      payload_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      message_id TEXT, project_id TEXT, document_type TEXT,
      source_image_path TEXT, extraction_json_path TEXT, parsed_json TEXT,
      ship_name TEXT, destination TEXT, cargo_name TEXT,
      candidate_status TEXT DEFAULT 'pending_match'
    );
    -- ingest 后续会 SELECT message_inbox 回填 message_id 等,所以也建一个空表
    CREATE TABLE message_inbox (
      id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT,
      raw_standard_image_path TEXT, extraction_json_path TEXT,
      received_datetime TEXT, inspection_candidate_id TEXT
    );
    """)
    conn.execute(
        "INSERT INTO release_batches (id, batch_key, project, ship_name, cargo_name, destination_station, notice_date, dispatch_status) "
        "VALUES ('lot_btb', 'k', 'chaoyang_steel', '宝腾海', '铁矿', '朝阳西', '2026-06-01', 'loading')"
    )
    conn.execute(
        "INSERT INTO release_dispatch_match_rules (id, release_batch_id, project, ship_name, destination_station, cargo_name, matching_str, matching_tokens_json, status) "
        "VALUES ('rule_btb', 'lot_btb', 'chaoyang_steel', '宝腾海', '朝阳西', '铁矿', 'x', ?, 'active')",
        (json.dumps({"ship": ["宝腾海"], "destination": ["朝阳西"], "cargo": ["铁矿"]}),)
    )
    conn.commit(); conn.close()

    agent = BusinessDataAgent()
    payload = {
        "rows": [
            {"seq": 1, "car_no": "u1", "cargo_info_raw": "乌兰浩特镍矿"},   # unmatched 头
            {"seq": 2, "car_no": "u2", "cargo_info_raw": ""},
            {"seq": 3, "car_no": "u3", "cargo_info_raw": "长航滨海"},        # 无 rule
            {"seq": 4, "car_no": "b1", "cargo_info_raw": "宝腾海"},          # 切组开始
            {"seq": 5, "car_no": "b2", "cargo_info_raw": ""},
            {"seq": 6, "car_no": "b3", "cargo_info_raw": ""},
        ]
    }
    res = agent.ingest_inspection_payload(payload, source_file_name="test_split_drop.jpg")
    assert res["status"] == "multi_group"
    # 算法:ship 锚点 seq 4 (宝腾海) 向前吸收 cargo 非空行:
    # seq 3 长航滨海 非空 → 吸收。seq 2 cargo='' 停。
    # 所以宝腾海段 = seq 3-6 = 4 行;unmatched = seq 1-2 = 2 行 dropped。
    assert res["dropped_unmatched_rows"] == 2
    assert len(res["groups"]) == 1
    import sqlite3
    conn = sqlite3.connect(str(temp_db))
    rows = conn.execute(
        "SELECT ship_name, candidate_status FROM inspection_ingestion_candidates"
    ).fetchall()
    conn.close()
    assert len(rows) == 1, f"unmatched 不该建 candidate;DB 应只 1 行(got {len(rows)})"
    assert rows[0][0] == "宝腾海"
    assert rows[0][1] == "candidate"
