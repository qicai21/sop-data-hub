"""#issue-20260619 Fix C:无检装车通知单时,从 95306 反查权威车 → 合成检装车候选。

朝钢(chaoyang_steel)发车后,若有效检装车通知单图未到(没到 / 被 title 闸拦掉 /
OCR 垃圾),发车文本触发器靠本模块从 95306 直接捞权威车,合成一个 candidate,
让既有检验链跑到 ingest/match(**不自动上传鞍钢**,skip_upload)。

朝钢专属锚点:95306 托运人记事 tyrjzsx 含船名(见 95306-tyrjzsx-ship-anchor)。
按 dest + cargo + tyrjzsx含船名 + 触发时间窗 反查,得到权威车号集。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any


def _car_window(conn_rail: sqlite3.Connection, *, ship: str, dest: str,
                cargo_like: str, start: str, end: str) -> list[dict[str, Any]]:
    rows = conn_rail.execute(
        """
        SELECT car_no, ticketed_at, cargo_name,
               json_extract(raw_core_json,'$.tyrjzsx') AS tyrjzsx
        FROM shipments
        WHERE destination_name LIKE ? AND cargo_name LIKE ?
          AND ticketed_at>=? AND ticketed_at<=?
          AND json_extract(raw_core_json,'$.tyrjzsx') LIKE ?
        ORDER BY ticketed_at
        """,
        (f"%{dest}%", cargo_like, start, end, f"%{ship}%"),
    ).fetchall()
    return [{"car_no": str(r[0]).strip(), "ticketed_at": r[1],
             "cargo_name": r[2], "tyrjzsx": r[3]} for r in rows if r[0]]


def synthesize_candidate_from_95306(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    ship: str,
    dest: str,
    expected: int,
    trigger_ts: str,
    group_name: str,
    message_id: str,
    rail_db_path: str,
    cargo_like: str = "%铁矿%",
    hours_before: float = 3.0,
    hours_after: float = 9.0,
    count_tolerance: int = 4,
) -> dict[str, Any]:
    """从 95306 合成候选。返回 {status, candidate_id, car_nos, cargo_name, message}。

    status: ok / no_cars / count_mismatch / bad_trigger_ts。
    only ok 时才落库 candidate(candidate_status='candidate',payload_json 带真车号 rows)。
    """
    try:
        base = datetime.strptime(str(trigger_ts).replace("T", " ")[:19],
                                 "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return {"status": "bad_trigger_ts", "message": f"无法解析 trigger_ts={trigger_ts!r}"}

    start = (base - timedelta(hours=hours_before)).strftime("%Y-%m-%d %H:%M:%S")
    end = (base + timedelta(hours=hours_after)).strftime("%Y-%m-%d %H:%M:%S")

    rail = sqlite3.connect(str(rail_db_path))
    try:
        cars = _car_window(rail, ship=ship, dest=dest, cargo_like=cargo_like,
                           start=start, end=end)
    finally:
        rail.close()

    if not cars:
        return {"status": "no_cars", "car_nos": [],
                "message": f"95306 窗口[{start}~{end}] {dest} {ship} 无车(可能还没制票)"}

    car_nos = [c["car_no"] for c in cars]
    if expected and abs(len(car_nos) - expected) > count_tolerance:
        return {"status": "count_mismatch", "car_nos": car_nos,
                "message": f"95306 反查 {len(car_nos)} 车 vs 触发器预期 {expected}"
                           f"(差 {len(car_nos)-expected:+d} 超容差 {count_tolerance}),不合成"}

    cargo_name = (cars[0].get("cargo_name") or "").strip()
    # payload_json:rows 用 95306 真车号,链的窗口反推会拿它当锚点确认
    rows = [{"seq": i + 1, "car_no": c["car_no"], "defect": False,
             "cargo_info_raw": ship} for i, c in enumerate(cars)]
    payload = {
        "rows": rows,
        "footer": {"zhuangche_jieshu": len(car_nos), "paiche_jieshu": None},
        "meta": {"daoxian": "", "date": "", "jiancheyuan": ""},
        "source": "95306_synthesized",
        "ship_name": ship, "destination": dest, "project": project_id,
        "synth_window": [start, end],
    }
    cand_id = hashlib.sha1(
        f"95306synth|{project_id}|{ship}|{dest}|{trigger_ts}".encode()).hexdigest()

    conn.execute(
        """
        INSERT INTO inspection_ingestion_candidates (
          id, source_file_name, status, reason, group_name, message_id,
          project_id, ship_name, destination, cargo_name, candidate_status,
          wagon_count, car_numbers_json, payload_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
          candidate_status='candidate', payload_json=excluded.payload_json,
          wagon_count=excluded.wagon_count, car_numbers_json=excluded.car_numbers_json,
          cargo_name=excluded.cargo_name, updated_at=CURRENT_TIMESTAMP
        """,
        (cand_id, f"95306_synth:{ship}", "candidate",
         "synthesized_from_95306_no_inspection_notice", group_name, message_id,
         project_id, ship, dest, cargo_name, "candidate",
         len(car_nos), json.dumps(car_nos, ensure_ascii=False),
         json.dumps(payload, ensure_ascii=False), trigger_ts),
    )
    conn.commit()
    return {"status": "ok", "candidate_id": cand_id, "car_nos": car_nos,
            "cargo_name": cargo_name,
            "message": f"95306 反查合成候选 {len(car_nos)} 车({ship}→{dest})"}
