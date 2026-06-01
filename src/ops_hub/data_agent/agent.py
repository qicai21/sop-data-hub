from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha1
import json
from pathlib import Path
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ops_hub.data_agent.db import open_db
from ops_hub.data_agent.json_utils import read_json, to_searchable_text, write_json

WORKSPACE_DOCS_DIR = Path(__file__).resolve().parents[3] / "doc"
PROJECT_SOPS_DIR = Path(__file__).resolve().parents[3] / "config" / "project_sops"
NON_BUSINESS_SOP_HINTS = ("archive", "sandbox", "归档", "沙箱")
ZHONGTANG_PROJECT = "中唐特钢铁矿发运项目"
WUGANG_PROJECT = "乌兰浩特钢铁铁矿发运项目"
FALLBACK_BUSINESS_SOP_TOKENS = {
    "zt_steel_baseline",
    ZHONGTANG_PROJECT,
    "chaoyang_steel_baseline",
    "朝阳钢铁铁矿发运项目",
    "wugang_steel_baseline",
    WUGANG_PROJECT,
}

# Common OCR confusions discovered in live release-batch documents.  These are
# station names, not project hard-coding: the final value still passes through
# the normal station canonicalization path below.
STATION_OCR_CORRECTIONS = {
    "沱子": "汐子",
}


@lru_cache(maxsize=1)
def active_business_sop_project_tokens() -> set[str]:
    """ProjectSOP projects that may feed the dispatch-in-progress index.

    The release-dispatch index is an operational queue, not a generic archive.
    Sandbox/archive ProjectSOP fixtures can classify or store evidence, but they
    must not make unrelated release batches participate in inspection matching.
    """
    tokens: set[str] = set()
    try:
        from ops_hub.models.project_sop import load_project_sop

        for sop_file in sorted(PROJECT_SOPS_DIR.glob("*.yaml")):
            sop = load_project_sop(sop_file)
            if sop.status != "active":
                continue
            haystack = f"{sop.project_id} {sop.project_name}"
            if any(hint in haystack for hint in NON_BUSINESS_SOP_HINTS):
                continue
            # A SOP is considered a business SOP if it routes any message into
            # a release/inspection/departure flow node. Keep this list aligned
            # with the node names used in config/project_sops/*.yaml. New node
            # names should be added here (rare) or — better — yaml authors
            # should pick names already in the set.
            _BUSINESS_NODE_NAMES = {
                "create_release_batch",        # legacy
                "process_inspection_slip",     # legacy
                "process_business_image",      # legacy
                "detect_release_notice",       # current — release flow trigger
                "detect_inspection_notice",    # current — inspection flow trigger
                "detect_departure_message",    # current — departure flow trigger
                "enrich_release_batch",        # current — freight detail enrichment
            }
            has_release_flow = any(
                route.target_node in _BUSINESS_NODE_NAMES
                for task in sop.listening_tasks
                for route in task.routing
            )
            if not has_release_flow:
                continue
            for value in (sop.project_id, sop.project_name):
                text = str(value or "").strip()
                if text:
                    tokens.add(text)
    except Exception:
        # Keep the runtime gate conservative even when optional YAML support is
        # unavailable in the host interpreter. These are the established
        # business SOPs; sandbox/archive fixtures are intentionally omitted.
        tokens.update(FALLBACK_BUSINESS_SOP_TOKENS)
    tokens.update(FALLBACK_BUSINESS_SOP_TOKENS)
    return tokens


def release_batch_sop_project(record: "ReleaseBatchRecord") -> str:
    """Return the authorized ProjectSOP token for a release batch, if any.

    New ingestions should carry `project` from the ProjectSOP gate.  Older rows
    can predate that column being populated, so keep a narrow compatibility
    inference for the two established business SOPs already used in production.
    """
    tokens = active_business_sop_project_tokens()
    project = str(record.project or "").strip()
    if project and project in tokens:
        return project

    text_parts = [
        record.project,
        record.ship_name,
        record.cargo_name,
        record.destination_station,
        record.contract_no,
        record.customer_name,
        record.consignor,
        record.consignee,
        record.id_label,
        record.commissioner_identifier,
        record.commissioner_note,
        record.source_file_name,
        json.dumps(record.source_json or {}, ensure_ascii=False),
    ]
    text = " ".join(str(part or "") for part in text_parts)

    chaoyang = "朝阳钢铁铁矿发运项目"
    if chaoyang in tokens and any(anchor in text for anchor in ("合远9", "朝阳钢铁", "朝钢", "朝阳西", "朝阳铁")):
        return chaoyang

    zhongtang = ZHONGTANG_PROJECT
    if zhongtang in tokens:
        if any(anchor in text for anchor in ("中唐", "赤峰中唐", "ZLZT")):
            return zhongtang
        if record.ship_name == "贝拉" and record.destination_station == "汐子" and "铁" in (record.cargo_name or ""):
            return zhongtang

    return ""


@dataclass
class ContractRecord:
    id: str
    party_a: str
    party_b: str
    origin_station: Optional[str]
    destination_station: Optional[str]
    transport_mode: Optional[str]
    transport_type: Optional[str]
    price: Optional[float]
    cargo_name: Optional[str]
    doc_path: Optional[str]
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "partyA": self.party_a,
            "partyB": self.party_b,
            "originStation": self.origin_station,
            "destinationStation": self.destination_station,
            "transportMode": self.transport_mode,
            "transportType": self.transport_type,
            "price": self.price,
            "cargoName": self.cargo_name,
            "docPath": self.doc_path,
            "updatedAt": self.updated_at,
        }


@dataclass
class ReleaseBatchRecord:
    id: str
    batch_key: str
    contract_id: Optional[str]
    contract_no: Optional[str]
    ship_name: str
    cargo_name: str
    cargo_product_name: Optional[str]
    consignor: Optional[str]
    consignee: Optional[str]
    trade_type: Optional[str]
    transport_mode: Optional[str]
    destination_station: Optional[str]
    yard_location: Optional[str]
    customs_release_qty: Optional[float]
    notice_date: str
    batch_date: Optional[str]
    batch_sequence: Optional[str]
    batch_quantity: Optional[float]
    total_planned_quantity: Optional[float]
    remaining_quantity: Optional[float]
    batch_count: int
    origin_station: Optional[str]
    agent_name: Optional[str]
    customer_name: Optional[str]
    id_label: Optional[str]
    actual_wagon_count: int
    dispatch_status: str
    dispatch_status_note: Optional[str]
    dispatch_status_updated_at: Optional[str]
    is_weighed: bool
    loading_weight: Optional[float]
    return_weight: Optional[float]
    tail_cargo_weight: Optional[float]
    tail_cargo_status: Optional[str]
    tail_cargo_remark: Optional[str]
    source_file_name: Optional[str]
    source_json: Dict[str, Any]
    updated_at: str
    project: Optional[str] = None
    commissioner_identifier: Optional[str] = None
    commissioner_note: Optional[str] = None
    plan_id: Optional[str] = None
    order_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "batchKey": self.batch_key,
            "contractId": self.contract_id,
            "contractNo": self.contract_no,
            "shipName": self.ship_name,
            "cargoName": self.cargo_name,
            "cargoProductName": self.cargo_product_name,
            "consignor": self.consignor,
            "consignee": self.consignee,
            "tradeType": self.trade_type,
            "transportMode": self.transport_mode,
            "destinationStation": self.destination_station,
            "yardLocation": self.yard_location,
            "customsReleaseQty": self.customs_release_qty,
            "noticeDate": self.notice_date,
            "batchDate": self.batch_date,
            "batchSequence": self.batch_sequence,
            "batchQuantity": self.batch_quantity,
            "totalPlannedQuantity": self.total_planned_quantity,
            "remainingQuantity": self.remaining_quantity,
            "batchCount": self.batch_count,
            "originStation": self.origin_station,
            "agentName": self.agent_name,
            "customerName": self.customer_name,
            "idLabel": self.id_label,
            "actualWagonCount": self.actual_wagon_count,
            "dispatchStatus": self.dispatch_status,
            "dispatchStatusNote": self.dispatch_status_note,
            "dispatchStatusUpdatedAt": self.dispatch_status_updated_at,
            "isWeighed": self.is_weighed,
            "loadingWeight": self.loading_weight,
            "returnWeight": self.return_weight,
            "tailCargoWeight": self.tail_cargo_weight,
            "tailCargoStatus": self.tail_cargo_status,
            "tailCargoRemark": self.tail_cargo_remark,
            "sourceFileName": self.source_file_name,
            "sourceJson": self.source_json,
            "updatedAt": self.updated_at,
            "project": self.project,
            "commissionerIdentifier": self.commissioner_identifier,
            "commissionerNote": self.commissioner_note,
            "planId": self.plan_id,
            "orderId": self.order_id,
        }


class BusinessDataAgent:
    def __init__(self) -> None:
        self.db = open_db()

    def upsert_contract(self, data: Dict[str, Any]) -> ContractRecord:
        record_id = data.get("id") or hash_text(f"{data.get('party_a')}|{data.get('party_b')}|{data.get('cargo_name')}")
        self.db.execute(
            """
            INSERT INTO contracts (
                id, party_a, party_b, origin_station, destination_station,
                transport_mode, transport_type, price, cargo_name, doc_path, updated_at
            ) VALUES (
                :id, :party_a, :party_b, :origin_station, :destination_station,
                :transport_mode, :transport_type, :price, :cargo_name, :doc_path, CURRENT_TIMESTAMP
            )
            ON CONFLICT(id) DO UPDATE SET
                party_a = excluded.party_a,
                party_b = excluded.party_b,
                origin_station = excluded.origin_station,
                destination_station = excluded.destination_station,
                transport_mode = excluded.transport_mode,
                transport_type = excluded.transport_type,
                price = excluded.price,
                cargo_name = excluded.cargo_name,
                doc_path = excluded.doc_path,
                updated_at = CURRENT_TIMESTAMP
            """,
            {**data, "id": record_id}
        )
        self.db.commit()
        return self.get_contract(record_id)

    def get_contract(self, contract_id: str) -> Optional[ContractRecord]:
        row = self.db.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
        if not row: return None
        return ContractRecord(
            id=row["id"], party_a=row["party_a"], party_b=row["party_b"],
            origin_station=row["origin_station"], destination_station=row["destination_station"],
            transport_mode=row["transport_mode"], transport_type=row["transport_type"],
            price=row["price"], cargo_name=row["cargo_name"], doc_path=row["doc_path"],
            updated_at=row["updated_at"]
        )

    def list_contracts(self) -> List[ContractRecord]:
        rows = self.db.execute("SELECT * FROM contracts ORDER BY updated_at DESC").fetchall()
        return [
            ContractRecord(
                id=row["id"], party_a=row["party_a"], party_b=row["party_b"],
                origin_station=row["origin_station"], destination_station=row["destination_station"],
                transport_mode=row["transport_mode"], transport_type=row["transport_type"],
                price=row["price"], cargo_name=row["cargo_name"], doc_path=row["doc_path"],
                updated_at=row["updated_at"]
            ) for row in rows
        ]

    def find_matching_contract(self, cargo_name: str, destination: str) -> Optional[ContractRecord]:
        # Simple matching logic: find by cargo and destination
        row = self.db.execute(
            "SELECT * FROM contracts WHERE cargo_name LIKE ? AND destination_station LIKE ? LIMIT 1",
            (f"%{cargo_name}%", f"%{destination}%")
        ).fetchone()
        if row:
            return self.get_contract(row["id"])
        return None

    def get_last_weighing_preference(self, ship_name: str) -> bool:
        row = self.db.execute(
            "SELECT is_weighed FROM release_batches WHERE ship_name = ? ORDER BY created_at DESC LIMIT 1",
            (ship_name,)
        ).fetchone()
        return bool(row["is_weighed"]) if row else False

    def ingest_release_batch(
        self,
        payload: Any,
        source_file_name: Optional[str] = None,
        contract_id: Optional[str] = None,
        contract_no: Optional[str] = None,
    ) -> List[ReleaseBatchRecord]:
        # ── Query existing batches for dedup ─────────────────────────────────
        # Parse payload early to get ship/cargo/destination so we can query DB
        payload_dict = read_json(payload)
        biz = payload_dict.get("business_info", {}) if isinstance(payload_dict, dict) else {}
        cargo = payload_dict.get("cargo_info", {}) if isinstance(payload_dict, dict) else {}
        ship_name = biz.get("进口船名") or biz.get("船名", "")
        cargo_name = cargo.get("货物品类") or cargo.get("货物名称", "")
        spec = payload_dict.get("special_matter", "") if isinstance(payload_dict, dict) else ""
        dest = parse_destination_station(spec) or ""

        existing_batches = None
        if ship_name and cargo_name and dest:
            existing_batches = self._query_existing_batches_like(ship_name, cargo_name, dest)
        # ── End query existing batches ──────────────────────────────────────

        normalized_rows = self.normalize_release_batch_payloads(
            payload=payload,
            source_file_name=source_file_name,
            contract_id=contract_id,
            contract_no=contract_no,
            existing_batches=existing_batches,
        )
        inserted: List[ReleaseBatchRecord] = []
        for normalized in normalized_rows:
            # If the remark was flagged as review_needed, override dispatch_status
            is_review_needed = bool(normalized.pop("_review_needed", False))
            if is_review_needed:
                normalized["dispatch_status"] = "review_needed"
                normalized["dispatch_status_note"] = "OCR序列号匹配但日期/重量不一致，需人工核实"

            batch_key = normalized["batch_key"]
            existing = self.get_by_batch_key(batch_key)
            if existing is not None:
                # Already exists — skip.
                inserted.append(existing)
                continue
            self.db.execute(
                """
                INSERT INTO release_batches (
                  id,
                  batch_key,
                  project,
                  contract_id,
                  contract_no,
                  ship_name,
                  cargo_name,
                  cargo_product_name,
                  consignor,
                  consignee,
                  commissioner_identifier,
                  commissioner_note,
                  trade_type,
                  transport_mode,
                  destination_station,
                  yard_location,
                  customs_release_qty,
                  notice_date,
                  batch_date,
                  batch_sequence,
                  batch_quantity,
                  total_planned_quantity,
                  remaining_quantity,
                  batch_count,
                  origin_station,
                  agent_name,
                  customer_name,
                  id_label,
                  actual_wagon_count,
                  dispatch_status,
                  dispatch_status_note,
                  dispatch_status_updated_at,
                  is_weighed,
                  loading_weight,
                  return_weight,
                  tail_cargo_weight,
                  tail_cargo_status,
                  tail_cargo_remark,
                  source_file_name,
                  source_json,
                  searchable_text,
                  plan_id,
                  order_id,
                  updated_at
                ) VALUES (
                  :id,
                  :batch_key,
                  :project,
                  :contract_id,
                  :contract_no,
                  :ship_name,
                  :cargo_name,
                  :cargo_product_name,
                  :consignor,
                  :consignee,
                  :commissioner_identifier,
                  :commissioner_note,
                  :trade_type,
                  :transport_mode,
                  :destination_station,
                  :yard_location,
                  :customs_release_qty,
                  :notice_date,
                  :batch_date,
                  :batch_sequence,
                  :batch_quantity,
                  :total_planned_quantity,
                  :remaining_quantity,
                  :batch_count,
                  :origin_station,
                  :agent_name,
                  :customer_name,
                  :id_label,
                  :actual_wagon_count,
                  :dispatch_status,
                  :dispatch_status_note,
                  :dispatch_status_updated_at,
                  :is_weighed,
                  :loading_weight,
                  :return_weight,
                  :tail_cargo_weight,
                  :tail_cargo_status,
                  :tail_cargo_remark,
                  :source_file_name,
                  :source_json,
                  :searchable_text,
                  :plan_id,
                  :order_id,
                  CURRENT_TIMESTAMP
                )
                ON CONFLICT(batch_key) DO UPDATE SET
                  contract_id = excluded.contract_id,
                  project = excluded.project,
                  contract_no = excluded.contract_no,
                  ship_name = excluded.ship_name,
                  cargo_name = excluded.cargo_name,
                  cargo_product_name = COALESCE(excluded.cargo_product_name, release_batches.cargo_product_name),
                  consignor = excluded.consignor,
                  consignee = excluded.consignee,
                  commissioner_identifier = excluded.commissioner_identifier,
                  commissioner_note = excluded.commissioner_note,
                  trade_type = excluded.trade_type,
                  transport_mode = excluded.transport_mode,
                  destination_station = excluded.destination_station,
                  yard_location = excluded.yard_location,
                  customs_release_qty = excluded.customs_release_qty,
                  notice_date = excluded.notice_date,
                  batch_date = excluded.batch_date,
                  batch_sequence = excluded.batch_sequence,
                  batch_quantity = excluded.batch_quantity,
                  total_planned_quantity = excluded.total_planned_quantity,
                  remaining_quantity = excluded.remaining_quantity,
                  batch_count = excluded.batch_count,
                  origin_station = excluded.origin_station,
                  agent_name = excluded.agent_name,
                  customer_name = excluded.customer_name,
                  id_label = excluded.id_label,
                  actual_wagon_count = excluded.actual_wagon_count,
                  dispatch_status = release_batches.dispatch_status,
                  dispatch_status_note = release_batches.dispatch_status_note,
                  dispatch_status_updated_at = release_batches.dispatch_status_updated_at,
                  is_weighed = excluded.is_weighed,
                  loading_weight = excluded.loading_weight,
                  return_weight = excluded.return_weight,
                  tail_cargo_weight = excluded.tail_cargo_weight,
                  tail_cargo_status = excluded.tail_cargo_status,
                  tail_cargo_remark = excluded.tail_cargo_remark,
                  source_file_name = excluded.source_file_name,
                  source_json = excluded.source_json,
                  source_json = excluded.source_json,
                  searchable_text = excluded.searchable_text,
                  plan_id = excluded.plan_id,
                  order_id = excluded.order_id,
                  tail_cargo_remark = CASE 
                      WHEN release_batches.batch_quantity != excluded.batch_quantity OR release_batches.batch_date != excluded.batch_date 
                      THEN ifnull(release_batches.tail_cargo_remark, '') || ' | 识别异常/更新: 原日期' || ifnull(release_batches.batch_date, '空') || ' 原重量' || ifnull(release_batches.batch_quantity, '空')
                      ELSE release_batches.tail_cargo_remark 
                  END,
                  updated_at = CURRENT_TIMESTAMP
                """,
                normalized,
            )
            new_record = self.get_by_batch_key(batch_key)
            if new_record:
                inserted.append(new_record)
        self.db.commit()
        for record in inserted:
            self.upsert_release_dispatch_match_rule(record)
        self.db.commit()
        return inserted

    def ingest_business_text(self, text: str) -> List[ReleaseBatchRecord]:
        """Parse a ProjectSOP-authorized text release instruction and create/update release_batches.

        Required text format:
        供方: ...
        船名：...
        货名：...
        港口：...
        数量：...
        计划号：...
        合同号：...
        """
        fields = parse_business_text_fields(text)
        required = ["供方", "船名", "货名", "港口", "数量", "计划号", "合同号"]
        if any(not fields.get(key) for key in required):
            return []

        quantity = parse_number(fields.get("数量"))
        matches = self.find_text_release_batch_candidates(fields, quantity)
        audit_base = {
            "message_type": "text",
            "raw_image_path": text,
            "classified_category": "文字放货指令",
            "project_id": "中唐特钢铁矿发运项目" if "ZLZT" in fields.get("合同号", "") or "马兰探险" in fields.get("船名", "") else None,
            "target_node": "create_release_batch",
        }
        if len(matches) != 1:
            reason = "no_release_batch_candidate" if not matches else "ambiguous_release_batch_match"
            self.write_image_ingestion_audit(
                {
                    **audit_base,
                    "db_action": "manual_match_pending",
                    "db_tables": ["release_batches"],
                    "db_record_ids": [record.id for record in matches],
                    "status": "pending",
                    "reason": reason,
                    "requires_manual_review": True,
                    "review_reasons": [reason],
                    "planned_write_count": 0,
                    "excluded_count": len(matches),
                }
            )
            return []

        record = matches[0]
        cargo_name = record.cargo_name or infer_cargo_category(fields["货名"])
        source_json = dict(record.source_json or {})
        source_json["text_release_instruction"] = {
            "fields": fields,
            "source_text": text,
        }
        self.db.execute(
            """
            UPDATE release_batches SET
              contract_no = ?,
              plan_id = ?,
              cargo_name = ?,
              cargo_product_name = ?,
              source_json = ?,
              searchable_text = ?,
              updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                fields["合同号"],
                fields["计划号"],
                cargo_name,
                fields["货名"],
                write_json(source_json, pretty=False),
                to_searchable_text(source_json),
                record.id,
            ),
        )
        self.db.commit()
        updated = self.get(record.id)
        if updated:
            self.upsert_release_dispatch_match_rule(updated)
            self.write_image_ingestion_audit(
                {
                    **audit_base,
                    "db_action": "release_batch_update",
                    "db_tables": ["release_batches"],
                    "db_record_ids": [updated.id],
                    "status": "ingested",
                    "reason": "unique_release_batch_match",
                    "requires_manual_review": False,
                    "planned_write_count": 1,
                    "excluded_count": 0,
                }
            )
            self.db.commit()
            return [updated]
        return []

    def find_text_release_batch_candidates(
        self,
        fields: Dict[str, str],
        quantity: Optional[float],
    ) -> List[ReleaseBatchRecord]:
        """Find existing release_batches for a text release instruction.

        Text messages are supplemental release instructions. They must not
        create a new lot; they may only update exactly one existing lot.
        """
        ship_name = str(fields.get("船名") or "").strip()
        if not ship_name:
            return []
        rows = self.db.execute(
            """
            SELECT * FROM release_batches
            WHERE ship_name = ?
            ORDER BY batch_date ASC, batch_sequence ASC, updated_at ASC
            """,
            (ship_name,),
        ).fetchall()
        candidates = [hydrate_row(row) for row in rows]

        destination = canonicalize_station_text(fields.get("到站") or fields.get("目的地") or "")
        if destination:
            candidates = [
                record
                for record in candidates
                if canonicalize_station_text(record.destination_station) == destination
            ]

        cargo_category = infer_cargo_category(fields.get("货名"))
        if cargo_category:
            cargo_filtered = [
                record for record in candidates if text_cargo_compatible(cargo_category, record.cargo_name)
            ]
            if cargo_filtered:
                candidates = cargo_filtered

        contract_no = str(fields.get("合同号") or "").strip()
        if contract_no:
            exact_contract = [record for record in candidates if str(record.contract_no or "").strip() == contract_no]
            if exact_contract:
                candidates = exact_contract

        plan_id = str(fields.get("计划号") or "").strip()
        if plan_id:
            exact_plan = [record for record in candidates if str(record.plan_id or "").strip() == plan_id]
            if exact_plan:
                candidates = exact_plan

        if quantity is not None:
            quantity_matches = [
                record
                for record in candidates
                if record.batch_quantity is not None and abs(float(record.batch_quantity) - quantity) < 0.001
            ]
            if quantity_matches:
                candidates = quantity_matches

        return candidates

    def ingest_release_batch_file(
        self,
        file_path: str,
        contract_id: Optional[str] = None,
        contract_no: Optional[str] = None,
    ) -> List[ReleaseBatchRecord]:
        source_path = Path(file_path)
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        return self.ingest_release_batch(
            payload=payload,
            source_file_name=source_path.name,
            contract_id=contract_id,
            contract_no=contract_no,
        )

    def get(self, record_id: str) -> Optional[ReleaseBatchRecord]:
        row = self.db.execute(
            "SELECT * FROM release_batches WHERE id = ?",
            (record_id,),
        ).fetchone()
        return hydrate_row(row) if row else None

    def get_by_batch_key(self, batch_key: str) -> Optional[ReleaseBatchRecord]:
        row = self.db.execute(
            "SELECT * FROM release_batches WHERE batch_key = ?",
            (batch_key,),
        ).fetchone()
        return hydrate_row(row) if row else None

    def list_release_batches(self) -> List[ReleaseBatchRecord]:
        rows = self.db.execute(
            "SELECT * FROM release_batches ORDER BY updated_at DESC, ship_name ASC"
        ).fetchall()
        return [hydrate_row(row) for row in rows]

    def reset_release_batches(self) -> None:
        self.db.execute("DELETE FROM release_batches")
        self.db.execute("DELETE FROM release_dispatch_match_rules")
        self.db.commit()

    def refresh_release_dispatch_match_rules(self) -> int:
        """Create/update active runtime dispatch matching rules from release_batches.

        Manual completed/suspended rules are preserved and not reactivated.
        Release batches outside established business ProjectSOPs are removed
        from the runtime index so new non-SOP material cannot auto-match.
        """
        rows = self.db.execute("SELECT * FROM release_batches").fetchall()
        count = 0
        authorized_ids: set[str] = set()
        for row in rows:
            record = hydrate_row(row)
            if not release_batch_sop_project(record):
                continue
            self.upsert_release_dispatch_match_rule(record)
            authorized_ids.add(record.id)
            count += 1
        if authorized_ids:
            placeholders = ",".join("?" for _ in authorized_ids)
            self.db.execute(
                f"DELETE FROM release_dispatch_match_rules WHERE release_batch_id NOT IN ({placeholders})",
                tuple(authorized_ids),
            )
        else:
            self.db.execute("DELETE FROM release_dispatch_match_rules")
        self.db.commit()
        return count

    def upsert_release_dispatch_match_rule(self, record: ReleaseBatchRecord) -> None:
        sop_project = release_batch_sop_project(record)
        if not sop_project:
            self.db.execute(
                "DELETE FROM release_dispatch_match_rules WHERE release_batch_id=?",
                (record.id,),
            )
            return
        tokens = self._release_dispatch_rule_tokens(record)
        matching_str = " ".join(token for token in tokens["all"] if token)
        rule_id = hash_text(f"release_dispatch_match_rule|{record.id}")
        self.db.execute(
            """
            INSERT INTO release_dispatch_match_rules (
              id, release_batch_id, project, ship_name, destination_station,
              cargo_name, matching_str, matching_tokens_json, status, priority, updated_at,
              completed_at, manual_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP,
              CASE WHEN ?='completed' THEN CURRENT_TIMESTAMP ELSE NULL END, ?)
            ON CONFLICT(release_batch_id) DO UPDATE SET
              project=excluded.project,
              ship_name=excluded.ship_name,
              destination_station=excluded.destination_station,
              cargo_name=excluded.cargo_name,
              matching_str=excluded.matching_str,
              matching_tokens_json=excluded.matching_tokens_json,
              priority=excluded.priority,
              status=excluded.status,
              completed_at=CASE
                WHEN excluded.status='completed' THEN COALESCE(release_dispatch_match_rules.completed_at, excluded.completed_at)
                ELSE NULL
              END,
              manual_note=CASE
                WHEN excluded.manual_note IS NOT NULL THEN excluded.manual_note
                ELSE release_dispatch_match_rules.manual_note
              END,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                rule_id,
                record.id,
                sop_project,
                record.ship_name,
                record.destination_station,
                record.cargo_name,
                matching_str,
                json.dumps(tokens, ensure_ascii=False),
                "active" if record.dispatch_status == "in_progress" else record.dispatch_status,
                100,
                "active" if record.dispatch_status == "in_progress" else record.dispatch_status,
                record.dispatch_status_note,
            ),
        )

    def update_release_dispatch_status(self, release_batch_id: str, status: str, manual_note: str | None = None) -> bool:
        normalized_status = "in_progress" if status == "active" else status
        if normalized_status not in {"in_progress", "completed", "suspended", "cancelled"}:
            raise ValueError("status must be one of: active, in_progress, completed, suspended, cancelled")
        cursor = self.db.execute(
            """
            UPDATE release_batches
            SET dispatch_status=?, dispatch_status_note=?, dispatch_status_updated_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (normalized_status, manual_note, release_batch_id),
        )
        changed = cursor.rowcount > 0
        if changed:
            record = self.get(release_batch_id)
            if record:
                self.upsert_release_dispatch_match_rule(record)
        self.db.commit()
        return changed

    def complete_release_dispatch_match_rule(self, release_batch_id: str, manual_note: str | None = None) -> bool:
        return self.update_release_dispatch_status(release_batch_id, "completed", manual_note=manual_note)

    def force_reopen_release_dispatch_match_rule(self, release_batch_id: str, manual_note: str | None = None) -> bool:
        """Re-open a completed batch so a user-directed supplement can be matched."""
        return self.update_release_dispatch_status(release_batch_id, "in_progress", manual_note=manual_note)

    def _release_dispatch_rule_tokens(self, record: ReleaseBatchRecord) -> Dict[str, List[str]]:
        ship_tokens = [record.ship_name] if record.ship_name else []
        destination_tokens = [record.destination_station] if record.destination_station else []
        cargo_tokens = [record.cargo_name] if record.cargo_name else []
        if record.cargo_name == "铁矿":
            cargo_tokens.append("铁矿粉")
        all_tokens = []
        project_token = record.project or release_batch_sop_project(record)
        for token in [project_token, record.ship_name, record.destination_station, record.cargo_name, record.batch_sequence]:
            if token:
                all_tokens.append(str(token))
        return {
            "ship": ship_tokens,
            "destination": destination_tokens,
            "cargo": cargo_tokens,
            "all": all_tokens,
        }

    def match_release_dispatch_rules_for_inspection(
        self,
        payload: Dict[str, Any],
        *,
        include_completed: bool = False,
    ) -> Dict[str, Any]:
        searchable = to_searchable_text(payload)
        status_filter = "status IN ('active', 'completed')" if include_completed else "status='active'"
        rows = self.db.execute(
            f"""
            SELECT * FROM release_dispatch_match_rules
            WHERE {status_filter}
            ORDER BY priority ASC, updated_at DESC
            """
        ).fetchall()
        ship_anchor_matches = []
        station_cargo_matches = []
        for row in rows:
            tokens = json.loads(row["matching_tokens_json"] or "{}")
            has_ship = any(token and token in searchable for token in tokens.get("ship", []))
            has_destination = any(token and token in searchable for token in tokens.get("destination", []))
            has_cargo = any(token and token in searchable for token in tokens.get("cargo", []))
            if has_ship and has_destination and has_cargo:
                ship_anchor_matches.append(row)
            elif has_destination and has_cargo:
                station_cargo_matches.append(row)

        if len(ship_anchor_matches) == 1:
            return {
                "status": "candidate",
                "reason": "matched_release_batch_waiting_95306_validation",
                "release_batch_ids": [ship_anchor_matches[0]["release_batch_id"]],
            }
        if len(ship_anchor_matches) > 1:
            return {
                "status": "ambiguous",
                "reason": "ambiguous_release_batch_candidate",
                "release_batch_ids": [row["release_batch_id"] for row in ship_anchor_matches],
            }
        # Station+cargo-only hits are useful evidence but not safe enough to auto-assign.
        if station_cargo_matches:
            return {
                "status": "pending",
                "reason": "no_release_batch_candidate",
                "release_batch_ids": [],
            }
        return {"status": "pending", "reason": "no_release_batch_candidate", "release_batch_ids": []}

    def match_release_dispatch_rule_for_inspection(
        self,
        payload: Dict[str, Any],
        *,
        include_completed: bool = False,
    ) -> Optional[str]:
        match = self.match_release_dispatch_rules_for_inspection(payload, include_completed=include_completed)
        ids = match.get("release_batch_ids") or []
        return ids[0] if match.get("status") == "candidate" and len(ids) == 1 else None

    def ingest_inspection_payload(
        self,
        payload: Dict[str, Any],
        source_file_name: Optional[str] = None,
        group_name: Optional[str] = None,
        include_completed_release_batches: bool = False,
    ) -> Dict[str, Any]:
        rows = payload.get("rows") if isinstance(payload, dict) else []
        rows = rows if isinstance(rows, list) else []
        car_numbers = [str(row.get("car_no") or "").strip() for row in rows if isinstance(row, dict) and str(row.get("car_no") or "").strip()]
        match = self.match_release_dispatch_rules_for_inspection(
            payload,
            include_completed=include_completed_release_batches,
        )
        status = str(match.get("status") or "pending")
        reason = str(match.get("reason") or "no_release_batch_candidate")
        release_batch_ids = [str(item) for item in (match.get("release_batch_ids") or []) if item]
        release_batch_id = release_batch_ids[0] if status == "candidate" and len(release_batch_ids) == 1 else None
        candidate_payload = dict(payload)
        if release_batch_ids:
            candidate_payload["_candidate_release_batch_ids"] = release_batch_ids
        candidate_id = hash_text(f"inspection|{source_file_name}|{','.join(car_numbers)}|{reason}")
        self.db.execute(
            """
            INSERT INTO inspection_ingestion_candidates (
              id, source_file_name, status, reason, group_name, release_batch_id,
              wagon_count, car_numbers_json, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
              status=excluded.status,
              reason=excluded.reason,
              group_name=excluded.group_name,
              release_batch_id=excluded.release_batch_id,
              wagon_count=excluded.wagon_count,
              car_numbers_json=excluded.car_numbers_json,
              payload_json=excluded.payload_json,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                candidate_id,
                source_file_name or "",
                status,
                reason,
                group_name,
                release_batch_id,
                len(car_numbers),
                json.dumps(car_numbers, ensure_ascii=False),
                write_json(candidate_payload, pretty=False),
            ),
        )
        self.db.commit()
        return {
            "status": status,
            "reason": reason,
            "candidate_ids": [candidate_id],
            "release_batch_ids": release_batch_ids,
            "wagon_count": len(car_numbers),
        }

    def assign_inspection_candidate(
        self,
        candidate_id: str,
        release_batch_id: str,
        *,
        operator_note: str = "",
    ) -> bool:
        release = self.db.execute(
            "SELECT id FROM release_batches WHERE id=?",
            (release_batch_id,),
        ).fetchone()
        if release is None:
            raise ValueError(f"release_batch_id not found: {release_batch_id}")
        row = self.db.execute(
            "SELECT * FROM inspection_ingestion_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"inspection candidate not found: {candidate_id}")
        payload = json.loads(row["payload_json"] or "{}")
        payload["_manual_assignment"] = {
            "release_batch_id": release_batch_id,
            "operator_note": operator_note,
            "assigned_at": datetime.now(timezone.utc).isoformat(),
        }
        self.db.execute(
            """
            UPDATE inspection_ingestion_candidates
            SET status='candidate',
                reason='manual_assigned_to_release_batch',
                release_batch_id=?,
                payload_json=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (release_batch_id, write_json(payload, pretty=False), candidate_id),
        )
        self.db.commit()
        return True

    def list_dispatch_rules(self, status: str | None = None) -> List[Dict[str, Any]]:
        params: tuple[Any, ...] = ()
        where = ""
        if status:
            rule_status = "active" if status in {"active", "in_progress"} else status
            where = "WHERE r.status=?"
            params = (rule_status,)
        rows = self.db.execute(
            f"""
            SELECT r.*, b.dispatch_status, b.batch_sequence, b.batch_quantity, b.batch_date,
                   b.plan_id, b.contract_no, b.cargo_product_name
            FROM release_dispatch_match_rules r
            LEFT JOIN release_batches b ON b.id = r.release_batch_id
            {where}
            ORDER BY r.status ASC, r.priority ASC, r.updated_at DESC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def write_image_ingestion_audit(self, data: Dict[str, Any]) -> str:
        record_id = data.get("id") or hash_text("|".join(str(data.get(key) or "") for key in ("group_name", "raw_image_path", "classified_image_path", "extraction_json_path")))
        self.db.execute(
            """
            INSERT INTO image_ingestion_audit (
              id, group_name, group_id, message_time, sender, message_id, local_id,
              message_type, raw_image_path, classified_category, classification_confidence,
              classified_image_path, extraction_json_path, project_id, target_node,
              adopted_fields, ignored_fields, db_action, db_tables, db_record_ids,
              status, reason, reconcile_plan, safe_to_commit, requires_manual_review,
              review_reasons, planned_write_count, excluded_count, project_archive_paths
            ) VALUES (
              :id, :group_name, :group_id, :message_time, :sender, :message_id, :local_id,
              :message_type, :raw_image_path, :classified_category, :classification_confidence,
              :classified_image_path, :extraction_json_path, :project_id, :target_node,
              :adopted_fields, :ignored_fields, :db_action, :db_tables, :db_record_ids,
              :status, :reason, :reconcile_plan, :safe_to_commit, :requires_manual_review,
              :review_reasons, :planned_write_count, :excluded_count, :project_archive_paths
            )
            ON CONFLICT(id) DO UPDATE SET
              classified_category=excluded.classified_category,
              classification_confidence=excluded.classification_confidence,
              classified_image_path=excluded.classified_image_path,
              extraction_json_path=excluded.extraction_json_path,
              db_action=excluded.db_action,
              db_tables=excluded.db_tables,
              db_record_ids=excluded.db_record_ids,
              status=excluded.status,
              reason=excluded.reason,
              reconcile_plan=excluded.reconcile_plan,
              safe_to_commit=excluded.safe_to_commit,
              requires_manual_review=excluded.requires_manual_review,
              review_reasons=excluded.review_reasons,
              planned_write_count=excluded.planned_write_count,
              excluded_count=excluded.excluded_count,
              project_archive_paths=excluded.project_archive_paths
            """,
            {
                "id": record_id,
                "group_name": data.get("group_name"),
                "group_id": data.get("group_id"),
                "message_time": data.get("message_time"),
                "sender": data.get("sender"),
                "message_id": data.get("message_id"),
                "local_id": data.get("local_id"),
                "message_type": data.get("message_type") or "image",
                "raw_image_path": data.get("raw_image_path"),
                "classified_category": data.get("classified_category"),
                "classification_confidence": data.get("classification_confidence"),
                "classified_image_path": data.get("classified_image_path"),
                "extraction_json_path": data.get("extraction_json_path"),
                "project_id": data.get("project_id"),
                "target_node": data.get("target_node"),
                "adopted_fields": write_json(data.get("adopted_fields") or [], pretty=False),
                "ignored_fields": write_json(data.get("ignored_fields") or [], pretty=False),
                "db_action": data.get("db_action"),
                "db_tables": write_json(data.get("db_tables") or [], pretty=False),
                "db_record_ids": write_json(data.get("db_record_ids") or [], pretty=False),
                "status": data.get("status"),
                "reason": data.get("reason"),
                "reconcile_plan": write_json(data.get("reconcile_plan") or {}, pretty=False) if data.get("reconcile_plan") else None,
                "safe_to_commit": int(bool(data.get("safe_to_commit"))) if data.get("safe_to_commit") is not None else (int(bool((data.get("reconcile_plan") or {}).get("safe_to_commit"))) if data.get("reconcile_plan") else None),
                "requires_manual_review": int(bool(data.get("requires_manual_review"))) if data.get("requires_manual_review") is not None else (int(bool((data.get("reconcile_plan") or {}).get("requires_manual_review"))) if data.get("reconcile_plan") else None),
                "review_reasons": write_json(data.get("review_reasons") or [], pretty=False) if data.get("review_reasons") is not None else (write_json((data.get("reconcile_plan") or {}).get("review_reasons") or [], pretty=False) if data.get("reconcile_plan") else None),
                "planned_write_count": data.get("planned_write_count") if data.get("planned_write_count") is not None else ((data.get("reconcile_plan") or {}).get("planned_write_count") if data.get("reconcile_plan") else None),
                "excluded_count": data.get("excluded_count") if data.get("excluded_count") is not None else (len((data.get("reconcile_plan") or {}).get("excluded") or []) if data.get("reconcile_plan") else None),
                "project_archive_paths": write_json(data.get("project_archive_paths") or {}, pretty=False) if data.get("project_archive_paths") else None,
            },
        )
        self.db.commit()
        return record_id

    def set_weighing_info(
        self,
        batch_id: str,
        is_weighed: bool,
        loading_weight: Optional[float] = None,
        return_weight: Optional[float] = None
    ) -> None:
        self.db.execute(
            """
            UPDATE release_batches SET
                is_weighed = ?,
                loading_weight = ?,
                return_weight = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (1 if is_weighed else 0, loading_weight, return_weight, batch_id)
        )
        self.db.commit()

    def update_tail_cargo(
        self,
        batch_id: str,
        actual_shipped_weight: float,
        status: Optional[str] = None,
        remark: Optional[str] = None
    ) -> None:
        record = self.get(batch_id)
        if not record:
            return
        
        planned = record.batch_quantity or 0
        tail_weight = planned - actual_shipped_weight
        
        self.db.execute(
            """
            UPDATE release_batches SET
                tail_cargo_weight = ?,
                tail_cargo_status = ?,
                tail_cargo_remark = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (tail_weight, status or record.tail_cargo_status, remark or record.tail_cargo_remark, batch_id)
        )
        self.db.commit()

    def _query_existing_batches_like(
        self, ship_name: str, cargo_name: str, destination_station: str
    ) -> List[Dict[str, Any]]:
        """Query existing release_batches matching ship + cargo + destination (fuzzy).

        Uses LIKE matching on destination_station so that minor OCR differences
        (e.g. '四平' vs '四平 ') don't cause misses.
        """
        dest_pattern = f"%{destination_station.strip()}%"
        rows = self.db.execute(
            """SELECT batch_sequence, batch_date, batch_quantity, dispatch_status
               FROM release_batches
               WHERE ship_name = ? AND cargo_name = ?
                 AND (destination_station LIKE ? OR destination_station = ?)
               ORDER BY batch_sequence""",
            (ship_name, cargo_name, dest_pattern, destination_station),
        ).fetchall()
        return [
            {
                "batch_sequence": r[0],
                "batch_date": r[1],
                "batch_quantity": r[2],
                "dispatch_status": r[3],
            }
            for r in rows
        ]

    def normalize_release_batch_payloads(
        self,
        payload: Any,
        source_file_name: Optional[str],
        contract_id: Optional[str],
        contract_no: Optional[str],
        existing_batches: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        normalized_payload = extract_single_record(read_json(payload))
        notice_date = normalize_chinese_date(
            normalized_payload.get("header_info", {}).get("通知日期")
        )
        remarks = parse_remarks(normalized_payload.get("remarks", []), notice_date)
        remarks = synthesize_missing_clean_bottom_remarks(
            remarks,
            normalized_payload.get("cargo_info", {}),
            notice_date,
        )
        if len(remarks) == 1 and not remarks[0].get("sequence"):
            # A formal departure plan that yields exactly one business batch is
            # still a concrete release lot.  Use lot01 so downstream project
            # archives and dispatch rules do not fall into lotunknown solely
            # because the notice lacks “第一次/lot01” wording.
            remarks[0]["sequence"] = "lot01"
        latest_remark = remarks[-1] if remarks else None
        special_matter = normalized_payload.get("special_matter", "")
        business_info = normalized_payload.get("business_info", {})
        cargo_info = normalized_payload.get("cargo_info", {})

        header_info = normalized_payload.get("header_info", {})
        project = normalized_payload.get("project") or normalized_payload.get("项目")
        commissioner_identifier = normalized_payload.get("commissioner_identifier") or normalized_payload.get("委托人标识")
        commissioner_note = normalized_payload.get("commissioner_note") or normalized_payload.get("委托备注")
        contract_no = contract_no or header_info.get("合同号")
        plan_id = header_info.get("入场计划号") or header_info.get("计划号")
        order_id = header_info.get("订单标识号") or header_info.get("订单号")

        ship_name = business_info.get("进口船名") or business_info.get("船名", "")
        cargo_name = cargo_info.get("货物品类") or cargo_info.get("货物名称", "")
        cargo_product_name = cargo_info.get("货物品名") or normalized_payload.get("货物品名")
        consignor = business_info.get("发货单位", "")
        consignee = business_info.get("收货单位", "")
        default_destination_station = parse_destination_station(special_matter) or (
            latest_remark.get("destination") if latest_remark else ""
        )

        # Inheritance logic for weighing
        is_weighed = self.get_last_weighing_preference(ship_name)

        # Attempt to match contract if not provided
        contract = None
        if contract_id:
            contract = self.get_contract(contract_id)
        else:
            contract = self.find_matching_contract(cargo_name, default_destination_station)
            if contract:
                contract_id = contract.id

        origin_station = contract.origin_station if contract else (cargo_info.get("发货站(地)") or business_info.get("到达港") or None)
        customer_name = contract.party_b if contract else consignee
        # For agent, we might need a better way, but for now let's use a heuristic or party_a
        agent_name = contract.party_a if contract else None 

        # ── Dedup against existing batches ──────────────────────────────────
        # If caller didn't supply existing_batches, query the DB now.
        if existing_batches is None and ship_name and cargo_name and default_destination_station:
            existing_batches = self._query_existing_batches_like(
                ship_name=ship_name,
                cargo_name=cargo_name,
                destination_station=default_destination_station,
            )

        review_needed_remarks: list[dict[str, Any]] = []
        filtered_remarks: list[dict[str, Any]] = []
        if existing_batches:
            # Build lookup: sequence -> (batch_date, batch_quantity)
            existing_map: dict[str, tuple[str | None, float | None]] = {}
            for eb in existing_batches:
                seq = eb.get("batch_sequence") or eb.get("sequence", "")
                if not seq:
                    continue
                existing_map[seq] = (eb.get("batch_date"), eb.get("batch_quantity"))

            for remark in remarks:
                seq = str(remark.get("sequence") or "")
                if not seq:
                    filtered_remarks.append(remark)
                    continue
                if seq not in existing_map:
                    # Sequence not in DB — new batch, proceed normally
                    filtered_remarks.append(remark)
                    continue

                # Sequence exists in DB — compare weight and date
                db_date, db_qty = existing_map[seq]
                remark_date = str(remark.get("date") or "")
                remark_qty = remark.get("quantity")

                # Normalize dates for comparison (strip leading "0" from month/day)
                def _norm_date(d: str) -> str:
                    parts = d.replace("年", "-").replace("月", "-").replace("日", "").split("-")
                    return "-".join(p.lstrip("0") for p in parts)

                date_match = _norm_date(remark_date) == _norm_date(str(db_date or "")) if remark_date and db_date else (remark_date == str(db_date or ""))
                qty_match = (float(remark_qty) if remark_qty else None) == (float(db_qty) if db_qty else None) if remark_qty and db_qty else True

                if date_match and qty_match:
                    # Fully matched — skip (already in DB)
                    continue
                else:
                    # Conflict: same sequence but different date/weight → mark review_needed
                    remark["_review_needed"] = True
                    review_needed_remarks.append(remark)
                    filtered_remarks.append(remark)
        else:
            filtered_remarks = remarks[:]
        # ── End dedup ───────────────────────────────────────────────────────

        # If all remarks were skipped, and some are review_needed, still return those
        if not filtered_remarks and review_needed_remarks:
            filtered_remarks = review_needed_remarks

        # Use filtered remarks for the rest of the method
        remarks = filtered_remarks
        if not remarks:
            return []

        base_key = "|".join(
            str(item).strip()
            for item in [ship_name, cargo_name]
        )
        total_planned_quantity = sum(item.get("quantity") or 0 for item in remarks)
        rows = []

        for remark in remarks:
            seq = str(remark.get("sequence") or "")
            dt = str(remark.get("date") or "")
            destination_station = remark.get("destination") or default_destination_station
            row_project = release_batch_project_for_destination(project, destination_station)
            
            # Temporal key: use normalised remark date when available;
            # fall back to notice_date for whole-ship releases with empty remarks.
            batch_date_key = normalize_chinese_date(dt) if dt else str(notice_date or "")
            # Sequence is the primary lot identifier; if absent, fall back to date
            unique_identifier = seq if seq else batch_date_key
            batch_key = "|".join([base_key, str(destination_station or ""), batch_date_key, unique_identifier])

            
            # Generate ID Label: 汐子铁矿粉/沈阳盛京颐昇代/鞍子河
            agent_label = f"{agent_name}代" if agent_name else "未知代理代"
            id_label = f"{destination_station}{cargo_name}/{agent_label}/{ship_name}"

            rows.append(
                {
                    "id": hash_text(batch_key),
                    "batch_key": batch_key,
                    "project": row_project,
                    "contract_id": contract_id,
                    "contract_no": contract_no,
                    "ship_name": ship_name,
                    "cargo_name": cargo_name,
                    "cargo_product_name": cargo_product_name,
                    "consignor": consignor,
                    "consignee": consignee,
                    "commissioner_identifier": commissioner_identifier,
                    "commissioner_note": commissioner_note,
                    "trade_type": normalized_payload.get("header_info", {}).get("内、外贸"),
                    "transport_mode": remark.get("transport_mode")
                    or cargo_info.get("运输方式"),
                    "destination_station": destination_station or remark.get("destination") or None,
                    "yard_location": parse_yard_location(special_matter),
                    "customs_release_qty": parse_customs_release_qty(special_matter),
                    "notice_date": notice_date,
                    "batch_date": remark.get("date") or notice_date,
                    "batch_sequence": remark.get("sequence"),
                    "batch_quantity": remark.get("quantity")
                    or parse_number(cargo_info.get("总重里")),
                    "total_planned_quantity": total_planned_quantity,
                    "remaining_quantity": remark.get("remaining_qty"),
                    "batch_count": len(remarks),
                    "origin_station": origin_station,
                    "agent_name": agent_name,
                    "customer_name": customer_name,
                    "id_label": id_label,
                    "actual_wagon_count": 0,
                    "dispatch_status": "in_progress",
                    "dispatch_status_note": None,
                    "dispatch_status_updated_at": None,
                    "is_weighed": is_weighed,
                    "loading_weight": None,
                    "return_weight": None,
                    "tail_cargo_weight": None,
                    "tail_cargo_status": "pending",
                    "tail_cargo_remark": None,
                    "source_file_name": source_file_name,
                    "source_json": write_json(normalized_payload, pretty=False),
                    "searchable_text": to_searchable_text(normalized_payload),
                    "plan_id": plan_id,
                    "order_id": order_id,
                    "_review_needed": remark.get("_review_needed", False),
                }
            )

        return rows


def hydrate_row(row) -> ReleaseBatchRecord:
    # sqlite3.Row doesn't support .get(), so we check keys if needed
    keys = row.keys()
    return ReleaseBatchRecord(
        id=row["id"],
        batch_key=row["batch_key"],
        contract_id=row["contract_id"],
        contract_no=row["contract_no"],
        ship_name=row["ship_name"],
        cargo_name=row["cargo_name"],
        cargo_product_name=row["cargo_product_name"] if "cargo_product_name" in keys else None,
        consignor=row["consignor"],
        consignee=row["consignee"],
        trade_type=row["trade_type"],
        transport_mode=row["transport_mode"],
        destination_station=row["destination_station"],
        yard_location=row["yard_location"],
        customs_release_qty=row["customs_release_qty"],
        notice_date=row["notice_date"],
        batch_date=row["batch_date"],
        batch_sequence=row["batch_sequence"],
        batch_quantity=row["batch_quantity"],
        total_planned_quantity=row["total_planned_quantity"],
        remaining_quantity=row["remaining_quantity"],
        batch_count=row["batch_count"],
        origin_station=row["origin_station"] if "origin_station" in keys else None,
        agent_name=row["agent_name"] if "agent_name" in keys else None,
        customer_name=row["customer_name"] if "customer_name" in keys else None,
        id_label=row["id_label"] if "id_label" in keys else None,
        actual_wagon_count=row["actual_wagon_count"] if "actual_wagon_count" in keys else 0,
        dispatch_status=row["dispatch_status"] if "dispatch_status" in keys else "in_progress",
        dispatch_status_note=row["dispatch_status_note"] if "dispatch_status_note" in keys else None,
        dispatch_status_updated_at=row["dispatch_status_updated_at"] if "dispatch_status_updated_at" in keys else None,
        is_weighed=bool(row["is_weighed"]) if "is_weighed" in keys else False,
        loading_weight=row["loading_weight"] if "loading_weight" in keys else None,
        return_weight=row["return_weight"] if "return_weight" in keys else None,
        tail_cargo_weight=row["tail_cargo_weight"] if "tail_cargo_weight" in keys else None,
        tail_cargo_status=row["tail_cargo_status"] if "tail_cargo_status" in keys else None,
        tail_cargo_remark=row["tail_cargo_remark"] if "tail_cargo_remark" in keys else None,
        source_file_name=row["source_file_name"],
        source_json=json.loads(row["source_json"]),
        updated_at=row["updated_at"],
        project=row["project"] if "project" in keys else None,
        commissioner_identifier=row["commissioner_identifier"] if "commissioner_identifier" in keys else None,
        commissioner_note=row["commissioner_note"] if "commissioner_note" in keys else None,
        plan_id=row["plan_id"] if "plan_id" in keys else None,
        order_id=row["order_id"] if "order_id" in keys else None,
    )



def _split_aliases(text: str) -> list[str]:
    if not text:
        return []
    text = text.strip()
    if not text:
        return []
    text = text.replace("（", "(").replace("）", ")")
    if "(" in text and text.endswith(")"):
        base, inside = text[:-1].split("(", 1)
        aliases = [base.strip()]
        aliases.extend(part.strip() for part in re.split(r"[，,、/；;\s]+", inside) if part.strip())
        return [alias for alias in aliases if alias]
    return [text]


@lru_cache(maxsize=1)
def load_station_alias_map() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    path = WORKSPACE_DOCS_DIR / "收发货单位和车站匹配关系.md"
    if not path.exists():
        return mapping
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw.startswith("|"):
            continue
        cells = [cell.strip() for cell in raw.strip("|").split("|")]
        if len(cells) < 2:
            continue
        left, right = cells[0], cells[1]
        if "单位简称" in left or ":---" in left:
            continue
        right_for_standard = right.replace("（", "(").replace("）", ")")
        standard_station = right_for_standard.split("(", 1)[0].strip()
        if not standard_station:
            continue
        for alias in _split_aliases(left):
            mapping[alias] = standard_station
        for alias in _split_aliases(right):
            mapping[alias] = standard_station
        mapping[standard_station] = standard_station
    return mapping


def canonicalize_station_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    raw = STATION_OCR_CORRECTIONS.get(raw, raw)
    alias_map = load_station_alias_map()
    if raw in alias_map:
        return alias_map[raw]
    for alias, standard in sorted(alias_map.items(), key=lambda item: len(item[0]), reverse=True):
        if alias and alias in raw:
            return standard
    return raw


def normalize_sequence_label(value: Optional[str], raw_line: Optional[str] = None) -> Optional[str]:
    text = f"{value or ''} {raw_line or ''}"
    if not text.strip():
        return None
    if str(value).startswith("lot"):
        return str(value)
    sequence_patterns = [
        (r"第?一次(?:下达)?(?:计划)?", "lot01"),
        (r"第?二次(?:下达)?(?:计划)?", "lot02"),
        (r"第?三次(?:下达)?(?:计划)?", "lot03"),
        (r"第?四次(?:下达)?(?:计划)?", "lot04"),
        (r"第?五次(?:下达)?(?:计划)?", "lot05"),
        (r"第?六次(?:下达)?(?:计划)?", "lot06"),
        (r"第?七次(?:下达)?(?:计划)?", "lot07"),
        (r"第?八次(?:下达)?(?:计划)?", "lot08"),
        (r"第?九次(?:下达)?(?:计划)?", "lot09"),
        (r"第?十次(?:下达)?(?:计划)?", "lot10"),
    ]
    for pattern, code in sequence_patterns:
        if re.search(pattern, text):
            return code
    return value or None


def extract_single_record(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, list):
        for item in payload:
            if item.get("is_target"):
                return item
        if payload:
            return payload[0]
        raise ValueError("No valid record found in payload.")
    return payload


def infer_cargo_category(product_name: Optional[str]) -> str:
    """Infer the coarse cargo category from a business-side cargo product name.

    中唐 SOP distinguishes the port-side 货物品类 (铁矿/铁矿粉/铁矿石)
    from the text-release 货物品名 (印度粉/混合粉/麦克粉/MB粉/etc.).
    Text releases only carry 品名, so keep the product in cargo_product_name and
    store a coarse category for matching instead of overwriting the category with
    the product name.
    """
    text = str(product_name or "").strip()
    if not text:
        return ""
    normalized = re.sub(r"\s+", "", text).lower()
    iron_product_tokens = [
        "粉",
        "矿",
        "pb",
        "mb",
        "纽曼",
        "麦克",
        "印粉",
        "印度",
        "混合",
        "巴粗",
        "巴混",
        "澳粉",
        "奥粉",
    ]
    if any(token in normalized for token in iron_product_tokens):
        return "铁矿粉"
    return text


def text_cargo_compatible(left: Optional[str], right: Optional[str]) -> bool:
    left_text = re.sub(r"\s+", "", str(left or ""))
    right_text = re.sub(r"\s+", "", str(right or ""))
    if not left_text or not right_text:
        return True
    if left_text == right_text or left_text in right_text or right_text in left_text:
        return True
    return "铁" in left_text and "铁" in right_text and "矿" in left_text and "矿" in right_text


def parse_business_text_fields(text: str) -> Dict[str, str]:
    """Parse the fixed WeChat text release format into SOP field labels."""
    fields: Dict[str, str] = {}
    label_aliases = {
        "供方": "供方",
        "供应方": "供方",
        "船名": "船名",
        "货名": "货名",
        "货物名称": "货名",
        "港口": "港口",
        "到站": "到站",
        "目的地": "到站",
        "数量": "数量",
        "货量": "数量",
        "计划号": "计划号",
        "入场计划号": "计划号",
        "合同号": "合同号",
    }
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^([^:：]{1,12})\s*[:：]\s*(.+?)\s*$", line)
        if not match:
            if "供方" not in fields:
                fields["供方"] = line
            continue
        raw_key, value = match.groups()
        key = label_aliases.get(raw_key.strip())
        if key and value.strip():
            fields[key] = value.strip()
    return fields


def parse_remarks(
    remarks: List[Dict[str, Any]], notice_date: Optional[str]
) -> List[Dict[str, Any]]:
    parsed = []
    for remark in remarks:
        raw_line = str(remark.get("raw_line") or remark.get("plan") or "")
        quantity_match = re.search(r"(\d+(?:\.\d+)?)吨", raw_line)
        remaining_match = re.search(r"剩余(\d+(?:\.\d+)?)吨", raw_line)
        destination_match = re.search(r"[（(]\s*(?:铁路|公路)?\s*([^\s）)]+)?", raw_line)

        if "铁路" in raw_line:
            transport_mode = "铁路"
        elif "公路" in raw_line:
            transport_mode = "公路"
        else:
            transport_mode = remark.get("transport_mode") or None

        destination = remark.get("destination") or parse_destination_station(raw_line)
        if not destination and destination_match:
            destination = destination_match.group(1)
        destination = canonicalize_station_text(destination)

        parsed.append(
            {
                "date": normalize_partial_date(remark.get("date"), notice_date),
                "sequence": normalize_sequence_label(remark.get("sequence"), raw_line),
                "quantity": float(quantity_match.group(1)) if quantity_match else None,
                "transport_mode": transport_mode,
                "destination": destination,
                "remaining_qty": float(remaining_match.group(1)) if remaining_match else None,
                "raw_line": raw_line,
            }
        )
    return parsed


def release_batch_project_for_destination(base_project: Optional[str], destination_station: Optional[str]) -> Optional[str]:
    """Split mixed-project departure-plan rows by explicit row destination."""
    destination = canonicalize_station_text(destination_station)
    if str(base_project or "").strip() == ZHONGTANG_PROJECT and destination == "乌兰浩特":
        return WUGANG_PROJECT
    return base_project


def synthesize_missing_clean_bottom_remarks(
    remarks: List[Dict[str, Any]],
    cargo_info: Dict[str, Any],
    notice_date: Optional[str],
) -> List[Dict[str, Any]]:
    """Recover an OCR loss where the final 清底 lot is omitted.

    If a remark says `剩余X吨`, later parsed quantities sum to Y, and the
    document's cargo total is exactly X-Y, the omitted final clean-bottom lot is
    reconstructed instead of silently losing a batch.
    """
    if not remarks:
        return remarks
    cargo_total = parse_number(cargo_info.get("总重里") or cargo_info.get("总重量"))
    if not cargo_total:
        return remarks
    for index, remark in enumerate(remarks):
        remaining = remark.get("remaining_qty")
        if remaining is None:
            continue
        later_quantity = sum(float(item.get("quantity") or 0) for item in remarks[index + 1 :])
        missing = float(remaining) - later_quantity
        if abs(missing - cargo_total) >= 0.001 or missing <= 0:
            continue
        if any(abs(float(item.get("quantity") or 0) - missing) < 0.001 for item in remarks[index + 1 :]):
            return remarks
        previous = remarks[-1]
        return [
            *remarks,
            {
                "date": notice_date or previous.get("date"),
                "sequence": next_lot_sequence(previous.get("sequence")),
                "quantity": missing,
                "transport_mode": previous.get("transport_mode") or remark.get("transport_mode"),
                "destination": previous.get("destination") or remark.get("destination"),
                "remaining_qty": None,
                "raw_line": "OCR补齐清底",
            },
        ]
    return remarks


def next_lot_sequence(sequence: Optional[str]) -> Optional[str]:
    match = re.match(r"lot(\d+)$", str(sequence or ""))
    if not match:
        return None
    return f"lot{int(match.group(1)) + 1:02d}"


def normalize_chinese_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", str(value))
    if not match:
        return str(value)
    year, month, day = match.groups()
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def normalize_partial_date(
    value: Optional[str], fallback_date: Optional[str]
) -> Optional[str]:
    if not value:
        return fallback_date
    match = re.search(r"(\d{1,2})月(\d{1,2})日", str(value))
    if not match:
        return value
    year = fallback_date[:4] if fallback_date else "1970"
    month, day = match.groups()
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def parse_destination_station(text: str) -> Optional[str]:
    text = str(text)
    match = re.search(r"到站[:：]\s*([^，。；\n]+)", text)
    if match:
        return canonicalize_station_text(match.group(1).strip())
    match = re.search(r"[（(]\s*(?:铁路|公路)?\s*([^\s）)]+)", text)
    if match:
        return canonicalize_station_text(match.group(1).strip())
    return None


def parse_yard_location(text: str) -> Optional[str]:
    match = re.search(r"货物在([^，。；]+)场地", str(text))
    return f"{match.group(1).strip()}场地" if match else None


def parse_customs_release_qty(text: str) -> Optional[float]:
    match = re.search(r"海关放行单[:：]\s*(\d+(?:\.\d+)?)吨", str(text))
    return float(match.group(1)) if match else None


def parse_number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    numeric = re.sub(r"[^\d.]", "", str(value))
    if not numeric:
        return None
    return float(numeric)


def hash_text(value: str) -> str:
    return sha1(value.encode("utf-8")).hexdigest()
