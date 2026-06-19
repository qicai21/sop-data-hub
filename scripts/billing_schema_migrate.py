"""费用/对账/发票/结算 五表建库(#P001-billing P1,2026-06-18)。

不单独建库 —— 在主库 data/sop_agent.db 扩费用结算域(理由:费用生成依赖
release_batches/wagon/dispatch_map,跨库 join 吃过亏;费用结构走 yaml 配置驱动)。

状态机(挂在 billing_reconciliation 上,费用项随对账单流转):
  已发完 → 生成费用 → 已发对账 → 对账确认 → 已申请开票 → 已开票 → 待结款 → 已结清

用法:python scripts/billing_schema_migrate.py            # 干跑(打印 DDL)
      python scripts/billing_schema_migrate.py --apply    # 建表
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

SOP_DB = REPO / "data" / "sop_agent.db"

DDL = """
-- 费用明细:每船/批 每费用项一行(可被结算方拆分)
CREATE TABLE IF NOT EXISTS billing_cost_item (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  batch_ref TEXT,            -- ship_name 或 release_batches.id
  ship_name TEXT,
  period TEXT,               -- 结算周期/发货日期段
  cost_code TEXT NOT NULL,   -- 对应 yaml cost_structure.items.code
  cost_name TEXT,
  side TEXT,                 -- income/cost/passthrough
  settle_party TEXT,         -- 结算方(铁发/中唐特钢/沈阳局/高天/二级公司…)
  rate REAL,                 -- 元/吨 或 元/车
  base_kind TEXT,            -- railway_billing_weight/settlement_weight/car_count
  base_qty REAL,             -- 计费基数(吨或车)
  car_count INTEGER,         -- 车数(可追溯)
  amount REAL,               -- = rate*base_qty
  tax_rate REAL,
  status TEXT DEFAULT '生成费用',
  reconciliation_id TEXT,    -- 归入哪张对账单
  source TEXT,               -- 数据来源(手帐ledger/dispatch_map/…)
  note TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_costitem_proj_code ON billing_cost_item(project, cost_code, settle_party);

-- 结算方车级变更:同船部分车改派结算方(运达7:23车 铁发→中唐特钢)
CREATE TABLE IF NOT EXISTS billing_settle_override (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  ship_name TEXT,
  cost_code TEXT NOT NULL,
  car_no TEXT,               -- 车号
  ydid TEXT,                 -- 95306 运单 id(更稳)
  hph TEXT,                  -- 货票号
  from_party TEXT,           -- 原结算方
  to_party TEXT,             -- 改派结算方
  billing_weight REAL,       -- 该车铁路计费重量
  note TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_override_proj_ship ON billing_settle_override(project, ship_name, cost_code);

-- 对账单:按 结算方+周期 汇总费用项 → 一张单,状态机载体
CREATE TABLE IF NOT EXISTS billing_reconciliation (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  counterparty TEXT,         -- 对账对象(客户/结算方)
  period TEXT,
  total_amount REAL,
  total_cars INTEGER,
  total_qty REAL,
  status TEXT DEFAULT '已发对账',  -- 已发对账/对账确认/已申请开票/已开票/待结款/已结清
  confirmed_at TEXT,
  note TEXT,
  created_at TEXT, updated_at TEXT
);

-- 发票
CREATE TABLE IF NOT EXISTS billing_invoice (
  id TEXT PRIMARY KEY,
  reconciliation_id TEXT,
  project TEXT,
  invoice_type TEXT,         -- 9%专票/6%专票/不征税普票
  amount REAL,
  tax_rate REAL,
  invoice_no TEXT,
  applied_at TEXT,           -- 申请开票
  issued_at TEXT,            -- 已开票
  status TEXT DEFAULT '已申请开票',
  note TEXT,
  created_at TEXT, updated_at TEXT
);

-- 收付款结算
CREATE TABLE IF NOT EXISTS billing_settlement (
  id TEXT PRIMARY KEY,
  reconciliation_id TEXT,
  invoice_id TEXT,
  project TEXT,
  direction TEXT,            -- 收/付
  amount REAL,
  status TEXT DEFAULT '待结款',  -- 待结款/已结清
  settled_at TEXT,
  note TEXT,
  created_at TEXT, updated_at TEXT
);
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.apply:
        print(DDL)
        print("DRY-RUN(加 --apply 建表)")
        return
    conn = sqlite3.connect(str(SOP_DB))
    conn.executescript(DDL)
    conn.commit()
    tabs = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'billing_%' ORDER BY name")]
    conn.close()
    print(f"COMMIT ✓ {now_iso_beijing()} billing 表:", tabs)


if __name__ == "__main__":
    main()
