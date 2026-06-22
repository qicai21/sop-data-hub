"""一次性脚本:建 jiusan + 诚信 2 个 loading 状态 release_batch。

放货信息(出港计划通知单,2026-06-11,数据单发群 wx_226 图,2026-06-19 用户转入):
- 中文船名: 诚信 / 英文船名: MV BASIC FAITH / 进口航次: 2602 / 产地: 巴西
- 完货时间: 2026-06-04 18:00
- 货物: 大豆(外贸进口),发货单位 锦州港物流发展有限公司
- 海关放行单总量 / 卸货数: 70463.32 吨
- 一张图含两联单据,拼起来:
    单据①(筒仓):       集装箱 30126 吨
    单据②(物流3、8号库): 集装箱 23337 + K车散粮 17000 = 40337 吨
  → lot01 集装箱 = 30126+23337 = 53463 吨;lot02 散粮K车 = 17000 吨(合计 70463 ≈ 放行 70463.32)
- 到站:集装箱→新台子站(三三0处);散粮→新台子站(九三铁岭/九三工厂专用线,与上一票和谐1同)
  (通知单虽列"新台子站、得胜台站",但用户确认本票两 lot 实际都走新台子站)

跟 harmony1 同构(scripts/oneoff_seed_jiusan_harmony1.py):
- dispatch_status='loading'(已放货未发运),batch_count=0,shipped=0,remaining=batch_quantity
- 没有 wagon 行,等 95306 + manifest 后续 enrich

⚠ 推断字段(通知单未明示,沿用九三循环列既有路由,待用户核):
- consignee/yard:集装箱→三三0处专用线、散粮→九三铁岭专用线(与昆娜/玛格丽特/和谐1 同路由)
- origin_station=高桥镇、contract_no=JGWL-JZTS-DD-202601(标准年度合同)
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
        # (lot, transport_mode, dest_station, dest_line, consignee, qty_tons, prod_name)
        ("lot01", "铁路集装箱", "新台子",
         "国家粮食和物资储备局辽宁局三三0处专用线",
         "国家粮食和物资储备局辽宁局三三0处", 53463.0, "大豆-集装箱"),
        ("lot02", "铁路散粮车", "新台子",
         "九三集团铁岭大豆科技有限公司专用线",
         "九三集团铁岭大豆科技有限公司", 17000.0, "大豆-散粮车"),
    ]
    ship_name = "诚信"
    notice_date = "2026-06-11"
    out = []
    for lot, mode, dest_station, destline, consignee, qty, prod_name in rows:
        batch_key = f"jiusan|{ship_name}|{lot}|{notice_date}"
        bid = stable_hash("jiusan", ship_name, lot, notice_date)
        src = {
            "ship_name_cn": ship_name,
            "ship_name_en": "MV BASIC FAITH",
            "voyage_no": "2602",
            "cargo_origin": "巴西",
            "cargo_discharge_complete_at": "2026-06-04 18:00",
            "release_date": notice_date,
            "customs_release_tons": 70463.32,
            "manifest_total_tons": 70463.32,
            "lot_planned_tons": qty,
            "notice_image": "数据单发群/wx_226 (9a3d9f0cf3adf08dbf35a24ea8918ad0)",
            "src": "wx_notice_20260611_ingested_20260619",
        }
        out.append({
            **common,
            "id": bid, "batch_key": batch_key, "ship_name": ship_name,
            "batch_sequence": lot, "transport_mode": mode,
            "destination_station": dest_station,
            "yard_location": destline,
            "consignee": consignee,
            "notice_date": notice_date, "batch_date": notice_date,
            "batch_quantity": qty, "total_planned_quantity": qty,
            "remaining_quantity": qty,
            "remaining_weight_tons": qty,
            "batch_count": 0, "actual_wagon_count": 0,
            "shipped_weight_tons": 0.0,
            "dispatch_status": "loading",
            "dispatch_status_note": "wx_notice_20260611_ingested_20260619",
            "dispatch_status_updated_at": now,
            "confirmed_received_at": None,
            "cargo_product_name": prod_name,
            "cargo_name_detail": prod_name,
            "source_file_name": "数据单发群/wx_226",
            "source_message_id": "wx_226",
            "source_group_id": "数据单发群",
            "source_json": json.dumps(src, ensure_ascii=False),
            "searchable_text": f"九三大豆 {ship_name} MV BASIC FAITH {lot} {mode} 高桥镇 {dest_station} {destline}",
            "import_ship_name": "MV BASIC FAITH",
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
              f"{b['transport_mode']:10s}  qty={b['batch_quantity']:.2f}t  到站={b['destination_station']}  status={b['dispatch_status']}")
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
