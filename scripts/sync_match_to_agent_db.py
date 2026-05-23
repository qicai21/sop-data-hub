#!/usr/bin/env python3
"""
sync_match_to_agent_db(release_batch_id)

把 rail DB (95306_collection.sqlite3) 中 shipment_release_batch_matches 的
已确认匹配数据，同步写入 agent.db 的 departure_records + wagon_shipments。

不经过 reconciler 门槛。
已存在的 departure_records / wagon_shipments 不会被重复写入。

用法:
    python scripts/sync_match_to_agent_db.py <release_batch_id>
    python scripts/sync_match_to_agent_db.py --all   # 同步所有活跃批次
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


def _agent_db_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "agent.db")


def _rail_db_path() -> str:
    return os.path.expanduser(
        "~/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3"
    )


def sync_match_to_agent_db(
    release_batch_id: str,
    *,
    agent_db: Optional[str] = None,
    rail_db: Optional[str] = None,
) -> dict:
    """
    将 shipment_release_batch_matches 中指定批次的匹配数据
    同步到 agent.db 的 departure_records + wagon_shipments。

    返回:
        {
            "release_batch_id": str,
            "synced": bool,
            "departure_record_id": str | None,
            "wagon_count": int,
            "cars_synced": int,
            "skipped_reason": str | None,
        }
    """
    agent_path = agent_db or _agent_db_path()
    rail_path = rail_db or _rail_db_path()

    agent = sqlite3.connect(agent_path)
    agent.row_factory = sqlite3.Row
    rail = sqlite3.connect(rail_path)
    rail.row_factory = sqlite3.Row

    # 1. 检查 release_batch 是否存在
    batch = agent.execute(
        "SELECT * FROM release_batches WHERE id=?", (release_batch_id,)
    ).fetchone()
    if not batch:
        agent.close()
        rail.close()
        return {"release_batch_id": release_batch_id, "synced": False, "skipped_reason": "release_batch_not_found"}

    # 2. 检查是否已有 departure_record（避免重复写入）
    existing_dep = agent.execute(
        "SELECT id, wagon_count FROM departure_records WHERE batch_id=?",
        (release_batch_id,)
    ).fetchone()

    # 3. 从 rail DB 读取匹配数据
    matches = rail.execute(
        """SELECT * FROM shipment_release_batch_matches
           WHERE release_batch_id=?
           ORDER BY inspection_row""",
        (release_batch_id,)
    ).fetchall()

    if not matches:
        agent.close()
        rail.close()
        return {"release_batch_id": release_batch_id, "synced": False, "skipped_reason": "no_matches_in_rail_db"}

    car_nos = [m["shipment_car_no"] for m in matches]
    car_models = {m["shipment_car_no"]: m["shipment_car_model"] or "" for m in matches}
    cargo_name = (matches[0]["cargo_name"] or batch["cargo_name"] or "").strip()
    origin = (matches[0]["origin_name"] or (batch["origin_station"] if "origin_station" in batch.keys() else "") or "").strip()
    dest = (matches[0]["destination_name"] or batch["destination_station"] or "").strip()
    earliest_ticket = min(m["ticketed_at"] or "" for m in matches)
    departure_date = matches[0]["release_batch_date"] or (batch["batch_date"] if "batch_date" in batch.keys() else "") or ""

    wagon_count = len(car_nos)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ship_name = batch["ship_name"]
    project = batch["project"] or "unknown"
    plan_id = (batch["plan_id"] if "plan_id" in batch.keys() else "") or ""
    contract_no = (batch["contract_no"] if "contract_no" in batch.keys() else "") or ""

    # 4. 写入 departure_records（如果不存在）
    if existing_dep:
        departure_id = existing_dep["id"]
        agent.execute(
            """UPDATE departure_records
               SET wagon_count=?, car_nos=?, updated_at=?
               WHERE id=?""",
            (wagon_count, ",".join(car_nos), now, departure_id)
        )
    else:
        depot_tag = ship_name.replace(" ", "_")[:20]
        departure_id = f"dep_{depot_tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        agent.execute(
            """INSERT INTO departure_records
               (id, batch_id, ship_name, cargo_name, plan_id, contract_no,
                departure_date, wagon_count, car_nos, doc_time, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                departure_id,
                release_batch_id,
                ship_name,
                cargo_name,
                plan_id,
                contract_no,
                departure_date or "",
                wagon_count,
                ",".join(car_nos),
                earliest_ticket or "",
                now,
                now,
            )
        )

    # 5. 写入 wagon_shipments（逐车写入，跳过已存在的）
    existing_cars = set()
    if existing_dep:
        ec = agent.execute(
            "SELECT car_no FROM wagon_shipments WHERE batch_id=?",
            (release_batch_id,)
        ).fetchall()
        existing_cars = {r["car_no"] for r in ec}

    cars_synced = 0
    for match in matches:
        car_no = match["shipment_car_no"]
        if car_no in existing_cars:
            continue
        wid = uuid.uuid4().hex
        agent.execute(
            """INSERT INTO wagon_shipments
               (id, departure_id, batch_id, car_no, car_model, cargo_name,
                origin_name, destination_name, ticketed_at, status_name, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                wid, departure_id, release_batch_id, car_no,
                car_models.get(car_no, match["shipment_car_model"] or ""),
                cargo_name, origin, dest,
                match["ticketed_at"] or "",
                match["status_name"] or "",
                now, now,
            )
        )
        cars_synced += 1

    # 6. 更新 release_batches.actual_wagon_count
    agent.execute(
        "UPDATE release_batches SET actual_wagon_count=?, updated_at=? WHERE id=?",
        (wagon_count, now, release_batch_id)
    )

    agent.commit()
    agent.close()
    rail.close()

    return {
        "release_batch_id": release_batch_id,
        "synced": True,
        "departure_record_id": departure_id,
        "wagon_count": wagon_count,
        "cars_synced": cars_synced,
        "cars_skipped": len(existing_cars) if existing_dep else 0,
        "skipped_reason": None,
    }


def main():
    parser = argparse.ArgumentParser(description="Sync rail DB matches to agent.db")
    parser.add_argument("release_batch_id", nargs="?", help="release_batch ID to sync")
    parser.add_argument("--all", action="store_true", help="Sync all active batches")
    parser.add_argument("--agent-db", default=None)
    parser.add_argument("--rail-db", default=None)
    args = parser.parse_args()

    if args.all:
        agent = sqlite3.connect(args.agent_db or _agent_db_path())
        agent.row_factory = sqlite3.Row
        batches = agent.execute(
            "SELECT id, ship_name FROM release_batches WHERE dispatch_status IN ('in_progress','pending')"
        ).fetchall()
        agent.close()
        for b in batches:
            result = sync_match_to_agent_db(b["id"], agent_db=args.agent_db, rail_db=args.rail_db)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.release_batch_id:
        result = sync_match_to_agent_db(args.release_batch_id, agent_db=args.agent_db, rail_db=args.rail_db)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
