"""延迟验证:扫描 pending 候选,重试链 + 超时上报。

业务模型:
- 检装车通知单收到后,候选若所有车都已落 wagon_shipments → matched(链已跑完)
- 若部分车票尚未到 95306 → candidate_status='pending_95306_match'
- 若车列事实已入库但批次仍缺货运信息 → candidate_status='pending_freight_info'
- 验证器每被触发(rail95306-sync 同步完一轮 / 手动 / launchd):
  - 对每个 pending 候选,re-trigger chain → 票/货运信息齐就 succeed
  - 超 6 h 仍未到齐 → 标 timeout_manual_review + 通知人工
  - 另扫可自愈的 pending_review(如 multiple_candidates / no_open_batch)：
    优先级配置后或 open lot 变化后应能重试,不再静默沉底
- 不动 matched / archived / timeout_manual_review 等终态

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
        cand = conn.execute(
            "SELECT message_id FROM inspection_ingestion_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        cand_message_id = str((cand[0] if cand else "") or "")
        if cand_message_id:
            row = conn.execute(
                "SELECT id, message_id FROM message_inbox "
                "WHERE message_id=? ORDER BY id DESC LIMIT 1",
                (cand_message_id,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE message_inbox SET inspection_candidate_id=? "
                    "WHERE id=? AND (inspection_candidate_id IS NULL OR inspection_candidate_id='')",
                    (candidate_id, row[0]),
                )
                conn.commit()
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
    """超时通知:发到数据单发群 [GROUP013](运营兜底通道)。

    2026-06-06:不再硬编码郭东北。候选超时是项目无关的运营通知,统一发到
    GROUP013 数据单发群,人工在群里排查。日后想分项目通知可读 candidate
    的 project 再走 yaml _resolve_send_target。
    """
    try:
        from sop_hub.sop.send_excel import send_to_wechat
        msg = (
            f"⚠️ 检装车候选超时未匹配 95306\n"
            f"候选 {candidate_id[:8]} (来自 {group_name})\n"
            f"已等 {elapsed_h:.1f} 小时未拿到全部票,需人工排查"
        )
        send_to_wechat(target="[GROUP013]", message=msg, file_path=None)
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
    # 2026-06-06:删 apply=True kwarg —— #98 收敛后 chain 默认即 apply,
    # 旧 apply= 参数早从签名里去掉了,留着这里调用导致 verifier 一直 TypeError。
    # #145:verifier 按 candidate_id 重试,必须把它定向传给链 —— 否则多船图时
    # 重试候选 X 但链 LIMIT 1 跑了候选 Y(首个),pending 的那船永远不前进。
    return _execute_chaoyang_inspection_chain(
        input_json={"message_inbox_id": inbox_id},
        message_id=message_id,
        db_path=Path(db_path),
        candidate_id=candidate_id,
    )


# pending_review reasons that may self-heal without human payload edits
_RETRYABLE_PENDING_REVIEW_REASONS: frozenset[str] = frozenset({
    "multiple_candidates",
    "no_open_batch",
    "no_match",
    "bad_input",
})


def _is_retryable_pending_review(reason: str | None) -> bool:
    r = (reason or "").strip()
    if r in _RETRYABLE_PENDING_REVIEW_REASONS:
        return True
    # infer tie reasons e.g. multi_candidate_tied:2
    if r.startswith("multi_candidate_tied"):
        return True
    return False


def verify_pending_candidates(
    *,
    db_path: str | Path = "data/sop_agent.db",
    timeout_hours: float = 6.0,
    send_timeout_notice: bool = True,
) -> VerifierSummary:
    """主入口:扫描可重试 pending 候选,逐个重试。"""
    summary = VerifierSummary()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, group_name, source_file_name, ship_name, candidate_status, "
            "       release_batch_id, created_at, updated_at, reason "
            "FROM inspection_ingestion_candidates "
            "WHERE candidate_status IN ("
            "  'pending_95306_match','pending_freight_info','pending_review'"
            ")"
        ).fetchall()
        rows = [
            r for r in rows
            if (r["candidate_status"] or "") != "pending_review"
            or _is_retryable_pending_review(r["reason"] if "reason" in r.keys() else None)
        ]
    finally:
        conn.close()

    summary.scanned = len(rows)
    now = datetime.now(BEIJING_TZ)  # tz-aware Beijing,跟 _parse_dt 输出一致

    for row in rows:
        cand_id = row["id"]
        candidate_status = row["candidate_status"] or ""
        created_at = row["created_at"] or row["updated_at"] or ""
        created_dt = _parse_dt(created_at)  # tz-aware Beijing
        elapsed_h = (
            (now - created_dt).total_seconds() / 3600.0
            if created_dt else float("inf")
        )

        # 先重试链路,再判超时。95306 往往是在候选已等了数小时后才补齐;
        # 若先 timeout,刚补齐的票会被挡在标准后置动作(excel/微信/上传)之外。
        try:
            res = _retry_chain(cand_id, db_path)
            summary.retried += 1
            oj = res.get("output_json") or {}
            status = res.get("status") or ""
            stage = oj.get("stage", "")
            note = oj.get("note", "")
            actual = oj.get("actual_in_db_count", 0)
            expected = oj.get("expected_count", 0)
            # P2#3 透传(2026-06-28):链的上传/发送/告警结果带进 verifier detail
            # (原来全丢 → 成功/失败都看不到鞍钢上传 + excel 发送结果)。
            _up = oj.get("consignee_upload") or {}
            _obs = {
                "upload_success": _up.get("success"),
                "upload_skipped": _up.get("skipped"),
                "send_success": (oj.get("send") or {}).get("success"),
                "warnings": oj.get("warnings") or [],
            }
            if status == "succeeded":
                summary.succeeded += 1
                summary.detail.append({
                    "candidate_id": cand_id, "action": "succeeded",
                    "elapsed_hours": round(elapsed_h, 2), **_obs,
                })
            elif stage == "waiting_95306_tickets":
                if candidate_status == "pending_95306_match" and elapsed_h > timeout_hours:
                    _mark_timeout(cand_id, db_path, elapsed_h)
                    summary.timed_out += 1
                    summary.detail.append({
                        "candidate_id": cand_id, "action": "timed_out",
                        "actual": actual, "expected": expected,
                        "elapsed_hours": round(elapsed_h, 2),
                        "ship_name": row["ship_name"] or "",
                    })
                    if send_timeout_notice:
                        _send_timeout_notice(
                            cand_id, row["group_name"] or "", elapsed_h,
                        )
                else:
                    summary.still_pending += 1
                    summary.detail.append({
                        "candidate_id": cand_id, "action": "still_pending",
                        "actual": actual, "expected": expected,
                        "elapsed_hours": round(elapsed_h, 2),
                    })
            elif stage == "awaiting_freight_info":
                summary.still_pending += 1
                summary.detail.append({
                    "candidate_id": cand_id, "action": "awaiting_freight_info",
                    "elapsed_hours": round(elapsed_h, 2),
                    "matched_release_batch_id": oj.get("matched_release_batch_id"),
                })
            else:
                summary.still_pending += 1
                summary.detail.append({
                    "candidate_id": cand_id,
                    "action": (f"retry_after_fail:{status}" if status == "failed"
                               else f"unknown_status:{status}"),
                    "note": note,
                    "elapsed_hours": round(elapsed_h, 2), **_obs,
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
        description=(
            "Scan pending_95306_match / pending_freight_info / "
            "retryable pending_review and retry/timeout."
        )
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
