"""一次性脚本:建 jiusan + 和谐1(MV SM HARMONY 1) 2 个 loading 状态 release_batch。

放货信息(用户 2026-06-10 提供):
- 英文船名: MV SM HARMONY 1 (用户写作 HARNONY,推测笔误)
- 中文船名: 和谐1
- 进口航次: 2637
- 货物产地: 巴西
- 离港时间: 2026-05-17 20:10
- 放货日期: 2026-06-09
- 卸货总量: 68245.68 吨
  - 散粮车 20150 吨
  - 集装箱 48090 吨

跟昆娜+玛格丽特格式一致(scripts/oneoff_seed_jiusan_kunna_magritte.py),区别:
- dispatch_status='loading'(已放货,未发运)
- batch_count=0, shipped_weight_tons=0(没车次)
- remaining_quantity = batch_quantity(待发)
- confirmed_received_at = NULL
- 没有 wagon 行,等 95306 + manifest 后续到位再 enrich
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

SOP_DB = REPO / "data" / "sop_agent.db"


def stable_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def build_batches(now: str) -> list[dict]:
    common = {
        "project": "jiusan",
        "contract_no": "JGWL-JZTS-DD-202601",
        "cargo_name": "大豆",
        "consignor": "锦州港物流发展有限公司",
        "origin_station": "高桥镇",
        "trade_type": "外贸进口",
    }
    rows = [
        # (lot, transport_mode, dest_line, consignee, qty_tons, prod_name)
        ("lot01", "铁路集装箱", "国家粮食和物资储备局辽宁局三三0处专用线",
         "国家粮食和物资储备局辽宁局三三0处", 48090.0, "大豆-集装箱"),
        ("lot02", "铁路散粮车", "九三集团铁岭大豆科技有限公司专用线",
         "九三集团铁岭大豆科技有限公司", 20150.0, "大豆-散粮车"),
    ]
    ship_name = "和谐1"
    notice_date = "2026-06-09"
    out = []
    for lot, mode, destline, consignee, qty, prod_name in rows:
        batch_key = f"jiusan|{ship_name}|{lot}|{notice_date}"
        bid = stable_hash("jiusan", ship_name, lot, notice_date)
        src = {
            "ship_name_cn": ship_name,
            "ship_name_en": "MV SM HARMONY 1",
            "voyage_no": "2637",
            "cargo_origin": "巴西",
            "ship_departure_at": "2026-05-17 20:10",
            "release_date": notice_date,
            "manifest_total_tons": 68245.68,
            "lot_planned_tons": qty,
            "src": "manual_release_designation_20260610",
        }
        out.append({
            **common,
            "id": bid, "batch_key": batch_key, "ship_name": ship_name,
            "batch_sequence": lot, "transport_mode": mode,
            "destination_station": "新台子",
            "yard_location": destline,
            "consignee": consignee,
            "notice_date": notice_date, "batch_date": notice_date,
            "batch_quantity": qty, "total_planned_quantity": qty,
            "remaining_quantity": qty,
            "remaining_weight_tons": qty,
            "batch_count": 0, "actual_wagon_count": 0,
            "shipped_weight_tons": 0.0,
            "dispatch_status": "loading",
            "dispatch_status_note": "manual_release_designated_20260610",
            "dispatch_status_updated_at": now,
            "confirmed_received_at": None,
            "cargo_product_name": prod_name,
            "cargo_name_detail": prod_name,
            "source_file_name": "manual_release_designation_20260610",
            "source_json": json.dumps(src, ensure_ascii=False),
            "searchable_text": f"九三大豆 {ship_name} {lot} {mode} 高桥镇 新台子 {destline}",
            "import_ship_name": "MV SM HARMONY 1",
            "created_at": now, "updated_at": now,
        })
    return out


def main(apply: bool = False):
    now = now_iso_beijing()
    conn = sqlite3.connect(str(SOP_DB))
    batches = build_batches(now)
    cur = conn.cursor()
    print(f"准备插 {len(batches)} 个 release_batch:")
    for b in batches:
        print(f"  {b['id'][:12]}  {b['ship_name']:5s} {b['batch_sequence']} "
              f"{b['transport_mode']:10s}  qty={b['batch_quantity']:.2f}t  status={b['dispatch_status']}")
        print(f"    consignee: {b['consignee']}")
        print(f"    yard:      {b['yard_location']}")
        cols = list(b.keys())
        placeholders = ",".join(["?"] * len(cols))
        cur.execute(
            f"INSERT OR REPLACE INTO release_batches ({','.join(cols)}) VALUES ({placeholders})",
            [b[c] for c in cols],
        )

    if apply:
        conn.commit()
        print(f"\nCOMMIT ✓")
    else:
        conn.rollback()
        print(f"\nDRY-RUN(加 --apply 真正提交)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    main(apply=args.apply)
