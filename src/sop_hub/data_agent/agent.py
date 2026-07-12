from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha1
import json
from pathlib import Path
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sop_hub.data_agent.db import open_db
from sop_hub.data_agent.json_utils import read_json, to_searchable_text, write_json
from sop_hub.utils.time import now_iso_beijing as _now_iso_beijing_full

WORKSPACE_DOCS_DIR = Path(__file__).resolve().parents[3] / "doc"
PROJECT_SOPS_DIR = Path(__file__).resolve().parents[3] / "config" / "project_sops"
NON_BUSINESS_SOP_HINTS = ("archive", "sandbox", "归档", "沙箱")
# 2026-06-18 #jilin-resend:出港计划通知单重发去重时,处于这些"终态"的批次
# 锁死不再改写(放货/装车阶段已结束,通知单重发只能新增 lot、不能动已完成的)。
_LOCKED_DISPATCH_STATES = frozenset({
    "all_loaded", "tracking", "delivered", "confirmed_received", "closed", "completed",
})
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
    # 中唐特钢汐子站 VLM(Qwen3.6)误识闭集 — 随用经验逐渐丰富。
    # 2026-06-06 用户补:沱子/沙子/夕子;2026-06-28 用户补:涉子。
    "沱子": "汐子",
    "沙子": "汐子",
    "夕子": "汐子",
    "涉子": "汐子",
}


# 2026-06-06 #ocr-normalize 用户补:VLM 把锦州新僡物流有限公司经常 OCR 成
# "锦州新德/新得/新儒/新铁晟" 等(僡 是单人旁+惠,生僻字 VLM 拼不准)。
# prompts.py:96 已在 VLM prompt 写归一指令但模型不严格执行,这里做读时兜底。
# 所有读到 release_batches.consignor / wagon_shipments.shipper_name 的入口
# 都应该过 canonicalize_shipper_text。
SHIPPER_OCR_CORRECTIONS = {
    "锦州新德物流有限公司": "锦州新僡物流有限公司",
    "锦州新得物流有限公司": "锦州新僡物流有限公司",
    "锦州新儒物流有限公司": "锦州新僡物流有限公司",
    "锦州新铁晟港口物流有限公司": "锦州新僡物流有限公司",
    "锦州新铁晟物流有限公司": "锦州新僡物流有限公司",
}


def canonicalize_shipper_text(text: Optional[str]) -> Optional[str]:
    """归一发货单位/承运代理名 — 兜底 VLM OCR 不准。"""
    if not text:
        return text
    raw = str(text).strip()
    if not raw:
        return raw
    if raw in SHIPPER_OCR_CORRECTIONS:
        return SHIPPER_OCR_CORRECTIONS[raw]
    # 模糊兜底:"锦州新"+"物流" 且不是已知正确"新僡" → 强归一
    if "锦州新" in raw and "物流" in raw and "新僡" not in raw:
        return "锦州新僡物流有限公司"
    return raw


# 2026-06-18 #ocr-normalize 用户补:VLM 把 jilin 船名 "蓝鳍" OCR 成 "蓝嶂"
# (嶂 是山字旁生僻字,模型把"鳍"误拼),出港计划通知单整组按错船名建了一批
# 空 release_batch(蓝嶂 lot01-08),只能人工删。船名是独立 OCR 归一闭集
# (与车站/发货单位分开;各项目活跃船登记在 yaml project_meta.known_ships)。
# 所有从 OCR 取到 release_batches.ship_name 的入口都应过 canonicalize_ship_text。
SHIP_OCR_CORRECTIONS = {
    "蓝嶂": "蓝鳍",
}


def canonicalize_ship_text(text: Optional[str]) -> Optional[str]:
    """归一进口船名 — 兜底 VLM OCR 不准(如 蓝鳍→蓝嶂)。"""
    if not text:
        return text
    raw = str(text).strip()
    if not raw:
        return raw
    return SHIP_OCR_CORRECTIONS.get(raw, raw)


def _source_file_order(value: Optional[str]) -> int:
    """Best-effort ordering key for WeChat-exported source files.

    Typical image/file names carry a monotonic numeric prefix like `880_xxx.jpg`.
    We use that prefix to prevent an older replayed notice from overwriting a
    later corrected notice for the same batch_key.
    """
    text = str(value or "").strip()
    if not text:
        return -1
    m = re.match(r"(\d+)_", text)
    if not m:
        return -1
    try:
        return int(m.group(1))
    except ValueError:
        return -1


def _inspection_payload_text(payload: Dict[str, Any]) -> str:
    return to_searchable_text(payload)


def _existing_path_or_empty(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        path = Path(text)
        if path.exists():
            return str(path)
    except Exception:
        return ""
    return ""


def _pending_image_from_extraction_path(value: Any, source_file_name: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        json_path = Path(text)
        stem = json_path.name
        if stem.endswith("_result.json"):
            image_name = stem[:-len("_result.json")] + ".jpg"
        elif source_file_name:
            image_name = source_file_name
        else:
            image_name = json_path.with_suffix(".jpg").name
        image_path = json_path.parent.parent / "images" / image_name
        if image_path.exists():
            return str(image_path)
    except Exception:
        return ""
    return ""


def project_known_ships(project_display: Optional[str]) -> set[str]:
    """加载项目 yaml 的 project_meta.known_ships。

    空集 = 该项目未登记 known_ships(或 yaml 找不到)→ 调用方应透明放行,
    不对船名做未知拦截,避免破坏未登记船名的老项目。
    """
    if not project_display:
        return set()
    try:
        from sop_hub.models.project_sop import PROJECT_ID_ALIASES
        import yaml as _yaml
        from sop_hub.sop.departure_excel import _find_yaml_for_project

        project_id = PROJECT_ID_ALIASES.get(project_display, project_display)
        yp = _find_yaml_for_project(project_id)
        raw = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    except Exception:
        return set()
    pm = raw.get("project_meta") or {}
    return {str(s).strip() for s in (pm.get("known_ships") or []) if s}


@lru_cache(maxsize=1)
def active_business_sop_project_tokens() -> set[str]:
    """ProjectSOP projects that may feed the dispatch-in-progress index.

    The release-dispatch index is an operational queue, not a generic archive.
    Sandbox/archive ProjectSOP fixtures can classify or store evidence, but they
    must not make unrelated release batches participate in inspection matching.
    """
    tokens: set[str] = set()
    try:
        from sop_hub.models.project_sop import load_project_sop

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
            batch_key = normalized["batch_key"]
            existing = self.get_by_batch_key(batch_key)
            if existing is not None:
                existing_order = _source_file_order(existing.source_file_name)
                incoming_order = _source_file_order(normalized.get("source_file_name"))
                if existing_order >= 0 and incoming_order >= 0 and incoming_order < existing_order:
                    inserted.append(existing)
                    continue
            # If the remark was flagged as review_needed, keep a human-facing note
            # but do not write an out-of-schema dispatch_status value.
            is_review_needed = bool(normalized.pop("_review_needed", False))
            if is_review_needed:
                normalized["dispatch_status_note"] = "OCR序列号匹配但日期/重量不一致，需人工核实"
                if existing is not None:
                    normalized["dispatch_status"] = existing.dispatch_status
            # OCR容错(#release-batch-robustness 缺口1):疑似放货日期误读
            # → 归并到现存 OPEN 同序号批 + 告警(不建重复批、不破坏第N批→lotN)。
            misread_into = self._find_open_batch_same_sequence(normalized)
            if misread_into is not None:
                self._merge_suspected_date_misread(misread_into, normalized.get("batch_date"))
                inserted.append(self.get_by_batch_key(misread_into.batch_key) or misread_into)
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

    # OCR容错(#release-batch-robustness 缺口1):疑似放货日期误读检测。
    # 合法的"不同航次同序号 lot"只会在旧批 closed 之后才出现;若新批与某仍 OPEN 的
    # 同(项目,船,货,到站,序号)批次仅日期不同 → 几乎必是 VLM 把放货日期读错(如 6.23→5.23)。
    # 仅 OPEN 状态触发,确保 closed 后的合法新航次(如 5/11 与 5/29 双 lot01)不被误并。
    _OPEN_DISPATCH_STATUSES = ("loading", "enriched", "pending", "review_needed")

    def _find_open_batch_same_sequence(
        self, normalized: dict
    ) -> Optional[ReleaseBatchRecord]:
        seq = normalized.get("batch_sequence")
        if not seq:
            return None
        placeholders = ",".join("?" for _ in self._OPEN_DISPATCH_STATUSES)
        row = self.db.execute(
            f"""SELECT * FROM release_batches
                WHERE IFNULL(project,'') = IFNULL(?,'')
                  AND IFNULL(ship_name,'') = IFNULL(?,'')
                  AND IFNULL(cargo_name,'') = IFNULL(?,'')
                  AND IFNULL(destination_station,'') = IFNULL(?,'')
                  AND batch_sequence = ?
                  AND IFNULL(batch_date,'') != IFNULL(?,'')
                  AND dispatch_status IN ({placeholders})
                ORDER BY updated_at DESC LIMIT 1""",
            (
                normalized.get("project"), normalized.get("ship_name"),
                normalized.get("cargo_name"), normalized.get("destination_station"),
                seq, normalized.get("batch_date"), *self._OPEN_DISPATCH_STATUSES,
            ),
        ).fetchone()
        return hydrate_row(row) if row else None

    def _merge_suspected_date_misread(
        self, existing: ReleaseBatchRecord, incoming_date: Optional[str]
    ) -> None:
        warn = (
            f"疑似放货日期误读: 通知单日期 {incoming_date or '空'} 与现存 lot 日期 "
            f"{existing.batch_date or '空'} 不符,已忽略本次日期、并入现存批次(未建重复批),请人工核。"
        )
        self.db.execute(
            """UPDATE release_batches
               SET tail_cargo_remark = TRIM(IFNULL(tail_cargo_remark,'') || ' | ' || ?, ' |'),
                   updated_at = CURRENT_TIMESTAMP
               WHERE batch_key = ?""",
            (warn, existing.batch_key),
        )
        import logging as _logging
        _logging.getLogger("sop_hub.data_agent").warning(
            "OCR容错·疑似日期误读: %s (batch_key=%s)", warn, existing.batch_key
        )

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
        # #125 lifecycle 8 值 → release_dispatch_match_rules 老 4 值映射
        # (rules.status 仍是 active/completed/suspended/cancelled,只是 chain
        # match 用的缓存,不参与 lifecycle 流转)
        _lc = record.dispatch_status
        if _lc in ("loading", "all_loaded", "tracking", "delivered",
                   "enriched", "pending_freight"):
            _rule_status = "active"
        elif _lc in ("confirmed_received", "closed"):
            _rule_status = "completed"
        else:
            _rule_status = _lc
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
                _rule_status,
                100,
                _rule_status,
                record.dispatch_status_note,
            ),
        )

    def update_release_dispatch_status(self, release_batch_id: str, status: str, manual_note: str | None = None) -> bool:
        # #125: lifecycle 新枚举 + 老值兼容(老调用者传 in_progress/completed 自动迁)
        from sop_hub.sop.lifecycle import ALL_PHASES, LEGACY_MIGRATION_MAP
        normalized_status = LEGACY_MIGRATION_MAP.get(status, status)
        # "active" 是历史别名,等价于 in_progress → loading
        if status == "active":
            normalized_status = "loading"
        if normalized_status not in set(ALL_PHASES):
            raise ValueError(
                f"status must be one of lifecycle.ALL_PHASES (got {status!r} → {normalized_status!r})"
            )
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

        batch_anchor_matches = []
        if not ship_anchor_matches:
            batch_rows = self.db.execute(
                """
                SELECT id AS release_batch_id, project, ship_name,
                       destination_station, cargo_name, dispatch_status
                FROM release_batches
                WHERE dispatch_status IN ('pending_freight','enriched','loading')
                ORDER BY notice_date DESC
                """
            ).fetchall()
            for row in batch_rows:
                ship = str(row["ship_name"] or "")
                dest = str(row["destination_station"] or "")
                cargo = str(row["cargo_name"] or "")
                has_ship = bool(ship and ship in searchable)
                has_destination = bool(dest and dest in searchable)
                has_cargo = bool(cargo and (cargo in searchable or (cargo == "铁矿" and "铁矿粉" in searchable)))
                if has_ship and has_destination and has_cargo:
                    batch_anchor_matches.append(row)

        if len(batch_anchor_matches) == 1:
            return {
                "status": "candidate",
                "reason": "matched_release_batch_from_batch_anchor_waiting_95306_validation",
                "release_batch_ids": [batch_anchor_matches[0]["release_batch_id"]],
            }
        if len(batch_anchor_matches) > 1:
            return {
                "status": "ambiguous",
                "reason": "ambiguous_release_batch_batch_anchor_candidate",
                "release_batch_ids": [row["release_batch_id"] for row in batch_anchor_matches],
            }

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

    @staticmethod
    def _cargo_anchor_variants(cargo_name: str) -> list[str]:
        cargo = str(cargo_name or "").strip()
        if not cargo:
            return []
        variants = {cargo}
        if "铁矿" in cargo or cargo == "铁":
            variants.update({"铁", "铁矿", "铁矿粉"})
        if "镍矿" in cargo or cargo == "镍":
            variants.update({"镍", "镍矿"})
        if "大豆" in cargo:
            variants.add("大豆")
        return [item for item in variants if item]

    def _text_has_managed_inspection_flow(self, text: str) -> bool:
        for bad, good in STATION_OCR_CORRECTIONS.items():
            if bad in text:
                text = text.replace(bad, good)
        rows = self.db.execute(
            """
            SELECT destination_station, cargo_name
            FROM release_dispatch_match_rules
            WHERE status IN ('active', 'completed')
              AND COALESCE(destination_station,'') <> ''
              AND COALESCE(cargo_name,'') <> ''
            UNION
            SELECT destination_station, cargo_name
            FROM release_batches
            WHERE dispatch_status IN ('pending_freight','enriched','loading')
              AND COALESCE(destination_station,'') <> ''
              AND COALESCE(cargo_name,'') <> ''
            """
        ).fetchall()
        for row in rows:
            dest = str(row["destination_station"] or "").strip()
            if not dest or dest not in text:
                continue
            if any(cargo in text for cargo in self._cargo_anchor_variants(row["cargo_name"])):
                return True
        return False

    @staticmethod
    def _text_has_known_station_cargo_flow(text: str) -> bool:
        try:
            from sop_hub.engines.inspection_slip import CARGO_TYPES, STATIONS
        except Exception:
            return False
        has_station = any(station and station in text for station in STATIONS)
        has_cargo = any(cargo and cargo in text for cargo in CARGO_TYPES)
        return bool(has_station and has_cargo)

    def _should_ignore_unmanaged_inspection_payload(self, payload: Dict[str, Any]) -> bool:
        """Ignore inspection slips whose station+cargo flow is outside managed SOPs.

        This is a whitelist gate: station+cargo strings like ``乌兰浩特铁`` are
        treated as flow anchors, not blacklist words. If the flow matches active
        release rules / open release batches, keep the candidate path; otherwise
        ignore it instead of hanging a pending review candidate.
        """
        text = _inspection_payload_text(payload)
        if self._text_has_managed_inspection_flow(text):
            return False
        return self._text_has_known_station_cargo_flow(text)

    def _split_payload_by_ship_rules(
        self, payload: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """#130 (2026-06-08):一图含多船(复合检车单)按 rule.ship token 拆 N 组。

        分组算法:走 rows,看 cargo_info_raw 是否含某 active rule 的 ship token。
        命中 != 当前 rule → 切新组。真车号行(cargo_info_raw 不含船 token)归到
        最近一个 rule 命中的组。

        Returns:
          - [payload] 当只 1 组(老行为)
          - [sub_payload_1, sub_payload_2, ...] 当多组,每个 sub_payload 是
            rows 切片 + meta 复制 + _matched_rule_id/_matched_ship_name 注入
        """
        rows = payload.get("rows") if isinstance(payload, dict) else []
        rows = rows if isinstance(rows, list) else []
        if not rows:
            return [payload]
        rules = self.db.execute(
            "SELECT id, release_batch_id, ship_name, matching_tokens_json FROM release_dispatch_match_rules "
            "WHERE status='active' ORDER BY priority ASC"
        ).fetchall()
        rule_ship_tokens: List[tuple[str, Any]] = []  # (ship_token, rule/batch row)
        for r in rules:
            try:
                tokens = json.loads(r["matching_tokens_json"] or "{}")
            except Exception:
                continue
            for ship_tok in tokens.get("ship") or []:
                if ship_tok:
                    rule_ship_tokens.append((str(ship_tok), r))
        existing_ship_tokens = {tok for tok, _ in rule_ship_tokens if tok}
        batch_rows = self.db.execute(
            """
            SELECT id AS release_batch_id, project, ship_name, destination_station,
                   cargo_name, dispatch_status
            FROM release_batches
            WHERE dispatch_status IN ('pending_freight','enriched','loading')
            ORDER BY notice_date DESC
            """
        ).fetchall()
        for b in batch_rows:
            ship_tok = str(b["ship_name"] or "").strip()
            if not ship_tok or ship_tok in existing_ship_tokens:
                continue
            rule_ship_tokens.append((ship_tok, b))
            existing_ship_tokens.add(ship_tok)

        # 算法:走 rows 找所有 ship 锚点 (seq, rule)。每段从
        # (上一锚点之后 + 本锚点前向吸收 N 表头行) 到 (下一锚点前向吸收 N - 1)。
        # 检车单 OCR 真实结构:每段开头 3-4 行是 destination/收货代理/船名/数量 等
        # 表头,本算法把 ship 锚点前的连续标注行(cargo 非空)向上吸收最多 N=4 行
        # 给该段,这样能涵盖完整段头(seq 15 朝阳西铁矿 / seq 16 鞍钢集团 /
        # seq 17 宝腾海 → 段头 3 行)。
        ANCHOR_BACK_LOOKUP = 4
        anchor_points: List[tuple[int, Any]] = []  # (row_idx, rule)
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            cargo = str(row.get("cargo_info_raw") or "")
            for ship_tok, rule in rule_ship_tokens:
                if ship_tok in cargo:
                    anchor_points.append((idx, rule))
                    break
        if not anchor_points:
            return [payload]

        # 2026-06-09:相邻同 rule 的 anchor 合并 — qwen3-vl 偶尔会把空单元格脑补
        # 成船名,使同一船的 anchor 出现 2 次。原算法会切成 N 段,导致 36 车
        # 一张鞍子河单被拆成 28+8 两个 candidate(2026-06-08 wx_17)。
        # 只对相邻 rule 比较:如果格式真"船A→船B→船A"交错(罕见),保留 3 段。
        deduped_anchors: List[tuple[int, Any]] = []
        last_rid = object()
        for a_idx, a_rule in anchor_points:
            if a_rule is None:
                rid = None
            elif "id" in a_rule.keys():
                rid = a_rule["id"]
            else:
                rid = f"batch:{a_rule['release_batch_id']}"
            if rid != last_rid:
                deduped_anchors.append((a_idx, a_rule))
                last_rid = rid
        anchor_points = deduped_anchors

        # 段起点 = anchor 前向吸收(直到 cargo 空或抵达上一段尾的下一行)
        boundaries: List[int] = []  # segment start indices
        last_seg_end = -1
        for anc_idx, _ in anchor_points:
            # 从 anc_idx 向前找连续 cargo 非空行(且 > last_seg_end)
            start = anc_idx
            for back in range(1, ANCHOR_BACK_LOOKUP + 1):
                cand = anc_idx - back
                if cand <= last_seg_end:
                    break
                if cand < 0:
                    break
                cand_cargo = str(rows[cand].get("cargo_info_raw") or "").strip()
                if not cand_cargo:
                    break
                start = cand
            boundaries.append(start)
            last_seg_end = anc_idx  # next anchor's back-lookup stops here
        # 段划分
        segments: List[tuple[Any, List[int]]] = []
        prefix_end = boundaries[0]
        if prefix_end > 0:
            segments.append((None, list(range(0, prefix_end))))
        for i, b_start in enumerate(boundaries):
            b_end = boundaries[i + 1] if i + 1 < len(boundaries) else len(rows)
            segments.append((anchor_points[i][1], list(range(b_start, b_end))))
        if len(segments) <= 1:
            return [payload]
        out: List[Dict[str, Any]] = []
        for rule, idxs in segments:
            sub_rows = [rows[i] for i in idxs]
            sub = dict(payload)
            sub["rows"] = sub_rows
            # 覆盖 footer.zhuangche_jieshu = 本段真车数(非 defect),否则 chain
            # 后续 sanity 检查会拿全图 jieshu 跟本段 loading_car_nos 比对,误判
            # 数量不一致 → pending_review。本段独立后,zhuangche_jieshu = 本段长。
            real_cars_in_seg = sum(
                1 for r in sub_rows
                if isinstance(r, dict) and r.get("car_no") and not r.get("defect")
            )
            sub_footer = dict(payload.get("footer") or {})
            sub_footer["zhuangche_jieshu"] = real_cars_in_seg
            sub["footer"] = sub_footer
            # 同时 meta.jieshu 也同步(如有)
            if "meta" in sub:
                _m = dict(sub["meta"])
                _m["jieshu"] = real_cars_in_seg
                sub["meta"] = _m
            if rule is not None:
                if "matching_tokens_json" not in rule.keys():
                    # Synthetic fallback from release_batches:没有 rules 行也能按船锚拆分,
                    # 后续 ingest 直接用 release_batch_id 归属。
                    sub["_split_group_rule_id"] = ""
                    sub["_split_group_release_batch_id"] = rule["release_batch_id"] or ""
                    sub["_split_group_ship_name"] = rule["ship_name"] or ""
                else:
                    sub["_split_group_rule_id"] = rule["id"]
                    sub["_split_group_ship_name"] = rule["ship_name"] or ""
            else:
                sub["_split_group_rule_id"] = ""
                sub["_split_group_ship_name"] = ""
                sub["_split_group_unmatched"] = True
            out.append(sub)
        return out

    def ingest_inspection_payload(
        self,
        payload: Dict[str, Any],
        source_file_name: Optional[str] = None,
        group_name: Optional[str] = None,
        include_completed_release_batches: bool = False,
    ) -> Dict[str, Any]:
        # #130:复合检车单按 ship rule 拆 N 组,各自 ingest
        # #131 (2026-06-08):unmatched 段(无 rule 命中,如乌兰浩特项目未建)
        #   **直接丢弃**,不建 candidate 占位 pending_review。用户决策:不投资
        #   未建项目的"等手工归属"路径。
        groups = self._split_payload_by_ship_rules(payload)
        if len(groups) > 1:
            results = []
            dropped = 0
            for g in groups:
                if g.get("_split_group_unmatched"):
                    dropped += len(g.get("rows") or [])
                    continue
                r = self._ingest_single_inspection_payload(
                    g, source_file_name=source_file_name, group_name=group_name,
                    include_completed_release_batches=include_completed_release_batches,
                )
                results.append(r)
            return {
                "status": "multi_group",
                "reason": (f"image split into {len(groups)} groups; "
                           f"{len(results)} matched, dropped {dropped} unmatched rows"),
                "groups": results,
                "dropped_unmatched_rows": dropped,
            }
        return self._ingest_single_inspection_payload(
            payload, source_file_name=source_file_name, group_name=group_name,
            include_completed_release_batches=include_completed_release_batches,
        )

    def _ingest_single_inspection_payload(
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
        if status != "candidate" and self._should_ignore_unmanaged_inspection_payload(payload):
            return {
                "status": "ignored",
                "reason": "ignored_unmanaged_inspection_flow",
                "candidate_ids": [],
                "release_batch_ids": [],
                "wagon_count": len(car_numbers),
            }
        release_batch_id = release_batch_ids[0] if status == "candidate" and len(release_batch_ids) == 1 else None
        candidate_payload = dict(payload)
        if release_batch_ids:
            candidate_payload["_candidate_release_batch_ids"] = release_batch_ids
        candidate_id = hash_text(f"inspection|{source_file_name}|{','.join(car_numbers)}|{reason}")
        _cand_now = _now_iso_beijing_full()  # TZ铁律:候选时间戳走北京,绝不 CURRENT_TIMESTAMP(UTC)
        self.db.execute(
            """
            INSERT INTO inspection_ingestion_candidates (
              id, source_file_name, status, reason, group_name, release_batch_id,
              wagon_count, car_numbers_json, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              status=excluded.status,
              reason=excluded.reason,
              group_name=excluded.group_name,
              release_batch_id=excluded.release_batch_id,
              wagon_count=excluded.wagon_count,
              car_numbers_json=excluded.car_numbers_json,
              payload_json=excluded.payload_json,
              updated_at=excluded.updated_at
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
                _cand_now,
                _cand_now,
            ),
        )
        self.db.commit()

        # ── 关联 message_inbox + 回填路径 ────────────────────────────────
        # 候选按 source_file_name 建,但 chaoyang_inspection_chain 执行器是按
        # candidate.message_id 回查的;同时它还要从 candidate 读
        # extraction_json_path / source_image_path 去算 loading_rows 和
        # footer.zhuangche_jieshu。之前三个都不回填 → 执行器报
        # "no inspection candidate" 或 "extraction JSON not found"。三个一起补。
        _has_message_inbox = bool(self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_inbox'"
        ).fetchone())
        if source_file_name and _has_message_inbox:
            _stem = source_file_name.rsplit(".", 1)[0]
            _cols = {
                str(r["name"])
                for r in self.db.execute("PRAGMA table_info(message_inbox)").fetchall()
            }
            _image_cols = [
                c for c in (
                    "business_archive_image_path",
                    "raw_standard_image_path",
                    "msg_path",
                    "raw_msg_path",
                    "missing_media_path",
                )
                if c in _cols
            ]
            _select_cols = ["message_id", "extraction_json_path", *_image_cols]
            _ib = self.db.execute(
                f"SELECT {', '.join(_select_cols)} "
                "FROM message_inbox "
                "WHERE raw_standard_image_path LIKE ? OR extraction_json_path LIKE ? "
                "ORDER BY id DESC LIMIT 1",
                (f"%{source_file_name}", f"%{_stem}%"),
            ).fetchone()
            if _ib:
                _resolved_image_path = ""
                for _col in _image_cols:
                    _resolved_image_path = _existing_path_or_empty(_ib[_col])
                    if _resolved_image_path:
                        break
                if not _resolved_image_path:
                    _resolved_image_path = _pending_image_from_extraction_path(
                        _ib["extraction_json_path"] or "", source_file_name)
                # COALESCE(NULLIF(?,'')...):仅在 inbox 有值时覆盖,空值不冲掉
                # 候选已有的(防再次跑覆盖)。三个字段一起 UPDATE。
                self.db.execute(
                    """UPDATE inspection_ingestion_candidates SET
                         message_id=COALESCE(NULLIF(?,''), message_id),
                         extraction_json_path=COALESCE(NULLIF(?,''), extraction_json_path),
                         source_image_path=COALESCE(NULLIF(?,''), source_image_path)
                       WHERE id=?""",
                    (
                        _ib["message_id"] or "",
                        _ib["extraction_json_path"] or "",
                        _resolved_image_path,
                        candidate_id,
                    ),
                )
                self.db.execute(
                    "UPDATE message_inbox "
                    "SET inspection_candidate_id=COALESCE(NULLIF(inspection_candidate_id,''), ?) "
                    "WHERE message_id=?",
                    (candidate_id, _ib["message_id"] or ""),
                )
                if _resolved_image_path:
                    _path_cols = [
                        c for c in ("raw_standard_image_path", "msg_path", "raw_msg_path")
                        if c in _cols
                    ]
                    if _path_cols:
                        _sets = ", ".join(f"{c}=?" for c in _path_cols)
                        self.db.execute(
                            f"UPDATE message_inbox SET {_sets} WHERE message_id=?",
                            [*([_resolved_image_path] * len(_path_cols)), _ib["message_id"] or ""],
                        )
                self.db.commit()

        # ── infer ship/dest/cargo/project_id 三层级联 ────────────────
        # B 文本融合 → A 证据打分 → 都不行 → pending_review(业务铁律)
        # 失败也不让 ingest 整体 fail,只是不更新 inferred 字段。
        try:
            from sop_hub.sop.infer_candidate_context import infer_candidate_context
            from sop_hub.data_agent.db import get_db_path
            received = ""
            if source_file_name:
                ib = self.db.execute(
                    "SELECT received_datetime FROM message_inbox "
                    "WHERE raw_standard_image_path LIKE ? "
                    "ORDER BY id DESC LIMIT 1",
                    (f"%{source_file_name}",),
                ).fetchone()
                if ib:
                    received = ib["received_datetime"] or ""
            inferred = infer_candidate_context(
                payload, group_name=group_name or "",
                received_datetime=received,
                db_path=str(get_db_path()),
            )
            # #130:如果 split_payload_by_ship_rules 已经命中明确 rule,
            # split 那一层结果就是 ground truth。绕开 infer 推断的不确定,
            # 直接用 rule 的 ship+batch_id,candidate_status=candidate 让 chain
            # 接得到 ship_name 等关键字段。
            _split_rule_id = payload.get("_split_group_rule_id") or ""
            if _split_rule_id and not payload.get("_split_group_unmatched"):
                _rule_row = self.db.execute(
                    "SELECT r.ship_name, r.destination_station, r.cargo_name, "
                    "       r.release_batch_id, rb.project "
                    "FROM release_dispatch_match_rules r "
                    "JOIN release_batches rb ON r.release_batch_id=rb.id "
                    "WHERE r.id=?", (_split_rule_id,),
                ).fetchone()
                if _rule_row:
                    inferred.ship_name = _rule_row["ship_name"] or inferred.ship_name
                    inferred.destination = _rule_row["destination_station"] or inferred.destination
                    inferred.cargo_name = _rule_row["cargo_name"] or inferred.cargo_name
                    inferred.project_id = _rule_row["project"] or inferred.project_id
                    inferred.release_batch_id = _rule_row["release_batch_id"]
                    inferred.candidate_status = "candidate"
                    inferred.reason = "split_group_ship_rule_matched"
            _split_batch_id = payload.get("_split_group_release_batch_id") or ""
            if _split_batch_id and not payload.get("_split_group_unmatched"):
                _batch_row = self.db.execute(
                    "SELECT id, ship_name, destination_station, cargo_name, project "
                    "FROM release_batches WHERE id=?", (_split_batch_id,),
                ).fetchone()
                if _batch_row:
                    inferred.ship_name = _batch_row["ship_name"] or inferred.ship_name
                    inferred.destination = _batch_row["destination_station"] or inferred.destination
                    inferred.cargo_name = _batch_row["cargo_name"] or inferred.cargo_name
                    inferred.project_id = _batch_row["project"] or inferred.project_id
                    inferred.release_batch_id = _batch_row["id"]
                    inferred.candidate_status = "candidate"
                    inferred.reason = "split_group_release_batch_anchor_matched"
            # 写回候选(含业务铁律:matched 才设 release_batch_id,挂起也明确状态)
            new_release_batch_id = inferred.release_batch_id or release_batch_id
            self.db.execute(
                """
                UPDATE inspection_ingestion_candidates SET
                  ship_name=?, destination=?, cargo_name=?, project_id=?,
                  release_batch_id=?, candidate_status=?, reason=?,
                  updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    inferred.ship_name, inferred.destination, inferred.cargo_name,
                    inferred.project_id, new_release_batch_id,
                    inferred.candidate_status, inferred.reason,
                    candidate_id,
                ),
            )
            self.db.commit()
            # 同步外部 status / release_batch_ids,让 runner 知道
            if inferred.matched:
                status = "candidate"
                reason = inferred.reason
                if inferred.release_batch_id:
                    release_batch_id = inferred.release_batch_id
                    release_batch_ids = [inferred.release_batch_id]
            else:
                status = "pending"
                reason = inferred.reason
        except Exception as exc:
            import logging
            logging.getLogger("sop_hub.data_agent").warning(
                "infer_candidate_context failed for candidate %s: %s",
                candidate_id, exc,
            )

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
            "assigned_at": _now_iso_beijing_full(),
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
            """SELECT id, batch_key, batch_sequence, batch_date, batch_quantity,
                      notice_date, dispatch_status, project
               FROM release_batches
               WHERE ship_name = ? AND cargo_name = ?
                 AND (destination_station LIKE ? OR destination_station = ?)
               ORDER BY batch_sequence""",
            (ship_name, cargo_name, dest_pattern, destination_station),
        ).fetchall()
        # 2026-06-06 #105:加 notice_date / project / id / batch_key 回传 ——
        # 老版本只返了 4 字段,下游 scope-by-notice_date 永远拿不到值 → filter 把
        # 整个 existing_batches 清空 → ingest 二次去重失效。鞍子河 5 段 remark
        # 全建新 batch 的根因。
        return [
            {
                "id": r[0],
                "batch_key": r[1],
                "batch_sequence": r[2],
                "batch_date": r[3],
                "batch_quantity": r[4],
                "notice_date": r[5],
                "dispatch_status": r[6],
                "project": r[7],
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
        # ── 项目 scope 过滤(2026-06-03 蓝鳍 lot4 公路/彰武鑫汇 误入根因) ──
        # 出港通知单的 remarks 里可能含**不属于本项目**的 lot(如 jilin_jingang
        # 合同明确只走铁路四平,但 notice 偶尔会带"(公路 彰武鑫汇)"这种走别的
        # 渠道的 lot)。这里按 project yaml 里 transport_modes / destination_station
        # 把超出范围的 remark 透明 skip 掉,不建 release_batch。skipped 走日志保留痕迹。
        _proj_display = normalized_payload.get("project") or normalized_payload.get("项目")
        remarks, _scope_skipped = _filter_remarks_by_project_scope(remarks, _proj_display)
        if _scope_skipped:
            import logging as _logging
            _logging.getLogger("sop_hub.data_agent").info(
                "ingest_release_batch: skipped %d out-of-scope remark(s) for project=%r: %s",
                len(_scope_skipped), _proj_display,
                [(r.get("sequence"), r.get("transport_mode"), r.get("destination"),
                  r.get("_skipped_reason")) for r in _scope_skipped],
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

        # #ocr-normalize 读时兜底:VLM 把船名拼错(蓝鳍→蓝嶂)→ 归一回正名。
        ship_name = canonicalize_ship_text(
            business_info.get("进口船名") or business_info.get("船名", "")
        ) or ""

        # ── 未知船名拦截(2026-06-18 蓝嶂 OCR 误读建空批次根因)──────────────
        # canonicalize_ship_text 是第一道防线;这是第二道:若项目 yaml 登记了
        # known_ships,而归一后船名仍不在其中,大概率是没收录的 OCR 误读(或真·
        # 新船没登记)→ 不建整组 release_batch,只记日志待人工。比建出一堆错船名
        # 的空批次再人工删要安全得多。仅当项目 known_ships 非空才启用,空集透明放行。
        _known_ships = project_known_ships(project)
        if ship_name and _known_ships and ship_name not in _known_ships:
            import logging as _logging
            _logging.getLogger("sop_hub.data_agent").warning(
                "ingest_release_batch: ship_name=%r 不在 project=%r known_ships=%s "
                "— 疑似 OCR 误读或未登记新船,跳过整组批次创建。修法:在 "
                "canonicalize_ship_text 补归一映射,或在 yaml project_meta.known_ships "
                "补登记后重跑。",
                ship_name, project, sorted(_known_ships),
            )
            return []

        cargo_name = cargo_info.get("货物品类") or cargo_info.get("货物名称", "")
        cargo_product_name = cargo_info.get("货物品名") or normalized_payload.get("货物品名")
        # #112 #ocr-normalize 读时兜底:VLM 拼不准"锦州新僡"。
        consignor = canonicalize_shipper_text(business_info.get("发货单位", ""))
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

        # R78: 尊重 chaoyang.release_batch_policy.do_not_merge_release_batches。
        # 同船(同到站同货)在不同 notice 下是不同 batch,sequence 应当
        # **同 notice_date 内** dedup;跨 notice 的 lot01 不该相互比对。
        # 之前 dedup map 把 c600 (notice=5/11 lot01) 跟新 9918a9
        # (notice=5/29 lot01) 按 sequence='lot01' 互比 → 误报
        # review_needed("date/qty 不一致")。
        #
        # 2026-06-11 fix ③:project policy 分流。
        # chaoyang yaml 明示 do_not_merge_release_batches=true → 走 notice_date scope.
        # jilin 没这配置,业务模型是"累计计划通知单"(一图含 7 次 remarks 累计),
        # 跨 notice 同 sequence 应对账,不该 scope 清空。否则:6/10 新通知单包含
        # 5/21/5/27/5/29 历史 remarks,这些 remark 的 batch_date 跟历史 lot 完全
        # 一致,但 notice_date 不同 → existing 被清空 → 误建重复 lot。
        _scope_by_notice = True  # 默认走原 chaoyang 行为(safer fallback)
        try:
            import yaml as _yaml
            for _yml in PROJECT_SOPS_DIR.glob("*.yaml"):
                with open(_yml, "r", encoding="utf-8") as _f:
                    _y = _yaml.safe_load(_f) or {}
                if _y.get("project_id") != project:
                    continue
                # 优先 release_batch_policy(顶层),其次 chaoyang 嵌在
                # release_notice_flow.steps[].rule 里也有,简化只看顶层。
                _pol = _y.get("release_batch_policy", {}) or {}
                _scope_by_notice = bool(_pol.get("do_not_merge_release_batches", False))
                break
        except Exception:
            pass
        if _scope_by_notice and existing_batches and notice_date:
            scoped = [
                eb for eb in existing_batches
                if (eb.get("notice_date") or "") == notice_date
            ]
            if scoped:
                existing_batches = scoped
            else:
                # 没 same-notice batch → 视为新 notice,跳过 sequence dedup
                existing_batches = []

        review_needed_remarks: list[dict[str, Any]] = []
        filtered_remarks: list[dict[str, Any]] = []
        if existing_batches:
            # Build lookup: sequence -> (batch_date, batch_quantity)
            existing_map: dict[str, tuple[str | None, float | None]] = {}
            # 2026-06-18 #jilin-resend:seq -> dispatch_status,用于锁死已完成批次。
            existing_status_map: dict[str, str] = {}
            for eb in existing_batches:
                seq = eb.get("batch_sequence") or eb.get("sequence", "")
                if not seq:
                    continue
                existing_map[seq] = (eb.get("batch_date"), eb.get("batch_quantity"))
                existing_status_map[seq] = (eb.get("dispatch_status") or "").strip().lower()

            # 2026-06-06 #105:date+qty fuzzy 反查表 —— 当 VLM 把 sequence 抽错
            # (长航滨海 lot01/lot03 case),seq 不在 existing_map,但 (date,qty)
            # 可能跟某个 existing 完全对上 → 用 existing 的 sequence 校正,避免
            # 误建新 batch。
            existing_by_date_qty: dict[tuple[str, float], str] = {}
            for eb in existing_batches:
                eb_date = str(eb.get("batch_date") or "")
                eb_qty = eb.get("batch_quantity")
                if eb_date and eb_qty is not None:
                    try:
                        existing_by_date_qty[(eb_date, float(eb_qty))] = (
                            eb.get("batch_sequence") or ""
                        )
                    except (TypeError, ValueError):
                        pass

            def _norm_date(d: str) -> str:
                # "4月18日" / "2026-04-18" / "2026年4月18日" 都归一为 "MM-DD"(丢年)
                # —— 同 notice_date scope 内年都相同,放心丢;remark 端常常没年。
                s = str(d or "").replace("年", "-").replace("月", "-").replace("日", "")
                parts = [p for p in s.split("-") if p]
                if not parts:
                    return ""
                if len(parts) >= 2:
                    parts = parts[-2:]
                return "-".join(p.lstrip("0") for p in parts)

            for remark in remarks:
                seq = str(remark.get("sequence") or "")
                if not seq:
                    filtered_remarks.append(remark)
                    continue
                if seq not in existing_map:
                    # 2026-06-06 #105:seq 不撞 → 用 date+qty fuzzy 反查
                    remark_date = str(remark.get("date") or "")
                    remark_qty = remark.get("quantity")
                    if remark_date and remark_qty is not None:
                        for (db_d, db_q), db_seq in existing_by_date_qty.items():
                            if (
                                _norm_date(remark_date) == _norm_date(db_d)
                                and abs(float(remark_qty) - db_q) < 0.01
                            ):
                                # 命中 — VLM 把 seq 抽错,校正成 DB 里现有 seq
                                import logging as _log
                                _log.getLogger("sop_hub.data_agent").info(
                                    "release_batch dedup: OCR sequence %r corrected to %r by date+qty match (date=%s qty=%s)",
                                    seq, db_seq, remark_date, remark_qty,
                                )
                                remark["sequence"] = db_seq
                                # 标记后跳过 INSERT(已存在)
                                seq = db_seq
                                break
                    if seq not in existing_map:
                        # 仍然不在 → 真新 batch
                        filtered_remarks.append(remark)
                        continue
                    # 校正后已存在 → 跳过
                    continue

                # Sequence exists in DB — compare weight and date
                db_date, db_qty = existing_map[seq]

                # 2026-06-18 #jilin-resend:已进入终态的批次锁死。出港计划通知单
                # 重发(尤其 jilin 累计通知单会把历史 lot 又带进来)绝不能再改写
                # 已"收货/关闭/发完/到货/在途"的批次。否则 OCR 数量噪声触发
                # date命中+qty不符 → review_needed → ON CONFLICT 重写 → updated_at
                # 翻新、群里误报"新建放货批次"。锁死=直接跳过,不比对不重写。
                if existing_status_map.get(seq, "") in _LOCKED_DISPATCH_STATES:
                    import logging as _log
                    _log.getLogger("sop_hub.data_agent").info(
                        "release_batch dedup: seq=%r 已终态(%s),通知单重发锁死跳过",
                        seq, existing_status_map.get(seq),
                    )
                    continue

                remark_date = str(remark.get("date") or "")
                remark_qty = remark.get("quantity")

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
                    "batch_date": _sane_batch_date(remark.get("date"), notice_date),
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
                    # #125 lifecycle:新批次默认 pending_freight(合同/订单可能后补),
                    # freight_detail_enrichment 完成后状态机推到 enriched → loading
                    "dispatch_status": "pending_freight",
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


def _sane_batch_date(raw: Optional[str], notice_date: Optional[str]) -> Optional[str]:
    """放货日期(第N次下达计划那行的日期)sanity 闸。

    下达计划日 ≤ 检装/通知日(计划先于发运);若解析出的日期**晚于 notice_date**,
    十有八九是 OCR 月份误读(实证 2026-06-28:马兰幸福/马兰希望 把 6-27 读成
    **8-27**,batch_date 落进未来,污染发运 excel 归档路径)→ 退回 notice_date 并告警。
    ISO 串(YYYY-MM-DD)可直接字典序比大小。
    """
    r = (raw or "").strip()
    nd = (notice_date or "").strip()
    if not r:
        return nd or r
    if nd and r > nd:
        import logging as _lg
        _lg.getLogger("sop_hub.data_agent").warning(
            "batch_date %r 晚于 notice_date %r — 疑 OCR 月份误读,退回 notice_date", r, nd)
        return nd
    return r


def normalize_sequence_label(value: Optional[str], raw_line: Optional[str] = None) -> Optional[str]:
    text = f"{value or ''} {raw_line or ''}"
    if not text.strip():
        return None
    v = str(value or "").strip()
    if v.lower().startswith("lot"):
        # 2026-06-06 #105:lot3 → lot03 归一,否则 ingest 二次去重撞不上。
        m = re.match(r"^lot\s*(\d+)\s*$", v, re.IGNORECASE)
        if m:
            return f"lot{int(m.group(1)):02d}"
        return v
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


def _filter_remarks_by_project_scope(
    remarks: List[Dict[str, Any]],
    project_display: Optional[str],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按项目 yaml 的 scope(transport_modes / destination_station)过滤超出
    项目范围的 remark。返回 (kept, skipped)。

    业务案例(2026-06-03 jilin_jingang 蓝鳍):
      "(公路 彰武鑫汇)" → transport_mode=公路 不在 ["铁路"] → skip
      "(铁路 四平)"      → keep
    显式标了 transport_mode/destination 且不匹配 → skip。
    没显式标 → 视为继承项目默认,keep(不强求,避免漏掉合规的 remark)。
    """
    if not project_display or not remarks:
        return remarks, []
    try:
        from sop_hub.models.project_sop import PROJECT_ID_ALIASES
        project_id = PROJECT_ID_ALIASES.get(project_display, project_display)
        import yaml as _yaml
        from sop_hub.sop.departure_excel import _find_yaml_for_project
        yp = _find_yaml_for_project(project_id)
        raw = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    except Exception:
        # yaml 找不到/加载失败 → 透明放行(向后兼容,不破老项目)
        return remarks, []

    # 兼容两种 yaml 布局:transport_modes / destination_station 可能在顶层
    # 或 project_meta 下(jilin_jingang.yaml 就是后者)。两处都试。
    meta = raw.get("project_meta") or {}
    allowed_modes = (meta.get("transport_modes")
                     or raw.get("transport_modes")
                     or [])
    allowed_dest = ((meta.get("destination_station")
                     or raw.get("destination_station")
                     or "").strip())
    if not allowed_modes and not allowed_dest:
        return remarks, []

    kept: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for r in remarks:
        mode = (r.get("transport_mode") or "").strip()
        dest = (r.get("destination") or "").strip()
        if allowed_modes and mode and mode not in allowed_modes:
            r["_skipped_reason"] = f"transport_mode {mode!r} not in {allowed_modes}"
            skipped.append(r)
            continue
        if allowed_dest and dest and allowed_dest not in dest:
            r["_skipped_reason"] = f"destination {dest!r} 不含 {allowed_dest!r}"
            skipped.append(r)
            continue
        kept.append(r)
    return kept, skipped


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
    # 场地/库场/库 措辞不统一(如"333W库场" vs "239W场地"),都是同一个场地,
    # 归一成"<X>场地"。非贪婪到第一个后缀,避免吞掉后续文字。
    match = re.search(r"货物在([^，。；]+?)(?:场地|库场|库)", str(text))
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
