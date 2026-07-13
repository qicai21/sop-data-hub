"""九三早间日对账:晨报快照 + 95306/货票三方核对,每日成功一次。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from sop_hub.utils.time import now_iso_beijing

DB = REPO / "data" / "sop_agent.db"
JOB_NAME = "jiusan_morning_reconcile"


def _ensure_log(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS scheduled_reconcile_run_log (
            job_name TEXT NOT NULL, business_date TEXT NOT NULL, status TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}', started_at TEXT, finished_at TEXT,
            PRIMARY KEY(job_name, business_date)
        )"""
    )
    conn.commit()


def _snapshot_exists_today(conn: sqlite3.Connection, business_date: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM container_pool_snapshot WHERE project='jiusan' AND ship_name='九三大豆' "
        "AND snapshot_date=?", (business_date,)
    ).fetchone())


def run() -> dict:
    now = now_iso_beijing()
    business_date = now[:10]
    conn = sqlite3.connect(str(DB))
    try:
        _ensure_log(conn)
        row = conn.execute(
            "SELECT status, detail_json FROM scheduled_reconcile_run_log WHERE job_name=? AND business_date=?",
            (JOB_NAME, business_date),
        ).fetchone()
        if row and row[0] == "succeeded":
            return {"status": "skipped", "reason": "already_succeeded_today", "business_date": business_date}
        conn.execute(
            "INSERT INTO scheduled_reconcile_run_log(job_name,business_date,status,started_at) VALUES(?,?,?,?) "
            "ON CONFLICT(job_name,business_date) DO UPDATE SET status=excluded.status,started_at=excluded.started_at",
            (JOB_NAME, business_date, "running", now),
        )
        conn.commit()
    finally:
        conn.close()

    import jiusan_morning_report_ingest as morning
    morning.main(apply=True)

    conn = sqlite3.connect(str(DB))
    has_snapshot = _snapshot_exists_today(conn, business_date)
    conn.close()
    if not has_snapshot:
        detail = {"reason": "today_morning_report_not_available"}
        status = "waiting_morning_report"
    else:
        try:
            import run_reconcile
            # 缺货票 Excel 是正常业务状态:引擎记录 NEW_UNATTR,但本轮仍是成功执行。
            run_reconcile.run("jiusan", None, apply=True, mark=False, delete_phantom=False)
            detail = {"morning_snapshot": business_date, "excel_reconcile": "completed"}
            status = "succeeded"
        except Exception as exc:
            detail = {"reason": "excel_reconcile_exception", "error": str(exc)}
            status = "failed"

    finished = now_iso_beijing()
    conn = sqlite3.connect(str(DB))
    try:
        conn.execute(
            "UPDATE scheduled_reconcile_run_log SET status=?, detail_json=?, finished_at=? "
            "WHERE job_name=? AND business_date=?",
            (status, json.dumps(detail, ensure_ascii=False), finished, JOB_NAME, business_date),
        )
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"status": status, "business_date": business_date, **detail}, ensure_ascii=False))
    return {"status": status, "business_date": business_date, **detail}


if __name__ == "__main__":
    result = run()
    raise SystemExit(0 if result["status"] in {"succeeded", "skipped", "waiting_morning_report"} else 1)
