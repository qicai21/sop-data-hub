"""Create wagon_shipments from 95306 shipment candidates — R45.

The final P0 executor in the departure_flow chain:

  departure_text → parse → QueryWindow → 95306 query → candidates
  → create_wagon_shipments → write to sop_agent.db

Supports repeated departures for the same ship/release_batch:
  蓝鳍 first trip 18 cars, second trip 28 cars → cumulative 46 cars.

This module:
- reads sop_agent.db (release_batches + wagon_shipments)
- reads 95306_collection.sqlite3 (shipment_release_batch_matches, read-only)
- writes sop_agent.db (INSERT wagon_shipments) only when status==safe_to_apply
- writes sop_agent.db (INSERT shipment_release_batch_matches) — local table
- updates release_batches.actual_wagon_count + dispatch_status
- does NOT write to 95306 DB — 95306 is read-only
- does NOT modify 95306 shipments table
- does NOT send messages, generate Excel/JSON, or modify SOP YAML
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sop_hub.sop.departure_text_parser import DepartureCandidate
from sop_hub.sop.shipment_query_window import ShipmentQueryResult, ShipmentCandidate


# ── Data models ──────────────────────────────────────────────────────────

@dataclass
class WagonPlan:
    """A single wagon_shipment to create."""

    ydid: str
    wagon_no: str
    waybill_no: str = ""
    container_no: str = ""
    origin_station: str = ""
    destination_station: str = ""
    cargo_name: str = ""
    ticketed_at: str = ""
    departed_at: str = ""
    arrived_at: str = ""
    delivered_at: str = ""
    current_status: str = ""
    action: str = "pending"  # insert | skip_existing | conflict_other_batch | filtered_out


@dataclass
class CreateWagonShipmentsRequest:
    """Request to create wagon_shipments from 95306 candidates.

    Fields:
      release_batch_id: target release_batches.id.
      departure_candidate: parsed departure text.
      shipment_query_result: result from query_95306_shipments_by_window.
      allow_partial: if True, allow fewer candidates than expected car_count.
      allow_existing_skip: skip wagons already in this release_batch.
    """

    release_batch_id: str
    departure_candidate: DepartureCandidate
    shipment_query_result: ShipmentQueryResult
    allow_partial: bool = False
    allow_existing_skip: bool = True


@dataclass
class CreateWagonShipmentsResult:
    """Result of a create_wagon_shipments operation.

    Fields:
      status: "safe_to_apply" | "pending_review" | "not_found" | "no_candidates"
      safe_to_apply: True if all automatic rules pass.
      planned_insert_count: number of wagons to insert.
      inserted_count: wagons actually inserted (apply mode).
      skipped_existing_count: wagons already in this release_batch.
      conflict_count: wagons in other release_batches.
      expected_car_count: from departure_candidate.car_count.
      candidate_count: from shipment_query_result.total_candidates.
      warnings: list of human-readable warning strings.
      schema_missing_fields: columns/table names absent from DB.
      plans: per-wagon WagonPlan list.
      release_batch_progress: fields to update on release_batches.
    """

    status: str
    safe_to_apply: bool = False
    planned_insert_count: int = 0
    inserted_count: int = 0
    skipped_existing_count: int = 0
    conflict_count: int = 0
    expected_car_count: int = -1
    candidate_count: int = 0
    warnings: list[str] = field(default_factory=list)
    schema_missing_fields: list[str] = field(default_factory=list)
    plans: list[WagonPlan] = field(default_factory=list)
    release_batch_progress: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "safe_to_apply": self.safe_to_apply,
            "planned_insert_count": self.planned_insert_count,
            "inserted_count": self.inserted_count,
            "skipped_existing_count": self.skipped_existing_count,
            "conflict_count": self.conflict_count,
            "expected_car_count": self.expected_car_count,
            "candidate_count": self.candidate_count,
            "warnings": list(self.warnings),
            "schema_missing_fields": list(self.schema_missing_fields),
            "plans": [
                {
                    "wagon_no": p.wagon_no,
                    "waybill_no": p.waybill_no,
                    "container_no": p.container_no,
                    "action": p.action,
                }
                for p in self.plans
            ],
            "release_batch_progress": self.release_batch_progress,
        }


# ── Helpers ─────────────────────────────────────────────────────────────

def _generate_departure_id(release_batch_id: str, ship_name: str) -> str:
    """Generate a deterministic departure_id from release_batch + ship."""
    return hashlib.sha1(f"{release_batch_id}|{ship_name}".encode()).hexdigest()[:16]


def _gen_wagon_id(ydid: str, release_batch_id: str) -> str:
    """Generate a deterministic wagon_shipments.id."""
    return hashlib.sha1(f"{ydid}|{release_batch_id}".encode()).hexdigest()[:24]


def _resolve_sop_db_path(db_path: str | Path | None = None) -> Path:
    import os
    if db_path:
        return Path(db_path)
    env = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    if env:
        return Path(env)
    return Path.cwd() / "data" / "sop_agent.db"


def _resolve_rail_db_path(rail_db_path: str | Path | None = None) -> Path:
    import os
    if rail_db_path:
        return Path(rail_db_path)
    env = os.environ.get("OPS_HUB_DB_95306_PATH") or os.environ.get("DB_95306_PATH")
    if env:
        return Path(env)
    return (
        Path.home()
        / "projects" / "repos" / "rail95306-sync" / "runtime" / "95306_collection.sqlite3"
    )


def _filter_by_station_cargo(
    candidates: list[ShipmentCandidate],
    departure: DepartureCandidate,
) -> list[ShipmentCandidate]:
    """Narrow candidates by origin/destination/cargo match with departure context.

    When candidate_count > expected car_count, this filters to the best-matching
    subset using station and cargo similarity heuristics.
    """
    dest = departure.destination
    if not dest:
        return candidates

    # Prefer candidates whose destination matches the departure
    matched = [c for c in candidates if dest in (c.destination_station or "")]
    if matched and len(matched) <= len(candidates):
        return matched

    # Also try cargo match as tiebreaker
    if departure.raw_text:
        cargo_keywords = _cargo_keywords(departure.raw_text)
        if cargo_keywords:
            cargo_matches = [
                c for c in candidates
                if any(kw in (c.cargo_name or "") for kw in cargo_keywords)
            ]
            if cargo_matches and len(cargo_matches) < len(candidates):
                return cargo_matches

    return candidates


def _cargo_keywords(raw_text: str) -> list[str]:
    """Extract cargo keywords from departure text."""
    keywords = []
    if "镍" in raw_text:
        keywords.append("镍")
    if "铁" in raw_text:
        keywords.append("铁")
    if "矿" in raw_text:
        keywords.append("矿")
    if "粉" in raw_text:
        keywords.append("粉")
    return keywords


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


# ── Core executor ────────────────────────────────────────────────────────

def create_wagon_shipments_from_candidates(
    *,
    release_batch_id: str,
    departure_candidate: DepartureCandidate,
    shipment_query_result: ShipmentQueryResult,
    allow_partial: bool = False,
    allow_existing_skip: bool = True,
    db_path: str | Path | None = None,
    rail_db_path: str | Path | None = None,
) -> CreateWagonShipmentsResult:
    """Create wagon_shipments rows from 95306 shipment candidates.

    #98 一体化:不再分 dry_run/apply。计划完成后,**status==safe_to_apply 即直接
    写库**;status==pending_review(数量对不上、需人工裁决,如多 lot 分票)则只返回
    计划、不写库 —— 这就是唯一保留的"人工 review 前预览"语义,由 status 自然驱动,
    不靠 flag。

    Args:
      release_batch_id: target release_batches.id.
      departure_candidate: parsed departure text.
      shipment_query_result: from query_95306_shipments_by_window.
      allow_partial: if True, accept fewer candidates than car_count.
      allow_existing_skip: if True, skip already-existing wagons.
      db_path: path to sop_agent.db.
      rail_db_path: path to 95306_collection.sqlite3.

    Returns:
      CreateWagonShipmentsResult.  写库仅在 safe_to_apply 时发生。
    """
    sop_path = _resolve_sop_db_path(db_path)

    result = CreateWagonShipmentsResult(
        status="no_candidates",
        expected_car_count=departure_candidate.car_count,
        candidate_count=shipment_query_result.total_candidates,
    )

    # ── 1. Check release_batch exists ────────────────────────────────
    sop_conn = sqlite3.connect(str(sop_path))
    sop_conn.row_factory = sqlite3.Row
    try:
        rb = sop_conn.execute(
            "SELECT * FROM release_batches WHERE id = ?", (release_batch_id,)
        ).fetchone()
        if rb is None:
            result.status = "not_found"
            result.warnings.append(f"release_batch_id not found: {release_batch_id}")
            return result
        ship_name = rb["ship_name"] or ""
        # 吉林金钢集装箱业务以箱级表为唯一事实源。旧 wagon_shipments 仅保留
        # 迁移前审计快照，不能再被新入库或分票流程改写。
        jilin_container_only = (rb["project"] or "") == "jilin_jingang_jinzhou"

        # ── 2. Get existing wagon_shipments for this batch ───────────
        existing_ws: dict[str, str] = {}  # key → wagon_id
        existing_wagon_keys: set[str] = set()
        try:
            source_table = "wagon_container_shipments" if jilin_container_only else "wagon_shipments"
            for row in sop_conn.execute(
                f"SELECT id, car_no, waybill_no, ydid FROM {source_table} WHERE batch_id = ?",
                (release_batch_id,),
            ):
                existing_ws[row["id"]] = row["car_no"] or ""
                # 去重键 = ydid(发运唯一键),**绝不用 car_no**:车号会跨船复用
                # (实证:吉林40车的16个复用车号在蓝鳍6-21等→若按车号会被误判重复 skip)
                existing_wagon_keys.add(row["ydid"] or "")
        except sqlite3.OperationalError:
            pass  # wagon_shipments table doesn't exist yet

        # ── 3. Get all existing wagon_shipments (cross-batch conflict check) ──
        existing_cross_batch: dict[str, str] = {}  # ydid → batch_id(按 ydid,非 car_no)
        try:
            source_table = "wagon_container_shipments" if jilin_container_only else "wagon_shipments"
            for row in sop_conn.execute(
                f"SELECT ydid, batch_id FROM {source_table} WHERE ydid IS NOT NULL AND ydid != ''"
            ):
                yd = row["ydid"]
                if yd and row["batch_id"] != release_batch_id:
                    existing_cross_batch[yd] = row["batch_id"]
        except sqlite3.OperationalError:
            pass

        # ── 4. Check wagon_shipments schema ──────────────────────────
        ws_columns: set[str] = set()
        if not jilin_container_only:
            try:
                ws_columns = {
                    r[1] for r in sop_conn.execute("PRAGMA table_info(wagon_shipments)").fetchall()
                }
            except sqlite3.OperationalError:
                result.schema_missing_fields.append("table:wagon_shipments")
                result.status = "schema_missing"
                return result

        required_cols = [
            "id", "departure_id", "batch_id", "car_no", "car_model", "cargo_name",
            "origin_name", "destination_name", "ticketed_at", "departed_at",
            "arrived_at", "delivered_at", "confirmed_received_at",
            "container_no", "waybill_no", "project_id", "ship_name",
            "dispatch_status", "source_message_id", "source_group_id",
        ]
        if not jilin_container_only:
            for col in required_cols:
                if col not in ws_columns:
                    result.schema_missing_fields.append(f"column:wagon_shipments.{col}")

        # ── 5. Get candidates and filter ─────────────────────────────
        candidates = list(shipment_query_result.candidates)
        expected = departure_candidate.car_count

        # ── 5a. No candidates → early return ────────────────────────
        if len(candidates) == 0:
            result.status = "no_candidates"
            result.safe_to_apply = False
            result.warnings.append("No candidates in query result")
            return result

        # ── 5b. If count > expected, try filtering ──────────────────
        if len(candidates) > expected and expected > 0:
            candidates = _filter_by_station_cargo(candidates, departure_candidate)

        # ── 5b. Build per-wagon plans ───────────────────────────────
        plans: list[WagonPlan] = []
        for c in candidates:
            plan = WagonPlan(
                ydid=c.ydid,
                wagon_no=c.wagon_no,
                waybill_no=c.waybill_no,
                container_no=c.container_no,
                origin_station=c.origin_station,
                destination_station=c.destination_station,
                cargo_name=c.cargo_name,
                ticketed_at=c.ticketed_at,
                departed_at=c.departed_at,
                arrived_at=c.arrived_at,
                delivered_at=c.delivered_at,
                current_status=c.current_status,
            )

            # ── Check existing same-batch(按 ydid 去重)──────────────
            if allow_existing_skip and plan.ydid and plan.ydid in existing_wagon_keys:
                plan.action = "skip_existing"
                result.skipped_existing_count += 1
                plans.append(plan)
                continue

            # ── Check cross-batch conflict(按 ydid,非 car_no)────────
            if plan.ydid and plan.ydid in existing_cross_batch:
                plan.action = "conflict_other_batch"
                result.conflict_count += 1
                plans.append(plan)
                continue

            plan.action = "insert"
            plans.append(plan)

        result.plans = plans
        insert_plans = [p for p in plans if p.action == "insert"]
        result.planned_insert_count = len(insert_plans)

        # 集装箱事实必须可追溯到 "ydid + 车号"。95306 有时先同步到运单/箱号、
        # 稍后才回填 car_no；这段时间绝不能把计划数伪报为已入库，也不能推进批次。
        missing_identity = [
            plan for plan in insert_plans
            if (
                not plan.ydid
                or not plan.wagon_no
                or (jilin_container_only and not plan.container_no)
            )
        ]
        if missing_identity:
            result.status = "pending_review"
            result.safe_to_apply = False
            result.warnings.append(
                "Missing required shipment identity (ydid/car_no"
                + ("/container_no" if jilin_container_only else "")
                + "): "
                + ", ".join(plan.ydid or "<missing-ydid>" for plan in missing_identity)
            )
            return result

        # ── 6. Determine status ──────────────────────────────────────
        if result.planned_insert_count == expected and expected > 0:
            result.status = "safe_to_apply"
            result.safe_to_apply = True
        elif result.planned_insert_count + result.skipped_existing_count == expected:
            result.status = "safe_to_apply"
            result.safe_to_apply = True
            result.warnings.append(
                f"Planned insert ({result.planned_insert_count}) + "
                f"existing ({result.skipped_existing_count}) = expected ({expected})"
            )
        elif result.planned_insert_count > expected and expected > 0:
            result.status = "pending_review"
            result.safe_to_apply = False
            result.warnings.append(
                f"Planned insert ({result.planned_insert_count}) > "
                f"expected ({expected}). Filtering may be needed."
            )
        elif result.planned_insert_count < expected and expected > 0:
            if allow_partial:
                result.status = "safe_to_apply"
                result.safe_to_apply = True
                result.warnings.append(
                    f"Partial: {result.planned_insert_count}/{expected} cars. "
                    f"allow_partial=True accepted."
                )
            else:
                result.status = "pending_review"
                result.safe_to_apply = False
                result.warnings.append(
                    f"Planned insert ({result.planned_insert_count}) < "
                    f"expected ({expected}). Set allow_partial=True to proceed."
                )
        else:
            # expected == -1 or 0 (no car count specified)
            result.status = "safe_to_apply"
            result.safe_to_apply = True

        # ── 7. Release batch progress fields ─────────────────────────
        rb_columns = {
            r[1] for r in sop_conn.execute("PRAGMA table_info(release_batches)").fetchall()
        }
        current_wagon_count = int(rb["actual_wagon_count"] or 0)
        new_total = current_wagon_count + result.planned_insert_count
        result.release_batch_progress = {
            "actual_wagon_count": new_total,
        }
        if "dispatch_status" in rb_columns:
            # #125 lifecycle:新枚举 8 值,wagon ingest 推到 loading;
            # 状态机的进一步推进(all_loaded / tracking / delivered)由
            # chain step 4b 后的 advance_lifecycle 调用接管。
            result.release_batch_progress["dispatch_status"] = "loading"
            from sop_hub.utils.time import now_iso_beijing_compact as _now_bj
            result.release_batch_progress["dispatch_status_updated_at"] = _now_bj()

        # ── 8. 需人工裁决(pending_review)→ 只返回计划,不写库 ──────────
        if not result.safe_to_apply:
            return result

        # ── 9. safe_to_apply → 直接写 sop_agent.db ───────────────────
        inserted = 0
        departure_id = _generate_departure_id(release_batch_id, ship_name)
        from sop_hub.utils.time import now_iso_beijing_compact
        now = now_iso_beijing_compact()

        # 装车线路(2026-06-16 设定):发运文本 lane_or_track 归一为中文大写正名
        # (六道/6道/煤6 → 煤六;港7/7道 → 七道),整列同一次发车共用。只落车级表。
        from sop_hub.sop.departure_text_parser import canonicalize_loading_line
        loading_line = canonicalize_loading_line(
            getattr(departure_candidate, "lane_or_track", "") or ""
        )
        if not jilin_container_only:
            try:
                _wcols = {r[1] for r in sop_conn.execute("PRAGMA table_info(wagon_shipments)")}
                if "loading_line" not in _wcols:
                    sop_conn.execute("ALTER TABLE wagon_shipments ADD COLUMN loading_line TEXT")
                if "dispatch_train_code" not in _wcols:
                    sop_conn.execute("ALTER TABLE wagon_shipments ADD COLUMN dispatch_train_code TEXT")
                sop_conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_wagon_shipments_dispatch_train_code "
                    "ON wagon_shipments(dispatch_train_code)"
                )
            except Exception as _exc:
                result.warnings.append(f"ensure wagon shipment extension columns failed: {_exc}")

        for plan in ([] if jilin_container_only else insert_plans):
            wagon_id = _gen_wagon_id(plan.ydid, release_batch_id)
            # cargo_count = 该车箱数。shipped_weight_rule(集装箱业务)按
            # `cargo_count × 单箱重` 算已发重量,不写这个字段 → 已发恒为 0、
            # 看板显示不全(2026-06-13 吉林金钢复盘根因)。从 container_numbers_json
            # 数箱,fallback container_no 按 "/" 拆。整车业务无箱 → 0,其规则也不用它。
            import json as _cc_json
            _raw_cnj = getattr(plan, "container_numbers_json", "") or ""
            _boxes: list[str] = []
            if _raw_cnj:
                try:
                    _boxes = [b for b in _cc_json.loads(_raw_cnj) if b]
                except Exception:
                    _boxes = []
            if not _boxes and plan.container_no:
                _boxes = [b.strip() for b in str(plan.container_no).split("/") if b.strip()]
            cargo_count = len(_boxes)
            try:
                # #123 (2026-06-07): 把 ydid 一起写进去
                # 老代码漏了 ydid,导致 jilin lot06 39 车 ydid 全空,新表迁移失败。
                # ydid 是 95306 运单唯一 id,业务上 wagon 必须有它才能做对账/反查。
                sop_conn.execute(
                    """INSERT INTO wagon_shipments (
                        id, departure_id, batch_id, car_no, car_model,
                        cargo_name, origin_name, destination_name,
                        ticketed_at, departed_at, arrived_at,
                        delivered_at, confirmed_received_at,
                        container_no, waybill_no, ydid, cargo_count,
                        project_id, ship_name, dispatch_status,
                        source_message_id, source_group_id, loading_line
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        wagon_id, departure_id, release_batch_id,
                        plan.wagon_no, "",
                        plan.cargo_name, plan.origin_station, plan.destination_station,
                        plan.ticketed_at, plan.departed_at, plan.arrived_at,
                        plan.delivered_at, "",
                        plan.container_no, plan.waybill_no, plan.ydid, cargo_count,
                        departure_candidate.project_id, ship_name, "pending",
                        departure_candidate.message_id, departure_candidate.group_id,
                        loading_line,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                result.warnings.append(f"Duplicate wagon: {plan.wagon_no} (ydid={plan.ydid})")
                continue

        sop_conn.commit()
        result.inserted_count = inserted

        # ── 9.5 #123 Phase 3 (2026-06-07):集装箱业务双写 wagon_container_shipments ──
        # 同 wagon_shipments 写完后,拆 box 写新表:每 box 一行,box.batch_id = 主 lot
        # (split 由后续 allocate_wagons 改 box.batch_id 调整)
        try:
            from sop_hub.sop.wagon_container_shipments import (
                ensure_schema as _ensure_wcs, is_container_business_project,
            )
            if is_container_business_project(departure_candidate.project_id):
                _ensure_wcs(db_path=db_path)
                import hashlib as _hash
                import json as _json
                inserted_container_ydids: set[str] = set()
                for plan in result.plans:
                    if plan.action not in ("insert", "skip_existing"):
                        continue
                    if not plan.ydid or not plan.wagon_no:
                        continue
                    # 拆 box(优先 container_numbers_json,fallback "/")
                    raw_cnj = getattr(plan, "container_numbers_json", "") or ""
                    boxes: list[str] = []
                    if raw_cnj:
                        try:
                            boxes = [b for b in _json.loads(raw_cnj) if b]
                        except Exception:
                            boxes = []
                    if not boxes and plan.container_no:
                        boxes = [b.strip() for b in str(plan.container_no).split("/") if b.strip()]
                    for idx, box in enumerate(boxes, start=1):
                        row_id = _hash.sha1(
                            f"{plan.wagon_no}|{box}|{plan.ydid}".encode()
                        ).hexdigest()[:24]
                        try:
                            cursor = sop_conn.execute(
                                """INSERT OR IGNORE INTO wagon_container_shipments (
                                    id, car_no, box_no, box_position, ydid, waybill_no,
                                    batch_id, ticketed_at, departed_at, arrived_at, delivered_at,
                                    origin_name, destination_name, cargo_name,
                                    project_id, ship_name, dispatch_status,
                                    source_message_id, source_group_id
                                ) VALUES (?,?,?,?,?,?, ?, ?,?,?,?, ?,?,?, ?,?,?, ?,?)""",
                                (
                                    row_id, plan.wagon_no, box, idx, plan.ydid, plan.waybill_no,
                                    release_batch_id,
                                    plan.ticketed_at, plan.departed_at, plan.arrived_at,
                                    plan.delivered_at,
                                    plan.origin_station, plan.destination_station,
                                    plan.cargo_name,
                                    departure_candidate.project_id, ship_name, "in_progress",
                                    departure_candidate.message_id, departure_candidate.group_id,
                                ),
                            )
                            if cursor.rowcount == 1:
                                inserted_container_ydids.add(plan.ydid)
                        except sqlite3.IntegrityError:
                            pass
                sop_conn.commit()
                if jilin_container_only:
                    # 吉林金钢没有车级双写；唯一可报告的成功数是本次实际写入
                    # 箱级事实的独立运单数，绝不能拿计划数冒充。
                    result.inserted_count = len(inserted_container_ydids)
        except Exception as exc:
            result.warnings.append(f"wagon_container_shipments dual-write failed: {exc}")

        try:
            from sop_hub.sop.dispatch_train_code import assign_dispatch_train_codes
            train_res = assign_dispatch_train_codes(sop_conn)
            if train_res.get("updated"):
                sop_conn.commit()
        except Exception as exc:
            result.warnings.append(f"dispatch_train_code assignment failed: {exc}")

        # ── 10. Update release_batches progress ──────────────────────
        set_clauses = []
        params: dict[str, Any] = {"id": release_batch_id}
        for field, val in result.release_batch_progress.items():
            if field in rb_columns:
                set_clauses.append(f"{field} = :{field}")
                params[field] = val
        if set_clauses:
            sql = f"UPDATE release_batches SET {', '.join(set_clauses)} WHERE id = :id"
            sop_conn.execute(sql, params)
            sop_conn.commit()

        # ── 11. Write shipment_release_batch_matches to sop_agent.db ─
        # 吉林金钢的唯一事实源是箱级表；这里的 wagon_shipment_id 无法指向一个
        # 真实车级行，禁止再造孤儿关联。
        if jilin_container_only:
            return result

        # Ensure the table exists
        sop_conn.execute("""
            CREATE TABLE IF NOT EXISTS shipment_release_batch_matches (
                id TEXT PRIMARY KEY,
                release_batch_id TEXT NOT NULL,
                wagon_shipment_id TEXT NOT NULL,
                ydid TEXT NOT NULL,
                waybill_no TEXT DEFAULT '',
                wagon_no TEXT NOT NULL,
                container_no TEXT DEFAULT '',
                match_source TEXT DEFAULT 'departure_text_match',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for plan in insert_plans:
            match_id = hashlib.sha1(
                f"{release_batch_id}|{plan.ydid}".encode()
            ).hexdigest()[:24]
            wagon_id = _gen_wagon_id(plan.ydid, release_batch_id)
            try:
                sop_conn.execute(
                    """INSERT INTO shipment_release_batch_matches (
                        id, release_batch_id, wagon_shipment_id,
                        ydid, waybill_no, wagon_no, container_no,
                        match_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        match_id, release_batch_id, wagon_id,
                        plan.ydid, plan.waybill_no, plan.wagon_no,
                        plan.container_no, "departure_text_match",
                    ),
                )
            except sqlite3.IntegrityError:
                pass  # already exists — idempotent
        sop_conn.commit()

        # ── 12. R76: recompute shipped_weight for the release_batch ──
        # SOP-driven via yaml shipped_weight_rule. Failures are surfaced as
        # warnings, never block ingestion. Uses its own connection so it sees
        # the just-committed rows.
        if result.inserted_count > 0:
            try:
                from sop_hub.sop.shipped_weight import compute_for_release_batch
                sw = compute_for_release_batch(release_batch_id, db_path=str(sop_path))
                if sw.get("ok"):
                    result.release_batch_progress["shipped_weight_tons"] = sw["shipped_weight_tons"]
                    result.release_batch_progress["remaining_weight_tons"] = sw.get("remaining_weight_tons")
                    result.release_batch_progress["unresolved_wagon_count"] = sw["unresolved_wagon_count"]
                else:
                    result.warnings.append(f"shipped_weight: {sw.get('error')}")
            except Exception as exc:
                result.warnings.append(f"shipped_weight exception: {exc!r}")

        return result

    finally:
        sop_conn.close()
