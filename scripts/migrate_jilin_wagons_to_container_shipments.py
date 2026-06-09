"""#123 Phase 1 — 把 jilin_jingang_jinzhou wagon_shipments 225 行迁移
到 wagon_container_shipments(每 wagon × N box → N 行)。

拆 box 的源:
  - container_numbers_json 优先(JSON list)
  - 退到 container_no "A/B" 字符串拆 "/"
  - 都没有 → wagon 当 0 box(不该出现在集装箱业务)

每 box 行的 batch_id:
  - split 车(container_batch_map 非空):cbm[box] 指的 lot
  - 整车:wagon.batch_id

迁完后:
  - 验证总 box 数(jilin 期望 ~450 = 225 wagon × 2 box,小部分车可能 1 box)
  - 验证 split 车的 cbm 拆分:1886696 / 1886708 box2 到正确 lot
"""
from __future__ import annotations
import json
import sqlite3
import sys
import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
DB = REPO / "data" / "sop_agent.db"

from sop_hub.sop.wagon_container_shipments import ensure_schema


def _row_boxes(row: dict) -> list[str]:
    cnj = row.get("container_numbers_json")
    if cnj:
        try:
            return [b for b in json.loads(cnj) if b]
        except Exception:
            pass
    raw = row.get("container_no") or ""
    return [b.strip() for b in str(raw).split("/") if b.strip()]


def _box_batch_for(wagon: dict, box: str) -> str:
    cbm_raw = wagon.get("container_batch_map")
    if cbm_raw:
        try:
            cbm = json.loads(cbm_raw)
            if isinstance(cbm, dict) and box in cbm:
                return cbm[box]
        except Exception:
            pass
    return wagon.get("batch_id", "")


def main():
    ensure_schema(db_path=str(DB))
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    wagons = conn.execute(
        "SELECT * FROM wagon_shipments WHERE project_id='jilin_jingang_jinzhou'"
    ).fetchall()
    print(f"=== source jilin wagons: {len(wagons)} ===")

    inserted = 0
    skipped_no_box = 0
    multi_lot_wagons = 0
    for w in wagons:
        wd = dict(w)
        boxes = _row_boxes(wd)
        if not boxes:
            skipped_no_box += 1
            continue
        lots_for_wagon = set()
        for idx, box in enumerate(boxes, start=1):
            bid = _box_batch_for(wd, box)
            lots_for_wagon.add(bid)
            row_id = hashlib.sha1(
                f"{wd['car_no']}|{box}|{wd['ydid']}".encode()
            ).hexdigest()[:24]
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO wagon_container_shipments (
                        id, car_no, box_no, box_position, ydid, czydid, waybill_no,
                        batch_id, car_model, ticketed_at, departed_at, arrived_at,
                        delivered_at, accepted_at, loaded_at,
                        status_name, latest_stage_key, latest_stage_name, latest_event_time,
                        origin_name, destination_name, transport_mode_code, transport_mode_name,
                        cargo_name, marked_weight,
                        project_id, ship_name, consignor, consignee, dispatch_status,
                        source_message_id, source_group_id, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row_id, wd["car_no"], box, idx, wd["ydid"], wd.get("czydid"),
                        wd.get("waybill_no"), bid,
                        wd.get("car_model"), wd.get("ticketed_at"), wd.get("departed_at"),
                        wd.get("arrived_at"), wd.get("delivered_at"),
                        wd.get("accepted_at"), wd.get("loaded_at"),
                        wd.get("status_name"), wd.get("latest_stage_key"),
                        wd.get("latest_stage_name"), wd.get("latest_event_time"),
                        wd.get("origin_name"), wd.get("destination_name"),
                        wd.get("transport_mode_code"), wd.get("transport_mode_name"),
                        wd.get("cargo_name"), wd.get("marked_weight"),
                        wd.get("project_id"), wd.get("ship_name"),
                        wd.get("shipper_name"), wd.get("consignee_name"),
                        wd.get("dispatch_status", "in_progress"),
                        wd.get("source_message_id"), wd.get("source_group_id"),
                        wd.get("created_at"), wd.get("updated_at"),
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError as exc:
                print(f"  IntegrityError car={wd['car_no']} box={box}: {exc}")
        if len(lots_for_wagon) > 1:
            multi_lot_wagons += 1
    conn.commit()

    # 验证
    box_count = conn.execute(
        "SELECT COUNT(*) FROM wagon_container_shipments "
        "WHERE project_id='jilin_jingang_jinzhou'"
    ).fetchone()[0]
    by_batch = conn.execute(
        "SELECT batch_id, COUNT(*) FROM wagon_container_shipments "
        "WHERE project_id='jilin_jingang_jinzhou' GROUP BY batch_id"
    ).fetchall()
    print(f"\n=== 迁移结果 ===")
    print(f"inserted: {inserted}")
    print(f"skipped (无 box): {skipped_no_box}")
    print(f"split 车(box 跨 lot): {multi_lot_wagons}")
    print(f"新表总 box 数(jilin): {box_count}")
    print(f"\n按 batch_id 分布:")
    for bid, n in by_batch:
        rb_seq = conn.execute(
            "SELECT batch_sequence FROM release_batches WHERE id=?", (bid,)
        ).fetchone()
        seq = rb_seq[0] if rb_seq else "—"
        print(f"  {bid[:12]} ({seq}): {n} box")

    conn.close()


if __name__ == "__main__":
    main()
