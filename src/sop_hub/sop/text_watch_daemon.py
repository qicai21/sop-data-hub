"""文本 ingest 接力 daemon — Phase 1 (wx-ops-agent 解耦).

定位:**message_inbox 的 consumer**,接 live_service 的产出做实际 ingest。

旧架构(2026-06-02 之前):
  wx-ops-agent media_resolver._classify_and_route_text
   → integrations/sop_data_hub.process_text_via_sop_data_hub
   → 同进程 BusinessDataAgent.ingest_business_text  ★ 跨仓 import + 同步阻塞

新架构(Phase 1 之后):
  sop-data-hub/scripts/run_live_service.py 已在做 → upsert message_inbox + text_router 分类
                                                                ↓
                                            本 daemon → SELECT 未 ingest 的 text 行
                                                     → BusinessDataAgent.ingest_business_text
                                                     → UPDATE inbox.db_action='text_ingest_*'

这样:
- wx 不再 import sop_hub
- live_service 一直在跑,产 inbox 行
- 本 daemon 独立轮询,只对未 ingest 的 text 做 ingest
- 互不阻塞,失败可独立 retry

判定"已 ingest":db_action 以 'text_ingest_' 开头 → skip。
ingest 完成:db_action='text_ingest_done' (有 records) / 'text_ingest_skipped'(无)
ingest 异常:db_action='text_ingest_error' + error_message。

启动:
  PYTHONPATH=src python3 -m sop_hub.sop.text_watch_daemon [--interval 5] [--db ...]
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


logger = logging.getLogger("sop_hub.text_watch")


DEFAULT_INTERVAL_SECONDS = 5
DEFAULT_DB = Path("data/sop_agent.db")


# ── DB layer ────────────────────────────────────────────────────────────


def _fetch_pending_text_rows(
    db_path: str | Path, *, limit: int = 200,
) -> list[dict[str, Any]]:
    """取 inbox 里尚未 ingest 的 text 行。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, message_id, text_content, processing_status, is_sop_msg, "
            "       sop_project_id, group_name "
            "FROM message_inbox "
            "WHERE msg_type='text' "
            "  AND text_content IS NOT NULL AND text_content != '' "
            "  AND (db_action IS NULL "
            "       OR (db_action NOT LIKE 'text_ingest_%' "
            "           AND db_action NOT LIKE '%text_ingest%')) "
            "ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _mark_ingest_result(
    db_path: str | Path,
    inbox_id: int,
    *,
    db_action: str,
    db_record_ids: list[str] | None = None,
    error_message: str = "",
) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE message_inbox SET "
            "  db_action=?, db_record_ids=?, error_message=?, "
            "  updated_at=datetime('now') "
            "WHERE id=?",
            (
                db_action,
                json.dumps(db_record_ids or [], ensure_ascii=False),
                error_message[:500] if error_message else "",
                inbox_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ── ingest layer ────────────────────────────────────────────────────────


def _ingest_one_row(row: dict[str, Any], *, db_path: str | Path) -> dict[str, Any]:
    """对单行调 BusinessDataAgent.ingest_business_text + 写回 inbox 状态。"""
    text = (row.get("text_content") or "").strip()
    inbox_id = row["id"]
    message_id = row.get("message_id", "")

    if not text:
        _mark_ingest_result(
            db_path, inbox_id, db_action="text_ingest_skipped",
            db_record_ids=[], error_message="empty_text",
        )
        return {"action": "skipped_empty", "message_id": message_id}

    try:
        from sop_hub.data_agent.agent import BusinessDataAgent
        agent = BusinessDataAgent()
        records = agent.ingest_business_text(text)
        record_ids = [r.id for r in records]
    except Exception as exc:
        logger.warning("ingest_business_text raised for %s: %s",
                       message_id, exc)
        _mark_ingest_result(
            db_path, inbox_id, db_action="text_ingest_error",
            error_message=str(exc),
        )
        return {"action": "error", "message_id": message_id, "error": str(exc)}

    if records:
        _mark_ingest_result(
            db_path, inbox_id, db_action="text_ingest_done",
            db_record_ids=record_ids,
        )
        return {
            "action": "ingested", "message_id": message_id,
            "record_count": len(records), "record_ids": record_ids,
        }
    else:
        _mark_ingest_result(
            db_path, inbox_id, db_action="text_ingest_skipped",
            db_record_ids=[],
        )
        return {
            "action": "skipped_no_records", "message_id": message_id,
        }


# ── main loop ───────────────────────────────────────────────────────────


def run_one_pass(
    *, db_path: str | Path, log_each: bool = False, batch_size: int = 200,
) -> dict[str, Any]:
    """跑一轮:扫 inbox pending text 行 → ingest 一批。"""
    counts = {
        "fetched": 0, "ingested": 0, "skipped": 0, "errors": 0,
    }
    rows = _fetch_pending_text_rows(db_path, limit=batch_size)
    counts["fetched"] = len(rows)
    for row in rows:
        result = _ingest_one_row(row, db_path=db_path)
        action = result.get("action", "")
        if action == "ingested":
            counts["ingested"] += 1
        elif action.startswith("skipped"):
            counts["skipped"] += 1
        elif action == "error":
            counts["errors"] += 1
        if log_each and action != "skipped_empty":
            logger.info(
                "inbox#%s msg=%s → %s (rec_count=%s)",
                row["id"], row.get("message_id"), action,
                result.get("record_count", 0),
            )
    return counts


_running = True


def _handle_signal(signum, _frame) -> None:
    global _running
    logger.info("received signal %s, stopping after current pass", signum)
    _running = False


def run_loop(
    *,
    db_path: str | Path = DEFAULT_DB,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    quiet: bool = False,
    once: bool = False,
    batch_size: int = 200,
) -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    import os
    logger.info(
        "text_watch_daemon started pid=%d db=%s interval=%ds batch=%d",
        os.getpid(), db_path, interval_seconds, batch_size,
    )

    pass_no = 0
    while _running:
        pass_no += 1
        try:
            counts = run_one_pass(
                db_path=db_path, log_each=not quiet, batch_size=batch_size,
            )
        except Exception as exc:
            logger.warning("pass %d failed: %s", pass_no, exc)
            counts = {"errors": 1, "fatal": str(exc)}
        non_silent = sum(counts.get(k, 0) for k in
                         ("ingested", "errors"))
        if non_silent or not quiet:
            shown = {k: v for k, v in counts.items() if v}
            logger.info("pass %d: %s", pass_no, shown or "{}")
        if once:
            break
        for _ in range(interval_seconds):
            if not _running:
                break
            time.sleep(1)

    logger.info("text_watch_daemon stopped after %d passes", pass_no)


# ── CLI ─────────────────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="sop-data-hub text ingest daemon (Phase 1 of wx-ops-agent decoupling).",
    )
    p.add_argument("--db", default=str(DEFAULT_DB),
                   help=f"path to sop_agent.db (default: {DEFAULT_DB})")
    p.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
                   help=f"poll interval (default: {DEFAULT_INTERVAL_SECONDS}s)")
    p.add_argument("--batch", type=int, default=200,
                   help="max rows per pass (default: 200)")
    p.add_argument("--once", action="store_true",
                   help="run one pass and exit")
    p.add_argument("--quiet", action="store_true",
                   help="only log when something happens")
    p.add_argument("--debug", action="store_true",
                   help="verbose debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_loop(
        db_path=args.db,
        interval_seconds=args.interval,
        quiet=args.quiet,
        once=args.once,
        batch_size=args.batch,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
