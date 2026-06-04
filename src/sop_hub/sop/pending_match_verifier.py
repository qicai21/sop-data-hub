"""延迟验证:扫描 pending_95306_match 候选,重试链 + 超时上报。

业务模型:
- 检装车通知单收到后,候选若所有车都已落 wagon_shipments → matched(链已跑完)
- 若部分车票尚未到 95306 → candidate_status='pending_95306_match'
- 验证器每被触发(rail95306-sync 同步完一轮 / 手动 / launchd):
  - 对每个 pending_95306_match 候选,re-trigger chain → 票到齐就 succeed
  - 超 6 h 仍未到齐 → 标 timeout_manual_review + 通知人工
- 不动 in_progress / completed / matched 等其他状态

调用:
  python3 -m sop_hub.sop.pending_match_verifier            # 默认 6h timeout
  python3 -m sop_hub.sop.pending_match_verifier --hours 4  # 自定义
  python3 -m sop_hub.sop.pending_match_verifier --db ...   # 自定义 DB

Library:
  from sop_hub.sop.pending_match_verifier import verify_pending_candidates
  summary = verify_pending_candidates(db_path="data/sop_agent.db")
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sop_hub.utils.time import (
    BEIJING_TZ,
    parse_any_timestamp,
)


logger = logging.getLogger("sop_hub.pending_match_verifier")


@dataclass
class VerifierSummary:
    scanned: int = 0
    retried: int = 0
    succeeded: int = 0
    still_pending: int = 0
    timed_out: int = 0
    errors: int = 0
    detail: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "retried": self.retried,
            "succeeded": self.succeeded,
            "still_pending": self.still_pending,
            "timed_out": self.timed_out,
            "errors": self.errors,
            "detail": self.detail,
        }


def _parse_dt(s: str) -> datetime | None:
    """容错 parse + tz-correct(转 Beijing aware)。

    旧版直接 fromisoformat 丢 tz → naive,跟 datetime.now() 减得到错误
    的 elapsed(偏 8h)。新版统一用 parse_any_timestamp,naive 字符串
    假设 UTC(SQLite/utcnow 历史默认),返回 tz-aware Beijing datetime。
    """
    return parse_any_timestamp(s)


def _find_inbox_id(candidate_id: str, db_path: str | Path) -> tuple[int | None, str]:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT id, message_id FROM message_inbox "
            "WHERE inspection_candidate_id=? ORDER BY id DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
        if row:
            return int(row[0]), str(row[1] or "")
    finally:
        conn.close()
    return None, ""


def _mark_timeout(
    candidate_id: str, db_path: str | Path, elapsed_h: float,
) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE inspection_ingestion_candidates "
            "SET candidate_status='timeout_manual_review', "
            "    reason=?, updated_at=datetime('now') "
            "WHERE id=?",
            (f"95306_ticket_timeout:elapsed_{elapsed_h:.1f}h", candidate_id),
        )
        conn.commit()
    finally:
        conn.close()


def _send_timeout_notice(
    candidate_id: str, group_name: str, elapsed_h: float,
) -> None:
    """超时通知:发到 yaml test target(郭东北/数据单发群)。"""
    try:
        from sop_hub.sop.send_excel import send_to_wechat
        msg = (
            f"⚠️ 检装车候选超时未匹配 95306\n"
            f"候选 {candidate_id[:8]} (来自 {group_name})\n"
            f"已等 {elapsed_h:.1f} 小时未拿到全部票,需人工排查"
        )
        send_to_wechat(target="郭东北", message=msg, file_path=None)
    except Exception as exc:
        logger.warning("超时通知发送失败 candidate=%s: %s", candidate_id, exc)


def _retry_chain(
    candidate_id: str, db_path: str | Path,
) -> dict[str, Any]:
    """对单个 pending 候选重跑 chain。"""
    inbox_id, message_id = _find_inbox_id(candidate_id, db_path)
    if not inbox_id:
        return {"status": "no_inbox_link", "candidate_id": candidate_id}

    from sop_hub.sop.workflow_task_executor import (
        _execute_chaoyang_inspection_chain,
    )
    return _execute_chaoyang_inspection_chain(
        input_json={"message_inbox_id": inbox_id},
        message_id=message_id,
        db_path=Path(db_path),
        apply=True,
    )


def verify_pending_candidates(
    *,
    db_path: str | Path = "data/sop_agent.db",
    timeout_hours: float = 6.0,
    send_timeout_notice: bool = True,
) -> VerifierSummary:
    """主入口:扫描 pending_95306_match,逐个重试或超时上报。"""
    summary = VerifierSummary()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, group_name, source_file_name, ship_name, "
            "       release_batch_id, created_at, updated_at "
            "FROM inspection_ingestion_candidates "
            "WHERE candidate_status='pending_95306_match'"
        ).fetchall()
    finally:
        conn.close()

    summary.scanned = len(rows)
    now = datetime.now(BEIJING_TZ)  # tz-aware Beijing,跟 _parse_dt 输出一致

    for row in rows:
        cand_id = row["id"]
        created_at = row["created_at"] or row["updated_at"] or ""
        created_dt = _parse_dt(created_at)  # tz-aware Beijing
        elapsed_h = (
            (now - created_dt).total_seconds() / 3600.0
            if created_dt else float("inf")
        )

        # 1. 超时判定
        if elapsed_h > timeout_hours:
            _mark_timeout(cand_id, db_path, elapsed_h)
            summary.timed_out += 1
            summary.detail.append({
                "candidate_id": cand_id, "action": "timed_out",
                "elapsed_hours": round(elapsed_h, 2),
                "ship_name": row["ship_name"] or "",
            })
            if send_timeout_notice:
                _send_timeout_notice(
                    cand_id, row["group_name"] or "", elapsed_h,
                )
            continue

        # 2. 重试链
        try:
            res = _retry_chain(cand_id, db_path)
            summary.retried += 1
            status = res.get("status") or ""
            stage = (res.get("output_json") or {}).get("stage", "")
            note = (res.get("output_json") or {}).get("note", "")
            actual = (res.get("output_json") or {}).get("actual_in_db_count", 0)
            expected = (res.get("output_json") or {}).get("expected_count", 0)
            if status == "succeeded":
                summary.succeeded += 1
                summary.detail.append({
                    "candidate_id": cand_id, "action": "succeeded",
                    "elapsed_hours": round(elapsed_h, 2),
                })
            elif stage == "waiting_95306_tickets":
                summary.still_pending += 1
                summary.detail.append({
                    "candidate_id": cand_id, "action": "still_pending",
                    "actual": actual, "expected": expected,
                    "elapsed_hours": round(elapsed_h, 2),
                })
            else:
                summary.still_pending += 1
                summary.detail.append({
                    "candidate_id": cand_id, "action": f"unknown_status:{status}",
                    "note": note,
                    "elapsed_hours": round(elapsed_h, 2),
                })
        except Exception as exc:
            summary.errors += 1
            summary.detail.append({
                "candidate_id": cand_id, "action": "error",
                "error": str(exc),
                "elapsed_hours": round(elapsed_h, 2),
            })

    return summary


# ── CLI ─────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    p = argparse.ArgumentParser(
        description="Scan pending_95306_match candidates and retry/timeout."
    )
    p.add_argument("--db", default="data/sop_agent.db",
                   help="path to sop_agent.db (default: data/sop_agent.db)")
    p.add_argument("--hours", type=float, default=6.0,
                   help="timeout in hours (default: 6.0)")
    p.add_argument("--no-notify", action="store_true",
                   help="skip wx-ui-bridge timeout notice")
    p.add_argument("--quiet", action="store_true",
                   help="only print summary counts, not detail")
    args = p.parse_args()

    summary = verify_pending_candidates(
        db_path=args.db,
        timeout_hours=args.hours,
        send_timeout_notice=not args.no_notify,
    )
    out = summary.to_dict()
    if args.quiet:
        out.pop("detail", None)
    print(json.dumps(out, ensure_ascii=False, indent=2))
