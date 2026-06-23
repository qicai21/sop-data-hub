"""中唐特钢补充货运信息 → release_batch enrich(#issue-20260623 缺口2)守门测试。

覆盖**生产实际路径**(workflow_task_executor._execute_freight_detail_enrichment 用的):
  extract_zhongtang_freight_supplement → auto_enrich_release_batches_from_zhongtang_supplement
样本取自用户 2026-06-23 给的真实中唐发运群货运文本(鞍子河/丰收散运)。

重点守:
  ① 7 字段全解析(供方/船名/货名/港口/数量/计划号/合同号),半/全角冒号都吃。
  ② 强关键词缺失(无计划号+合同号)→ no_match,不误吞别的文本。
  ③ enrich 按 合同号→计划号 反查命中批次,落 order_identifier(计划号)+合同+货名。
  ④ import_ship_name 规则:货运船名≠出港通知单船名(转水/海铁两段)→ 落 import_ship_name;
     一致→留空。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sop_hub.sop.zhongtang_freight_text_extractor import (
    extract_zhongtang_freight_supplement,
)
from sop_hub.sop.enrich_release_batch import (
    auto_enrich_release_batches_from_zhongtang_supplement,
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


def _mk_db(db_path: Path, *, ship_name: str, contract_no: str = "",
           plan_id: str = "") -> None:
    """建最小 release_batches(含 import_ship_name/plan_id 列)+ 一条在途批次。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE release_batches (
            id TEXT PRIMARY KEY, batch_key TEXT UNIQUE,
            project TEXT, ship_name TEXT, import_ship_name TEXT,
            cargo_name TEXT, cargo_product_name TEXT,
            contract_no TEXT, plan_id TEXT, order_identifier TEXT,
            batch_sequence TEXT, dispatch_status TEXT,
            updated_at TEXT )"""
    )
    conn.execute(
        """INSERT INTO release_batches
           (id, batch_key, project, ship_name, cargo_name, contract_no, plan_id,
            batch_sequence, dispatch_status)
           VALUES ('rb_zt_1','k1','zhongtang_special_steel',?,?,?,?, 'lot01','loading')""",
        (ship_name, "铁矿粉", contract_no, plan_id),
    )
    conn.commit()
    conn.close()


def _row(db_path: Path) -> sqlite3.Row:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM release_batches WHERE id='rb_zt_1'").fetchone()
    conn.close()
    return r


# ── ① 提取器:真实样本全字段 ────────────────────────────────────────────
def test_supplement_parses_real_samples_all_fields():
    c = extract_zhongtang_freight_supplement(SAMPLE_ANZIHE)
    assert c.status == "complete"
    assert c.project_id == "zhongtang_special_steel"
    assert c.ship_name == "鞍子河"
    assert c.cargo_product_name == "印度粉"
    assert c.plan_id == "90260600006"
    assert c.contract_no == "ZLZT-2026060301"
    assert c.quantity_tons == 10000
    assert "福建漳龙集团有限公司" in c.supplier      # 含全角括号公司全名不漏

    c2 = extract_zhongtang_freight_supplement(SAMPLE_FENGSHOU)
    assert (c2.ship_name, c2.plan_id, c2.contract_no) == (
        "丰收散运", "90260500008", "ZLZT-2026050801")


def test_supplement_requires_plan_and_contract():
    # 只有船名货名、缺计划号+合同号 → 不认定为中唐货运
    c = extract_zhongtang_freight_supplement("船名：鞍子河\n货名：印度粉\n港口：锦州港")
    assert c.status == "no_match"


# ── ③④ enrich 端到端 ───────────────────────────────────────────────────
def test_enrich_matches_by_contract_and_fills_plan_cargo(tmp_path: Path):
    """合同号反查命中在途批次 → 落 order_identifier(计划号)+货名。"""
    db = tmp_path / "t.db"
    _mk_db(db, ship_name="鞍子河", contract_no="ZLZT-2026060301")  # 同船命中
    c = extract_zhongtang_freight_supplement(SAMPLE_ANZIHE)
    res = auto_enrich_release_batches_from_zhongtang_supplement(c, apply=True, db_path=db)
    assert res["status"] == "applied"
    row = _row(db)
    assert row["plan_id"] == "90260600006"                # 计划号落 plan_id 列
    assert row["cargo_product_name"] == "印度粉"
    assert not (row["import_ship_name"] or "")            # 同船 → import_ship_name 留空


def test_enrich_transship_sets_import_ship_name(tmp_path: Path):
    """货运船名(进口大船)≠出港通知单到港船 → import_ship_name 落货运船名、ship_name 不动。"""
    db = tmp_path / "t.db"
    _mk_db(db, ship_name="运达7", contract_no="ZLZT-2026060301")  # 到港船≠货运船
    c = extract_zhongtang_freight_supplement(SAMPLE_ANZIHE)       # 货运船=鞍子河
    res = auto_enrich_release_batches_from_zhongtang_supplement(c, apply=True, db_path=db)
    assert res["status"] == "applied"
    row = _row(db)
    assert row["ship_name"] == "运达7"            # 到港船保持不变
    assert row["import_ship_name"] == "鞍子河"    # 进口大船落 import_ship_name


def test_enrich_no_anchor_batch_returns_no_match(tmp_path: Path):
    """合同号/计划号都对不上任何批次 → no_match(通知单还没来,留重试)。"""
    db = tmp_path / "t.db"
    _mk_db(db, ship_name="鞍子河", contract_no="ZLZT-9999999999")
    c = extract_zhongtang_freight_supplement(SAMPLE_ANZIHE)
    res = auto_enrich_release_batches_from_zhongtang_supplement(c, apply=True, db_path=db)
    assert res["status"] == "no_match"
