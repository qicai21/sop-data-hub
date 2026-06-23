"""中唐特钢补充货运信息 → release_batch enrich(#issue-20260623 缺口2)守门测试。

覆盖**生产路径**(workflow_task_executor._execute_freight_detail_enrichment 用的):
  extract_zhongtang_freight_supplement → auto_enrich_release_batches_from_zhongtang_supplement
样本取自用户 2026-06-23 给的真实中唐发运群货运文本(鞍子河/丰收散运)。

匹配口径(用户 2026-06-23):**按船名**,一眼能 match 的就填、拿不准的挂起等人工:
  ① 在途批次中同船名、缺计划号的——唯一 → 填(同船不落 import_ship_name);
  ② 多个同船名缺计划号 → 挂起(歧义,人工指定 lot);
  ③ 货运船名对不上任何在途到港船 → 挂起(进口大船/转水,人工说明);
  ④ 只看在途(loading/enriched/pending);已收货批次不参与。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sop_hub.sop.zhongtang_freight_text_extractor import (
    extract_zhongtang_freight_supplement,
)
from sop_hub.sop.enrich_release_batch import (
    auto_enrich_release_batches_from_zhongtang_supplement as enrich_zt,
)

# 用户 2026-06-23 真实样本(供方行 ASCII 冒号、其余全角冒号、公司名带全角括号)
SAMPLE_ANZIHE = """供方: 福建漳龙集团有限公司（天津茂远）
船名：鞍子河
货名：印度粉
港口：锦州港
数量：10000
计划号：90260600006
合同号：ZLZT-2026060301"""

SAMPLE_FENGSHOU = """供方: 福建漳龙集团有限公司（天津茂远）
船名：丰收散运
货名：纽曼粉
港口：锦州港
数量：10000
计划号：90260500008
合同号：ZLZT-2026050801"""


def _mk_db(db_path: Path, batches: list[dict]) -> None:
    """建最小 release_batches(含 import_ship_name/plan_id 列)+ 若干批次。
    batch dict: id, ship_name, plan_id='', dispatch_status='loading'。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE release_batches (
            id TEXT PRIMARY KEY, batch_key TEXT UNIQUE,
            project TEXT, ship_name TEXT, import_ship_name TEXT,
            cargo_name TEXT, cargo_product_name TEXT,
            contract_no TEXT, plan_id TEXT, order_identifier TEXT,
            batch_sequence TEXT, dispatch_status TEXT, updated_at TEXT )"""
    )
    for i, b in enumerate(batches):
        conn.execute(
            "INSERT INTO release_batches (id, batch_key, project, ship_name, cargo_name, "
            "plan_id, batch_sequence, dispatch_status) VALUES (?,?,?,?,?,?,?,?)",
            (b["id"], f"k{i}", "zhongtang_special_steel", b["ship_name"], "铁矿粉",
             b.get("plan_id", ""), f"lot0{i + 1}", b.get("dispatch_status", "loading")),
        )
    conn.commit()
    conn.close()


def _row(db_path: Path, bid: str) -> sqlite3.Row:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM release_batches WHERE id=?", (bid,)).fetchone()
    conn.close()
    return r


# ── 提取器:真实样本全字段 ──────────────────────────────────────────────
def test_supplement_parses_real_samples_all_fields():
    c = extract_zhongtang_freight_supplement(SAMPLE_ANZIHE)
    assert c.status == "complete"
    assert c.project_id == "zhongtang_special_steel"
    assert (c.ship_name, c.cargo_product_name, c.plan_id, c.contract_no, c.quantity_tons) == (
        "鞍子河", "印度粉", "90260600006", "ZLZT-2026060301", 10000)
    assert "福建漳龙集团有限公司" in c.supplier      # 含全角括号公司全名不漏

    c2 = extract_zhongtang_freight_supplement(SAMPLE_FENGSHOU)
    assert (c2.ship_name, c2.plan_id, c2.contract_no) == (
        "丰收散运", "90260500008", "ZLZT-2026050801")


def test_supplement_requires_plan_and_contract():
    c = extract_zhongtang_freight_supplement("船名：鞍子河\n货名：印度粉\n港口：锦州港")
    assert c.status == "no_match"


# ── enrich:按船名匹配 ──────────────────────────────────────────────────
def test_enrich_unique_same_ship_missing_plan_fills(tmp_path: Path):
    """规则①:在途同船名、唯一缺计划号 → 填入(同船 → import_ship_name 留空)。"""
    db = tmp_path / "t.db"
    _mk_db(db, [{"id": "b1", "ship_name": "鞍子河", "plan_id": ""}])
    res = enrich_zt(extract_zhongtang_freight_supplement(SAMPLE_ANZIHE), apply=True, db_path=db)
    assert res["status"] == "applied"
    row = _row(db, "b1")
    assert row["plan_id"] == "90260600006"
    assert row["cargo_product_name"] == "印度粉"
    assert not (row["import_ship_name"] or "")       # 同船 → 不落 import_ship_name


def test_enrich_ambiguous_same_ship_suspends(tmp_path: Path):
    """规则②:多个同船名在途批次都缺计划号 → 挂起,绝不自动填。"""
    db = tmp_path / "t.db"
    _mk_db(db, [{"id": "b1", "ship_name": "鞍子河", "plan_id": ""},
                {"id": "b2", "ship_name": "鞍子河", "plan_id": ""}])
    res = enrich_zt(extract_zhongtang_freight_supplement(SAMPLE_ANZIHE), apply=True, db_path=db)
    assert res["status"] == "suspended"
    assert set(res["candidate_batch_ids"]) == {"b1", "b2"}
    assert not (_row(db, "b1")["plan_id"] or "")     # 都没被填
    assert not (_row(db, "b2")["plan_id"] or "")


def test_enrich_ship_not_found_suspends(tmp_path: Path):
    """规则③:货运船名(鞍子河)对不上任何在途到港船 → 挂起(进口大船/转水)。"""
    db = tmp_path / "t.db"
    _mk_db(db, [{"id": "b1", "ship_name": "运达7", "plan_id": ""}])
    res = enrich_zt(extract_zhongtang_freight_supplement(SAMPLE_ANZIHE), apply=True, db_path=db)
    assert res["status"] == "suspended"
    assert "进口大船" in res["reason"] or "转水" in res["reason"]
    assert not (_row(db, "b1")["plan_id"] or "")


def test_enrich_same_ship_already_filled_noop(tmp_path: Path):
    """同船在途批次计划号都已填 → no_op(多半重复消息)。"""
    db = tmp_path / "t.db"
    _mk_db(db, [{"id": "b1", "ship_name": "鞍子河", "plan_id": "90260600006"}])
    res = enrich_zt(extract_zhongtang_freight_supplement(SAMPLE_ANZIHE), apply=True, db_path=db)
    assert res["status"] == "no_op"


def test_enrich_only_open_batches_considered(tmp_path: Path):
    """④ 已收货批次不参与:同船但 confirmed_received → 在途里没有 → 挂起。"""
    db = tmp_path / "t.db"
    _mk_db(db, [{"id": "b1", "ship_name": "鞍子河", "plan_id": "",
                 "dispatch_status": "confirmed_received"}])
    res = enrich_zt(extract_zhongtang_freight_supplement(SAMPLE_ANZIHE), apply=True, db_path=db)
    assert res["status"] == "suspended"
