"""Apply 蓝鳍 46 节多 lot 分票方案 → wagon_shipments + release_batches.

Reads:
  - sidecar: runtime/lot_assignments/蓝鳍_煤六_46节_20260602.json
  - 95306 DB: ~/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3
  - sop_hub DB: data/sop_agent.db

Writes (sop_hub DB):
  - INSERT 47 rows wagon_shipments (1572454 拆 2 行)
  - UPDATE release_batches.actual_wagon_count + shipped_weight_tons (3 lots)
  - UPDATE message_inbox.id=229 db_action=manual_dispatch_applied

Idempotent: re-running detects existing rows by ydid+container and skips.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, UTC
from pathlib import Path

SOP_DB = Path("data/sop_agent.db")
RAIL_DB = Path.home() / "projects/repos/rail95306-sync/runtime/95306_collection.sqlite3"
SIDECAR = Path("runtime/lot_assignments/蓝鳍_煤六_46节_20260602.json")

NOW_ISO = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
DEPARTURE_ID = "dep_蓝鳍_20260602_煤六46"


def _row_id() -> str:
    return uuid.uuid4().hex


def _load_95306_by_ydid(rail: sqlite3.Connection, ydid: str) -> dict:
    rail.row_factory = sqlite3.Row
    r = rail.execute(
        "SELECT * FROM shipments WHERE ydid=?", (ydid,)
    ).fetchone()
    return dict(r) if r else {}


def _detect_existing(sop: sqlite3.Connection, ydid: str, container_no: str | None) -> bool:
    """True if this exact ydid+container_no combo already in wagon_shipments."""
    cur = sop.execute(
        "SELECT id FROM wagon_shipments WHERE ydid=?", (ydid,),
    ).fetchall()
    if not cur:
        return False
    # if existing rows present, check container_no equality
    for row in cur:
        existing_id = row[0]
        existing_container = sop.execute(
            "SELECT container_no FROM wagon_shipments WHERE id=?", (existing_id,),
        ).fetchone()[0]
        # for full-car (container_no = "A/B"), container_no param is None
        if container_no is None and "/" in (existing_container or ""):
            return True
        if container_no and existing_container and container_no in existing_container:
            return True
    return False


def build_row(
    rail_row: dict,
    car_no: str,
    container_no: str | None,
    containers_json: list[str],
    batch_id: str,
    cargo_count: int,
    weight: float,
    weight_basis: str,
) -> dict:
    """Construct a wagon_shipments INSERT dict."""
    return {
        "id": _row_id(),
        "departure_id": DEPARTURE_ID,
        "batch_id": batch_id,
        "car_no": car_no,
        "car_model": rail_row.get("car_model") or "",
        "cargo_name": rail_row.get("cargo_name") or "铁矿粉",
        "shipper_name": rail_row.get("shipper_name"),
        "consignee_name": rail_row.get("consignee_name"),
        "origin_name": rail_row.get("origin_name") or "高桥镇",
        "destination_name": rail_row.get("destination_name") or "四平",
        "ticketed_at": rail_row.get("ticketed_at") or "",
        "departed_at": rail_row.get("departed_at") or "",
        "arrived_at": rail_row.get("arrived_at") or "",
        "status_name": rail_row.get("status_name") or "已制单",
        "freight_fee": rail_row.get("freight_fee"),
        "detail_json": rail_row.get("detail_json"),
        "created_at": NOW_ISO,
        "updated_at": NOW_ISO,
        "delivered_at": rail_row.get("delivered_at"),
        "confirmed_received_at": rail_row.get("confirmed_received_at"),
        "container_no": container_no if container_no else "/".join(containers_json),
        "waybill_no": "",
        "project_id": "jilin_jingang_jinzhou",
        "ship_name": "蓝鳍",
        "dispatch_status": "ticketed",
        "source_message_id": "229",
        "source_group_id": "铁晟业务工作群",
        "ydid": rail_row.get("ydid") or "",
        "czydid": rail_row.get("czydid") or rail_row.get("ydid") or "",
        "transport_mode_code": rail_row.get("transport_mode_code") or "3",
        "transport_mode_name": rail_row.get("transport_mode_name") or "集装箱运输",
        "marked_weight": rail_row.get("marked_weight"),
        "cargo_count": cargo_count,
        "container_numbers_json": json.dumps([container_no] if container_no else containers_json,
                                             ensure_ascii=False),
        "accepted_at": rail_row.get("accepted_at") or "",
        "loaded_at": rail_row.get("loaded_at") or "",
        "latest_stage_key": rail_row.get("latest_stage_key") or "",
        "latest_stage_name": rail_row.get("latest_stage_name") or "",
        "latest_event_time": rail_row.get("latest_event_time") or "",
        "computed_loading_weight": weight,
        "weight_rule_basis": weight_basis,
    }


def main(apply: bool = False):
    sidecar = json.loads(SIDECAR.read_text(encoding="utf-8"))
    sop = sqlite3.connect(str(SOP_DB))
    rail = sqlite3.connect(str(RAIL_DB))

    pending_rows: list[dict] = []
    skipped: list[str] = []
    lot_counts = {"lot01": [0, 0.0, 0], "lot02": [0, 0.0, 0], "lot03": [0, 0.0, 0]}
    # [整车数, 吨数, 拆出额外箱行数]

    for assign in sidecar["wagon_assignments"]:
        car_no = assign["car_no"]
        containers = assign["containers"]
        ydid = assign["ydid"]
        rail_row = _load_95306_by_ydid(rail, ydid)
        if not rail_row:
            skipped.append(f"95306 ydid={ydid} not found for car {car_no}")
            continue

        for split in assign["split_to"]:
            container_no = split["container_no"]
            batch_id = split["batch_id"]
            lot = split["lot"]

            if container_no == "ALL":
                # 整车 — 2 箱合一行
                if _detect_existing(sop, ydid, None):
                    skipped.append(f"exist car={car_no} ydid={ydid} full")
                    continue
                row = build_row(
                    rail_row, car_no, container_no=None,
                    containers_json=containers,
                    batch_id=batch_id, cargo_count=2,
                    weight=64.6,
                    weight_basis="multiply(cargo_count=2*32.3)->64.6",
                )
                pending_rows.append(row)
                lot_counts[lot][0] += 1
                lot_counts[lot][1] += 64.6
            else:
                # 单箱拆行
                if _detect_existing(sop, ydid, container_no):
                    skipped.append(f"exist car={car_no} container={container_no}")
                    continue
                row = build_row(
                    rail_row, car_no, container_no=container_no,
                    containers_json=[container_no],
                    batch_id=batch_id, cargo_count=1,
                    weight=32.3,
                    weight_basis="multiply(cargo_count=1*32.3)->32.3 (per-container split)",
                )
                pending_rows.append(row)
                lot_counts[lot][2] += 1
                lot_counts[lot][1] += 32.3

    print(f"=== plan ===")
    print(f"  to insert: {len(pending_rows)} rows")
    print(f"  to skip:   {len(skipped)}")
    for s in skipped[:10]:
        print(f"    skip: {s}")
    print(f"  lot impact (整车数, 总吨数, 额外单箱行):")
    for lot, (c, w, x) in lot_counts.items():
        print(f"    {lot}  +{c} 车 / +{w:.1f}t / +{x} 单箱行")

    if not apply:
        print("\n(dry run, no writes — pass --apply to commit)")
        return

    print("\n=== APPLYING ===")
    cols = list(pending_rows[0].keys()) if pending_rows else []
    placeholders = ",".join("?" * len(cols))
    col_clause = ",".join(cols)
    insert_sql = f"INSERT INTO wagon_shipments ({col_clause}) VALUES ({placeholders})"
    for r in pending_rows:
        sop.execute(insert_sql, [r[c] for c in cols])

    # UPDATE release_batches
    for lot, (c, w, x) in lot_counts.items():
        bid = sidecar["lot_batch_ids"][lot]
        sop.execute(
            "UPDATE release_batches "
            "SET actual_wagon_count = COALESCE(actual_wagon_count,0) + ?, "
            "    shipped_weight_tons = COALESCE(shipped_weight_tons,0) + ?, "
            "    shipped_weight_last_computed_at = ?, "
            "    updated_at = ? "
            "WHERE id = ?",
            (c, w, NOW_ISO, NOW_ISO, bid),
        )

    # mark message_inbox 229
    rb_list = json.dumps(list(sidecar["lot_batch_ids"].values()), ensure_ascii=False)
    sop.execute(
        "UPDATE message_inbox SET db_action='manual_dispatch_applied', "
        "release_batch_id=?, summary=? || ' / applied 47 rows' "
        "WHERE id=229",
        (rb_list, sidecar["dispatch_event"]["text"]),
    )

    sop.commit()
    print(f"  inserted {len(pending_rows)} wagon_shipments rows")
    print(f"  updated 3 release_batches")
    print(f"  marked message_inbox 229")

    # 验证
    print("\n=== verify ===")
    for lot, bid in sidecar["lot_batch_ids"].items():
        r = sop.execute(
            "SELECT actual_wagon_count, shipped_weight_tons FROM release_batches WHERE id=?",
            (bid,)).fetchone()
        cnt = sop.execute(
            "SELECT COUNT(*) FROM wagon_shipments WHERE batch_id=?", (bid,)).fetchone()[0]
        print(f"  {lot}  actual_wagon_count={r[0]}  shipped_weight_tons={r[1]:.1f}  wagon_shipments rows={cnt}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    main(apply=a.apply)
