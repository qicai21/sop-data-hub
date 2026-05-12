from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from hashlib import sha1
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from ops_hub.data_agent.db import open_db
from ops_hub.data_agent.json_utils import read_json, to_searchable_text, write_json

WORKSPACE_DOCS_DIR = Path(__file__).resolve().parents[3] / "doc"

# Common OCR confusions discovered in live release-batch documents.  These are
# station names, not project hard-coding: the final value still passes through
# the normal station canonicalization path below.
STATION_OCR_CORRECTIONS = {
    "沱子": "汐子",
}


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
        # Try to find a default contract if not provided
        normalized_rows = self.normalize_release_batch_payloads(
            payload=payload,
            source_file_name=source_file_name,
            contract_id=contract_id,
            contract_no=contract_no,
        )
        for normalized in normalized_rows:
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
        self.db.commit()
        return [
            self.get_by_batch_key(normalized["batch_key"])
            for normalized in normalized_rows
            if self.get_by_batch_key(normalized["batch_key"]) is not None
        ]

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

        today = date.today().isoformat()
        quantity = parse_number(fields.get("数量"))
        quantity_text = f"{quantity:g}吨" if quantity is not None else str(fields.get("数量") or "")
        payload = {
            "is_target": True,
            "title": "文字放货指令",
            "header_info": {
                "通知日期": today,
                "合同号": fields["合同号"],
                "入场计划号": fields["计划号"],
            },
            "business_info": {
                "发货单位": fields["供方"],
                "进口船名": fields["船名"],
                "船名": fields["船名"],
                "到达港": fields["港口"],
            },
            "cargo_info": {
                "货物名称": fields["货名"],
                "总重里": fields["数量"],
                "发货站(地)": fields["港口"],
            },
            "special_matter": f"港口：{fields['港口']}",
            "remarks": [
                {
                    "date": today,
                    "sequence": fields["计划号"],
                    "plan": f"文字放货指令 {quantity_text}",
                    "raw_line": f"文字放货指令 {quantity_text}",
                    "quantity": quantity,
                }
            ],
            "source_text": text,
        }
        return self.ingest_release_batch(
            payload=payload,
            source_file_name="wechat_text_release_instruction",
            contract_no=fields["合同号"],
        )

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
        self.db.commit()

    def ingest_inspection_payload(
        self,
        payload: Dict[str, Any],
        source_file_name: Optional[str] = None,
        group_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        rows = payload.get("rows") if isinstance(payload, dict) else []
        rows = rows if isinstance(rows, list) else []
        car_numbers = [str(row.get("car_no") or "").strip() for row in rows if isinstance(row, dict) and str(row.get("car_no") or "").strip()]
        searchable = to_searchable_text(payload)
        release_rows = self.db.execute(
            """
            SELECT id, ship_name, destination_station FROM release_batches
            WHERE (? = '' OR searchable_text LIKE ? OR destination_station LIKE ? OR cargo_name LIKE ? OR ship_name LIKE ?)
            ORDER BY updated_at DESC LIMIT 1
            """,
            (
                searchable,
                f"%{searchable[:80]}%",
                "%朝阳%" if "朝阳" in searchable else "%汐子%" if "汐子" in searchable else "%__no_match__%",
                "%铁%" if "铁" in searchable else "%__no_match__%",
                "%合远9%" if "合远9" in searchable else "%__no_match__%",
            ),
        ).fetchall()
        release_batch_id = release_rows[0]["id"] if release_rows else None
        if release_rows and "汐子" in searchable:
            # 汐子 is shared by multiple non-identical ships in live traffic
            # (e.g. 鞍子河、贝拉、马兰探险).  A station/cargo-only hit against
            # release_batches is too broad and can falsely attach a 贝拉/马兰
            # inspection slip to 鞍子河.  Keep it pending unless the OCR payload
            # contains the matched release ship anchor.
            matched_ship = str(release_rows[0]["ship_name"] or "").strip()
            if matched_ship and matched_ship not in searchable:
                release_batch_id = None
        status = "candidate" if release_batch_id else "pending"
        reason = "matched_release_batch_waiting_95306_validation" if release_batch_id else "no_release_batch_candidate"
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
                write_json(payload, pretty=False),
            ),
        )
        self.db.commit()
        return {
            "status": status,
            "reason": reason,
            "candidate_ids": [candidate_id],
            "release_batch_ids": [release_batch_id] if release_batch_id else [],
            "wagon_count": len(car_numbers),
        }

    def write_image_ingestion_audit(self, data: Dict[str, Any]) -> str:
        record_id = data.get("id") or hash_text("|".join(str(data.get(key) or "") for key in ("group_name", "raw_image_path", "classified_image_path", "extraction_json_path")))
        self.db.execute(
            """
            INSERT INTO image_ingestion_audit (
              id, group_name, group_id, message_time, sender, message_id, local_id,
              message_type, raw_image_path, classified_category, classification_confidence,
              classified_image_path, extraction_json_path, project_id, target_node,
              adopted_fields, ignored_fields, db_action, db_tables, db_record_ids,
              status, reason
            ) VALUES (
              :id, :group_name, :group_id, :message_time, :sender, :message_id, :local_id,
              :message_type, :raw_image_path, :classified_category, :classification_confidence,
              :classified_image_path, :extraction_json_path, :project_id, :target_node,
              :adopted_fields, :ignored_fields, :db_action, :db_tables, :db_record_ids,
              :status, :reason
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
              reason=excluded.reason
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

    def normalize_release_batch_payloads(
        self,
        payload: Any,
        source_file_name: Optional[str],
        contract_id: Optional[str],
        contract_no: Optional[str],
    ) -> List[Dict[str, Any]]:
        normalized_payload = extract_single_record(read_json(payload))
        notice_date = normalize_chinese_date(
            normalized_payload.get("header_info", {}).get("通知日期")
        )
        remarks = parse_remarks(normalized_payload.get("remarks", []), notice_date)
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
        cargo_name = cargo_info.get("货物名称", "")
        consignor = business_info.get("发货单位", "")
        consignee = business_info.get("收货单位", "")
        destination_station = parse_destination_station(special_matter) or (
            latest_remark.get("destination") if latest_remark else ""
        )

        # Inheritance logic for weighing
        is_weighed = self.get_last_weighing_preference(ship_name)

        # Attempt to match contract if not provided
        contract = None
        if contract_id:
            contract = self.get_contract(contract_id)
        else:
            contract = self.find_matching_contract(cargo_name, destination_station)
            if contract:
                contract_id = contract.id

        origin_station = contract.origin_station if contract else (cargo_info.get("发货站(地)") or business_info.get("到达港") or None)
        customer_name = contract.party_b if contract else consignee
        # For agent, we might need a better way, but for now let's use a heuristic or party_a
        agent_name = contract.party_a if contract else None 

        base_key = "|".join(
            str(item).strip()
            for item in [ship_name, cargo_name, consignor, consignee, destination_station]
        )
        total_planned_quantity = sum(item.get("quantity") or 0 for item in remarks)
        rows = []

        for remark in remarks:
            seq = str(remark.get("sequence") or "")
            dt = str(remark.get("date") or "")
            
            # 使用 sequence 作为 batch_key 核心；如果没有 sequence，退化为使用 date
            unique_identifier = seq if seq else dt
            batch_key = "|".join([base_key, unique_identifier])

            
            # Generate ID Label: 汐子铁矿粉/沈阳盛京颐昇代/鞍子河
            agent_label = f"{agent_name}代" if agent_name else "未知代理代"
            id_label = f"{destination_station}{cargo_name}/{agent_label}/{ship_name}"

            rows.append(
                {
                    "id": hash_text(batch_key),
                    "batch_key": batch_key,
                    "project": project,
                    "contract_id": contract_id,
                    "contract_no": contract_no,
                    "ship_name": ship_name,
                    "cargo_name": cargo_name,
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
