"""R69: workflow_task_db → executor_runner bridge.

Replaces the old live_service hard-coded "四平" trigger with
task-driven execution: message_inbox → workflow_task_db → executor_runner.

For jljg_departure_text_chain:
  1. Read input_json → reconstruct MessageEvent
  2. Call run_departure_executor_chain (dry-run by default)
  3. Capture ExecutionPreview → output_json
  4. Write back: task_status, output_json/error_message, last_run_at, retry_count
  5. Update message_inbox.processing_status

Other task_types: marked skipped/not_implemented for now.
"""

from __future__ import annotations

import json
import sqlite3
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ── DB path ──────────────────────────────────────────────────────────────

def _get_db_path(db_path: str | Path | None = None) -> Path:
    if db_path:
        return Path(db_path)
    import os
    env = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "data" / "sop_agent.db"


_CN_NUMS = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


def _cn_num(n: int) -> str:
    """1→一,2→二,…,10→十;>10 用阿拉伯数字。"""
    if 0 <= n <= 10:
        return _CN_NUMS[n]
    return str(n)


def _now_iso() -> str:
    from sop_hub.utils.time import now_iso_beijing
    return now_iso_beijing()


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_ROOT = REPO_ROOT / "runtime"

# ── Task status conventions ──────────────────────────────────────────────

STATUSES = {
    "PENDING": "pending",
    "RUNNING": "running",
    "SUCCEEDED": "succeeded",
    "FAILED": "failed",
    "SKIPPED": "skipped",
}

MESSAGE_INBOX_STATUS_MAP = {
    "succeeded": "task_succeeded",
    "failed": "task_failed",
    "skipped": "task_skipped",
}


# ── DB helpers ───────────────────────────────────────────────────────────

def _mark_task(db_path: Path, task_id: int, status: str, **extra) -> None:
    conn = sqlite3.connect(str(db_path))
    now = _now_iso()
    sets = ["task_status = ?", "updated_at = ?"]
    values = [status, now]

    if status == STATUSES["RUNNING"]:
        sets.append("last_run_at = ?")
        values.append(now)

    for key in ("output_json", "error_message"):
        if key in extra:
            sets.append(f"{key} = ?")
            values.append(extra[key])

    if "retry_count" in extra:
        sets.append("retry_count = ?")
        values.append(extra["retry_count"])

    values.append(str(task_id))
    conn.execute(
        f"UPDATE workflow_task_db SET {', '.join(sets)} WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()


def _update_message_inbox_status(
    db_path: Path, message_id: str, status: str,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE message_inbox SET processing_status = ?, updated_at = ? WHERE message_id = ?",
        (status, _now_iso(), message_id),
    )
    conn.commit()
    conn.close()


# ── Core execution ───────────────────────────────────────────────────────

def run_workflow_task(
    task_id: int,
    *,
    db_path: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Execute a single workflow_task by id.

    Returns: {task_id, task_type, action, status, output_json, error_message}
    """
    db = _get_db_path(db_path)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM workflow_task_db WHERE id = ?", (task_id,)
    ).fetchone()

    if not row:
        conn.close()
        return {"task_id": task_id, "error": "not found"}

    rd = dict(row)
    task_type = rd["task_type"]
    task_status = rd["task_status"]
    message_id = rd["message_id"]
    input_json = json.loads(rd["input_json"]) if rd.get("input_json") else {}
    conn.close()

    # Idempotent: skip already terminal tasks unless --force
    if task_status in ("succeeded", "failed") and not force:
        return {
            "task_id": task_id,
            "task_type": task_type,
            "action": "skipped",
            "reason": f"already {task_status}",
            "status": task_status,
        }

    # Mark running
    _mark_task(db, task_id, STATUSES["RUNNING"])

    # ── Dispatch by task_type ─────────────────────────────────────────
    try:
        if task_type == "jljg_departure_text_chain":
            result = _execute_jljg_departure(input_json, message_id, db_path=db, task_id=task_id)
        elif task_type == "create_release_batch":
            result = _execute_create_release_batch(input_json, message_id, db_path=db)
        elif task_type in ("chaoyang_inspection_chain",
                            "zhongtang_inspection_chain"):
            # 中唐复用朝阳同一执行器(95306 反推 / wagon_shipments / excel / 发群
            # 结构完全同构)。朝阳鞍钢门户上传段在执行器内已自门控
            # (project_id == "chaoyang_steel"),中唐自动跳过。
            # #145:走 _multi 包装层,一 inbox 多船候选时每船各跑各的。
            result = _execute_chaoyang_inspection_chain_multi(
                input_json, message_id, db_path=db, task_id=task_id,
            )
        elif task_type == "inspection_text_trigger" or task_type.startswith(
            "inspection_text_trigger:"
        ):
            # #143:扇出后 task_type 后缀船名("inspection_text_trigger:鞍子河")
            result = _execute_inspection_text_trigger(
                input_json, message_id, db_path=db, task_id=task_id,
            )
        elif task_type == "freight_detail_enrichment":
            result = _execute_freight_detail_enrichment(
                input_json, message_id, db_path=db,
            )
        elif task_type in ("chaoyang_dispatch_context", "generic_sop_task"):
            result = {
                "action": "skipped",
                "status": "skipped",
                "output_json": {
                    "reason": f"task_type {task_type} not implemented (R69)",
                    "not_implemented": True,
                },
            }
        else:
            result = {
                "action": "failed",
                "status": "failed",
                "error_message": f"unknown task_type {task_type}",
            }
    except Exception as exc:
        result = {
            "action": "failed",
            "status": "failed",
            "error_message": f"{exc}\n{traceback.format_exc()[-500:]}",
        }

    # Write back
    status = result["status"]
    output = result.get("output_json")
    error = result.get("error_message")

    # 发运延迟门:deferred 只是"还没到点"的轮询,不算一次真尝试,不累加
    # retry_count(否则 2 小时窗口里每轮 +1,污染重试语义)。
    _mark_kwargs: dict[str, Any] = {
        "output_json": json.dumps(output, ensure_ascii=False) if output else None,
        "error_message": error,
    }
    if result.get("action") != "deferred":
        _mark_kwargs["retry_count"] = (rd.get("retry_count") or 0) + 1
    _mark_task(db, task_id, status, **_mark_kwargs)

    # Update message_inbox(deferred 不改 inbox 状态:任务仍 pending 待下一轮,
    # 别把它误标 task_failed/skipped)
    if result.get("action") != "deferred":
        mi_status = MESSAGE_INBOX_STATUS_MAP.get(status, "task_failed")
        _update_message_inbox_status(db, message_id, mi_status)

    return {
        "task_id": task_id,
        "task_type": task_type,
        "action": result["action"],
        "status": status,
        "output_json": output,
        "error_message": error,
    }


def _execute_jljg_departure(
    input_json: dict[str, Any],
    message_id: str,
    *,
    db_path: Path,
    task_id: int = 0,
) -> dict[str, Any]:
    """Execute the jilin_jingang departure text chain.

    #98 一体化:进来即真跑(写库、生成 Excel、真上传工厂、反查、发微信)。是否进
    来由调用方(daemon --run-chains)决定。幂等(R71)由 external_action_log 守住:
    若该 task 的对外动作**已执行过**,直接跳过、绝不二次提交。"""
    from sop_hub.sop.executor_runner import (
        run_departure_executor_chain,
        ExecutionPreview,
    )
    from sop_hub.sop.monitoring_plan_matcher import MessageEvent

    text_content = input_json.get("text_content", "")
    group_id = input_json.get("group_name", "")
    received_at = input_json.get("received_datetime", "")

    event = MessageEvent(
        message_id=message_id,
        channel="wechat",
        group_id=group_id,
        text=text_content,
        received_at=received_at,
    )

    # ── R71 幂等:对外动作已执行过 → 跳过重跑,绝不二次提交工厂 ──────────
    already_executed = False
    try:
        import sqlite3 as _sql
        _conn = _sql.connect(str(db_path))
        already_executed = _conn.execute(
            "SELECT COUNT(*) FROM external_action_log "
            "WHERE workflow_task_id=? AND action_status='executed'",
            (task_id,),
        ).fetchone()[0] > 0
        _conn.close()
    except Exception:
        pass  # table may not exist yet
    if already_executed:
        return {
            "action": "skipped",
            "status": "succeeded",
            "output_json": {
                "reason": "external actions already executed (idempotent) — skip re-submit",
            },
        }

    preview: ExecutionPreview = run_departure_executor_chain(
        event,
        runtime_root=RUNTIME_ROOT,
        db_path=str(db_path),
    )

    output = preview.to_dict()

    # ── 发运对齐延迟门(2026-06-16):本轮在延迟窗口内 → 不规划/不执行任何
    # 对外动作,任务保持 pending,下一轮 daemon 再判。等过点后正常跑全链。
    if getattr(preview, "deferred", False):
        return {
            "action": "deferred",
            "status": "pending",
            "output_json": {
                **output,
                "deferred_until": preview.deferred_until,
                "skip_reason": preview.skipped_reason,
            },
        }

    # ── 缺陷A 重试(2026-06-27 工单·发车识别):发运文本早于 95306 制票 → query 0
    # candidates。不算完成,挂 **pending** 让 daemon 每轮回扫;rail sync 进了制票车后
    # 下一轮自然跑通(与 deferred 同机制)。超 18h 仍 0 → failed + 数据单发群告警
    # (⚠️ 前缀,text_router guard 防自激),杜绝无限等。本块在规划对外动作**之前**返回,
    # 0 车时不规划/不执行任何对外动作。
    _TICKET_WAIT_TIMEOUT_H = 18.0
    if (not preview.error and not preview.skipped_reason
            and (getattr(preview, "query_total_candidates", 0) or 0) == 0):
        elapsed_h = _inbox_elapsed_hours(message_id, db_path)
        if elapsed_h is not None and elapsed_h >= _TICKET_WAIT_TIMEOUT_H:
            try:
                from sop_hub.sop.send_excel import send_to_wechat
                send_to_wechat(target="[GROUP013]", file_path=None, message=(
                    f"⚠️ 吉林发运文本超时未等到 95306 制票\n"
                    f"{(getattr(event, 'text', '') or '').strip()[:40]}\n"
                    f"已等 {elapsed_h:.1f} 小时,需人工排查"))
            except Exception as _exc:
                output["timeout_notice_error"] = str(_exc)
            return {
                "action": "failed", "status": "failed",
                "error_message": f"发运文本超时未等到95306制票 (已等{elapsed_h:.1f}h)",
                "output_json": {**output, "waiting_reason": "95306_ticketing_timeout"},
            }
        return {
            "action": "waiting_95306", "status": "pending",
            "output_json": {
                **output, "waiting_reason": "95306_ticketing",
                "waited_hours": round(elapsed_h, 1) if elapsed_h is not None else None,
            },
        }

    # ── R70: plan external actions with idempotency keys ──────────────
    external_actions: list[dict[str, Any]] = []
    wagon_count = 0
    try:
        release_batch_id = preview.release_batch_id or ""
        step3 = (preview.wagon_result or {})
        if isinstance(step3, dict):
            wagon_count = step3.get("planned_insert_count", 0) or step3.get("expected_car_count", 0) or 0

        from sop_hub.sop.external_action_log import plan_jljg_external_actions
        mi_id = int(input_json.get("message_inbox_id") or 0)
        external_actions = plan_jljg_external_actions(
            db_path=str(db_path),
            workflow_task_id=task_id,
            message_inbox_id=mi_id,
            message_id=message_id,
            release_batch_id=release_batch_id,
            wagon_count=wagon_count,
            ship_name=preview.release_batch_ship or "",
            apply_mode=True,
        )
        output["external_actions"] = {
            "planned": len([a for a in external_actions if a.get("action") == "created"]),
            "skipped_duplicate": len([a for a in external_actions if a.get("action") == "skipped"]),
            "actions": external_actions,
        }
    except Exception as exc:
        output["external_actions_error"] = str(exc)

    # ── R71: 链路真正跑通(无 skip/无 error)→ 标记对外动作 executed ──────
    if not preview.skipped_reason and not preview.error:
        try:
            from sop_hub.sop.external_action_log import mark_external_action_executed
            steps = preview.to_dict().get("steps", {})
            # Generate same keys to match
            rb_id = preview.release_batch_id or ""
            wc = wagon_count
            for atype, resp_key, resp_data in [
                ("generate_shipping_excel", "4_departure_excel",
                 {"excel_path": steps.get("4_departure_excel", {}).get("path", ""),
                  "rows": steps.get("4_departure_excel", {}).get("rows", 0)}),
                ("factory_upload_submit", "5_factory_upload",
                 {"login_success": steps.get("5_factory_upload", {}).get("login_success", False),
                  "payloads": steps.get("5_factory_upload", {}).get("payloads", 0),
                  "success": steps.get("5_factory_upload", {}).get("success", 0),
                  "failure": steps.get("5_factory_upload", {}).get("failure", 0),
                  "verified": steps.get("5b_factory_verify", {}).get("verified", False)}),
                ("send_shipping_excel_wechat", "6_send_excel",
                 {"sent": steps.get("6_send_excel", {}).get("sent", False),
                  "target": steps.get("6_send_excel", {}).get("target", "")}),
            ]:
                from sop_hub.sop.external_action_log import build_idempotency_key
                biz = f"{rb_id}:{wc}" if rb_id else f"fallback:{message_id}:{atype}"
                key = build_idempotency_key("jilin_jingang_jinzhou", atype, biz)
                mark_external_action_executed(key, db_path=str(db_path), response_json=resp_data)
        except Exception as exc:
            output["external_actions_mark_error"] = str(exc)

    # Determine status
    if preview.error:
        return {
            "action": "failed",
            "status": "failed",
            "error_message": preview.error,
            "output_json": output,
        }
    elif preview.skipped_reason:
        return {
            "action": "skipped",
            "status": "skipped",
            "output_json": {
                **output,
                "skip_reason": preview.skipped_reason,
            },
        }
    else:
        return {
            "action": "succeeded",
            "status": "succeeded",
            "output_json": output,
        }


def _execute_create_release_batch(
    input_json: dict[str, Any],
    message_id: str,
    *,
    db_path: Path,
) -> dict[str, Any]:
    """Execute create_release_batch: OCR extraction JSON → release_batches DB.

    #98 一体化:进来即真写库(ingest_release_batch_file 幂等)。"""
    extraction_path = input_json.get("extraction_json_path")
    if not extraction_path:
        return {
            "action": "failed",
            "status": "failed",
            "error_message": "no extraction_json_path in input_json",
        }

    ext_path = Path(extraction_path)
    if not ext_path.exists():
        return {
            "action": "failed",
            "status": "failed",
            "error_message": f"extraction_json_path not found: {extraction_path}",
        }

    # 真正 ingest(幂等:已存在的 batch 会 skip)
    from sop_hub.data_agent.agent import BusinessDataAgent
    agent = BusinessDataAgent()
    records = agent.ingest_release_batch_file(str(ext_path))

    # 缺陷B / 出港0批次告警(2026-06-27 环球信任工单):授权的出港计划通知单却建出 0 批次,
    # 多半是 VL 误读日期当历史过滤 / scope 全过滤 / 漏建。静默最危险 → 发数据单发群核对。
    # (⚠️ 前缀被 text_router guard 挡,不自激。)
    if not records:
        try:
            import json as _json
            _ext = _json.loads(ext_path.read_text(encoding="utf-8"))
            _ship = (_ext.get("business_info", {}) or {}).get("船名", "")
            _date = (_ext.get("header_info", {}) or {}).get("通知日期", "")
            from sop_hub.sop.send_excel import send_to_wechat
            send_to_wechat(target="[GROUP013]", file_path=None, message=(
                f"⚠️ 出港计划通知单建出 0 批次,疑似漏建\n"
                f"船名:{_ship}  通知日期:{_date}\n"
                f"({message_id})可能 VL 误读日期/scope 全过滤/已存在,请人工核对"))
        except Exception as _exc:
            pass

    return {
        "action": "executed",
        "status": "succeeded",
        "output_json": {
            "batch_count": len(records),
            "batch_ids": [r.id for r in records],
            "batch_sequences": [r.batch_sequence for r in records],
            "ship_names": list({r.ship_name for r in records if r.ship_name}),
        },
    }


def _event_excel_batch_specs(
    candidate_id: str, db_path: Path | str,
) -> tuple[list[tuple[str, list[str] | None]], bool, list[str]]:
    """发运 excel = **一张简装车通知单(source_image)的数据展现**(2026-06-27 用户确认,
    全项目统一)。按 source_image_path 聚同单兄弟候选,返回:
      - batch_specs: [(release_batch_id, car_nos), ...] 按候选顺序(每船一块);
      - all_matched: 同单的船是否都已 matched(False → 调用方等齐再合成,不发半截);
      - ship_names: 同单船名列表。
    car_nos 取候选自带的 car_numbers_json(本次单子的车,非批次累计)。
    """
    import json as _json
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT source_image_path FROM inspection_ingestion_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        src = row["source_image_path"] if row else None
        q = ("SELECT ship_name, release_batch_id, car_numbers_json, candidate_status "
             "FROM inspection_ingestion_candidates WHERE ")
        if src:
            sibs = conn.execute(q + "source_image_path=? ORDER BY id", (src,)).fetchall()
        else:
            sibs = conn.execute(q + "id=?", (candidate_id,)).fetchall()
    finally:
        conn.close()
    _matched = ("matched", "matched_by_inference")
    all_matched = bool(sibs) and all(s["candidate_status"] in _matched for s in sibs)
    specs: list[tuple[str, list[str] | None]] = []
    ships: list[str] = []
    for s in sibs:
        bid = s["release_batch_id"]
        if not bid:
            continue
        cars = None
        cnj = s["car_numbers_json"]
        if cnj:
            try:
                cars = [str(x).strip() for x in _json.loads(cnj) if x]
            except Exception:
                cars = None
        specs.append((bid, cars))
        if s["ship_name"]:
            ships.append(s["ship_name"])
    return specs, all_matched, ships


def _inbox_elapsed_hours(message_id: str, db_path: Path) -> float | None:
    """消息到现在多少小时(超时判断基准)。用 message_inbox.created_at(带 +08:00 时区,
    解析可靠);**不用 received_datetime**——那列是裸北京时间,parse_any_timestamp 会当 UTC
    错加 8h(2026-06-27 实测 -6h 负 elapsed 的坑)。"""
    try:
        from sop_hub.utils.time import now_iso_beijing, parse_any_timestamp
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT created_at FROM message_inbox "
                "WHERE message_id=? LIMIT 1", (message_id,)).fetchone()
        finally:
            conn.close()
        ref = row[0] if row else None
        if not ref:
            return None
        mt = parse_any_timestamp(ref)
        return (parse_any_timestamp(now_iso_beijing()) - mt).total_seconds() / 3600.0
    except Exception:
        return None


def _zt_retry_or_timeout(
    res: dict[str, Any], message_id: str, db_path: Path, *,
    reason: str, timeout_h: float = 24.0,
) -> dict[str, Any]:
    """中唐货运 enrich 暂时匹配不上(通知单/批次还没到可匹配态)→ 缺陷A:挂 **pending**
    让 daemon 每轮回扫,批次就绪后下轮自然匹配上;超 timeout_h → 终态 + 数据单发群⚠️告警。
    安全:重试只是重跑 auto_enrich(规则1唯一才填、歧义仍挂起),不会错填。"""
    eh = _inbox_elapsed_hours(message_id, db_path)
    if eh is not None and eh >= timeout_h:
        try:
            from sop_hub.sop.send_excel import send_to_wechat
            send_to_wechat(target="[GROUP013]", file_path=None, message=(
                f"⚠️ 中唐货运超时未匹配批次({reason})\n"
                f"{(res.get('reason') or '')[:50]}\n已等 {eh:.1f} 小时,需人工排查"))
        except Exception:
            pass
        return {"action": "skipped", "status": "succeeded",
                "output_json": {**res, "waiting_reason": "zt_freight_match_timeout"}}
    return {"action": "waiting_match", "status": "pending",
            "output_json": {**res, "waiting_reason": reason,
                            "waited_hours": round(eh, 1) if eh is not None else None}}


def _execute_freight_detail_enrichment(
    input_json: dict[str, Any],
    message_id: str,
    *,
    db_path: Path,
) -> dict[str, Any]:
    """#96: freight_detail text → match release_batch → enrich cargo_product_name 等。
    #98:进来即真写(内部安全,无对外提交)。

    Project 分支(2026-06-04):
      - chaoyang_steel(default):订单标识 CGR + 合同号 HNMC 模板(印粉/麦克粉…)
      - zhongtang_special_steel:供方/船名/货名/港口/数量/计划号/合同号 7 字段模板,
        且含"海铁联运两段船"的 import_ship_name 双存逻辑。
    """
    text = input_json.get("text_content", "") or ""
    group_id = input_json.get("group_name") or input_json.get("source_group_id") or ""
    project_id = (input_json.get("sop_project_id") or "").strip()

    # ── 中唐分支:补充货运信息(7 字段) ──────────────────────────────────
    if project_id == "zhongtang_special_steel":
        from sop_hub.sop.zhongtang_freight_text_extractor import (
            extract_zhongtang_freight_supplement,
        )
        from sop_hub.sop.enrich_release_batch import (
            auto_enrich_release_batches_from_zhongtang_supplement,
        )
        candidate_zt = extract_zhongtang_freight_supplement(
            text, message_id=message_id, group_id=group_id,
        )
        if candidate_zt.status == "no_match":
            return {
                "action": "skipped",
                "status": "succeeded",
                "output_json": {
                    "reason": "zhongtang supplement no_match (无 contract_no/plan_id) — terminal no-op",
                    "project": "zhongtang_special_steel",
                },
            }
        res = auto_enrich_release_batches_from_zhongtang_supplement(
            candidate_zt, apply=True, db_path=db_path,
        )
        st = res.get("status")
        if st == "schema_missing_fields":
            return {
                "action": "failed",
                "status": "failed",
                "error_message": f"release_batches 缺列: {res.get('schema_missing_fields')}",
                "output_json": res,
            }
        if st == "no_match":
            # 合同号/计划号没在 release_batches 里 — 通知单/批次还没来。缺陷A(2026-06-27):
            # 原标 skipped(不在 daemon 重跑集 → 永不重试,正是中唐货运 bug)。改挂 pending 重试。
            return _zt_retry_or_timeout(res, message_id, db_path, reason="通知单/批次未到")
        if st == "suspended":
            _reason = res.get("reason", "")
            if "歧义" in _reason:
                # 规则1:多个同船缺计划号 → 必人工指定,绝不自动错填(retry 也填不对)。终态 + WARN。
                import logging as _logging
                _logging.getLogger("sop_hub.sop").warning(
                    "中唐货运挂起待人工(歧义): %s", _reason
                )
                return {"action": "suspended", "status": "succeeded", "output_json": res}
            # 规则2:对不上任何在途船 = 批次还没到可匹配态(timing,如 lot 还没进 pending_freight)。
            # 缺陷A:挂 pending 重试;批次就绪后规则1唯一命中即填(不唯一→转歧义→人工),不会错填。
            return _zt_retry_or_timeout(res, message_id, db_path, reason="对不上在途船(待批次就绪)")
        # 缺陷C(2026-06-27 环球信任工单):enrich 填齐计划号/合同号后,把匹配批次
        # pending_freight → enriched。否则有了货运信息状态仍卡 pending_freight。
        # advance_lifecycle 幂等 + 校验(已 loading/更后的批次=noop/rejected,不倒退)。
        try:
            from sop_hub.sop.lifecycle_transition import advance_lifecycle
            for _r in (res.get("results") or []):
                _bid = _r.get("release_batch_id")
                if _bid:
                    advance_lifecycle(
                        _bid, "enriched",
                        reason="中唐货运 enrich 填齐计划号/合同号",
                        triggered_by="freight_detail_enrichment",
                        db_path=str(db_path),
                    )
        except Exception as _exc:
            res["lifecycle_advance_error"] = str(_exc)
        return {
            "action": "executed",
            "status": "succeeded",
            "output_json": res,
        }

    # ── 默认分支:chaoyang/jilin 的 freight_detail(订单标识 CGR + 合同号 HNMC)──
    from sop_hub.sop.freight_detail_extractor import extract_freight_detail
    from sop_hub.sop.enrich_release_batch import (
        auto_enrich_release_batches_from_freight_detail,
    )

    candidate = extract_freight_detail(
        text, message_id=message_id, group_id=group_id,
    )
    if candidate.status == "no_match":
        # Not freight-detail text at all (no 订单标识) — this can NEVER enrich,
        # so terminate the task (succeeded no-op) instead of leaving it pending
        # to re-run every daemon pass forever.
        return {
            "action": "skipped",
            "status": "succeeded",
            "output_json": {
                "reason": "freight_detail text no_match (无 订单标识) — terminal no-op",
                "matched": [],
            },
        }

    res = auto_enrich_release_batches_from_freight_detail(
        candidate, apply=True, db_path=db_path,
    )
    st = res.get("status")
    if st == "schema_missing_fields":
        return {
            "action": "failed",
            "status": "failed",
            "error_message": "release_batches missing column cargo_product_name",
            "output_json": res,
        }
    if st == "no_match":
        # Parsed OK but no release_batch carries this CGR/HNMC *yet* — the batch
        # may be created later, so keep retryable (non-terminal skipped).
        return {"action": "skipped", "status": "skipped", "output_json": res}

    # #128 lifecycle 触发器:freight 补齐了 → 推 batch 到 enriched
    # (res.results 含每个被 enrich 的 release_batch_id)
    try:
        from sop_hub.sop.lifecycle_transition import advance_lifecycle
        for r in (res.get("results") or []):
            rb_id = r.get("release_batch_id") or ""
            if rb_id:
                advance_lifecycle(
                    rb_id, "enriched",
                    reason="freight_detail 合同/订单/品名补齐",
                    triggered_by="chain.freight_detail_enrichment",
                    db_path=db_path,
                )
    except Exception:
        pass  # 非阻塞:lifecycle 推不动不影响主流程

    # applied / no_op → success
    return {
        "action": "executed",
        "status": "succeeded",
        "output_json": res,
    }


# #143:文本触发器等检装车通知单的超时窗(文本常先于通知单图到达)
_INSPECTION_TEXT_TRIGGER_TIMEOUT_HOURS = 12.0
# #issue-20260619 Fix C:等满此宽限期仍无候选,朝钢从 95306 反查合成候选(给真通知单先到的机会)
_INSPECTION_TEXT_TRIGGER_SYNTH_GRACE_HOURS = 3.0
_RAIL_DB_PATH = "/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3"
# 检装车候选里"可被链消费"的状态(排除归档/作废/被取代/**已匹配入库**);其余才算活跃。
# #issue-20260623:'matched' 漏在外面 → 已消费的旧候选被新发车触发器再抓(rendezvous 退化,
# 今早中联发发车抓了 6 天前 matched 的 44 车候选)。已匹配=已消费,必须排除。
_INACTIVE_CANDIDATE_STATUSES = (
    "archived", "cancelled_legacy", "superseded", "timeout_manual_review", "matched",
)
# 触发器"预期车数"与候选车数的容差;超出视为不是同一趟 → 不抓该候选(防数量对不上还硬配)
_INSPECTION_TEXT_TRIGGER_COUNT_TOL = 4
# 候选"临近窗":只认触发器前 这个小时数内产生的检装车候选,不抓陈年旧候选
_INSPECTION_TEXT_TRIGGER_CANDIDATE_MAX_AGE_H = 24.0
# 文本触发器判断同一发车事件的窗口:以触发文本时间为锚,而不是 daemon 重试时的 now。
_INSPECTION_TEXT_TRIGGER_EVENT_BEFORE_H = 12.0
_INSPECTION_TEXT_TRIGGER_EVENT_AFTER_H = 6.0


def _inspection_text_trigger_event_bounds(
    conn,
    input_json: dict[str, Any],
) -> tuple[str, str] | None:
    trigger_ts = (input_json.get("received_datetime") or "").strip()
    if not trigger_ts and input_json.get("message_inbox_id"):
        row = conn.execute(
            "SELECT received_datetime FROM message_inbox WHERE id=?",
            (input_json.get("message_inbox_id"),),
        ).fetchone()
        trigger_ts = str((row[0] if row else "") or "").strip()
    if not trigger_ts:
        return None
    try:
        from sop_hub.utils.time import parse_any_timestamp
        dt = parse_any_timestamp(trigger_ts)
    except Exception:
        return None
    lo = dt - timedelta(hours=_INSPECTION_TEXT_TRIGGER_EVENT_BEFORE_H)
    hi = dt + timedelta(hours=_INSPECTION_TEXT_TRIGGER_EVENT_AFTER_H)
    return lo.strftime("%Y-%m-%d %H:%M:%S"), hi.strftime("%Y-%m-%d %H:%M:%S")


def _find_candidate_inbox(
    conn, candidate_id: str, candidate_message_id: str,
) -> tuple[int | None, str]:
    """找指向某检装车候选的 message_inbox 行(图片那条,非文本触发那条)。"""
    row = conn.execute(
        "SELECT id, message_id FROM message_inbox "
        "WHERE inspection_candidate_id=? ORDER BY id DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    if row:
        return int(row[0]), str(row[1] or "")
    if candidate_message_id:
        row = conn.execute(
            "SELECT id, message_id FROM message_inbox "
            "WHERE message_id=? ORDER BY id DESC LIMIT 1",
            (candidate_message_id,),
        ).fetchone()
        if row:
            return int(row[0]), str(row[1] or "")
    return None, ""


def _execute_inspection_text_trigger(
    input_json: dict[str, Any],
    message_id: str,
    *,
    db_path: Path,
    task_id: int = 0,
) -> dict[str, Any]:
    """#143:检装车文本触发器 → 与检装车通知单候选 rendezvous → 委托既有检验链。

    业务定位:微信文本("汐子铁鞍子河5节")只当**触发器 + 预期车数**。车号顺序
    与 lot 归属的权威仍归检装车通知单 JSON。本执行器:
      1. 按 trigger_ship + trigger_dest 找活跃检装车候选(图片侧产生的)。
      2. 找到 → 指向候选的 inbox 跑 _execute_chaoyang_inspection_chain(它内部做
         95306 时间窗反推 + 与通知单比对 + 入库 + excel + 上传)。把文本预期车数
         作为交叉校验 note 附上。
      3. 没找到 → 通知单图还没到。任务保持 pending(下一轮重试),超 12h → skipped
         + 发群通知人工。
    """
    import sqlite3 as _sql

    project = (input_json.get("trigger_project") or "").strip()
    ship = (input_json.get("trigger_ship") or "").strip()
    dest = (input_json.get("trigger_dest") or "").strip()
    expected = int(input_json.get("trigger_expected_count") or 0)

    if not ship:
        return {"action": "failed", "status": "failed",
                "error_message": "inspection_text_trigger 缺 trigger_ship"}

    conn = _sql.connect(str(db_path))
    conn.row_factory = _sql.Row
    try:
        event_bounds = _inspection_text_trigger_event_bounds(conn, input_json)
        # 候选匹配:ship + dest + 活跃状态 + **车数对得上** + **临近时间窗** 的最新一条。
        # #issue-20260623:仅 ship+dest+latest 会抓到已 matched / 车数对不上 / 陈年的旧候选
        # (今早中联发发车 50 节抓了 6 天前 44 车的 matched 候选)。加三道闸:
        #   ① 状态排除已消费('matched' 等,见 _INACTIVE_CANDIDATE_STATUSES)
        #   ② 车数交叉校验:|候选车数 − 触发预期| ≤ 容差(expected=0 未知时跳过)
        #   ③ 时间窗:只认 MAX_AGE_H 小时内产生的候选,不抓陈年旧候选
        q = (
            "SELECT id, message_id, candidate_status FROM inspection_ingestion_candidates "
            "WHERE ship_name=? "
            f"  AND candidate_status NOT IN ({','.join('?' * len(_INACTIVE_CANDIDATE_STATUSES))}) "
            "  AND (? = 0 OR ABS(COALESCE(wagon_count, 0) - ?) <= ?) "
        )
        params: list[Any] = [
            ship, *_INACTIVE_CANDIDATE_STATUSES,
            expected, expected, _INSPECTION_TEXT_TRIGGER_COUNT_TOL,
        ]
        if event_bounds:
            q += "  AND created_at BETWEEN ? AND ? "
            params.extend(event_bounds)
        else:
            q += "  AND created_at >= datetime('now', ?) "
            params.append(f"-{_INSPECTION_TEXT_TRIGGER_CANDIDATE_MAX_AGE_H} hours")
        if dest:
            q += "  AND (destination=? OR destination='' OR destination IS NULL) "
            params.append(dest)
        q += "ORDER BY created_at DESC LIMIT 1"
        cand = conn.execute(q, params).fetchone()
        if not cand and event_bounds:
            q_fallback = (
                "SELECT id, message_id, candidate_status FROM inspection_ingestion_candidates "
                "WHERE ship_name=? "
                f"  AND candidate_status NOT IN ({','.join('?' * len(_INACTIVE_CANDIDATE_STATUSES))}) "
                "  AND (? = 0 OR ABS(COALESCE(wagon_count, 0) - ?) <= ?) "
                "  AND created_at >= datetime('now', ?) "
            )
            fallback_params: list[Any] = [
                ship, *_INACTIVE_CANDIDATE_STATUSES,
                expected, expected, _INSPECTION_TEXT_TRIGGER_COUNT_TOL,
                f"-{_INSPECTION_TEXT_TRIGGER_CANDIDATE_MAX_AGE_H} hours",
            ]
            if dest:
                q_fallback += "  AND (destination=? OR destination='' OR destination IS NULL) "
                fallback_params.append(dest)
            q_fallback += "ORDER BY created_at DESC LIMIT 1"
            cand = conn.execute(q_fallback, fallback_params).fetchone()

        synth_cand_id = None  # Fix C:非空=本次靠 95306 合成的候选(链 skip_upload)
        if not cand:
            # 已被通知单链处理?同船 + 车数对得上 + 时间窗内的**已 matched** 候选 =
            # 通知单图走了自己的检验链处理完了(2026-06-28 丰收散运60车 wx_71 即此:
            # 通知单链 09:30 matched 并发了 excel,文本触发器却因 rendezvous 只认非
            # matched 候选而晾着、12h 误报"通知单未到")。→ 触发器直接结束,不再等/报警。
            done = conn.execute(
                "SELECT id FROM inspection_ingestion_candidates "
                "WHERE ship_name=? AND candidate_status='matched' "
                "  AND (? = 0 OR ABS(COALESCE(wagon_count,0) - ?) <= ?) "
                + ("  AND created_at BETWEEN ? AND ? " if event_bounds else "  AND created_at >= datetime('now', ?) ")
                + ("  AND destination=? " if dest else "")
                + "ORDER BY created_at DESC LIMIT 1",
                (
                    ship, expected, expected, _INSPECTION_TEXT_TRIGGER_COUNT_TOL,
                    *(event_bounds or (f"-{_INSPECTION_TEXT_TRIGGER_CANDIDATE_MAX_AGE_H} hours",)),
                    *((dest,) if dest else ()),
                ),
            ).fetchone()
            if done:
                return {
                    "action": "executed", "status": "skipped",
                    "output_json": {
                        "stage": "already_handled_by_notice_chain",
                        "ship": ship, "expected_count": expected,
                        "matched_candidate": done[0],
                        "note": "通知单图已由检验链 matched 处理,文本触发器无需再等/报警",
                    },
                }
            # rendezvous 等待:通知单图还没到。看任务已等多久。
            trow = conn.execute(
                "SELECT created_at FROM workflow_task_db WHERE id=?", (task_id,),
            ).fetchone()
            elapsed_h = float("inf")
            if trow and trow[0]:
                from sop_hub.utils.time import BEIJING_TZ, parse_any_timestamp
                try:
                    created = parse_any_timestamp(str(trow[0]))
                    now = datetime.now(BEIJING_TZ)
                    elapsed_h = (now - created).total_seconds() / 3600.0
                except Exception:
                    elapsed_h = 0.0
            # #issue-20260619 Fix C:朝钢等满宽限期仍无候选 → 从 95306 反查权威车
            # 合成候选,跑链到 ingest/match(skip_upload,不自动上传鞍钢)。给真通知单
            # 先到的机会(宽限期内仍 pending),仅 chaoyang_steel(靠 tyrjzsx 船锚)。
            synth_msg = ""
            if (project == "chaoyang_steel"
                    and elapsed_h >= _INSPECTION_TEXT_TRIGGER_SYNTH_GRACE_HOURS):
                trigger_ts = (input_json.get("received_datetime") or "")
                if not trigger_ts:
                    _r = conn.execute(
                        "SELECT received_datetime FROM message_inbox WHERE id=?",
                        (input_json.get("message_inbox_id"),)).fetchone()
                    trigger_ts = (_r[0] if _r else "") or ""
                from sop_hub.sop.inspection_95306_synthesize import (
                    synthesize_candidate_from_95306,
                )
                synth = synthesize_candidate_from_95306(
                    conn, project_id=project, ship=ship, dest=dest,
                    expected=expected, trigger_ts=str(trigger_ts),
                    group_name=str(input_json.get("group_name") or ""),
                    message_id=message_id, rail_db_path=_RAIL_DB_PATH,
                )
                synth_msg = synth.get("message", "")
                if synth.get("status") == "ok":
                    synth_cand_id = synth.get("candidate_id")

            if synth_cand_id:
                # 合成成功:指向触发器自己的 inbox 跑链(skip_upload)
                cand_inbox_id = input_json.get("message_inbox_id")
                cand_id = synth_cand_id
                cand_msg_id = message_id
            elif elapsed_h <= _INSPECTION_TEXT_TRIGGER_TIMEOUT_HOURS:
                return {
                    "action": "executed", "status": "pending",
                    "output_json": {
                        "stage": "waiting_inspection_notice",
                        "ship": ship, "destination": dest,
                        "expected_count": expected,
                        "elapsed_hours": round(elapsed_h, 2),
                        "synth_attempt": synth_msg,
                        "note": "检装车通知单图未到,保持 pending 等下一轮",
                    },
                }
            else:
                # 超时且合成也未成:发群通知,task 退 skipped
                try:
                    from sop_hub.sop.send_excel import send_to_wechat
                    send_to_wechat(
                        target="[GROUP013]",
                        message=(
                            f"⚠️ 检装车文本触发器超时未等到通知单\n"
                            f"{ship} {dest} 预期 {expected} 节\n"
                            f"已等 {elapsed_h:.1f} 小时,通知单图未到,需人工排查\n"
                            f"95306合成:{synth_msg}"
                        ),
                        file_path=None,
                    )
                except Exception:
                    pass
                return {
                    "action": "executed", "status": "skipped",
                    "output_json": {
                        "stage": "waiting_inspection_notice_timeout",
                        "ship": ship, "destination": dest,
                        "expected_count": expected,
                        "elapsed_hours": round(elapsed_h, 2),
                        "synth_attempt": synth_msg,
                    },
                }
        else:
            # 找到候选 → 指向候选的 inbox 跑既有检验链
            cand_id = cand["id"]
            cand_inbox_id, cand_msg_id = _find_candidate_inbox(
                conn, cand_id, str(cand["message_id"] or ""))
            if not cand_inbox_id:
                return {
                    "action": "executed", "status": "pending",
                    "output_json": {
                        "stage": "candidate_found_no_inbox",
                        "candidate_id": cand_id, "ship": ship,
                        "note": "候选无 inbox 链接,等下一轮(可能图还在入库)",
                    },
                }
    finally:
        conn.close()

    # #145:定向到本船触发器解析出的候选(多船图时别让核心 LIMIT 1 选错船)
    # Fix C:合成候选 → skip_upload(只 ingest/match,不自动上传鞍钢)
    chain_res = _execute_chaoyang_inspection_chain(
        {"message_inbox_id": cand_inbox_id},
        cand_msg_id, db_path=db_path, task_id=task_id, candidate_id=cand_id,
        skip_upload=bool(synth_cand_id),
    )
    # 附上文本侧预期车数做交叉校验
    out = chain_res.get("output_json") or {}
    if isinstance(out, dict):
        out["text_trigger"] = {
            "ship": ship, "destination": dest,
            "expected_count": expected,
            "delegated_to_candidate": cand_id,
            "candidate_source": "95306_synthesized" if synth_cand_id else "inspection_notice",
            "auto_upload": not bool(synth_cand_id),
        }
        chain_res["output_json"] = out
    # Fix C:合成路径成功 ingest/match 后,发群提醒"已自动从95306恢复,待人工确认上传"
    if synth_cand_id and chain_res.get("status") in ("succeeded", "skipped"):
        try:
            from sop_hub.sop.send_excel import send_to_wechat
            send_to_wechat(
                target="[GROUP013]",
                message=(
                    f"✅ 检装车通知单未到,已从 95306 自动反查合成入库\n"
                    f"{ship} {dest} 预期 {expected} 节\n"
                    f"已 ingest/match,⚠️未自动上传鞍钢——请人工核对后再走标准上传+反查"
                ),
                file_path=None,
            )
        except Exception:
            pass
    return chain_res


def _execute_chaoyang_inspection_chain_multi(
    input_json: dict[str, Any],
    message_id: str,
    *,
    db_path: Path,
    task_id: int = 0,
) -> dict[str, Any]:
    """#145:一 inbox 多候选(多船检车单图拆 N 组)时,对每个候选各跑一次链。

    根因:`_execute_chaoyang_inspection_chain` 老行为 LIMIT 1 只跑首候选(wx_753
    4 船图只跑首船,宝腾海当时靠手工补)。本包装层找该 inbox 全部 status='candidate'
    的候选,逐个 candidate_id 定向跑核心链。单候选时退化为直接调核心(零行为变化)。

    候选级重试仍归 pending_match_verifier(按 candidate_id 各自重试),本层只负责
    首轮把每船都 kick 一次。
    """
    import sqlite3 as _sql

    inbox_id = input_json.get("message_inbox_id")
    if not inbox_id:
        return {"action": "failed", "status": "failed",
                "error_message": "no message_inbox_id in input_json"}

    conn = _sql.connect(str(db_path))
    conn.row_factory = _sql.Row
    try:
        cands = conn.execute(
            "SELECT id, ship_name FROM inspection_ingestion_candidates "
            "WHERE (message_id = (SELECT message_id FROM message_inbox WHERE id=?) "
            "   OR id IN (SELECT inspection_candidate_id FROM message_inbox WHERE id=?)) "
            "   AND candidate_status='candidate' "
            "ORDER BY created_at ASC",
            (inbox_id, inbox_id),
        ).fetchall()
    finally:
        conn.close()

    # 0 或 1 个 → 退化为老路径(0 个时核心返回 no candidate 的 failed,保持原语义)
    if len(cands) <= 1:
        return _execute_chaoyang_inspection_chain(
            input_json, message_id, db_path=db_path, task_id=task_id,
        )

    per_candidate = []
    any_failed = False
    for c in cands:
        r = _execute_chaoyang_inspection_chain(
            input_json, message_id, db_path=db_path, task_id=task_id,
            candidate_id=c["id"],
        )
        per_candidate.append({
            "candidate_id": c["id"], "ship": c["ship_name"],
            "status": r.get("status"),
            "stage": (r.get("output_json") or {}).get("stage"),
            "error": r.get("error_message"),
        })
        if r.get("status") == "failed":
            any_failed = True

    return {
        "action": "executed",
        "status": "failed" if any_failed else "succeeded",
        "output_json": {
            "stage": "multi_candidate_fanout",
            "candidate_count": len(cands),
            "per_candidate": per_candidate,
        },
    }


def _execute_chaoyang_inspection_chain(
    input_json: dict[str, Any],
    message_id: str,
    *,
    db_path: Path,
    task_id: int = 0,
    candidate_id: str | None = None,
    skip_upload: bool = False,
) -> dict[str, Any]:
    """R78: 朝阳钢铁检装车通知单 → release_batch 匹配 → wagon_shipments → excel。

    #145:candidate_id 显式定向单个候选(多船图一 inbox 多候选时,每船各跑各的);
    不传则沿用老行为(按 inbox 选 status='candidate' 的 LIMIT 1)。

    #98 一体化:进来即真跑(写库 / 写文件)。多/无匹配 → candidate 标 pending_review
    并 task 退 skipped(这就是人工 review 入口)。步骤:
      1. 从 message_inbox_id 找 inspection_ingestion_candidates 行
      2. 读 VLM 抽取 JSON,过滤 defect=true 的车(排车不入 wagon_shipments)
      3. 用 match_release_batch_by_ship_destination_cargo 找匹配 batch
      4. 单一匹配 → 用车号逐一查 95306 拿 ydid,直插 wagon_shipments
      5. 多匹配/无匹配 → 把 candidate 标 pending_review,task 退 skipped
      6. wagon_shipments 写完 → 重算 shipped_weight + 生成 excel
      7. 返回 output_json 含:matched_batch_id / wagon_count / excel_path
    """
    import hashlib
    import json as _json
    import sqlite3 as _sql
    from sop_hub.sop.match_release_batch import (
        match_release_batch_by_ship_destination_cargo,
    )

    # 用模块常量(而非内联硬编码)→ 可被测试 monkeypatch _RAIL_DB_PATH 注入 fixture 95306。
    RAIL_DB = Path(_RAIL_DB_PATH)

    inbox_id = input_json.get("message_inbox_id")
    if not inbox_id:
        return {"action": "failed", "status": "failed",
                "error_message": "no message_inbox_id in input_json"}

    conn = _sql.connect(str(db_path))
    conn.row_factory = _sql.Row
    try:
        # ── 1. Find candidate ──────────────────────────────────────
        # #145:candidate_id 显式定向 → 直接取该候选(多船图每船各跑各的)。
        if candidate_id:
            cand = conn.execute(
                "SELECT * FROM inspection_ingestion_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
        else:
            # #130:多组 candidate(复合检车单按 ship rule 拆 N 组)时,优先取
            # candidate_status='candidate' 的(已成功匹配 release_batch),跳过
            # pending_review 的兜底组。LIMIT 1 — 多组并发由 _multi 包装层循环
            # candidate_id 处理(#145)。
            cand = conn.execute(
                "SELECT * FROM inspection_ingestion_candidates "
                "WHERE (message_id = (SELECT message_id FROM message_inbox WHERE id=?) "
                "   OR id IN (SELECT inspection_candidate_id FROM message_inbox WHERE id=?)) "
                "   AND candidate_status='candidate' "
                "ORDER BY created_at DESC LIMIT 1",
                (inbox_id, inbox_id),
            ).fetchone()
            if not cand:
                # 兜底:没 matched candidate,也试 pending(老行为)
                cand = conn.execute(
                    "SELECT * FROM inspection_ingestion_candidates "
                    "WHERE message_id = (SELECT message_id FROM message_inbox WHERE id=?) "
                    "   OR id IN (SELECT inspection_candidate_id FROM message_inbox WHERE id=?) "
                    "ORDER BY created_at DESC LIMIT 1",
                    (inbox_id, inbox_id),
                ).fetchone()
        if not cand:
            return {"action": "failed", "status": "failed",
                    "error_message": f"no inspection candidate for inbox {inbox_id}"}
        cand_d = dict(cand)
        candidate_id = cand_d["id"]
        ship = (cand_d.get("ship_name") or "").strip()
        dest = (cand_d.get("destination") or "").strip()
        cargo = (cand_d.get("cargo_name") or "").strip()
        project_id = (cand_d.get("project_id") or "").strip()
        if not (ship and dest and project_id):
            # #issue-20260619:ship/dest/project 推不出来 ≠ 硬失败。候选此前 infer
            # 已挂 pending_review(no-ship 必挂起铁律),链应**优雅跳过**、保住候选给
            # 人工/后续 infer 接管,而不是把 task 标 failed 污染状态、看着像系统崩了。
            # 与下方 match 失败(907-913)同一姿势:挂 pending_review + skipped。
            conn.execute(
                "UPDATE inspection_ingestion_candidates "
                "SET candidate_status='pending_review', "
                "    reason='candidate_missing_ship_dest_project', "
                "    updated_at=datetime('now') WHERE id=?",
                (candidate_id,),
            )
            conn.commit()
            return {
                "action": "executed",
                "status": "skipped",
                "output_json": {
                    "stage": "candidate_field_check",
                    "candidate_id": candidate_id,
                    "candidate_status": "pending_review",
                    "reason": "candidate_missing_ship_dest_project",
                    "missing": {"ship": ship, "dest": dest, "project": project_id},
                },
            }

        # ── 2. Read rows ──────────────────────────────────────────
        # #130 (2026-06-08):优先 candidate.payload_json(split 后的 sub_payload,
        # 只含本组真车),否则 fallback extraction_json_path 全图(单组场景)。
        # 关键:复合检车单图,chain 必须按本组真车走,不能拿全图前 4 个表头
        # 伪车号查 95306(95306 永远查不到)。
        cand_payload_raw = cand_d.get("payload_json") or ""
        all_rows = []
        ext_data: dict = {}
        if cand_payload_raw:
            try:
                _cp = _json.loads(cand_payload_raw)
                if isinstance(_cp, dict) and _cp.get("rows"):
                    all_rows = _cp.get("rows") or []
                    ext_data = _cp  # 用 candidate.payload_json 同时充当 ext_data
                                    # 让 footer 等读取兜底
            except Exception:
                all_rows = []
        if not all_rows:
            ext_path = cand_d.get("extraction_json_path")
            if not ext_path or not Path(ext_path).exists():
                return {"action": "failed", "status": "failed",
                        "error_message": f"extraction JSON not found: {ext_path}"}
            ext_data = _json.loads(Path(ext_path).read_text(encoding="utf-8"))
            all_rows = ext_data.get("rows") or []
        # 排车 / 缺陷车不入。cargo_info_raw 是手写标注(船名/收货代理等),
        # 不影响是否真车 — 真车的判断是 car_no 非空 + 非 defect。
        loading_rows = [r for r in all_rows
                        if r.get("car_no") and not r.get("defect")]
        loading_car_nos = [str(r.get("car_no") or "").strip()
                           for r in loading_rows if r.get("car_no")]
        if not loading_car_nos:
            return {"action": "failed", "status": "failed",
                    "error_message": "no non-defect car_no in extraction JSON"}

        # ── 3. Match release_batch ─────────────────────────────────
        m = match_release_batch_by_ship_destination_cargo(
            project_id=project_id, ship_name=ship,
            destination_station=dest, cargo_name=cargo, db_conn=conn,
            allow_pending_freight_for_facts=True,
        )
        if m.reason in ("no_open_batch", "no_match", "multiple_candidates"):
            conn.execute(
                "UPDATE inspection_ingestion_candidates "
                "SET candidate_status='pending_review', reason=?, "
                "    updated_at=datetime('now') WHERE id=?",
                (m.reason, candidate_id),
            )
            conn.commit()
            return {
                "action": "executed",
                "status": "skipped",
                "output_json": {
                    "stage": "match_release_batch",
                    "match_result": m.to_dict(),
                    "candidate_id": candidate_id,
                    "loading_car_count": len(loading_car_nos),
                    "candidate_status": "pending_review",
                },
            }

        matched_batch_id = m.matched_release_batch_id

        # ── 4a. 95306 时间窗反推 → 真装车权威列表(治本) ───────────────
        # VLM 可能把真装车误标排车(无理由 defect)→ 通知单 loading 漏算 1 车。
        # 用 1-2 个装车号反查 95306 ticketed_at,建窗,窗内同到站+货名的所有
        # 制票车号才是权威。前 4 个查不到 = 还没制单,挂起等(业务约定)。
        # 2026-06-03 宝腾海 53→52 漏触发即此处治本。
        from sop_hub.sop.inspection_window_recover import (
            autocorrect_config_from_env,
            persist_car_no_corrections,
            recover_loading_cars_via_window,
        )
        all_notice_car_nos = [
            str(r.get("car_no") or "").strip()
            for r in all_rows if r.get("car_no")
        ]
        # #144:锚点票时间下界 = 通知时间 - 12h。同车同到站历史有旧票,
        # 不带下界会锚到上一批的票,整窗错位。
        min_ticketed_at = None
        notice_ts = (
            conn.execute(
                "SELECT received_datetime FROM message_inbox WHERE id=?",
                (inbox_id,),
            ).fetchone() or [None]
        )[0] or cand_d.get("created_at") or ""
        if notice_ts:
            from datetime import datetime as _dt, timedelta as _td
            try:
                parsed = _dt.strptime(
                    str(notice_ts).replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
                min_ticketed_at = (parsed - _td(hours=12)).strftime(
                    "%Y-%m-%d %H:%M:%S")
            except ValueError:
                min_ticketed_at = None
        recover = recover_loading_cars_via_window(
            rail_db_path=str(RAIL_DB),
            loading_car_nos=loading_car_nos,
            all_notice_car_nos=all_notice_car_nos,
            destination=dest,
            cargo_pattern="%铁矿%",
            max_anchor_attempts=4,
            window_minutes=120,
            min_ticketed_at=min_ticketed_at,
            **autocorrect_config_from_env(),
        )
        if recover["status"] == "no_ticket_yet":
            # 95306 还没制票 → 候选挂 pending_95306_match,延迟验证器后续重试
            conn.execute(
                "UPDATE inspection_ingestion_candidates "
                "SET candidate_status='pending_95306_match', "
                "    reason=?, updated_at=? WHERE id=?",
                (recover["message"], _now_iso(), candidate_id),
            )
            conn.commit()
            return {
                "action": "executed",
                "status": "skipped",
                "output_json": {
                    "stage": "95306_window_recover",
                    "recover_result": recover,
                    "matched_release_batch_id": matched_batch_id,
                    "candidate_id": candidate_id,
                    "candidate_status": "pending_95306_match",
                },
            }
        if recover["status"] == "anomaly":
            # 窗内有通知单没列的车号 → 拼批/窗太宽,需人工裁决
            conn.execute(
                "UPDATE inspection_ingestion_candidates "
                "SET candidate_status='pending_review', "
                "    reason=?, updated_at=datetime('now') WHERE id=?",
                (recover["message"], candidate_id),
            )
            conn.commit()
            return {
                "action": "executed",
                "status": "skipped",
                "output_json": {
                    "stage": "95306_window_recover",
                    "recover_result": recover,
                    "matched_release_batch_id": matched_batch_id,
                    "candidate_id": candidate_id,
                    "candidate_status": "pending_review",
                },
            }
        # status == "ok":用 95306 权威装车列表替换 VLM 抽的(治 52→53)
        # #检装车号95306自动核对纠错:若反推时把通知单错号唯一配到了窗内真号
        # (真排车数==异常数==N,且各自唯一近似),写审计(旧号→新号、来源=95306
        # 窗口、置信依据)并把候选车号集合更正成真号,候选随之干净匹配照常推进。
        if recover.get("corrections"):
            persist_car_no_corrections(
                conn,
                candidate_id=candidate_id,
                release_batch_id=matched_batch_id,
                corrections=recover["corrections"],
                anchor_car_no=recover.get("anchor_car_no"),
                anchor_ticketed_at=recover.get("anchor_ticketed_at"),
                window_minutes=recover.get("window_minutes"),
            )
            conn.commit()
        authoritative_loading = recover["loading_car_nos"]
        if authoritative_loading:
            # 跟通知单 footer.zhuangche_jieshu 再 sanity 一道
            expected = int((ext_data.get("footer") or {}).get("zhuangche_jieshu") or 0)
            if expected and len(authoritative_loading) != expected:
                conn.execute(
                    "UPDATE inspection_ingestion_candidates "
                    "SET candidate_status='pending_review', "
                    "    reason=?, updated_at=datetime('now') WHERE id=?",
                    (
                        f"window 反推 {len(authoritative_loading)} 车 ≠ "
                        f"通知单 footer.zhuangche_jieshu {expected}",
                        candidate_id,
                    ),
                )
                conn.commit()
                return {
                    "action": "executed",
                    "status": "skipped",
                    "output_json": {
                        "stage": "95306_window_recover",
                        "recover_result": recover,
                        "footer_zhuangche_jieshu": expected,
                        "matched_release_batch_id": matched_batch_id,
                        "candidate_id": candidate_id,
                        "candidate_status": "pending_review",
                    },
                }
            # 2026-06-06:保留通知单物理顺序(列车机车头到尾)。
            # authoritative_loading 是 95306 ticketed_at 序,直接用会让发运
            # excel 顺序错乱。业务铁律:车号顺序 = 通知单 seq 顺序,因为这是
            # 列车装载物理排序,跟收货端核对、人工查表都按这个顺序来。
            # 处理:用 authoritative_loading 做"装/排"判断,但车号排序按
            # 通知单 all_notice_car_nos 的位置。authoritative 里"通知单没有"
            # 的车号(last-minute 换车 / OCR 错字 → 95306 实有 1739479 但
            # 通知单 OCR 成 1799479),没法精准插回原位,追加到末尾。
            auth_set = set(authoritative_loading)
            notice_kept = [c for c in all_notice_car_nos if c in auth_set]
            kept_set = set(notice_kept)
            extras = [c for c in authoritative_loading if c not in kept_set]
            loading_car_nos = notice_kept + extras

        # ── 4. Query 95306 per car_no, build wagon_shipments rows ─
        if not RAIL_DB.exists():
            return {"action": "failed", "status": "failed",
                    "error_message": f"95306 DB not found: {RAIL_DB}"}
        rail = _sql.connect(f"file:{RAIL_DB}?mode=ro", uri=True)
        rail.row_factory = _sql.Row
        try:
            # 逐车号查 95306 拿权威货票行(朝阳西到站 + 货描含铁矿 + 最近一张),
            # 收成 ticket dict 列表。落库统一交给 wagon_ingest.ingest_wagons —— 标载/
            # hph/列序号/重算都在那一处口径(2026-06-28 收敛掉这里的手搓 INSERT)。
            tickets: list[dict] = []
            no_match = []
            for cno in loading_car_nos:
                row = rail.execute("""
                    SELECT car_no, ydid, czydid, car_model, marked_weight, cargo_count,
                           cargo_name, transport_mode_code, transport_mode_name,
                           container_no_raw, container_numbers_json,
                           origin_name, destination_name, ticketed_at, departed_at,
                           arrived_at, delivered_at, status_name, latest_stage_key,
                           latest_stage_name, latest_event_time, accepted_at, loaded_at,
                           raw_core_json
                    FROM shipments
                    WHERE car_no=? AND destination_name=?
                      AND cargo_name LIKE '%铁矿%'
                    ORDER BY ticketed_at DESC LIMIT 1
                """, (cno, dest)).fetchone()
                if not row:
                    no_match.append(cno)
                    continue
                tickets.append(dict(row))
        finally:
            rail.close()

        # ── 4b. 统一入库口(ingest_wagons)──────────────────────────
        # build_wagon_row 内部已处理 标载(车型推)/ hph / dispatch_status。
        # dispatch_status='completed' 维持朝阳"发运即定稿"口径(已交付的会被
        # build_wagon_row 升级成 confirmed_received)。recompute=False:下方 step 5
        # 显式 compute_for_release_batch,这里不重复重算。
        from sop_hub.sop.wagon_ingest import ingest_wagons, gen_wagon_id
        ingest_res = ingest_wagons(
            matched_batch_id, tickets,
            project_id=project_id, ship_name=ship, db_path=db_path,
            dispatch_status="completed",
            source_message_id=cand_d.get("message_id") or "",
            recompute=False,
        )
        inserted = ingest_res.get("new", 0)

        # ── 4c. 对单关系(ingest_wagons 不写 match 表)── 逐车补
        # shipment_release_batch_matches。wid = gen_wagon_id(同 ingest 口径,FK 对齐)。
        # match_source 按项目派生,审计区分朝阳 vs 中唐自动链。
        ms = (
            "zhongtang_inspection_chain"
            if project_id == "zhongtang_special_steel"
            else "chaoyang_inspection_chain"
        )
        for t in tickets:
            ydid = t.get("ydid") or ""
            wid = gen_wagon_id(ydid, matched_batch_id)
            mid = hashlib.sha1(
                f"{matched_batch_id}|{ydid}".encode()
            ).hexdigest()[:24]
            try:
                conn.execute("""INSERT INTO shipment_release_batch_matches
                    (id, release_batch_id, wagon_shipment_id, ydid,
                     waybill_no, wagon_no, container_no, match_source)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (mid, matched_batch_id, wid, ydid,
                     "", t.get("car_no") or "", t.get("container_no_raw") or "", ms))
            except _sql.IntegrityError:
                pass

        conn.commit()

        # ── 4.5. Guard:必须全部 loading 车都已落库才能继续 ───────────
        # 业务铁律:95306 票延迟时(支票晚到 1-2 h),不能用半套数据
        # 出 excel。期望数 = footer.zhuangche_jieshu(VLM 抽的实装),
        # 兜底 = len(loading_car_nos)。
        # 实际数 = DB 中 batch_id+本次 car_nos 的实际行数(idempotent
        # 重跑也对 — 不依赖 inserted 计数)。
        expected_count = (
            int((ext_data.get("footer") or {}).get("zhuangche_jieshu") or 0)
            or len(loading_car_nos)
        )
        placeholders = ",".join("?" * len(loading_car_nos))
        db_count = conn.execute(
            f"SELECT COUNT(*) FROM wagon_shipments "
            f"WHERE batch_id=? AND car_no IN ({placeholders})",
            (matched_batch_id, *loading_car_nos),
        ).fetchone()[0]

        if db_count < expected_count:
            # 票尚未全部到达 → 挂 pending_95306_match,等延迟验证器
            conn.execute(
                "UPDATE inspection_ingestion_candidates "
                "SET candidate_status='pending_95306_match', "
                "    reason=?, updated_at=datetime('now') "
                "WHERE id=?",
                (f"waiting_95306_tickets:{db_count}/{expected_count}",
                 candidate_id),
            )
            conn.commit()
            return {
                "action": "executed",
                "status": "skipped",
                "output_json": {
                    "stage": "waiting_95306_tickets",
                    "matched_release_batch_id": matched_batch_id,
                    "candidate_id": candidate_id,
                    "loading_car_count": len(loading_car_nos),
                    "expected_count": expected_count,
                    "actual_in_db_count": db_count,
                    "wagon_shipments_inserted": inserted,
                    "wagon_shipments_no_95306_match": no_match,
                    "candidate_status": "pending_95306_match",
                    "note": (f"票尚未全到 95306({db_count}/{expected_count}),"
                             f"候选已挂起,等延迟验证(6h timeout)"),
                },
            }

        if m.matched_dispatch_status == "pending_freight":
            conn.execute(
                "UPDATE inspection_ingestion_candidates "
                "SET candidate_status='pending_freight_info', "
                "    release_batch_id=?, reason=?, updated_at=datetime('now') "
                "WHERE id=?",
                (
                    matched_batch_id,
                    "wagon_facts_ingested_awaiting_freight_info",
                    candidate_id,
                ),
            )
            conn.commit()
            return {
                "action": "executed",
                "status": "skipped",
                "output_json": {
                    "stage": "awaiting_freight_info",
                    "matched_release_batch_id": matched_batch_id,
                    "matched_dispatch_status": m.matched_dispatch_status,
                    "candidate_id": candidate_id,
                    "loading_car_count": len(loading_car_nos),
                    "expected_count": expected_count,
                    "actual_in_db_count": db_count,
                    "wagon_shipments_inserted": inserted,
                    "wagon_shipments_no_95306_match": no_match,
                    "candidate_status": "pending_freight_info",
                    "note": "车列事实已入库;批次仍缺货运信息,暂停发运 Excel/发送/上传",
                },
            }

        # ── 5. Recompute shipped_weight (yaml rule) ───────────────
        try:
            from sop_hub.sop.shipped_weight import compute_for_release_batch
            sw = compute_for_release_batch(matched_batch_id, db_path=str(db_path))
        except Exception as exc:
            sw = {"ok": False, "error": str(exc)}

        # ── 6. Generate excel ─────────────────────────────────────
        # excel 只导出"本次单子"的车号,不是 batch 历史累计。
        try:
            from sop_hub.sop.departure_excel import generate_departure_excel
            excel_result = generate_departure_excel(
                matched_batch_id, project_id=project_id,
                car_nos=loading_car_nos,
            )
            excel_info = {
                "path": excel_result.output_path,
                "wagon_count": excel_result.wagon_count,
                "error": excel_result.error,
            }
        except Exception as exc:
            excel_info = {"path": "", "wagon_count": 0, "error": str(exc)}

        # ── 7. Mark candidate matched(先标后发,这样算"第几列"时
        #         当前候选已计入)─────────────────────────────────
        conn.execute(
            "UPDATE inspection_ingestion_candidates "
            "SET candidate_status='matched', release_batch_id=?, "
            "    reason='auto_matched_by_chain', "
            "    updated_at=datetime('now') WHERE id=?",
            (matched_batch_id, candidate_id),
        )
        conn.commit()

        # "第几列" = 该批次的**实际发车趟次**(wagon_shipments 不同制票日去重)。
        # #issue-20260623:旧逻辑数 matched 候选数 → 有的趟走 95306 同步/合成没留
        # matched 候选(或留了 0 车垃圾候选)就漏数:今早实际第 5 列被数成"第三列"
        # (中联发 5 趟 6-12/14/16/19/23,但只有 3 条 matched 候选)。改按真实发车日去重,
        # 与发运事实一致。(cross-day 制票罕见;朝阳西短链各趟单日,够用。)
        try:
            nth = conn.execute(
                "SELECT COUNT(DISTINCT substr(ticketed_at, 1, 10)) "
                "FROM wagon_shipments WHERE batch_id=?",
                (matched_batch_id,),
            ).fetchone()[0]
            if nth <= 0:
                nth = 1
        except Exception:
            nth = 1

        # ── 6b. 发运 excel = 这张简装车通知单(source_image)的数据展现 ──────
        # 全项目统一(2026-06-27 用户确认):同一张检装车单上的所有船 → 一张**分块** excel
        # (每船一块:船名+列头+本次车号+该批次货运 footer),**一张单只发一次**。
        # 逐船候选都 matched 后才合成发;没齐先等(不发半截)。幂等键 = 检装车单(source_image)。
        import os as _os
        send_info: dict[str, Any] = {"skipped": True}
        try:
            _specs, _all_matched, _ships = _event_excel_batch_specs(candidate_id, db_path)
            if not _specs:
                send_info = {"skipped": True, "reason": "本检装车单无可导出的批次"}
            elif not _all_matched:
                send_info = {"skipped": True,
                             "reason": f"同检装车单 {len(_ships)} 船未全 matched({'/'.join(_ships)}),等齐再合成发"}
            else:
                from sop_hub.sop.departure_excel import generate_multibatch_departure_excel
                _mb = generate_multibatch_departure_excel(
                    _specs, project_id=project_id, db_path=str(db_path))
                excel_info = {"path": _mb.output_path, "wagon_count": _mb.wagon_count,
                              "error": _mb.error, "batches": len(_specs)}
                if not _mb.output_path or _mb.error:
                    send_info = {"skipped": False, "error": _mb.error or "multibatch excel 生成失败"}
                else:
                    from sop_hub.sop.external_action_log import (
                        build_idempotency_key, get_action_by_key,
                        plan_external_action, mark_external_action_executed,
                    )
                    # 幂等键 = **批次集 + 总车数**(2026-06-28 共享群工单):同一张检装车单
                    # 若两群各发一份(铁晟 + 中唐发运群),source_image 不同但批次/车数相同 →
                    # 按批次集 → 同键 → 只发一次,杜绝两群重复发。
                    _bkey = ",".join(sorted(rbid for rbid, _ in _specs))
                    _idem = build_idempotency_key(
                        project_id, "send_shipping_excel_wechat",
                        f"batches:{_bkey}:{_mb.wagon_count}")
                    _prev = get_action_by_key(_idem, db_path=db_path)
                    if _prev and str(_prev.get("action_status")) == "executed":
                        send_info = {"skipped": True, "idempotency_key": _idem,
                                     "reason": "该检装车单发运excel已发(幂等跳过)"}
                    else:
                        from sop_hub.sop.send_excel import send_to_wechat
                        target = _resolve_send_target(project_id)
                        if not target:
                            send_info = {"skipped": True, "reason": "no send_report target in yaml"}
                        else:
                            plan_external_action(
                                db_path=db_path, workflow_task_id=task_id,
                                message_id=message_id or "", project_id=project_id,
                                action_type="send_shipping_excel_wechat",
                                idempotency_key=_idem, target_system="wechat",
                                artifact_path=_mb.output_path,
                            )
                            msg = (f"发运数据 {'+'.join(_ships)} 共{_mb.wagon_count}车"
                                   f"(按批次分块,{len(_specs)}船)")
                            sr = send_to_wechat(target=target, message=msg, file_path=_mb.output_path)
                            if sr.success:
                                mark_external_action_executed(
                                    _idem, db_path=db_path,
                                    response_json={"sent": True, "target": target},
                                    artifact_path=_mb.output_path)
                            send_info = {"skipped": False, "target": target, "message": msg,
                                         "success": sr.success,
                                         "output_tail": (sr.output or "")[-300:],
                                         "error": sr.error, "idempotency_key": _idem}
        except Exception as exc:
            send_info = {"skipped": False, "error": str(exc)}

        # ── 6c. 上传到鞍钢门户(朝阳钢铁专属)+ 立即反查 ──────────
        # 业务铁律:每次上传必须紧跟一次查询验证(详见 docs/business-rules/
        # chaoyang_upload_verify.md)。upload_and_verify 内置 verify。
        # 跟 6b 不冲突 — 发 excel 给微信群是给人看,上传 ansteel 是给收货系统。
        upload_info: dict[str, Any] = {"skipped": True}
        if skip_upload:
            # #issue-20260619 Fix C:合成候选(从 95306 反查、无人工通知单)只跑到
            # ingest/match,**不自动对外上传鞍钢**,留人工确认后再走标准上传。
            upload_info = {"skipped": True,
                           "reason": "skip_upload(95306合成候选,待人工确认再上传)"}
        elif project_id == "chaoyang_steel" and loading_car_nos:
            # P2#1 审计(2026-06-28):鞍钢上传也走 external_action_log,否则上传全程
            # 无审计痕迹(原工单"无上传动作"误判即源于此)。action_type 独立标识。
            from sop_hub.sop.external_action_log import (
                build_idempotency_key as _bik,
                plan_external_action as _pea,
                mark_external_action_executed as _mae,
            )
            _uidem = _bik(project_id, "upload_ansteel_consignee",
                          f"{matched_batch_id}:{len(loading_car_nos)}")
            try:
                _pea(db_path=db_path, workflow_task_id=task_id, message_id=message_id or "",
                     project_id=project_id, action_type="upload_ansteel_consignee",
                     idempotency_key=_uidem, target_system="ansteel_portal")
            except Exception:
                pass
            try:
                from sop_hub.external.chaoyang_ansteel.upload_wagons import (
                    upload_and_verify,
                )
                ur = upload_and_verify(
                    db_path=str(db_path),
                    batch_id=matched_batch_id,
                    ship_name=ship,
                    car_nos=loading_car_nos,  # 用本次单子的精确车号集
                )
                upload_info = {
                    "skipped": False,
                    "success": ur.success,
                    "uploaded": ur.uploaded_count,
                    "server_returned": ur.server_returned_count,
                    "verified": ur.verified_count,
                    "missing": ur.missing_car_nos,
                    "extra": ur.extra_car_nos,
                    "plan": ur.plan_summary,
                    "error": ur.error,
                }
                if ur.success:
                    try:
                        _mae(_uidem, db_path=db_path, response_json={
                            "uploaded": ur.uploaded_count, "verified": ur.verified_count,
                            "server_returned": ur.server_returned_count})
                    except Exception:
                        pass
            except Exception as exc:
                upload_info = {"skipped": False, "error": str(exc)}

        # ── 6d. Lifecycle close(#85 downstream 2026-06-06)──────────
        # shipped_is_completed 模式(朝阳钢铁)且本批已发满 → 立刻关 batch。
        # full_track_to_received 模式(吉林/中唐/九三)留 in_progress,
        # 等 95306 到货 daemon 后续标 confirmed_received 再关。
        lifecycle_info: dict[str, Any] = {"mode": "", "closed_now": False}
        try:
            mode = _resolve_lifecycle_mode(project_id)
            lifecycle_info["mode"] = mode
            if mode == "shipped_is_completed" and sw.get("ok"):
                shipped_t = float(sw.get("shipped_weight_tons") or 0)
                planned_t = float(sw.get("planned_tons") or 0)
                # 小余量 0.5 吨容忍(浮点 + 计量误差),宁可早关一点也别永远不关
                if planned_t > 0 and shipped_t >= planned_t - 0.5:
                    # #125: 朝阳 shipped_is_completed mode → 跳到 closed
                    # (跳过 tracking/delivered/confirmed_received,因为这种 mode
                    # 业务上"发完即结算",不等 95306 到货)
                    conn.execute(
                        "UPDATE release_batches "
                        "SET dispatch_status='closed', "
                        "    dispatch_status_note='lifecycle.shipped_is_completed: "
                        "shipped_weight >= planned',"
                        "    dispatch_status_updated_at=datetime('now'),"
                        "    updated_at=datetime('now') WHERE id=?",
                        (matched_batch_id,),
                    )
                    conn.commit()
                    lifecycle_info["closed_now"] = True
                    lifecycle_info["reason"] = (
                        f"shipped {shipped_t:.1f}/{planned_t:.1f} tons"
                    )
        except Exception as exc:
            lifecycle_info["error"] = str(exc)

        # ── 真实 status:不再硬编码 succeeded(2026-06-28 6b丢失/假完成工单)──────
        # 鞍钢上传(关键对外动作)或发运 excel(6b)**该成功却没成功** → status=failed,
        # 让 verifier 重试(两者都幂等,重试不会重复动作),杜绝静默假完成 + 6b 丢失。
        # 合法跳过(已发/无批次/等兄弟船齐/无配置)不算失败。
        warnings: list[str] = []
        _upload_problem = (not upload_info.get("skipped")) and (not upload_info.get("success"))
        if _upload_problem:
            warnings.append(f"鞍钢上传未成功: {upload_info.get('error') or '见 consignee_upload'}")
        _send_reason = str(send_info.get("reason") or "")
        _send_legit_skip = bool(send_info.get("skipped")) and any(
            k in _send_reason for k in ("已发", "无可导出", "等齐", "send_report target"))
        _send_problem = (not send_info.get("success")) and not _send_legit_skip
        if _send_problem:
            warnings.append(f"发运excel未发: {_send_reason or send_info.get('error') or '未知'}")
        _status = "failed" if (_upload_problem or _send_problem) else "succeeded"

        return {
            "action": "executed",
            "status": _status,
            "output_json": {
                "matched_release_batch_id": matched_batch_id,
                "candidate_id": candidate_id,
                "ship_name": ship,
                "nth_loading": nth,
                "loading_car_count": len(loading_car_nos),
                "non_loading_car_count": len(all_rows) - len(loading_car_nos),
                "wagon_shipments_inserted": inserted,
                "wagon_shipments_no_95306_match": no_match,
                "shipped_weight": sw,
                "excel": excel_info,
                "lifecycle": lifecycle_info,
                "send": send_info,
                "consignee_upload": upload_info,  # 新:ansteel 上传 + 反查
                "warnings": warnings,             # 新:上传/发送 该成功未成功 暴露在此
            },
        }

    finally:
        conn.close()


def _resolve_send_target(project_id: str) -> str | None:
    """Read send target from yaml report_delivery_flow.send_report.

    优先级:target_group > target_contact。群比个人触达面广、可追溯,业务
    缺省就该发到群。target_contact 留作 fallback(yaml 只配了联系人时)。
    2026-06-03 宝腾海漏发数据单发群、只发郭东北的根因即此原优先级反了。

    程序不区分"测试 / 生产"阶段(只看配置)—— 2026-06-04 收敛掉原
    send_test_report / send_production_report 双轨,统一一个 send_report
    节点;要切收件人改 yaml 即可。
    """
    try:
        import yaml as _yaml
        from sop_hub.sop.departure_excel import _find_yaml_for_project
        yp = _find_yaml_for_project(project_id)
        raw = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    flows = (raw.get("flows") or {}).get("report_delivery_flow") or {}
    for step in flows.get("steps") or []:
        if step.get("node") != "send_report":
            continue
        groups = step.get("target_group") or []
        if groups:
            g = str(groups[0]).strip()
            # wx-ui-bridge 搜群必须用 [GROUPxxx] 完整格式(方括号是搜索码的
            # 一部分),裸搜 GROUP013 会命中别的会话。yaml 漏写就在这兜底。
            if g.startswith("GROUP") and not g.startswith("["):
                g = f"[{g}]"
            return g
        contacts = step.get("target_contact") or []
        if contacts:
            return str(contacts[0])
    return None


# ── Lifecycle resolution(2026-06-06 #85)─────────────────────────────────
# 从 project_meta.lifecycle 读出项目跟踪模式:
#   "shipped_is_completed"  — 短链(发运即完);chaoyang
#   "full_track_to_received" — 全程(跟到 confirmed_received);jilin/中唐/九三
# 调用方:发运 chain 结束后看这个字段决定:
#   shipped_is_completed → release_batch.dispatch_status → completed 立刻
#   full_track_to_received → 留在 in_progress,等 95306 到货 daemon 再标 completed


def _resolve_lifecycle_mode(project_id: str) -> str:
    """Return lifecycle mode for project (default: full_track_to_received)."""
    try:
        import yaml as _yaml
        from sop_hub.sop.departure_excel import _find_yaml_for_project
        yp = _find_yaml_for_project(project_id)
        raw = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    except Exception:
        return "full_track_to_received"
    lc = ((raw.get("project_meta") or {}).get("lifecycle") or {})
    mode = str(lc.get("mode") or "").strip()
    if mode in ("shipped_is_completed", "full_track_to_received"):
        return mode
    return "full_track_to_received"




def run_pending_workflow_tasks(
    *,
    limit: int = 10,
    task_type: str | None = None,
    db_path: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run all pending workflow tasks, optionally filtered by type.

    #98 一体化:跑就真跑。是否调用本函数(以及跑哪些 task_type)由调用方决定
    (daemon:freight enrich 默认自动跑;外部提交链需 --run-chains)。

    Returns: {ran, succeeded, failed, skipped, results: [...]}
    """
    db = _get_db_path(db_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    if task_type and task_type.endswith(":"):
        # #143:前缀匹配一族 task_type(如 "inspection_text_trigger:" 匹配所有
        # 后缀船名的扇出 task)。末尾冒号是前缀约定。
        rows = conn.execute(
            "SELECT id FROM workflow_task_db WHERE task_status = 'pending' "
            "AND task_type LIKE ? ORDER BY id LIMIT ?",
            (task_type + "%", limit),
        ).fetchall()
    elif task_type:
        rows = conn.execute(
            "SELECT id FROM workflow_task_db WHERE task_status = 'pending' AND task_type = ? ORDER BY id LIMIT ?",
            (task_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM workflow_task_db WHERE task_status = 'pending' ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()

    results = []
    counts = {"ran": 0, "succeeded": 0, "failed": 0, "skipped": 0}

    for row in rows:
        r = run_workflow_task(row["id"], db_path=db, force=force)
        results.append(r)
        counts["ran"] += 1
        s = r.get("status", "unknown")
        if s in counts:
            counts[s] += 1

    return {**counts, "results": results}


def get_task_with_message(
    task_id: int, *, db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Get a workflow_task row joined with its message_inbox row."""
    db = _get_db_path(db_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        """SELECT wt.*, mi.text_content, mi.group_name, mi.received_datetime
           FROM workflow_task_db wt
           LEFT JOIN message_inbox mi ON wt.message_inbox_id = mi.id
           WHERE wt.id = ?""",
        (task_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── CLI ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="workflow_task executor CLI")
    p.add_argument("--run-task", type=int, help="Execute a single task by id")
    p.add_argument("--run-pending", action="store_true", help="Execute pending tasks")
    p.add_argument("--task-type", type=str, help="Filter --run-pending by task_type")
    p.add_argument("--limit", type=int, default=10, help="Max tasks for --run-pending")
    p.add_argument("--force", action="store_true",
                   help="Re-run already succeeded/failed tasks")
    p.add_argument("--get", type=int, help="Get task with message_inbox join")
    p.add_argument("--db", type=str, default=None)

    args = p.parse_args()
    _db = Path(args.db) if args.db else None

    if args.run_task:
        result = run_workflow_task(
            args.run_task, db_path=_db, force=args.force,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.run_pending:
        result = run_pending_workflow_tasks(
            limit=args.limit, task_type=args.task_type,
            db_path=_db, force=args.force,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.get:
        task = get_task_with_message(args.get, db_path=_db)
        if task:
            print(json.dumps(task, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"error": "not found"}, ensure_ascii=False))

    else:
        p.print_help()
