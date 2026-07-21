"""Four-probe live 95306 tracking for Jiusan container cycle trains."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOP_DB = REPO_ROOT / "data" / "sop_agent.db"
DEFAULT_RAIL_REPO = Path.home() / "projects" / "repos" / "rail95306-sync"
DEFAULT_RAIL_DB = DEFAULT_RAIL_REPO / "runtime" / "95306_collection.sqlite3"
DEFAULT_CACHE = REPO_ROOT / "runtime" / "jiusan_cycle_tracking_latest.json"
DEFAULT_ACCOUNT = "xts"
RETURN_EMPTY_HOURS = 10
PROBES_PER_TRAIN = 4
TRANSFERRED_CYCLES = frozenset({4})


def _parse_time(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt, length in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%dT%H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%dT%H:%M", 16),
    ):
        try:
            return datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
    return None


def _open_readonly(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def discover_latest_cycle_trains(
    sop_db: str | Path = DEFAULT_SOP_DB,
    *,
    transferred_cycles: set[int] | frozenset[int] = TRANSFERRED_CYCLES,
) -> list[dict[str, Any]]:
    """Return the latest ticketed/departed trip for each active cycle."""
    conn = _open_readonly(sop_db)
    try:
        rows = conn.execute(
            """
            SELECT wbp.home_cycle_no cycle_no, wcs.ship_name, wcs.ydid,
                   wcs.car_no, wcs.ticketed_at, wcs.departed_at
            FROM wagon_container_shipments wcs
            JOIN wagon_body_pool wbp
              ON wbp.project='jiusan' AND wbp.car_no=wcs.car_no
            WHERE wcs.project_id='jiusan'
              AND wbp.home_cycle_no IS NOT NULL
              AND COALESCE(wcs.ydid,'')!=''
              AND COALESCE(wcs.ticketed_at,'')!=''
            ORDER BY wbp.home_cycle_no, wcs.ticketed_at, wcs.ydid
            """
        ).fetchall()
    finally:
        conn.close()

    by_cycle: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        cycle_no = int(row["cycle_no"])
        if cycle_no in transferred_cycles:
            continue
        by_cycle.setdefault(cycle_no, []).append(dict(row))

    trains: list[dict[str, Any]] = []
    for cycle_no, cycle_rows in sorted(by_cycle.items()):
        # One ydid has two box rows in the fact table; collapse to one car.
        unique = {str(r["ydid"]): r for r in cycle_rows}
        cycle_rows = list(unique.values())
        departed_values = [str(r.get("departed_at") or "") for r in cycle_rows if r.get("departed_at")]
        latest_departed = max(departed_values, default="")

        pending = [
            r for r in cycle_rows
            if not r.get("departed_at") and str(r.get("ticketed_at") or "") > latest_departed
        ]
        if pending:
            latest_ticket = max(str(r.get("ticketed_at") or "") for r in pending)
            latest_dt = _parse_time(latest_ticket)
            if latest_dt:
                pending = [
                    r for r in pending
                    if (_parse_time(str(r.get("ticketed_at") or "")) or datetime.min)
                    >= latest_dt - timedelta(hours=6)
                ]
            selected = pending
            trip_key = f"ticketed:{latest_ticket[:16]}"
        elif latest_departed:
            selected = [
                r for r in cycle_rows
                if str(r.get("departed_at") or "")[:16] == latest_departed[:16]
            ]
            trip_key = f"departed:{latest_departed[:16]}"
        else:
            latest_ticket = max(str(r.get("ticketed_at") or "") for r in cycle_rows)
            selected = [r for r in cycle_rows if str(r.get("ticketed_at") or "")[:10] == latest_ticket[:10]]
            trip_key = f"ticketed:{latest_ticket[:16]}"

        if not selected:
            continue
        selected.sort(key=lambda r: (str(r.get("ticketed_at") or ""), str(r.get("ydid") or "")))
        trains.append(
            {
                "cycle_no": cycle_no,
                "trip_key": trip_key,
                "ship_name": str(selected[-1].get("ship_name") or ""),
                "ticketed_at": min(str(r.get("ticketed_at") or "") for r in selected),
                "departed_at": max(str(r.get("departed_at") or "") for r in selected),
                "car_count": len(selected),
                "cars": selected,
            }
        )
    return trains


def load_previous_outbound_by_car(
    train: dict[str, Any],
    *,
    sop_db: str | Path = DEFAULT_SOP_DB,
    rail_db: str | Path = DEFAULT_RAIL_DB,
) -> dict[str, str]:
    """Load each current car's latest prior 95306 unloading-outbound time."""
    cars = [str(r["car_no"]) for r in train.get("cars") or []]
    current_ydids = {str(r["ydid"]) for r in train.get("cars") or []}
    if not cars or not Path(rail_db).exists():
        return {}
    conn = _open_readonly(sop_db)
    try:
        conn.execute("ATTACH DATABASE ? AS rail", (f"file:{Path(rail_db)}?mode=ro",))
        car_ph = ",".join("?" for _ in cars)
        ydid_ph = ",".join("?" for _ in current_ydids)
        sql = f"""
            WITH history AS (
              SELECT w.car_no, w.ydid, w.ticketed_at,
                     json_extract(r.loading_unloading_timeline_json,'$.xcdcsj') outbound,
                     row_number() OVER (PARTITION BY w.car_no ORDER BY w.ticketed_at DESC) rn
              FROM wagon_container_shipments w
              JOIN rail.shipments r ON r.ydid=w.ydid
              WHERE w.project_id='jiusan'
                AND w.car_no IN ({car_ph})
                AND w.ydid NOT IN ({ydid_ph})
                AND json_extract(r.loading_unloading_timeline_json,'$.xcdcsj') IS NOT NULL
            )
            SELECT car_no, outbound FROM history WHERE rn=1
        """
        params = [*cars, *sorted(current_ydids)]
        return {str(r[0]): str(r[1]) for r in conn.execute(sql, params).fetchall()}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def select_four_probes(
    cars: Sequence[dict[str, Any]],
    previous_outbound_by_car: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Select two probes per historical half; fall back to four train quartiles."""
    ordered = sorted(cars, key=lambda r: (str(r.get("ticketed_at") or ""), str(r.get("ydid") or "")))
    if not ordered:
        return [], "none"
    if len(ordered) <= PROBES_PER_TRAIN:
        return [dict(r, probe_role=f"probe_{i + 1}") for i, r in enumerate(ordered)], "all_available"

    history = previous_outbound_by_car or {}
    with_history = [r for r in ordered if history.get(str(r.get("car_no") or ""))]
    if len(with_history) >= PROBES_PER_TRAIN:
        by_outbound = sorted(
            with_history,
            key=lambda r: (history[str(r.get("car_no") or "")], str(r.get("ticketed_at") or "")),
        )
        split = max(1, len(by_outbound) // 2)
        halves = (by_outbound[:split], by_outbound[split:])
        if halves[0] and halves[1]:
            selected: list[dict[str, Any]] = []
            for half_name, half in zip(("half_a", "half_b"), halves):
                half_ordered = sorted(
                    half,
                    key=lambda r: (str(r.get("ticketed_at") or ""), str(r.get("ydid") or "")),
                )
                picks = (half_ordered[0], half_ordered[-1])
                for edge, row in zip(("first", "last"), picks):
                    selected.append(dict(row, probe_role=f"{half_name}_{edge}"))
            # A very short cohort can choose the same car twice; de-duplicate, then fill below.
            selected = list({str(r["ydid"]): r for r in selected}.values())
            if len(selected) == PROBES_PER_TRAIN:
                return selected, "previous_outbound_halves"

    n = len(ordered)
    indexes = sorted({0, max(0, n // 2 - 1), min(n - 1, n // 2), n - 1})
    while len(indexes) < min(PROBES_PER_TRAIN, n):
        indexes = sorted(set(indexes) | {round((len(indexes)) * (n - 1) / 3)})
    roles = ("train_first", "front_half_last", "rear_half_first", "train_last")
    return [dict(ordered[idx], probe_role=roles[i]) for i, idx in enumerate(indexes[:4])], "quartiles"


def classify_train_state(
    samples: Sequence[dict[str, Any]],
    *,
    now: datetime | None = None,
    return_empty_hours: int = RETURN_EMPTY_HOURS,
) -> dict[str, Any]:
    """Collapse four probe timelines into one conservative cycle-train state."""
    now = now or datetime.now()
    valid = [s for s in samples if not s.get("error")]
    if not valid:
        return {"node_key": "unknown", "node_label": "轨迹查询失败", "state_at": ""}

    outbound = [_parse_time(str(s.get("unloading_outbound") or "")) for s in valid]
    if len(valid) == len(samples) and samples and all(outbound):
        final_out = max(t for t in outbound if t is not None)
        return_at = final_out + timedelta(hours=return_empty_hours)
        if now < return_at:
            return {
                "node_key": "transit_empty",
                "node_label": "在途(空)",
                "state_at": final_out.isoformat(sep=" ", timespec="seconds"),
                "next_transition_at": return_at.isoformat(sep=" ", timespec="seconds"),
            }
        return {
            "node_key": "port_loaded",
            "node_label": "返港待装",
            "state_at": return_at.isoformat(sep=" ", timespec="seconds"),
        }

    if any(s.get("unloading_inbound") for s in valid):
        times = [
            _parse_time(str(s.get("unloading_inbound") or ""))
            for s in valid if s.get("unloading_inbound")
        ]
        return {
            "node_key": "ground330",
            "node_label": "三三零线上作业",
            "state_at": min(t for t in times if t is not None).isoformat(sep=" ", timespec="seconds"),
        }
    if any(s.get("arrived_at") for s in valid):
        times = [_parse_time(str(s.get("arrived_at") or "")) for s in valid if s.get("arrived_at")]
        return {
            "node_key": "xtz",
            "node_label": "新台子到站等待",
            "state_at": min(t for t in times if t is not None).isoformat(sep=" ", timespec="seconds"),
        }
    if any(s.get("departed_at") for s in valid):
        times = [_parse_time(str(s.get("departed_at") or "")) for s in valid if s.get("departed_at")]
        return {
            "node_key": "transit_loaded",
            "node_label": "在途(重)",
            "state_at": min(t for t in times if t is not None).isoformat(sep=" ", timespec="seconds"),
        }
    if any(s.get("ticketed_at") for s in valid):
        times = [_parse_time(str(s.get("ticketed_at") or "")) for s in valid if s.get("ticketed_at")]
        return {
            "node_key": "port_loaded",
            "node_label": "港内制票/待发",
            "state_at": min(t for t in times if t is not None).isoformat(sep=" ", timespec="seconds"),
        }
    return {"node_key": "unknown", "node_label": "轨迹节点未知", "state_at": ""}


def _live_query_factory(account: str = DEFAULT_ACCOUNT) -> Callable[[dict[str, Any]], dict[str, Any]]:
    if str(DEFAULT_RAIL_REPO) not in sys.path:
        sys.path.insert(0, str(DEFAULT_RAIL_REPO))
    from query95306.shipment_query import QueryInput, ShipmentQueryClient  # type: ignore

    client = ShipmentQueryClient(account)

    def query(row: dict[str, Any]) -> dict[str, Any]:
        anchor = _parse_time(str(row.get("ticketed_at") or row.get("departed_at") or "")) or datetime.now()
        query_input = QueryInput(
            start_date=(anchor - timedelta(days=2)).date().isoformat(),
            end_date=(datetime.now() + timedelta(days=1)).date().isoformat(),
            origin_tmis="51632",
            destination_tmis="53918",
            shipment_id=str(row["ydid"]),
            page_num=1,
            page_size=5,
            result_limit=5,
        )
        response = client.query_send_legacy(query_input)
        records = (response.get("body", {}).get("data", {}) or {}).get("list", []) or []
        record = next((r for r in records if str(r.get("ydid") or r.get("czydid")) == str(row["ydid"])), None)
        if record is None:
            raise RuntimeError(f"95306 未返回 ydid={row['ydid']}")
        return {
            "ticketed_at": record.get("zpsj") or row.get("ticketed_at") or "",
            "departed_at": record.get("fcsj") or row.get("departed_at") or "",
            "arrived_at": record.get("dzsj") or "",
            "unloading_inbound": record.get("xcddsj") or "",
            "unloading_outbound": record.get("xcdcsj") or "",
            "status_code": record.get("ydztgj") or "",
            "queried_status": record.get("ydztgjmc") or record.get("ztgjjc") or "",
        }

    return query


def _read_cache(path: str | Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def update_cycle_tracking_cache(
    *,
    sop_db: str | Path = DEFAULT_SOP_DB,
    rail_db: str | Path = DEFAULT_RAIL_DB,
    cache_path: str | Path = DEFAULT_CACHE,
    account: str = DEFAULT_ACCOUNT,
    query_one: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    now: datetime | None = None,
    sleep_seconds: float = 0.35,
) -> dict[str, Any]:
    """Query at most four live 95306 shipment records per active cycle and cache them."""
    now = now or datetime.now()
    query_one = query_one or _live_query_factory(account)
    previous_cache = _read_cache(cache_path)
    previous_trains = {str(t.get("cycle_no")): t for t in previous_cache.get("trains") or []}
    trains_out: list[dict[str, Any]] = []
    total_queries = 0
    total_errors = 0

    for train in discover_latest_cycle_trains(sop_db):
        history = load_previous_outbound_by_car(train, sop_db=sop_db, rail_db=rail_db)
        probes, strategy = select_four_probes(train["cars"], history)
        old_train = previous_trains.get(str(train["cycle_no"])) or {}
        old_samples = {str(s.get("ydid")): s for s in old_train.get("samples") or []}
        samples: list[dict[str, Any]] = []
        for index, probe in enumerate(probes):
            sample = {
                "probe_role": probe.get("probe_role") or f"probe_{index + 1}",
                "ydid": str(probe["ydid"]),
                "car_no": str(probe["car_no"]),
            }
            try:
                sample.update(query_one(probe))
                sample["queried_at"] = now.isoformat(sep=" ", timespec="seconds")
            except Exception as exc:  # one failed probe must not erase a good same-trip snapshot
                total_errors += 1
                old = old_samples.get(str(probe["ydid"]))
                if old and old_train.get("trip_key") == train["trip_key"]:
                    sample.update(old)
                    sample["error"] = str(exc)
                    sample["stale_sample"] = True
                else:
                    sample["error"] = str(exc)
            samples.append(sample)
            total_queries += 1
            if sleep_seconds and index < len(probes) - 1:
                time.sleep(sleep_seconds)

        state = classify_train_state(samples, now=now)
        trains_out.append(
            {
                "cycle_no": train["cycle_no"],
                "trip_key": train["trip_key"],
                "ship_name": train["ship_name"],
                "car_count": train["car_count"],
                "sample_count": len(samples),
                "sample_strategy": strategy,
                "samples": samples,
                **state,
            }
        )

    report = {
        "version": 1,
        "generated_at": now.isoformat(sep=" ", timespec="seconds"),
        "account": account,
        "interval_seconds": 7200,
        "return_empty_hours": RETURN_EMPTY_HOURS,
        "query_count": total_queries,
        "error_count": total_errors,
        "trains": trains_out,
    }
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return report
