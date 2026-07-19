"""Periodic 95306 → sop status sync for active release batches.

Discovers (project_id, ship_name) pairs that still need tracking and runs
ShipmentStatusSync for each. Intended for CLI / launchd, not every text-watch
tick (too heavy).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from sop_hub.sop.shipment_status_sync import ShipmentStatusSync

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOP_DB = REPO_ROOT / "data" / "sop_agent.db"
DEFAULT_RAIL_DB = (
    Path.home() / "projects" / "repos" / "rail95306-sync" / "runtime" / "95306_collection.sqlite3"
)
DEFAULT_REPORT_DIR = REPO_ROOT / "runtime" / "status_sync_reports"

# Batches still in motion — need stage/status refresh from 95306.
_ACTIVE_STATUSES: tuple[str, ...] = (
    "pending_freight",
    "enriched",
    "loading",
    "all_loaded",
    "tracking",
    "delivered",
)


@dataclass(frozen=True)
class ActiveShip:
    project_id: str
    ship_name: str
    lot_count: int
    statuses: str


@dataclass
class ShipSyncOutcome:
    project_id: str
    ship_name: str
    total_units: int = 0
    matched: int = 0
    unmatched: int = 0
    update_count: int = 0
    departed: int = 0
    arrived: int = 0
    delivered: int = 0
    error: str = ""


@dataclass
class ActiveStatusSyncReport:
    started_at: str
    finished_at: str = ""
    dry_run: bool = True
    run_closeout: bool = False
    ships: list[ShipSyncOutcome] = field(default_factory=list)
    closeout: dict[str, Any] = field(default_factory=dict)
    report_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dry_run": self.dry_run,
            "run_closeout": self.run_closeout,
            "ship_count": len(self.ships),
            "ships": [asdict(s) for s in self.ships],
            "closeout": self.closeout,
            "report_path": self.report_path,
            "totals": {
                "units": sum(s.total_units for s in self.ships),
                "matched": sum(s.matched for s in self.ships),
                "updates": sum(s.update_count for s in self.ships),
                "errors": sum(1 for s in self.ships if s.error),
            },
        }


def discover_active_ships(
    conn: sqlite3.Connection,
    *,
    projects: Sequence[str] | None = None,
    statuses: Sequence[str] = _ACTIVE_STATUSES,
) -> list[ActiveShip]:
    """Return distinct (project, ship) with at least one active-status lot."""
    ph = ",".join("?" * len(statuses))
    sql = (
        "SELECT project, ship_name, COUNT(*) AS lot_count, "
        "GROUP_CONCAT(DISTINCT dispatch_status) AS statuses "
        f"FROM release_batches WHERE dispatch_status IN ({ph}) "
        "AND COALESCE(ship_name,'') != '' "
    )
    params: list[Any] = list(statuses)
    if projects:
        pph = ",".join("?" * len(projects))
        sql += f"AND project IN ({pph}) "
        params.extend(projects)
    sql += "GROUP BY project, ship_name ORDER BY project, ship_name"
    rows = conn.execute(sql, params).fetchall()
    out: list[ActiveShip] = []
    for r in rows:
        proj = str(r[0] or "").strip()
        ship = str(r[1] or "").strip()
        if not proj or not ship:
            continue
        out.append(
            ActiveShip(
                project_id=proj,
                ship_name=ship,
                lot_count=int(r[2] or 0),
                statuses=str(r[3] or ""),
            )
        )
    return out


def run_active_status_sync(
    *,
    sop_db: str | Path = DEFAULT_SOP_DB,
    rail_db: str | Path = DEFAULT_RAIL_DB,
    dry_run: bool = True,
    run_closeout: bool = False,
    projects: Sequence[str] | None = None,
    ships: Sequence[tuple[str, str]] | None = None,
    report_dir: str | Path | None = DEFAULT_REPORT_DIR,
) -> ActiveStatusSyncReport:
    """Sync all active ships (or an explicit list). Writes a JSON report."""
    started = datetime.now().isoformat(timespec="seconds")
    report = ActiveStatusSyncReport(started_at=started, dry_run=dry_run, run_closeout=run_closeout)
    sop_path = Path(sop_db)
    rail_path = Path(rail_db)
    if not rail_path.exists():
        report.finished_at = datetime.now().isoformat(timespec="seconds")
        report.ships.append(
            ShipSyncOutcome(project_id="", ship_name="", error=f"rail_db_not_found:{rail_path}")
        )
        return _write_report(report, report_dir)

    syncer = ShipmentStatusSync(sop_db_path=sop_path, rail_db_path=rail_path)
    targets: list[tuple[str, str]] = []
    if ships:
        targets = [(p, s) for p, s in ships]
    else:
        conn = sqlite3.connect(str(sop_path))
        try:
            active = discover_active_ships(conn, projects=projects)
        finally:
            conn.close()
        targets = [(a.project_id, a.ship_name) for a in active]

    for project_id, ship_name in targets:
        outcome = ShipSyncOutcome(project_id=project_id, ship_name=ship_name)
        try:
            result = syncer.sync(
                project_id=project_id,
                ship_name=ship_name,
                dry_run=dry_run,
            )
            outcome.total_units = result.total_wagons
            outcome.matched = result.matched_count
            outcome.unmatched = result.unmatched_count
            outcome.update_count = result.update_count
            outcome.departed = result.departed_update_count
            outcome.arrived = result.arrived_update_count
            outcome.delivered = result.delivered_update_count
        except Exception as exc:  # noqa: BLE001 — batch loop must not abort
            outcome.error = str(exc)
        report.ships.append(outcome)

    if run_closeout and not dry_run:
        try:
            from sop_hub.sop.lifecycle_closeout import run_lifecycle_closeout

            co = run_lifecycle_closeout(db_path=str(sop_path))
            # Drop bulky lists in report; keep counters
            report.closeout = {
                k: v for k, v in co.items() if k not in ("advances", "rejected")
            }
            if co.get("advances"):
                report.closeout["advanced_batches"] = [
                    {
                        "batch_id": a.get("batch_id"),
                        "from": a.get("from_phase"),
                        "to": a.get("to_phase"),
                    }
                    for a in co.get("advances") or []
                ]
        except Exception as exc:  # noqa: BLE001
            report.closeout = {"error": str(exc)}

    report.finished_at = datetime.now().isoformat(timespec="seconds")
    return _write_report(report, report_dir)


def _write_report(
    report: ActiveStatusSyncReport,
    report_dir: str | Path | None,
) -> ActiveStatusSyncReport:
    if not report_dir:
        return report
    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    mode = "dry" if report.dry_run else "apply"
    path = out_dir / f"active_status_sync_{mode}_{stamp}.json"
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    # also latest pointer
    latest = out_dir / f"active_status_sync_{mode}_latest.json"
    latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    report.report_path = str(path)
    return report
