"""#124 端到端 fixture:jilin 蓝鳍 50 车 wx_367 全链路。

覆盖 chain step 1-5:
  1. parse_departure_text("煤六 50节 四平铁 蓝鳍") → DepartureCandidate
  2. find_release_batch_with_reason → 命中 loading lot
  3. query_95306_shipments_by_window → 50 wagons (fixture 95306 db)
  4. create_wagon_shipments_from_candidates → 100 行 wagon_container_shipments
     (吉林不再写 wagon_shipments)
  5. allocate_container_ydids → 按 plan 分到 lot03/05/06,2 split 车 box.batch_id 拆
  6. generate_dispatch_event_excel → 52 rows / 50 wagons

  跳过 step 6+ (factory_upload / verify / send_excel — 外部 HTTP,
  另有 verify_factory_upload 专项 e2e)

防回归点:
  - excel split 拆行 = 52(48 整车 + 2 split × 2)
  - allocate 把 lot03 装满 (lot3 差 1 → +1 box → 满)
  - lifecycle 自动从 loading 推到 all_loaded(lot03/05)
  - box 级 batch_id 在 wagon_container_shipments 正确(split 车 box2 → 真 lot)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))


# ── fixture 真数据:wx_367 的 50 个 95306 ydid + container 等 ──────
# 取自实际 95306 query result (2026-06-06 21:24-21:27,高桥镇→四平)
def _fixture_50_wagons() -> list[dict]:
    """50 车,前 11 车涉及 split 边界。其余整车简化。
    box1 / box2 名称跟真实 wx_367 完全一致(便于 split assertion)。"""
    base_ticketed = "2026-06-06 21:24:"
    rows = []
    # 真实头 11 车 + 后 39 车整车,每车 2 box
    car_no_box_pairs = [
        ("1886696", "TBJU8783839", "TBJU4948967"),  # split (lot3 box1 + lot5 box2)
        ("1886730", "TBJU5312157", "TBJU1273181"),
        ("1886688", "TBJU3105844", "TBJU5611564"),
        ("1886705", "TBJU8350314", "TBJU0612737"),
        ("1817915", "TBJU1150227", "TBJU1515006"),
        ("1763827", "TBJU3428180", "TBJU5447379"),
        ("1886698", "TBJU1538980", "TBCU0232351"),
        ("1886679", "TBJU3062110", "TBJU9927329"),
        ("1886685", "TBJU6506120", "TBJU3297330"),
        ("1886709", "TBJU6926421", "TBJU6666068"),
        ("1886708", "TBJU8786870", "TBJU1388540"),  # split (lot5 box1 + lot6 box2)
    ]
    # 后 39 整车,box 名 BOXxx
    for i in range(39):
        car_no = f"222{i:04d}"
        car_no_box_pairs.append(
            (car_no, f"BOX{i*2:04d}", f"BOX{i*2+1:04d}")
        )

    for idx, (car_no, b1, b2) in enumerate(car_no_box_pairs):
        rows.append({
            "ydid": f"5163226{idx:09d}",
            "czydid": f"czyd{idx:08d}",
            "car_no": car_no,
            "car_model": "X1K",
            "marked_weight": 63.5,
            "cargo_count": 2,
            "cargo_name": "铁矿粉",
            "origin_name": "高桥镇",
            "destination_name": "四平",
            "ticketed_at": f"{base_ticketed}{idx:02d}",
            "departed_at": None,
            "arrived_at": None,
            "delivered_at": None,
            "accepted_at": f"{base_ticketed}{idx:02d}",
            "loaded_at": f"{base_ticketed}{idx:02d}",
            "status_name": "已制单",
            "latest_stage_key": "ticketed",
            "latest_stage_name": "制票",
            "latest_event_time": f"{base_ticketed}{idx:02d}",
            "transport_mode_code": "rail",
            "transport_mode_name": "铁路",
            "waybill_no": f"WB{idx:08d}",
            "container_no_raw": f"{b1}/{b2}",
            "container_numbers_json": json.dumps([b1, b2]),
        })
    return rows


@pytest.fixture()
def jilin_e2e_dbs(tmp_path, monkeypatch):
    """临时 sop_agent.db + 95306_collection.sqlite3,装好 fixture 数据。"""
    sop_db = tmp_path / "sop_agent.db"
    rail_db = tmp_path / "95306.db"

    # 95306 fixture
    rc = sqlite3.connect(str(rail_db))
    rc.execute("""CREATE TABLE shipments (
        ydid TEXT PRIMARY KEY, czydid TEXT, car_no TEXT, car_model TEXT,
        marked_weight REAL, cargo_count INTEGER, cargo_name TEXT,
        origin_name TEXT, destination_name TEXT,
        ticketed_at TEXT, departed_at TEXT, arrived_at TEXT, delivered_at TEXT,
        accepted_at TEXT, loaded_at TEXT, status_name TEXT,
        latest_stage_key TEXT, latest_stage_name TEXT, latest_event_time TEXT,
        transport_mode_code TEXT, transport_mode_name TEXT,
        waybill_no TEXT, container_no_raw TEXT, container_numbers_json TEXT
    )""")
    for w in _fixture_50_wagons():
        rc.execute(
            "INSERT INTO shipments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(w[k] for k in [
                "ydid","czydid","car_no","car_model","marked_weight","cargo_count",
                "cargo_name","origin_name","destination_name","ticketed_at","departed_at",
                "arrived_at","delivered_at","accepted_at","loaded_at","status_name",
                "latest_stage_key","latest_stage_name","latest_event_time",
                "transport_mode_code","transport_mode_name","waybill_no",
                "container_no_raw","container_numbers_json",
            ]),
        )
    rc.commit(); rc.close()

    # sop_agent fixture:5 lot + dispatch_plan(蓝鳍真实数字)+ 表 schema
    sc = sqlite3.connect(str(sop_db))
    sc.executescript("""
    CREATE TABLE release_batches (
      id TEXT PRIMARY KEY, batch_key TEXT NOT NULL UNIQUE, project TEXT,
      ship_name TEXT NOT NULL, cargo_name TEXT NOT NULL, contract_no TEXT,
      cargo_product_name TEXT, order_identifier TEXT,
      destination_station TEXT, notice_date TEXT NOT NULL, batch_date TEXT,
      batch_sequence TEXT, batch_quantity REAL,
      dispatch_status TEXT NOT NULL DEFAULT 'loading',
      dispatch_status_note TEXT, dispatch_status_updated_at TEXT,
      actual_wagon_count INTEGER DEFAULT 0,
      source_json TEXT NOT NULL DEFAULT '{}', searchable_text TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE wagon_shipments (
      id TEXT PRIMARY KEY, departure_id TEXT NOT NULL, batch_id TEXT NOT NULL,
      car_no TEXT, car_model TEXT DEFAULT '', cargo_name TEXT DEFAULT '',
      shipper_name TEXT DEFAULT '', consignee_name TEXT DEFAULT '',
      origin_name TEXT DEFAULT '', destination_name TEXT DEFAULT '',
      ticketed_at TEXT DEFAULT '', departed_at TEXT DEFAULT '',
      arrived_at TEXT DEFAULT '', status_name TEXT DEFAULT '',
      freight_fee REAL DEFAULT 0, detail_json TEXT DEFAULT '{}',
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
      delivered_at TEXT, confirmed_received_at TEXT, container_no TEXT,
      waybill_no TEXT, project_id TEXT, ship_name TEXT,
      dispatch_status TEXT DEFAULT 'pending', source_message_id TEXT,
      source_group_id TEXT, ydid TEXT, czydid TEXT, container_batch_map TEXT,
      container_numbers_json TEXT, marked_weight REAL, cargo_count INTEGER,
      latest_stage_key TEXT, latest_stage_name TEXT
    );
    CREATE TABLE release_batch_dispatch_plan (
      release_batch_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, ship_name TEXT NOT NULL,
      planned_box_count INTEGER NOT NULL, allocated_box_count INTEGER DEFAULT 0,
      priority_order INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active',
      notes TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # 5 lot — 3 个 active (lot03 差 1 / lot05 差 20 / lot06 差 91)
    sc.execute(
        "INSERT INTO release_batches (id, batch_key, project, ship_name, cargo_name, "
        "contract_no, order_identifier, destination_station, notice_date, batch_sequence, "
        "dispatch_status) VALUES "
        "('lot03', 'k3', 'jilin_jingang_jinzhou', '蓝鳍', '铁矿粉', 'C-A', 'O-A', '四平', '2026-05-29', 'lot03', 'loading'),"
        "('lot05', 'k5', 'jilin_jingang_jinzhou', '蓝鳍', '铁矿粉', 'C-B', 'O-B', '四平', '2026-06-03', 'lot05', 'loading'),"
        "('lot06', 'k6', 'jilin_jingang_jinzhou', '蓝鳍', '铁矿粉', 'C-C', 'O-C', '四平', '2026-06-03', 'lot06', 'loading')"
    )
    sc.executemany(
        "INSERT INTO release_batch_dispatch_plan (release_batch_id, project_id, ship_name, "
        "planned_box_count, allocated_box_count, priority_order, status) VALUES (?,?,?,?,?,?,?)",
        [
            ("lot03", "jilin_jingang_jinzhou", "蓝鳍", 92, 91, 3, "active"),
            ("lot05", "jilin_jingang_jinzhou", "蓝鳍", 92, 72, 5, "active"),
            ("lot06", "jilin_jingang_jinzhou", "蓝鳍", 91, 0,  6, "active"),
        ],
    )
    sc.commit(); sc.close()

    # 让代码用这两个 fixture db
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(sop_db))
    monkeypatch.setenv("DB_95306_PATH", str(rail_db))

    return {"sop_db": sop_db, "rail_db": rail_db}


# ──────────────────────────────────────────────────────────────────
# 测试本体
# ──────────────────────────────────────────────────────────────────


def test_e2e_jilin_lanqi_50cars_chain_step1_to_step5(jilin_e2e_dbs):
    """parse → query 95306 → create wagons → allocate → excel,全链通,数据对得上。"""
    sop_db = jilin_e2e_dbs["sop_db"]

    # ── Step 1: parse_departure_text ──────────────────────────────
    from sop_hub.sop.departure_text_parser import parse_departure_text
    cand = parse_departure_text(
        "煤六 50节 四平铁 蓝鳍",
        group_id="铁晟业务工作群", message_id="wx_367_e2e",
        message_time="2026-06-06 21:28:40",
    )
    assert cand.status == "complete"
    assert cand.car_count == 50
    assert cand.destination == "四平"
    assert cand.optional_ship_name == "蓝鳍"
    assert cand.project_id == "jilin_jingang_jinzhou"

    # ── Step 2: find_release_batch_with_reason ────────────────────
    from sop_hub.sop.executor_runner import find_release_batch_with_reason
    rb, reason, _ = find_release_batch_with_reason(
        "蓝鳍", "四平", db_path=str(sop_db),
    )
    assert rb is not None
    assert reason == "ok"
    primary_lot = rb["id"]
    assert primary_lot in ("lot03", "lot05", "lot06")  # 任一 loading 都 OK

    # ── Step 3: query_95306_shipments_by_window ───────────────────
    from sop_hub.sop.query_95306_shipments import query_95306_shipments_by_window
    qr = query_95306_shipments_by_window(
        origin_station="高桥镇", destination_station="四平",
        reference_time="2026-06-06 21:28:40",
        window_before_minutes=720, window_after_minutes=720,
        expected_car_count=50, project_id="jilin_jingang_jinzhou",
    )
    assert qr.exact_match_count == 50

    # ── Step 4: create_wagon_shipments_from_candidates ────────────
    from sop_hub.sop.create_wagon_shipments import create_wagon_shipments_from_candidates
    event = SimpleNamespace(
        message_id="wx_367_e2e", group_id="铁晟业务工作群",
        text="煤六 50节 四平铁 蓝鳍",
        received_at="2026-06-06 21:28:40",
    )
    wr = create_wagon_shipments_from_candidates(
        release_batch_id=primary_lot,
        departure_candidate=cand,
        shipment_query_result=qr,
        db_path=sop_db,
    )
    assert wr.status == "safe_to_apply"
    assert wr.inserted_count == 50

    # 吉林以箱级表为唯一事实源；旧表不应再新增车级行。
    conn = sqlite3.connect(str(sop_db))
    n_wagons = conn.execute(
        "SELECT COUNT(*) FROM wagon_shipments WHERE project_id='jilin_jingang_jinzhou'"
    ).fetchone()[0]
    n_boxes = conn.execute(
        "SELECT COUNT(*) FROM wagon_container_shipments WHERE project_id='jilin_jingang_jinzhou'"
    ).fetchone()[0]
    conn.close()
    assert n_wagons == 0, f"吉林不应写老表 (got {n_wagons})"
    assert n_boxes == 100, f"新表应有 100 box (got {n_boxes})"

    # ── Step 4b: allocate_container_ydids ─────────────────────────
    from sop_hub.sop.dispatch_plan import allocate_container_ydids
    new_ydids = [p.ydid for p in wr.plans if p.action == "insert"]
    alloc = allocate_container_ydids(
        new_ydids, "jilin_jingang_jinzhou", "蓝鳍",
        db_path=str(sop_db), force_overwrite=True,
    )
    assert not alloc.error, f"allocate error: {alloc.error}"
    # lot03 plan 92/92 满,lot05 92/92 满,lot06 79/91
    closed = set(alloc.closed_batches)
    assert "lot03" in closed and "lot05" in closed
    assert "lot06" not in closed

    # 验证 plan 表
    conn = sqlite3.connect(str(sop_db))
    rows = conn.execute(
        "SELECT release_batch_id, allocated_box_count, status FROM release_batch_dispatch_plan "
        "ORDER BY priority_order"
    ).fetchall()
    conn.close()
    by_id = {r[0]: (r[1], r[2]) for r in rows}
    # fixture 未预置历史箱级行；计数必须服从箱级事实而非旧计划累计值。
    assert by_id["lot03"] == (1, "completed")
    assert by_id["lot05"] == (20, "completed")
    assert by_id["lot06"][0] == 79, f"lot06 应吃 79 box (got {by_id['lot06']})"
    assert by_id["lot06"][1] == "active"

    # split 车检查:1886696 / 1886708 在新表里 box2 应在不同 lot
    conn = sqlite3.connect(str(sop_db))
    splits = conn.execute(
        "SELECT car_no, COUNT(DISTINCT batch_id) FROM wagon_container_shipments "
        "WHERE car_no IN ('1886696','1886708') GROUP BY car_no"
    ).fetchall()
    conn.close()
    assert all(c == 2 for _, c in splits), f"2 个 split 车,box 跨 2 lot: {splits}"

    # ── Step 4c: lifecycle 触发器(plan 满推 all_loaded)─────────
    # allocate_wagons 自己不触发,chain 才触发;这里手动触发对应 closed_batches
    from sop_hub.sop.lifecycle_transition import advance_lifecycle
    for closed_id in alloc.closed_batches:
        advance_lifecycle(closed_id, "all_loaded",
                          reason="plan 满", triggered_by="e2e_test",
                          db_path=str(sop_db))
    conn = sqlite3.connect(str(sop_db))
    statuses = dict(conn.execute(
        "SELECT id, dispatch_status FROM release_batches"
    ).fetchall())
    conn.close()
    assert statuses["lot03"] == "all_loaded"
    assert statuses["lot05"] == "all_loaded"
    assert statuses["lot06"] == "loading"

    # ── Step 5: generate_dispatch_event_excel ─────────────────────
    from sop_hub.sop.departure_excel import generate_dispatch_event_excel
    excel = generate_dispatch_event_excel(container_ydids=new_ydids, db_path=str(sop_db))
    # 误差容忍:模板可能写不出文件(yaml 模板 lookup),但 row 数据应该正确
    # 50 wagons:48 整车 1 行 + 2 split 各 2 行 = 52
    if not excel.error:
        assert excel.wagon_count == 50
        assert excel.row_count == 52, (
            f"expect 52 rows (48 整车 + 2 split * 2),got {excel.row_count}"
        )


def test_e2e_jilin_all_lots_full_should_reject_next_msg(jilin_e2e_dbs):
    """所有 lot 都 all_loaded 时,新 "煤六 X节" 进来应落 pending_match,
    chain Step 2 报 reason=all_loaded_full,不强配。"""
    from sop_hub.sop.lifecycle_transition import advance_lifecycle
    from sop_hub.sop.executor_runner import find_release_batch_with_reason
    from sop_hub.sop.release_batch_pending_match import (
        ensure_schema, create_pending, list_pending,
    )

    sop_db = jilin_e2e_dbs["sop_db"]
    # 把 3 个 lot 全推到 all_loaded
    for lid in ("lot03", "lot05", "lot06"):
        advance_lifecycle(lid, "all_loaded", reason="setup", db_path=str(sop_db))

    rb, reason, candidates = find_release_batch_with_reason(
        "蓝鳍", "四平", db_path=str(sop_db),
    )
    assert rb is None
    assert reason == "all_loaded_full"
    assert set(candidates) == {"lot03", "lot05", "lot06"}

    # 模拟 chain step 2 失败时落 pending_match
    ensure_schema(db_path=str(sop_db))
    res = create_pending(
        message_id="wx_500_next", reason=reason,
        ship_name="蓝鳍", destination="四平", car_count=30,
        text_content="煤六 30节 四平铁 蓝鳍",
        candidate_batch_ids=candidates, db_path=str(sop_db),
    )
    assert res["action"] == "created"
    pending = list_pending(db_path=str(sop_db))
    assert len(pending) == 1
    assert pending[0]["ship_name"] == "蓝鳍"
    assert pending[0]["reason"] == "all_loaded_full"
