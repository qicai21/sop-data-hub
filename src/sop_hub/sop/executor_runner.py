"""Executor runner: bridge live_service monitor → SOP executor chain.

R52/R55: Connects the match/preview layer (live_service) to the execution layer
(departure_text_parser → query_95306 → create_wagon_shipments →
 departure_excel → factory_upload).

Scope:
- Only jilin_jingang_jinzhou departure_flow.
- Writes execution_preview JSON to runtime/execution_previews/.
- Does NOT refresh dispatch_board, send messages, or modify SOP YAML.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sop_hub.sop.departure_text_parser import DepartureCandidate, parse_departure_text
from sop_hub.utils.time import now_iso_beijing_compact
from sop_hub.sop.monitoring_plan_matcher import MessageEvent
from sop_hub.sop.query_95306_shipments import query_95306_shipments_by_window
from sop_hub.sop.create_wagon_shipments import (
    CreateWagonShipmentsResult,
    create_wagon_shipments_from_candidates,
)
from sop_hub.sop.shipment_query_window import ShipmentQueryResult


# ── Data model ──────────────────────────────────────────────────────────

@dataclass
class ExecutionPreview:
    """Complete dry-run or apply preview of a departure executor chain."""

    message_id: str
    group_id: str
    project_id: str
    chain: str = "departure_text → query_95306 → create_wagon_shipments"
    parsed_at: str = ""

    # Step 1: parse_departure_text
    departure_candidate: dict[str, Any] | None = None
    departure_status: str = ""

    # Step 2: query_95306_shipments_by_window
    query_result: dict[str, Any] | None = None
    query_total_candidates: int = 0

    # Step 3: create_wagon_shipments
    wagon_result: dict[str, Any] | None = None
    wagon_status: str = ""
    wagon_planned_insert: int = 0
    wagon_actual_insert: int = 0

    # Release batch lookup
    release_batch_id: str = ""
    release_batch_ship: str = ""

    # Step 4: departure_excel (R55)
    excel_path: str = ""
    excel_rows: int = 0
    excel_wagons: int = 0

    # Step 5: factory_upload (R55)
    factory_payloads: int = 0
    factory_success: int = 0
    factory_failure: int = 0
    factory_login: bool = False
    factory_login_error: str = ""

    # Step 5b: factory_verify (R55)
    factory_verified: bool = False
    factory_verify_total_match: bool = False
    factory_verify_boxes_ok: bool = False
    factory_verify_api_total: int = 0

    # Step 6: send_excel via wx-ui-bridge (R55)
    excel_sent: bool = False
    excel_target: str = ""

    # Overall
    error: str = ""
    skipped_reason: str = ""
    deferred: bool = False          # 在发运延迟窗口内,本轮不跑,等下一轮(任务保持 pending)
    deferred_until: str = ""        # 可跑的最早时刻(ISO Beijing)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "group_id": self.group_id,
            "project_id": self.project_id,
            "chain": self.chain,
            "parsed_at": self.parsed_at,
            "steps": {
                "1_parse_departure_text": {
                    "status": self.departure_status,
                    "candidate": self.departure_candidate,
                },
                "2_query_95306": {
                    "total_candidates": self.query_total_candidates,
                    "result": self.query_result,
                },
                "3_create_wagon_shipments": {
                    "status": self.wagon_status,
                    "planned_insert": self.wagon_planned_insert,
                    "actual_insert": self.wagon_actual_insert,
                    "result": self.wagon_result,
                },
                "4_departure_excel": {
                    "path": self.excel_path,
                    "rows": self.excel_rows,
                    "wagons": self.excel_wagons,
                },
                "5_factory_upload": {
                    "login_success": self.factory_login,
                    "login_error": self.factory_login_error,
                    "payloads": self.factory_payloads,
                    "success": self.factory_success,
                    "failure": self.factory_failure,
                },
                "5b_factory_verify": {
                    "verified": self.factory_verified,
                    "total_match": self.factory_verify_total_match,
                    "boxes_ok": self.factory_verify_boxes_ok,
                    "api_total": self.factory_verify_api_total,
                },
                "6_send_excel": {
                    "sent": self.excel_sent,
                    "target": self.excel_target,
                },
            },
            "release_batch": {
                "id": self.release_batch_id,
                "ship_name": self.release_batch_ship,
            },
            "error": self.error,
            "skipped_reason": self.skipped_reason,
            "deferred": self.deferred,
            "deferred_until": self.deferred_until,
        }


# ── 发运对齐延迟门(2026-06-16)─────────────────────────────────────────

def _dispatch_delay_hours(project_id: str) -> float:
    """读 yaml project_meta.dispatch_alignment_delay_hours(默认 2,0=关)。"""
    if not project_id:
        return 0.0
    try:
        import yaml as _yaml
        from sop_hub.sop.departure_excel import _find_yaml_for_project
        raw = _yaml.safe_load(
            _find_yaml_for_project(project_id).read_text(encoding="utf-8")
        ) or {}
        pm = raw.get("project_meta") or {}
        val = pm.get("dispatch_alignment_delay_hours", 2)
        return float(val) if val is not None else 0.0
    except Exception:
        return 2.0  # 读不到 yaml 时保守延迟,避免抓制单中数据


def _dispatch_delay_gate(project_id: str, ref_time: str) -> tuple[str, bool]:
    """判断本次发运是否还在对齐延迟窗口内。

    Returns (ok_at_iso, should_defer)。ok_at = ref_time + delay_hours。
    ref_time 解析失败 / delay<=0 → 不延迟(("", False))。
    """
    hours = _dispatch_delay_hours(project_id)
    if hours <= 0 or not ref_time:
        return "", False
    from datetime import datetime, timedelta
    raw = str(ref_time).strip().replace("T", " ")[:19]
    parsed = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        return "", False  # 解析不了文本时间,不卡链(宁可跑,别永久挂起)
    ok_at = parsed + timedelta(hours=hours)
    now_raw = now_iso_beijing_compact()[:19].replace("T", " ")
    try:
        now_dt = datetime.strptime(now_raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "", False
    return ok_at.isoformat(sep=" "), now_dt < ok_at


# ── DB helpers ──────────────────────────────────────────────────────────

def _resolve_sop_db_path() -> Path:
    import os

    env = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    if env:
        return Path(env)
    return Path.home() / "projects" / "repos" / "sop-data-hub" / "data" / "sop_agent.db"


def _find_release_batch(
    ship_name: str,
    destination: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, str] | None:
    """#126 (2026-06-07): phase-aware match — 只匹配 enriched/loading 的 batch。

    Returns dict with 'id', 'ship_name' on match.
    Returns None when **no open lot is available**. Caller should look at
    `find_release_batch_with_reason()` if it needs to differentiate
    "ship 没找到" vs "ship 找到了但全 all_loaded"。
    """
    rb, _reason, _candidates = find_release_batch_with_reason(
        ship_name, destination, db_path=db_path,
    )
    return rb


def find_release_batch_with_reason(
    ship_name: str,
    destination: str,
    *,
    db_path: str | Path | None = None,
) -> tuple[dict[str, str] | None, str, list[str]]:
    """Return (batch_dict|None, reason_code, candidate_batch_ids).

    reason_code:
      - "ok"               : 找到 enriched/loading batch
      - "all_loaded_full"  : ship+dest 有 batch,但全在 all_loaded 之后(plan 满)
      - "ship_not_found"   : ship 名在系统里没有
      - "no_open_lot"      : ship 有 batch,但 phase 不在 open 集(可能都 pending_freight)
    candidate_batch_ids:
      reason='all_loaded_full' 时,返回那些被过滤掉的 batch_id 让 caller 落 pending
    """
    from sop_hub.sop.lifecycle import PHASES_OPEN_TO_DEPARTURE_MATCH

    sop_path = _resolve_sop_db_path() if db_path is None else Path(db_path)
    if not sop_path.exists():
        return None, "ship_not_found", []

    open_phases = tuple(PHASES_OPEN_TO_DEPARTURE_MATCH)
    placeholders = ",".join("?" * len(open_phases))

    conn = sqlite3.connect(str(sop_path))
    conn.row_factory = sqlite3.Row
    try:
        # 先取 ship+dest 全部 batch(任何 phase)看分布
        all_rows = conn.execute(
            "SELECT id, ship_name, dispatch_status, destination_station, project, batch_sequence "
            "FROM release_batches WHERE ship_name LIKE ? AND destination_station LIKE ?",
            (f"%{ship_name}%", f"%{destination}%"),
        ).fetchall()
        if not all_rows:
            # 试 ship-only(老 fallback)
            rows2 = conn.execute(
                "SELECT id, ship_name, dispatch_status, batch_sequence FROM release_batches "
                "WHERE ship_name LIKE ?", (f"%{ship_name}%",),
            ).fetchall()
            if not rows2:
                return None, "ship_not_found", []
            all_rows = rows2

        open_rows = [r for r in all_rows if r["dispatch_status"] in open_phases]
        if open_rows:
            # 优先 loading 再 enriched(loading 已开始装,优先填满)
            open_rows.sort(
                key=lambda r: (0 if r["dispatch_status"] == "loading" else 1),
            )
            r = open_rows[0]
            return ({"id": r["id"], "ship_name": r["ship_name"] or ship_name,
                     "batch_sequence": r["batch_sequence"] or ""},
                    "ok", [])

        # ship 有 batch 但没 open 的 — 区分 all_loaded vs 其他
        beyond_loading = ["all_loaded", "tracking", "delivered",
                          "confirmed_received", "closed"]
        full_rows = [r for r in all_rows if r["dispatch_status"] in beyond_loading]
        if full_rows:
            return None, "all_loaded_full", [r["id"] for r in full_rows]
        return None, "no_open_lot", [r["id"] for r in all_rows]
    finally:
        conn.close()


# ── Core runner ─────────────────────────────────────────────────────────

def run_departure_executor_chain(
    event: MessageEvent,
    *,
    runtime_root: str | Path,
    db_path: str | Path | None = None,
) -> ExecutionPreview:
    """Execute the full departure executor chain.

    Chain: parse_departure_text → query_95306 → create_wagon_shipments
           → departure_excel → factory_upload → factory_verify → send_excel

    #98 一体化:不再分 dry/apply,跑就真跑 —— 写 wagon_shipments、生成 Excel、
    真上传工厂、反查、发微信。是否真跑由调用方(daemon --run-chains)决定;
    一旦进来就是真的。各步失败结构化记录在 ExecutionPreview.error,不抛断链。
    create_wagon_shipments 内部仍按 status 自我把关(pending_review 不写)。
    """
    rt = Path(runtime_root)
    chain_str = (
        "departure_text → query_95306 → create_wagon_shipments → "
        "departure_excel → factory_upload → factory_verify → "
        "send_excel (wx-ui-bridge)"
    )
    preview = ExecutionPreview(
        message_id=event.message_id,
        group_id=event.group_id or "",
        project_id="",
        chain=chain_str,
        parsed_at=now_iso_beijing_compact(),
    )

    # ── Step 1: parse departure text ──────────────────────────────────
    candidate = parse_departure_text(event)
    preview.departure_candidate = candidate.to_dict()
    preview.departure_status = candidate.status
    preview.project_id = candidate.project_id

    if candidate.status == "no_match":
        preview.skipped_reason = "departure_text_parser: no_match"
    elif candidate.project_id != "jilin_jingang_jinzhou":
        preview.skipped_reason = f"not jilin_jingang (project_id={candidate.project_id})"
    elif candidate.status == "incomplete":
        preview.skipped_reason = f"departure incomplete: car_count={candidate.car_count}"
    else:
        # ── Step 2: find matching release_batch (#126 phase-aware) ────
        rb = None
        match_reason = "ship_not_found"
        candidate_batch_ids: list[str] = []
        if candidate.optional_ship_name:
            rb, match_reason, candidate_batch_ids = find_release_batch_with_reason(
                candidate.optional_ship_name, candidate.destination, db_path=db_path
            )
        if rb is None:
            # #126:落 pending_match 表等用户审,不强配
            try:
                from sop_hub.sop.release_batch_pending_match import create_pending
                create_pending(
                    message_id=event.message_id,
                    reason=match_reason,
                    ship_name=candidate.optional_ship_name or "",
                    destination=candidate.destination or "",
                    car_count=candidate.car_count,
                    text_content=getattr(event, "text", "") or "",
                    received_at=getattr(event, "received_at", "") or "",
                    group_id=event.group_id or "",
                    project_id=candidate.project_id,
                    candidate_batch_ids=candidate_batch_ids,
                    db_path=db_path,
                )
            except Exception as exc:
                preview.error += f"pending_match create: {exc}; "
            human_reason = {
                "all_loaded_full": "船所有 lot 都已 all_loaded(plan 满),挂起待用户配新 lot 或挪票",
                "ship_not_found":  f"ship={candidate.optional_ship_name!r} 不在系统",
                "no_open_lot":     "船有 batch 但 phase 不在 open(enriched/loading)集",
            }.get(match_reason, match_reason)
            preview.skipped_reason = (
                f"no open release_batch for ship={candidate.optional_ship_name} "
                f"dest={candidate.destination} — {human_reason}"
            )
        else:
            preview.release_batch_id = rb["id"]
            preview.release_batch_ship = rb["ship_name"]

            # ── Step 2.5: 发运对齐延迟门(2026-06-16 设定)────────────
            # 业务事实:发运文本("六道 四平铁 马兰希望 55车")到群时,95306
            # 往往还在制单 —— 此刻对齐会抓到空车号/不全的票(就是 06-16
            # 马兰希望 发错 excel 的导火索)。配置延迟 N 小时,未到点本轮
            # 不跑 Step3-6,任务保持 pending,等下一轮重判。延迟时长走 yaml
            # project_meta.dispatch_alignment_delay_hours(默认 2,0=关)。
            _ref = candidate.message_time or getattr(event, "received_at", "") or ""
            ok_at, defer = _dispatch_delay_gate(candidate.project_id, _ref)
            if defer:
                preview.deferred = True
                preview.deferred_until = ok_at
                preview.skipped_reason = (
                    f"deferred: 发运对齐延迟窗口内(文本 {_ref[:16]},"
                    f"{ok_at[:16]} 后再与 95306 对齐),等 95306 制单完成"
                )
                return preview

            # ── Step 3: query 95306 ───────────────────────────────────
            origin = "高桥镇"
            dest = candidate.destination or "四平"
            # ref_time 给 95306 query 用,空格分隔的 naive 风格(无 tz 后缀),
            # 仓库历史约定。从 ISO Beijing 截前 19 字符 + T→空格。
            ref_time = candidate.message_time or now_iso_beijing_compact()[:19].replace("T", " ")
            query_result = query_95306_shipments_by_window(
                origin_station=origin,
                destination_station=dest,
                reference_time=ref_time,
                window_before_minutes=720,
                window_after_minutes=720,
                expected_car_count=candidate.car_count,
                project_id=candidate.project_id,
            )
            query_dict = {
                "origin_station": origin,
                "destination_station": dest,
                "reference_time": ref_time,
                "total_candidates": query_result.total_candidates,
                "exact_match_count": query_result.exact_match_count,
                "ambiguous_count": query_result.ambiguous_count,
                "expected_car_count": candidate.car_count,
                "window": {
                    "start": query_result.window.start_time,
                    "end": query_result.window.end_time,
                },
            }
            preview.query_result = query_dict
            preview.query_total_candidates = query_result.total_candidates

            # ── Step 4: create_wagon_shipments(内部按 status 自我把关)──
            # 2026-06-11 fix ②:allow_partial=True 接受 candidate_count < expected_car_count
            # 业务事实:"煤六 61 节" 这种 lane 可能是混合列车(柔远空箱 55 + 蓝鳍重箱 6),
            # 95306 反查只能命中本项目的 6 节;严格阈值会卡 chain → step 4-6 全停。
            # 真正的完整性由 step 5b factory_verify 反查工厂 list API 担保,这里放过。
            wagon_result = create_wagon_shipments_from_candidates(
                release_batch_id=preview.release_batch_id,
                departure_candidate=candidate,
                shipment_query_result=query_result,
                db_path=db_path,
                allow_partial=True,
            )
            wr_dict = wagon_result.to_dict()
            preview.wagon_result = wr_dict
            preview.wagon_status = wagon_result.status
            preview.wagon_planned_insert = wagon_result.planned_insert_count
            preview.wagon_actual_insert = wagon_result.inserted_count

            # ── Step 4b: plan-aware allocation(#111 Phase 2 2026-06-06)──
            # 集装箱多 lot 共存时,看 release_batch_dispatch_plan;有 plan 走
            # box-level 分配 + per-event excel/upload,跨 lot 自动拆箱。无 plan
            # 维持单 batch 老路径(适合 1 lot in_progress 的简单场景)。
            plan_mode = False
            event_wagon_ids: list[str] = []
            # ── 本次发车事件的 wagon_ids ──────────────────────────────
            # = 这条消息刚 INSERT(或幂等命中)的、有车号的车。与有无
            # dispatch_plan 无关 —— 发运 excel/upload 必须 per-event,单 lot
            # 项目(无 active plan)也走事件口径,绝不退化成整批。
            # 2026-06-16 修:马兰希望(无 plan)历来落整批兜底,把 lot 累计
            # 181 车全导出 = 发运 excel 三宗错(lot 标签/车数/全列)的总根。
            # 空车号(95306 制单未完)会被 `and p.wagon_no` 滤空 → event 为空
            # → 下面 step5 拒绝出表/上传,而不是吐整批。
            if wagon_result.status == "safe_to_apply":
                inserted_car_nos = [
                    p.wagon_no for p in wagon_result.plans
                    if p.action in ("insert", "skip_existing") and p.wagon_no
                ]
                if inserted_car_nos:
                    import sqlite3 as _sql
                    _c = _sql.connect(str(db_path))
                    _c.row_factory = _sql.Row
                    in_ph = ",".join("?" * len(inserted_car_nos))
                    rows = _c.execute(
                        f"SELECT id FROM wagon_shipments "
                        f"WHERE car_no IN ({in_ph}) AND batch_id=?",
                        (*inserted_car_nos, preview.release_batch_id),
                    ).fetchall()
                    event_wagon_ids = [r["id"] for r in rows]
                    _c.close()

            # ── Step 4b: plan-aware allocation(#111 Phase 2 2026-06-06)──
            # 仅多 lot 共存(有 active plan)时需要按 box 拆分到各 lot;无 plan
            # 的单 lot 项目不分配,但上面已拿到 event_wagon_ids 照样走 per-event。
            try:
                from sop_hub.sop.dispatch_plan import (
                    list_active_plans, allocate_wagons,
                )
                ship_name = preview.release_batch_ship or rb.get("ship_name") or ""
                project_id_local = candidate.project_id
                active_plans = (
                    list_active_plans(project_id_local, ship_name, db_path=db_path)
                    if (project_id_local and ship_name)
                    else []
                )
                if active_plans and event_wagon_ids:
                    # force_overwrite=True:本次新 INSERT 的 wagon,step 3 给了
                    # placeholder batch_id(指向 active plan 的一个 lot),
                    # allocate_wagons 老幂等检查会误判已分配 → 全堆 placeholder lot。
                    alloc = allocate_wagons(
                        event_wagon_ids, project_id_local, ship_name,
                        db_path=db_path, force_overwrite=True,
                    )
                    plan_mode = True
                    preview.error += "" if not alloc.error else f"plan_alloc: {alloc.error}; "
                    # #128 lifecycle 触发器:plan 装满的 batch → all_loaded
                    try:
                        from sop_hub.sop.lifecycle_transition import advance_lifecycle
                        for closed_bid in (alloc.closed_batches or []):
                            advance_lifecycle(
                                closed_bid, "all_loaded",
                                reason=f"plan 装满({ship_name})",
                                triggered_by="chain.step4b.allocate_wagons",
                                db_path=db_path,
                            )
                        # 主 batch(还在装的)→ 至少推到 loading
                        advance_lifecycle(
                            preview.release_batch_id, "loading",
                            reason=f"first wagons ingested into {ship_name}",
                            triggered_by="chain.step4.create_wagon_shipments",
                            db_path=db_path,
                        )
                    except Exception as exc:
                        preview.error += f"lifecycle_advance: {exc}; "
            except Exception as exc:
                preview.error += f"plan_alloc: {exc}; "

            # ── Step 5: departure_excel + factory_upload(仅 safe_to_apply)─
            excel_result = None
            factory_result = None
            if wagon_result.status == "safe_to_apply":
                # ── Step 4c: 回填 marked_weight + hph(2026-06-27 工单)─────
                # create_wagon 写精简行不落这俩 → upload 前校验「货票号缺/标重缺」挂起。
                # 车制票时 95306 已有,这里同 run 立即按 ydid 回填,使 upload 不被拦。
                if event_wagon_ids:
                    try:
                        from sop_hub.sop.wagon_ingest import backfill_event_fields_from_95306
                        backfill_event_fields_from_95306(event_wagon_ids, db_path=db_path)
                    except Exception as exc:
                        preview.error += f"backfill_fields: {exc}; "
                try:
                    if event_wagon_ids:
                        # 发运 excel 永远 per-event(#107):本次事件涉及的
                        # wagon_ids,跨 lot 合并一张表。不再有"整批兜底"。
                        from sop_hub.sop.departure_excel import generate_dispatch_event_excel
                        excel_result = generate_dispatch_event_excel(
                            wagon_ids=event_wagon_ids, db_path=db_path,
                        )
                        preview.excel_path = excel_result.output_path
                        preview.excel_rows = getattr(excel_result, "row_count", 0)
                        preview.excel_wagons = getattr(excel_result, "wagon_count", 0)
                    else:
                        # 本次事件无可识别车号(95306 制单未完/车号为空/未匹配)。
                        # 退化成整批 excel 是历史大坑(发错表),宁可不出表 + 报错。
                        preview.error += ("excel_skipped: 本次发车无有效车号,"
                                          "拒绝出整批表(等 95306 车号齐再重跑); ")
                except Exception as exc:
                    preview.error += f"excel: {exc}; "

                try:
                    if event_wagon_ids:
                        # 工厂上传同样 per-event:只传本次事件的箱,不传整批。
                        from sop_hub.sop.factory_upload import upload_dispatch_event_wagons
                        factory_result = upload_dispatch_event_wagons(
                            wagon_ids=event_wagon_ids,
                            project_id=candidate.project_id,
                            db_path=db_path,
                        )
                        preview.factory_login = factory_result.login_success
                        preview.factory_login_error = factory_result.login_error
                        preview.factory_payloads = factory_result.total_wagons
                        preview.factory_success = factory_result.success_count
                        preview.factory_failure = factory_result.failure_count
                    else:
                        preview.error += "factory_upload_skipped: 本次发车无有效车号; "
                except Exception as exc:
                    preview.error += f"factory_upload: {exc}; "

                # ── Step 5b: verify factory upload (R55) ──────────────
                # plan 模式时按 order_identifier_groups 逐组 verify;否则单 batch verify
                try:
                    from sop_hub.sop.factory_verify import verify_factory_upload
                    if factory_result is None:
                        pass  # 上传被跳过(本次无有效车号),无可反查
                    elif plan_mode and hasattr(factory_result, "order_identifier_groups"):
                        verify_total_match = True
                        verify_all_boxes = True
                        verify_api_total = 0
                        verify_missing = 0
                        verify_duplicate = 0
                        # 2026-06-27 工单(plan 模式与非 plan 同款 scope 修):每个 (oid,bid)
                        # 也只验**本次事件该批次的箱**,不传整批 release_batch_id(否则 verify
                        # 从 lot 累计所有箱推 expected → 门户全量误报)。用 inserted_car_nos 收窄。
                        # 2026-06-29 口径:通过条件 = 本次箱都在门户(missing==0)且各自唯一
                        # (duplicate==0);门户按计划号查必返整单全量累计 + 收货端偶发删数,
                        # 故不看 total/extra(见 factory_verify.evaluate_presence_and_uniqueness)。
                        import sqlite3 as _sqlp
                        _cp = _sqlp.connect(str(db_path)); _cp.row_factory = _sqlp.Row

                        def _event_boxes_for(_bid: str) -> set:
                            if not inserted_car_nos:
                                return set()
                            _pph = ",".join("?" * len(inserted_car_nos))
                            return {
                                _r["box_no"] for _r in _cp.execute(
                                    f"SELECT box_no FROM wagon_container_shipments "
                                    f"WHERE batch_id=? AND car_no IN ({_pph})",
                                    (_bid, *inserted_car_nos))
                                if _r["box_no"]
                            }
                        try:
                            for oid in factory_result.order_identifier_groups.keys():
                                # 每个 order_identifier 反查一次(对应 1 个 release_batch)
                                bids = factory_result.order_identifier_groups[oid]
                                for bid in bids:
                                    _evb = _event_boxes_for(bid)
                                    v = verify_factory_upload(
                                        order_id=oid, release_batch_id=bid,
                                        expected_box_numbers=_evb or None,
                                        expected_count=(len(_evb) or None),
                                    )
                                    verify_total_match = verify_total_match and v.total_match
                                    verify_all_boxes = verify_all_boxes and v.all_boxes_found
                                    verify_api_total += v.api_total
                                    verify_missing += len(v.missing_boxes)
                                    verify_duplicate += len(v.duplicate_boxes)
                        finally:
                            _cp.close()
                        preview.factory_verified = True
                        preview.factory_verify_total_match = verify_total_match
                        preview.factory_verify_boxes_ok = verify_all_boxes
                        preview.factory_verify_api_total = verify_api_total
                        if not verify_all_boxes:
                            # 文案按真实原因区分,不再一律写 "some boxes missing"
                            _vparts = []
                            if verify_missing:
                                _vparts.append(f"missing={verify_missing}")
                            if verify_duplicate:
                                _vparts.append(f"duplicate={verify_duplicate}")
                            preview.error += (
                                "verify(per-event): "
                                f"{', '.join(_vparts) or '本次箱未全部到位/不唯一'}; "
                            )
                    else:
                        # 2026-06-27 修(工单):verify 只验**本次发车事件**的箱,不验整批。
                        # 老路径传整批 release_batch_id → verify 从 lot 累计所有箱推 expected
                        # (lot02 累计 vs 门户全量 → missing/extra 几百误报,把 verify 变废)。
                        # 改:用 inserted_car_nos 把 expected 收窄到本次 80 箱。
                        event_boxes: set[str] = set()
                        if inserted_car_nos:
                            import sqlite3 as _sqlv
                            _cv = _sqlv.connect(str(db_path)); _cv.row_factory = _sqlv.Row
                            _bph = ",".join("?" * len(inserted_car_nos))
                            for _r in _cv.execute(
                                f"SELECT box_no FROM wagon_container_shipments "
                                f"WHERE batch_id=? AND car_no IN ({_bph})",
                                (preview.release_batch_id, *inserted_car_nos),
                            ):
                                if _r["box_no"]:
                                    event_boxes.add(_r["box_no"])
                            _cv.close()
                        verify = verify_factory_upload(
                            order_id="", release_batch_id=preview.release_batch_id,
                            expected_box_numbers=event_boxes or None,
                            expected_count=(len(event_boxes) or None),
                        )
                        preview.factory_verified = True
                        preview.factory_verify_total_match = verify.total_match
                        preview.factory_verify_boxes_ok = verify.all_boxes_found
                        preview.factory_verify_api_total = verify.api_total
                        # 2026-06-29 口径:闸只看"本次箱都进了门户(missing==0)且各自唯一
                        # (duplicate==0)";不看 total/extra(门户按计划号查必返整单全量累计 +
                        # 收货端偶发删数,整批对齐不可取)。详 factory_verify。
                        if not verify.all_boxes_found:
                            preview.error += (
                                f"verify: 本次{len(event_boxes)}箱 "
                                f"missing={len(verify.missing_boxes)} "
                                f"duplicate={len(verify.duplicate_boxes)}; "
                            )
                except Exception as exc:
                    preview.error += f"factory_verify: {exc}; "

                # ── Step 6: send Excel to contact (R55) ──────────────
                # 只在本次事件真出了表时才发;没出表(车号缺失)不发空消息。
                if excel_result is not None and getattr(excel_result, "output_path", ""):
                  try:
                    from sop_hub.sop.send_excel import send_to_wechat
                    # 2026-06-06:不再硬编码 "郭东北"。收件人由 jilin yaml
                    # flows.report_delivery_flow.send_report.target_group 决定
                    # (当前 ["GROUP013"] = 数据单发群)。yaml 缺配置才兜底。
                    from sop_hub.sop.workflow_task_executor import _resolve_send_target
                    target = _resolve_send_target("jilin_jingang_jinzhou") or "[GROUP013]"
                    batch_ship = preview.release_batch_ship
                    # lot 标签用 release_batch 真实 batch_sequence,不再硬编码 lot02
                    # (2026-06-16 修:文本报告 lot 永远显示 lot02);车数 = 本次事件车数。
                    lot_label = (rb.get("batch_sequence") or "lot01").strip()
                    msg = (f"吉林金钢发运数据 {batch_ship} {lot_label} "
                           f"{excel_result.wagon_count}车")
                    send_result = send_to_wechat(
                        target=target,
                        message=msg,
                        file_path=excel_result.output_path,
                    )
                    preview.excel_sent = send_result.success
                    preview.excel_target = target
                    if not send_result.success:
                        preview.error += f"send_excel: {send_result.error}; "
                  except Exception as exc:
                    preview.error += f"send_excel: {exc}; "

    # ── Write execution preview (all code paths) ──────────────────────
    previews_dir = rt / "execution_previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    out_path = previews_dir / f"{event.message_id}.json"
    out_path.write_text(
        json.dumps(preview.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return preview


def run_departure_executor_chain_if_applicable(
    event: MessageEvent,
    *,
    runtime_root: str | Path,
    db_path: str | Path | None = None,
) -> ExecutionPreview | None:
    """Convenience: run chain only if departure text matches jilin_jingang.

    Returns None if the message is not applicable.
    """
    if not event.text or not event.text.strip():
        return None
    if "四平" not in event.text:
        return None
    return run_departure_executor_chain(
        event, runtime_root=runtime_root, db_path=db_path,
    )
