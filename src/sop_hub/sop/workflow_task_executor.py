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
from datetime import datetime, timezone
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
        elif task_type == "chaoyang_inspection_chain":
            result = _execute_chaoyang_inspection_chain(
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

    _mark_task(
        db, task_id, status,
        output_json=json.dumps(output, ensure_ascii=False) if output else None,
        error_message=error,
        retry_count=(rd.get("retry_count") or 0) + 1,
    )

    # Update message_inbox
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
            # 合同号/计划号没在 release_batches 里 — 通知单还没来,留 skipped 重试
            return {"action": "skipped", "status": "skipped", "output_json": res}
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

    # applied / no_op → success
    return {
        "action": "executed",
        "status": "succeeded",
        "output_json": res,
    }


def _execute_chaoyang_inspection_chain(
    input_json: dict[str, Any],
    message_id: str,
    *,
    db_path: Path,
    task_id: int = 0,
) -> dict[str, Any]:
    """R78: 朝阳钢铁检装车通知单 → release_batch 匹配 → wagon_shipments → excel。

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

    RAIL_DB = Path(
        "/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3"
    )

    inbox_id = input_json.get("message_inbox_id")
    if not inbox_id:
        return {"action": "failed", "status": "failed",
                "error_message": "no message_inbox_id in input_json"}

    conn = _sql.connect(str(db_path))
    conn.row_factory = _sql.Row
    try:
        # ── 1. Find candidate ──────────────────────────────────────
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
            return {"action": "failed", "status": "failed",
                    "error_message": f"candidate missing fields: "
                                     f"ship={ship!r} dest={dest!r} project={project_id!r}"}

        # ── 2. Read extraction JSON, filter defect ────────────────
        ext_path = cand_d.get("extraction_json_path")
        if not ext_path or not Path(ext_path).exists():
            return {"action": "failed", "status": "failed",
                    "error_message": f"extraction JSON not found: {ext_path}"}
        ext_data = _json.loads(Path(ext_path).read_text(encoding="utf-8"))
        all_rows = ext_data.get("rows") or []
        # 排车 / 缺陷车不入 wagon_shipments
        loading_rows = [r for r in all_rows if not r.get("defect")]
        loading_car_nos = [str(r.get("car_no") or "").strip()
                           for r in loading_rows if r.get("car_no")]
        if not loading_car_nos:
            return {"action": "failed", "status": "failed",
                    "error_message": "no non-defect car_no in extraction JSON"}

        # ── 3. Match release_batch ─────────────────────────────────
        m = match_release_batch_by_ship_destination_cargo(
            project_id=project_id, ship_name=ship,
            destination_station=dest, cargo_name=cargo, db_conn=conn,
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
            recover_loading_cars_via_window,
        )
        all_notice_car_nos = [
            str(r.get("car_no") or "").strip()
            for r in all_rows if r.get("car_no")
        ]
        recover = recover_loading_cars_via_window(
            rail_db_path=str(RAIL_DB),
            loading_car_nos=loading_car_nos,
            all_notice_car_nos=all_notice_car_nos,
            destination=dest,
            cargo_pattern="%铁矿%",
            max_anchor_attempts=4,
            window_minutes=120,
        )
        if recover["status"] == "no_ticket_yet":
            # 95306 还没制票 → 候选挂 pending_95306_match,延迟验证器后续重试
            conn.execute(
                "UPDATE inspection_ingestion_candidates "
                "SET candidate_status='pending_95306_match', "
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
            loading_car_nos = authoritative_loading

        # ── 4. Query 95306 per car_no, build wagon_shipments rows ─
        if not RAIL_DB.exists():
            return {"action": "failed", "status": "failed",
                    "error_message": f"95306 DB not found: {RAIL_DB}"}
        rail = _sql.connect(f"file:{RAIL_DB}?mode=ro", uri=True)
        rail.row_factory = _sql.Row
        try:
            inserted = 0
            no_match = []
            for cno in loading_car_nos:
                # 找朝阳西到站 + 货描含铁矿 + ticketed_at 最近的一条
                row = rail.execute("""
                    SELECT car_no, ydid, czydid, car_model, marked_weight, cargo_count,
                           cargo_name, transport_mode_code, transport_mode_name,
                           container_no_raw, container_numbers_json,
                           origin_name, destination_name, ticketed_at, departed_at,
                           arrived_at, delivered_at, status_name, latest_stage_key,
                           latest_stage_name, latest_event_time, accepted_at, loaded_at
                    FROM shipments
                    WHERE car_no=? AND destination_name=?
                      AND cargo_name LIKE '%铁矿%'
                    ORDER BY ticketed_at DESC LIMIT 1
                """, (cno, dest)).fetchone()
                if not row:
                    no_match.append(cno)
                    continue
                r = dict(row)
                wid = hashlib.sha1(
                    f"{r['ydid']}|{matched_batch_id}".encode()
                ).hexdigest()[:24]
                try:
                    conn.execute("""INSERT INTO wagon_shipments
                        (id, batch_id, car_no, ydid, czydid, car_model, marked_weight,
                         cargo_count, cargo_name, shipper_name, consignee_name,
                         origin_name, destination_name, ticketed_at, departed_at,
                         arrived_at, delivered_at, status_name, latest_stage_key,
                         latest_stage_name, latest_event_time, accepted_at, loaded_at,
                         transport_mode_code, transport_mode_name,
                         container_no, container_numbers_json,
                         project_id, ship_name, dispatch_status,
                         source_message_id, source_group_id, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                ?,?,?,?,?,datetime('now'),datetime('now'))""",
                        (wid, matched_batch_id, cno, r['ydid'], r['czydid'],
                         r['car_model'], float(r['marked_weight']) if r['marked_weight'] else None,
                         int(r['cargo_count']) if r['cargo_count'] else None,
                         r['cargo_name'], "", "",
                         r['origin_name'], r['destination_name'], r['ticketed_at'],
                         r['departed_at'], r['arrived_at'], r['delivered_at'],
                         r['status_name'], r['latest_stage_key'], r['latest_stage_name'],
                         r['latest_event_time'], r['accepted_at'], r['loaded_at'],
                         r['transport_mode_code'], r['transport_mode_name'],
                         r['container_no_raw'] or "", r['container_numbers_json'] or "",
                         project_id, ship, "completed",
                         cand_d.get("message_id") or "", ""))
                    # match 表
                    mid = hashlib.sha1(
                        f"{matched_batch_id}|{r['ydid']}".encode()
                    ).hexdigest()[:24]
                    try:
                        conn.execute("""INSERT INTO shipment_release_batch_matches
                            (id, release_batch_id, wagon_shipment_id, ydid,
                             waybill_no, wagon_no, container_no, match_source)
                            VALUES (?,?,?,?,?,?,?,?)""",
                            (mid, matched_batch_id, wid, r['ydid'],
                             "", cno, r['container_no_raw'] or "",
                             "chaoyang_inspection_chain"))
                    except _sql.IntegrityError:
                        pass
                    inserted += 1
                except _sql.IntegrityError:
                    # 同 batch 同 ydid 已存在,跳过
                    pass
        finally:
            rail.close()

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

        # "第几列" = 该 batch 下**全部已落实**候选总数(含当前)。包含
        # matched(已经链路对上)和 matched_by_inference(推断阶段已对上但
        # 链路还没真正跑过,如今天宝腾海 06-02 那张)。之前只数 'matched'
        # 漏掉 matched_by_inference,2026-06-03 宝腾海算成"第二列"
        # (实际第三列)的根因。pending_review / pending_95306_match 这些
        # 还没落实的不算。
        try:
            nth = conn.execute(
                "SELECT COUNT(*) FROM inspection_ingestion_candidates "
                "WHERE release_batch_id=? AND candidate_status IN "
                "      ('matched','matched_by_inference')",
                (matched_batch_id,),
            ).fetchone()[0]
        except Exception:
            nth = 1

        # ── 6b. Send excel via wx-ui-bridge ─────────────────────────
        # 默认走 yaml 中的 test 模式 target(郭东北/数据单发群),不直发
        # 生产群。production 路径要 input_json 里显式给 send_mode="production"。
        send_info: dict[str, Any] = {"skipped": True}
        if excel_info.get("path") and not excel_info.get("error"):
            try:
                from sop_hub.sop.send_excel import send_to_wechat
                send_mode = str(input_json.get("send_mode") or "test")
                target = _resolve_send_target(project_id, send_mode)
                if target:
                    msg = f"{ship} 第{_cn_num(nth)}列 {excel_info['wagon_count']}车"
                    sr = send_to_wechat(
                        target=target,
                        message=msg,
                        file_path=excel_info["path"],
                    )
                    send_info = {
                        "skipped": False,
                        "mode": send_mode,
                        "target": target,
                        "message": msg,
                        "success": sr.success,
                        "output_tail": (sr.output or "")[-300:],
                        "error": sr.error,
                    }
                else:
                    send_info = {"skipped": True,
                                 "reason": f"no target_contact configured for "
                                           f"send_{send_mode}_report in yaml"}
            except Exception as exc:
                send_info = {"skipped": False, "error": str(exc)}

        # ── 6c. 上传到鞍钢门户(朝阳钢铁专属)+ 立即反查 ──────────
        # 业务铁律:每次上传必须紧跟一次查询验证(详见 docs/business-rules/
        # chaoyang_upload_verify.md)。upload_and_verify 内置 verify。
        # 跟 6b 不冲突 — 发 excel 给微信群是给人看,上传 ansteel 是给收货系统。
        upload_info: dict[str, Any] = {"skipped": True}
        if project_id == "chaoyang_steel" and loading_car_nos:
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
            except Exception as exc:
                upload_info = {"skipped": False, "error": str(exc)}

        return {
            "action": "executed",
            "status": "succeeded",
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
                "send": send_info,
                "consignee_upload": upload_info,  # 新:ansteel 上传 + 反查
            },
        }

    finally:
        conn.close()


def _resolve_send_target(project_id: str, mode: str = "test") -> str | None:
    """Read send target from yaml report_delivery_flow.

    优先级:target_group > target_contact。群比个人触达面广、可追溯,业务
    缺省就该发到群。target_contact 留作 fallback(yaml 只配了联系人时)。
    2026-06-03 宝腾海漏发数据单发群、只发郭东北的根因即此原优先级反了。

    Modes: "test" -> send_test_report.{target_group[0] or target_contact[0]}
           "production" -> send_production_report.{target_group[0] or target_contact[0]}
    """
    try:
        import yaml as _yaml
        from sop_hub.sop.departure_excel import _find_yaml_for_project
        yp = _find_yaml_for_project(project_id)
        raw = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    flows = (raw.get("flows") or {}).get("report_delivery_flow") or {}
    node_name = f"send_{mode}_report"
    for step in flows.get("steps") or []:
        if step.get("node") != node_name:
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

    if task_type:
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
