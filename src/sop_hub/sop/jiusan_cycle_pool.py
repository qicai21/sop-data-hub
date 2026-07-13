"""九三集装箱循环车体池的保守归列。"""
from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
from datetime import datetime
from typing import Iterable


PROJECT_ID = "jiusan"
POOL_SCOPE = "九三大豆"
WINDOW_GAP_HOURS = 2
MIN_NEW_CYCLE_CARS = 35
MAX_ATTACHED_UNKNOWN_CARS = 10


def _stable_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _group_windows(rows: Iterable[sqlite3.Row]) -> list[list[sqlite3.Row]]:
    groups: list[list[sqlite3.Row]] = []
    previous: datetime | None = None
    for row in rows:
        ticketed_at = row["ticketed_at"]
        current = datetime.strptime(ticketed_at, "%Y-%m-%d %H:%M:%S")
        if previous is None or (current - previous).total_seconds() > WINDOW_GAP_HOURS * 3600:
            groups.append([])
        groups[-1].append(row)
        previous = current
    return groups


def _upsert_pool_car(
    conn: sqlite3.Connection,
    *,
    car_no: str,
    car_model: str,
    cycle_no: int,
    ticketed_at: str,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO wagon_body_pool
          (id, project, ship_name, car_no, car_model, home_cycle_no, status,
           first_seen_date, last_seen_date, last_seen_cycle_no, dispatch_count,
           note, created_at, updated_at)
        VALUES (?,?,?,?,?,?, 'active', ?,?,?,1, ?,?,?)
        """,
        (
            _stable_hash(PROJECT_ID, car_no),
            PROJECT_ID,
            POOL_SCOPE,
            car_no,
            car_model,
            cycle_no,
            ticketed_at[:10],
            ticketed_at[:10],
            cycle_no,
            "auto_inferred_from_95306_window",
            now,
            now,
        ),
    )


def reconcile_recent_cycle_pool(
    conn: sqlite3.Connection,
    *,
    since: str,
    now: str,
) -> dict:
    """将近期可确定的九三循环车体补入项目级车体池。

    规则刻意保守:
    - 一列内已有唯一主列,且未知车不超过 10 辆,未知车跟随主列;
    - 整列(>=35车)全部未知时,登记为下一循环列;
    - 其余未知车保持未归列,不凭车号或船名臆测。
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT car_no, max(car_model) AS car_model, min(ticketed_at) AS ticketed_at
        FROM wagon_container_shipments
        WHERE project_id=?
          AND coalesce(ticketed_at, '') >= ?
          AND car_no IS NOT NULL AND car_no != ''
          AND coalesce(transport_mode_name, '') LIKE '%集装箱%'
        GROUP BY car_no, substr(ticketed_at, 1, 16)
        ORDER BY ticketed_at, car_no
        """,
        (PROJECT_ID, since),
    ).fetchall()
    pool_rows = conn.execute(
        """
        SELECT car_no, home_cycle_no
        FROM wagon_body_pool
        WHERE project=? AND home_cycle_no IS NOT NULL
        """,
        (PROJECT_ID,),
    ).fetchall()
    home_by_car = {row["car_no"]: int(row["home_cycle_no"]) for row in pool_rows}
    next_cycle = max(home_by_car.values(), default=0) + 1

    attached: list[tuple[str, int]] = []
    created: list[tuple[str, int]] = []
    unresolved: list[str] = []
    for window in _group_windows(rows):
        cars = {row["car_no"]: row for row in window}
        known = Counter(home_by_car[car_no] for car_no in cars if car_no in home_by_car)
        unknown = [car_no for car_no in cars if car_no not in home_by_car]
        if not unknown:
            continue

        target_cycle: int | None = None
        action = ""
        if len(known) == 1 and len(unknown) <= MAX_ATTACHED_UNKNOWN_CARS:
            target_cycle = next(iter(known))
            action = "attached"
        elif not known and len(unknown) >= MIN_NEW_CYCLE_CARS:
            target_cycle = next_cycle
            next_cycle += 1
            action = "created"

        if target_cycle is None:
            unresolved.extend(unknown)
            continue
        for car_no in unknown:
            row = cars[car_no]
            _upsert_pool_car(
                conn,
                car_no=car_no,
                car_model=row["car_model"] or "",
                cycle_no=target_cycle,
                ticketed_at=row["ticketed_at"],
                now=now,
            )
            home_by_car[car_no] = target_cycle
            (attached if action == "attached" else created).append((car_no, target_cycle))

    return {
        "attached_to_existing_cycle": attached,
        "created_new_cycle": created,
        "unresolved_car_nos": sorted(set(unresolved)),
    }
