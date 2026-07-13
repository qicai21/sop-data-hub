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

# 内部安全 task_type:只写本地 sop_agent.db,**无任何对外提交** → daemon 每轮
# 默认自动 apply,不需要任何 flag。#96 freight_detail_enrichment(填
# cargo_product_name)属于这类,所以 "自动 enrich" 名副其实。
AUTO_SAFE_TASK_TYPES = (
    "freight_detail_enrichment",
    # 九三内部群单船发车文本只写本地分票台账和同步库,不发微信/不传门户。
    "jiusan_departure_text_reconcile",
)

# 含对外提交(工厂上传:吉林金钢 / 朝阳鞍钢)的 task_type → 必须 --run-chains 显式
# 开启;#98 一体化后开了就是真提交(幂等由 external_action_log 守)。
# create_release_batch 虽是内部写库,但属核心建批链,保守起见同样放在显式开启集合。
EXTERNAL_CHAIN_TASK_TYPES = (
    "jljg_departure_text_chain",
    "chaoyang_inspection_chain",
    "zhongtang_inspection_chain",
    "create_release_batch",
    # #143:文本触发器(扇出后 task_type 后缀船名)。末尾 ':' = 前缀匹配整族。
    # 委托检验链含工厂上传 → 归 --run-chains 门控。
    "inspection_text_trigger:",
)


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
            "       sop_project_id, sop_flow, sop_node, group_name "
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


def _count_unrouted_sop_rows(db_path: str | Path) -> int:
    """matched_sop 行里还没有对应 workflow_task 的数量。

    用作 backfill 触发条件:稳态下为 0,只在有遗留/新漏网行时才真正 backfill,
    避免每轮都对全部 matched_sop 行空跑一遍 create_task。"""
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM message_inbox mi "
                "LEFT JOIN workflow_task_db wt ON wt.message_inbox_id = mi.id "
                "WHERE mi.processing_status='matched_sop' AND mi.is_sop_msg=1 "
                "  AND wt.id IS NULL",
            ).fetchone()[0]
        except sqlite3.OperationalError:
            # workflow_task_db 还没建表 → 视为无需 backfill
            return 0
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

    # SOP-classified messages (出港通知/检装车通知 等) are *chain triggers*, not
    # freight-detail text. Route them straight into the workflow_task pipeline
    # instead of BusinessDataAgent.ingest_business_text — which returns no
    # records for them (→ text_ingest_skipped) and the chain never starts.
    # This was the B0 root cause: detect_departure_message rows were dead-ended.
    if (
        row.get("is_sop_msg")
        and (row.get("processing_status") or "") == "matched_sop"
        and (row.get("sop_node") or "")
    ):
        try:
            from sop_hub.sop.workflow_task_store import create_task_from_message_inbox
            res = create_task_from_message_inbox(inbox_id, db_path=db_path)
        except Exception as exc:
            logger.warning("create_task_from_message_inbox failed for %s: %s",
                           inbox_id, exc)
            _mark_ingest_result(
                db_path, inbox_id, db_action="text_ingest_error",
                error_message=str(exc),
            )
            return {"action": "error", "message_id": message_id, "error": str(exc)}
        # #143:复合文本触发器扇出成多个 task → db_record_ids 记全部 id
        if res.get("action") == "created_multi":
            ids = [str(i) for i in (res.get("ids") or [])]
            _mark_ingest_result(
                db_path, inbox_id, db_action="text_ingest_workflow_task",
                db_record_ids=ids,
            )
            return {
                "action": "workflow_tasks_created", "message_id": message_id,
                "task_ids": ids, "task_type": res.get("task_type"),
                "created": len(ids),
            }
        task_id = res.get("id")
        # db_action keeps the 'text_ingest_' prefix so the fetch query treats the
        # row as processed and won't re-pick it next pass.
        _mark_ingest_result(
            db_path, inbox_id, db_action="text_ingest_workflow_task",
            db_record_ids=[str(task_id)] if task_id else [],
        )
        return {
            "action": "workflow_task_created", "message_id": message_id,
            "task_id": task_id, "task_type": res.get("task_type"),
            "created": res.get("action") == "created",
        }

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
    *,
    db_path: str | Path,
    log_each: bool = False,
    batch_size: int = 200,
    run_chains: bool = False,
    chain_limit: int = 10,
) -> dict[str, Any]:
    """跑一轮:

    1. 扫 inbox pending text 行 → 货运明细 ingest / SOP 行建 workflow_task。
    2. backfill:补建遗留 matched_sop 行的 workflow_task(只在有漏网行时)。
    3. 默认自动跑内部安全链(freight enrichment,无对外提交)。
    4. run_chains=True:额外跑含对外提交的链(吉林工厂上传/朝阳)——#98 一体化:
       跑就真跑真提交,幂等由 external_action_log 守。默认 run_chains=False 不跑。
    """
    counts = {
        "fetched": 0, "ingested": 0, "skipped": 0, "errors": 0,
        "sop_routed": 0,
    }
    rows = _fetch_pending_text_rows(db_path, limit=batch_size)
    counts["fetched"] = len(rows)
    for row in rows:
        result = _ingest_one_row(row, db_path=db_path)
        action = result.get("action", "")
        if action == "ingested":
            counts["ingested"] += 1
        elif action == "workflow_task_created":
            counts["sop_routed"] += 1
        elif action.startswith("skipped"):
            counts["skipped"] += 1
        elif action == "error":
            counts["errors"] += 1
        if log_each and action != "skipped_empty":
            logger.info(
                "inbox#%s msg=%s → %s (rec_count=%s task=%s)",
                row["id"], row.get("message_id"), action,
                result.get("record_count", 0), result.get("task_id", ""),
            )

    # ── 图片升级:classified 检装车/出港图片 → matched_sop(否则建不了 task)。
    #    promoter 之前没接进任何 daemon,导致检装车链卡死(2026-06-03 宝腾海)。──────
    try:
        from sop_hub.sop.image_route_promoter import (
            promote_classified_image_messages,
        )
        pr = promote_classified_image_messages(db_path)
        if pr.get("promoted"):
            counts["images_promoted"] = pr["promoted"]
            if log_each:
                logger.info("promoted %d classified image(s) → matched_sop",
                            pr["promoted"])
    except Exception as exc:
        logger.warning("image promote failed: %s", exc)

    # ── backfill:补建遗留 matched_sop 行(如 id=229 已被标 text_ingest_skipped,
    #    fetch 不会再捞到它) → 用独立扫描兜底,幂等。 ────────────────────────
    try:
        if _count_unrouted_sop_rows(db_path) > 0:
            from sop_hub.sop.workflow_task_store import (
                create_tasks_for_matched_messages,
            )
            t = create_tasks_for_matched_messages(db_path=db_path)
            counts["backfilled"] = t.get("created_count", 0)
            if counts["backfilled"] and log_each:
                logger.info("backfilled %d workflow_task(s) for legacy matched_sop rows",
                            counts["backfilled"])
    except Exception as exc:
        logger.warning("workflow_task backfill failed: %s", exc)

    # ── 默认:自动跑内部安全链(freight enrichment)—— 只写本地库,无对外提交 ──
    try:
        from sop_hub.sop.workflow_task_executor import run_pending_workflow_tasks
        safe_ran = safe_ok = 0
        for tt in AUTO_SAFE_TASK_TYPES:
            ch = run_pending_workflow_tasks(
                db_path=db_path, limit=chain_limit, task_type=tt,
            )
            safe_ran += ch.get("ran", 0)
            safe_ok += ch.get("succeeded", 0)
        if safe_ran:
            counts["safe_chains_ran"] = safe_ran
            counts["safe_chains_succeeded"] = safe_ok
            if log_each:
                logger.info("auto-safe chains: ran %d → ok %d", safe_ran, safe_ok)
    except Exception as exc:
        logger.warning("auto-safe chain run failed: %s", exc)

    # ── --run-chains:跑含对外提交的链(吉林工厂上传/朝阳)——跑就真提交。──────
    if run_chains:
        try:
            from sop_hub.sop.workflow_task_executor import (
                run_pending_workflow_tasks,
            )
            ran = succeeded = failed = 0
            for tt in EXTERNAL_CHAIN_TASK_TYPES:
                ch = run_pending_workflow_tasks(
                    db_path=db_path, limit=chain_limit, task_type=tt,
                )
                ran += ch.get("ran", 0)
                succeeded += ch.get("succeeded", 0)
                failed += ch.get("failed", 0)
            counts["chains_ran"] = ran
            counts["chains_succeeded"] = succeeded
            counts["chains_failed"] = failed
            if ran and log_each:
                logger.info(
                    "ran %d external workflow_task(s) → ok=%d fail=%d",
                    ran, succeeded, failed,
                )
        except Exception as exc:
            logger.warning("run_pending_workflow_tasks failed: %s", exc)

    # ── #127 lifecycle closeout:扫 active phase batch,wagons 全收货推 confirmed_received ──
    try:
        from sop_hub.sop.lifecycle_closeout import run_lifecycle_closeout
        lc_res = run_lifecycle_closeout(db_path=db_path)
        counts["lifecycle_scanned"] = lc_res.get("scanned", 0)
        counts["lifecycle_advanced"] = lc_res.get("advanced", 0)
        if lc_res.get("advanced") and log_each:
            for a in lc_res.get("advances", []):
                logger.info(
                    "lifecycle advance: batch %s %s→%s (%s)",
                    a.get("batch_id", "")[:12],
                    a.get("from_phase"), a.get("to_phase"), a.get("reason"),
                )
    except Exception as exc:
        logger.warning("run_lifecycle_closeout failed: %s", exc)

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
    run_chains: bool = False,
    chain_limit: int = 10,
) -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    import os
    logger.info(
        "text_watch_daemon started pid=%d db=%s interval=%ds batch=%d run_chains=%s",
        os.getpid(), db_path, interval_seconds, batch_size, run_chains,
    )

    pass_no = 0
    while _running:
        pass_no += 1
        try:
            counts = run_one_pass(
                db_path=db_path, log_each=not quiet, batch_size=batch_size,
                run_chains=run_chains, chain_limit=chain_limit,
            )
        except Exception as exc:
            logger.warning("pass %d failed: %s", pass_no, exc)
            counts = {"errors": 1, "fatal": str(exc)}
        non_silent = sum(counts.get(k, 0) for k in
                         ("ingested", "errors", "sop_routed", "backfilled",
                          "safe_chains_ran", "chains_ran"))
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
    p.add_argument("--run-chains", action="store_true",
                   help="每轮额外跑含对外提交的链(吉林工厂上传/朝阳)——跑就真提交。"
                        "默认关:只建 task + 跑内部安全链(freight enrich)")
    p.add_argument("--chain-limit", type=int, default=10,
                   help="每轮最多跑多少个 workflow_task(default: 10)")
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
        run_chains=args.run_chains,
        chain_limit=args.chain_limit,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
