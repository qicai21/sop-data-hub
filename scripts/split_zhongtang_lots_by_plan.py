"""按汐子放货跟踪表的 plan 段拆分上一步入库的 8 船 lot01。

策略:
1. 每船把跟踪表里对应 plan 段按 date_range[0] 升序排
2. 取该船所有 wagon_shipments 按 ticketed_at 升序
3. 顺序消费:plan_i 收 total_i 个 wagon,记 plan_id/notice_date
4. 单 plan 船:就地 UPDATE 原 lot01 加 plan_id
5. 多 plan 船:lot01 改 plan_id + 重算 cars,余下新建 lot02/lot03... 重链 wagon
6. plan 总和 < 商务车:剩余归末段(避免漏车),flag 给 commissioner_note
7. plan 总和 > 商务车:末段实际收尾即可
8. 埃克尔_公正混的 143 车(3/18-3/20)对应不上:新建 lot_unmatched

每个 lot 的 batch_quantity 暂用该 lot 内 wagon 实重和兜底,等用户给 plan 真值。
notice_date 改为该 plan 段的 date_range[0]。
batch_key 同步:<ship>|铁矿粉|汐子|<notice_date>|lotNN。
"""
from __future__ import annotations
import sqlite3, json, hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "data" / "sop_agent.db"
TRACK = json.loads(Path("/Users/qicai21/Desktop/补项目数据/中唐/整理结果/汐子跟踪表_解析.json").read_text(encoding="utf-8"))

# 每船的 plan 拆分 ── 显式列出,避免歧义
SPLIT_PLANS = {
    "非凡": [
        ("90260100025", "2026-01-07", "2026-01-09", 147),
        ("90260100026", "2026-01-09", "2026-01-10", 153),
        ("90260100058", "2026-01-10", "2026-01-15", 263),
    ],
    "德邻惠航": [
        ("90260200009", "2026-02-18", "2026-02-20", 261),
    ],
    "公正": [
        ("90260200065", "2026-03-17", "2026-03-18", 72),
    ],
    "金泰68": [
        ("90260300037", "2026-03-20", "2026-04-02", 233),
    ],
    "厦门世纪": [
        ("90260400049", "2026-04-16", "2026-04-16", 46),
    ],
    "环球信任": [
        ("90260400017", "2026-04-10", "2026-04-14", 289),
        ("90260400031", "2026-04-16", "2026-04-24", 144),
        ("90260400037", "2026-04-24", "2026-04-25", 144),
        ("90260400064", "2026-04-25", "2026-04-26", 148),
        ("90260400072", "2026-04-27", "2026-04-29", 204),
    ],
    "喜悦": [
        ("90260300023", "2026-03-10", "2026-03-13", 161),
        ("90260300035", "2026-03-13", "2026-03-17", 148),
        ("90260300069", "2026-03-20", "2026-03-25", 147),
        ("90260300099", "2026-03-25", "2026-03-28", 147),
        ("90260300106", "2026-03-28", "2026-04-01", 220),
        ("90260300131", "2026-04-01", "2026-04-07", 147),
        ("90260400006", "2026-04-07", "2026-04-07", 106),
        ("90260400005", "2026-04-07", "2026-04-07", 14),
    ],
    "埃克尔_公正混": [
        # 商务表混合,跟踪表里只有 公正/埃克尔 3/8-3/10 这两段对得上
        ("90260300017", "2026-03-08", "2026-03-09", 44),  # 公正
        ("90260300018", "2026-03-09", "2026-03-10", 41),  # 埃克尔
        # 剩 143 车走 unmatched(3/18-3/20 商务记录,跟踪表里 公正 90260300061 / 埃克尔 90260300062
        # 都对不上车数和窗口,留 plan_id=NULL 给用户后补)
    ],
}


def split_ship(conn, ship):
    """对单船拆 lot。返回 (lot_id_list, summary)。"""
    r = conn.execute("""SELECT id FROM release_batches
        WHERE project='zhongtang_special_steel' AND ship_name=? AND batch_sequence='lot01'
        ORDER BY notice_date DESC LIMIT 1""", (ship,)).fetchone()
    if not r:
        return [], f"{ship}: 找不到 lot01,跳过"
    lot01_id = r["id"]
    wagons = conn.execute(
        "SELECT id, ydid, ticketed_at, marked_weight FROM wagon_shipments WHERE batch_id=? ORDER BY ticketed_at, ydid",
        (lot01_id,),
    ).fetchall()

    plan_segs = SPLIT_PLANS.get(ship, [])
    if not plan_segs:
        return [lot01_id], f"{ship}: 无 plan 段定义,跳过"

    n_total = len(wagons)
    # plan total 总和
    sum_plan = sum(p[3] for p in plan_segs)

    # 顺序消费 wagon,plan 段切片
    idx = 0
    lot_buckets = []  # [(plan_id, start_date, wagon_rows)]
    for i, (plan_id, ds, de, total) in enumerate(plan_segs):
        if i == len(plan_segs) - 1:
            # 末段收尾:把剩下全部归这里(覆盖 plan total)
            take = n_total - idx
        else:
            take = min(total, n_total - idx)
        bucket = wagons[idx:idx+take]
        idx += take
        lot_buckets.append((plan_id, ds, bucket))
        if idx >= n_total:
            break

    # 剩余 wagon(只有埃克尔_公正混会触发,plan 总 85 < 商务 228)
    leftover = wagons[idx:] if idx < n_total else []

    actions = []
    # === lot01: 用第一段 plan 重写元信息 ===
    plan_id_0, ds_0, bucket_0 = lot_buckets[0]
    total_w0 = sum((w["marked_weight"] or 0) for w in bucket_0)
    new_key_0 = f"{ship}|铁矿粉|汐子|{ds_0}|lot01"
    conn.execute("""
      UPDATE release_batches SET
        plan_id=?, notice_date=?, batch_date=?, batch_key=?,
        actual_wagon_count=?, batch_quantity=?, total_planned_quantity=?,
        commissioner_note=?, updated_at=datetime('now')
      WHERE id=?
    """, (plan_id_0, ds_0, ds_0, new_key_0,
          len(bucket_0), total_w0, total_w0,
          f"待补充:大船+合同 (plan_id 已挂)",
          lot01_id))
    actions.append(f"  lot01 -> plan={plan_id_0} | {len(bucket_0)} 车 | {ds_0}")

    # === lot02..N: 新建 + 重链 wagon ===
    lot_ids = [lot01_id]
    for i, (plan_id, ds, bucket) in enumerate(lot_buckets[1:], start=2):
        if not bucket:
            continue
        seq = f"lot{i:02d}"
        new_key = f"{ship}|铁矿粉|汐子|{ds}|{seq}"
        new_id = hashlib.sha1(new_key.encode("utf-8")).hexdigest()
        total_w = sum((w["marked_weight"] or 0) for w in bucket)
        conn.execute("""
          INSERT INTO release_batches (
            id, batch_key, project, ship_name,
            cargo_name, cargo_product_name,
            consignor, consignee,
            commissioner_note, destination_station, origin_station,
            notice_date, batch_date, batch_sequence,
            batch_quantity, total_planned_quantity, batch_count, actual_wagon_count,
            customer_name, plan_id, dispatch_status,
            source_file_name, source_json, searchable_text,
            is_weighed, updated_at
          )
          SELECT
            ?, ?, project, ship_name, cargo_name, cargo_product_name,
            consignor, consignee,
            ?, destination_station, origin_station,
            ?, ?, ?, ?, ?, 1, ?,
            customer_name, ?, dispatch_status,
            source_file_name, source_json, searchable_text,
            is_weighed, datetime('now')
          FROM release_batches WHERE id=?
        """, (new_id, new_key,
              "待补充:大船+合同 (plan_id 已挂)",
              ds, ds, seq, total_w, total_w, len(bucket),
              plan_id, lot01_id))
        # 重链 wagon
        ph = ",".join(["?"] * len(bucket))
        conn.execute(f"UPDATE wagon_shipments SET batch_id=? WHERE id IN ({ph})",
                     [new_id] + [w["id"] for w in bucket])
        lot_ids.append(new_id)
        actions.append(f"  {seq}  -> plan={plan_id} | {len(bucket)} 车 | {ds}")

    # === unmatched 段(只 埃克尔_公正混 有) ===
    if leftover:
        seq = f"lot{len(lot_buckets) + 1:02d}"
        # leftover wagon 的最早 ticketed_at 当 notice_date
        earliest = (leftover[0]["ticketed_at"] or "")[:10] or "2026-01-01"
        new_key = f"{ship}|铁矿粉|汐子|{earliest}|{seq}_unmatched"
        new_id = hashlib.sha1(new_key.encode("utf-8")).hexdigest()
        total_w = sum((w["marked_weight"] or 0) for w in leftover)
        conn.execute("""
          INSERT INTO release_batches (
            id, batch_key, project, ship_name,
            cargo_name, cargo_product_name,
            consignor, consignee,
            commissioner_note, destination_station, origin_station,
            notice_date, batch_date, batch_sequence,
            batch_quantity, total_planned_quantity, batch_count, actual_wagon_count,
            customer_name, plan_id, dispatch_status,
            source_file_name, source_json, searchable_text,
            is_weighed, updated_at
          )
          SELECT
            ?, ?, project, ship_name, cargo_name, cargo_product_name,
            consignor, consignee,
            ?, destination_station, origin_station,
            ?, ?, ?, ?, ?, 1, ?,
            customer_name, NULL, dispatch_status,
            source_file_name, source_json, searchable_text,
            is_weighed, datetime('now')
          FROM release_batches WHERE id=?
        """, (new_id, new_key,
              "**待用户确认 plan**:跟踪表对不上,商务表归类异常",
              earliest, earliest, seq, total_w, total_w, len(leftover),
              lot01_id))
        ph = ",".join(["?"] * len(leftover))
        conn.execute(f"UPDATE wagon_shipments SET batch_id=? WHERE id IN ({ph})",
                     [new_id] + [w["id"] for w in leftover])
        lot_ids.append(new_id)
        actions.append(f"  {seq}* unmatched(plan=NULL) | {len(leftover)} 车 | {earliest} — **需用户补**")

    summary = f"{ship}: {n_total} 车 → {len(lot_ids)} lot\n" + "\n".join(actions)
    return lot_ids, summary


def main():
    conn = sqlite3.connect(str(DB), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row

    for ship in SPLIT_PLANS:
        lot_ids, summary = split_ship(conn, ship)
        print("\n" + "=" * 60)
        print(summary)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
