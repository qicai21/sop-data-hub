"""Freight detail text extractor for SOP freight_detail_flow.

Parses Chinese freight detail text messages such as:

    锦州港今日放货印粉3000吨, 入场合同号HNMC20260520-1X-1, 订单标识CGR20260520095954
    鲅鱼圈港今日放货mb粉2000吨入厂合同号XYSJLJR25KF1222-01-1，订单标识CGR20251222174305

Extracts: order_identifier, contract_no, cargo_name_detail, port, quantity_tons.

Key rules:
  - "订单标识" is the strong identification keyword; without it → no_match.
  - "合同号/入厂合同号/入场合同号" are auxiliary keywords.
  - These texts have NO ship name → binding_status = needs_manual_binding.
  - System MUST NOT auto-bind to any release_batch.
  - Output is a FreightDetailCandidate for Agent/manual review.

This module stays local-only:
- accepts a MessageEvent or text + metadata;
- returns a FreightDetailCandidate dataclass;
- does NOT write sop_agent.db, query 95306, or send reports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sop_hub.sop.monitoring_plan_matcher import MessageEvent


# ── Regex fragments ─────────────────────────────────────────────────────

# "订单标识" followed by CGR-prefixed identifier
_RE_ORDER_ID = re.compile(r"订单标识\s*(CGR\d+)")

# Contract number patterns: "合同号", "入厂合同号", "入场合同号"
_RE_CONTRACT_NO = re.compile(
    r"(?:入厂合同号|入场合同号|合同号)\s*([A-Za-z0-9\-–—]+)"
)

# Cargo name: word chars + 粉/矿 just before quantity or "吨"
_RE_CARGO_NAME = re.compile(
    r"放货\s*(\S+?)\s*\d{3,5}\s*吨"
)

# Quantity: digits + 吨
_RE_QUANTITY = re.compile(r"(\d{3,5})\s*吨")

# Port: word chars + 港 before "今日" or standalone
_RE_PORT = re.compile(r"(\S{2,4}港)\s*(?:今日|今天)?")

# Ship name marker — freight detail should NOT have ship names
_RE_SHIP_MARKER = re.compile(r"船名|船号|船\s*名")


# ── Data model ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FreightDetailCandidate:
    """Parsed freight detail text result.

    Fields:
      message_id: source message identifier.
      group_id: source group identifier.
      message_time: message timestamp.
      raw_text: original message text.
      project_id: SOP project_id (jilin_jingang_jinzhou).
      port: departure port name (e.g. 锦州港, 鲅鱼圈港).
      cargo_name_detail: detailed cargo name (e.g. 印粉, mb粉).
      quantity_tons: extracted quantity in tons. -1 if unparseable.
      contract_no: contract number (e.g. HNMC20260520-1X-1).
      order_identifier: CGR order identifier (e.g. CGR20260520095954).
      status: "complete" | "incomplete" | "no_match".
      binding_status: "needs_manual_binding" (always — has no ship name).

    status = "no_match"       → text has no "订单标识".
    status = "incomplete"     → has order_identifier but missing other fields.
    status = "complete"       → order_identifier + at least one other field found.
    """

    message_id: str
    group_id: str
    message_time: str
    raw_text: str
    project_id: str = "jilin_jingang_jinzhou"
    port: str = ""
    cargo_name_detail: str = ""
    quantity_tons: int = -1
    contract_no: str = ""
    order_identifier: str = ""
    status: str = "no_match"
    binding_status: str = "needs_manual_binding"
    source: str = "freight_detail_extractor"

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "group_id": self.group_id,
            "message_time": self.message_time,
            "raw_text": self.raw_text,
            "project_id": self.project_id,
            "port": self.port,
            "cargo_name_detail": self.cargo_name_detail,
            "quantity_tons": self.quantity_tons,
            "contract_no": self.contract_no,
            "order_identifier": self.order_identifier,
            "status": self.status,
            "binding_status": self.binding_status,
            "source": self.source,
        }


_NO_MATCH = FreightDetailCandidate(
    message_id="", group_id="", message_time="", raw_text="",
    status="no_match",
)


# ── Extraction ──────────────────────────────────────────────────────────

def extract_freight_detail(
    event_or_text: MessageEvent | str,
    *,
    group_id: str = "",
    message_id: str = "",
    message_time: str = "",
) -> FreightDetailCandidate:
    """Parse freight detail text into a FreightDetailCandidate.

    Args:
      event_or_text: a MessageEvent, or a plain string.
      group_id: only used when event_or_text is str.
      message_id: only used when event_or_text is str.
      message_time: only used when event_or_text is str.

    Returns:
      FreightDetailCandidate with status "complete", "incomplete", or "no_match".
    """
    if isinstance(event_or_text, MessageEvent):
        raw = event_or_text.text or ""
        group_id = event_or_text.group_id or ""
        message_id = event_or_text.message_id
        message_time = event_or_text.received_at or ""
    else:
        raw = event_or_text or ""

    if not raw.strip():
        return _NO_MATCH

    # ── Guard: freight detail text must NOT have a ship name ────────────
    # If text has ship-related markers, it's likely a departure text
    if _RE_SHIP_MARKER.search(raw):
        return _NO_MATCH

    # ── 1. Order identifier (strong keyword — REQUIRED) ─────────────────
    m_order = _RE_ORDER_ID.search(raw)
    has_order_identifier = m_order is not None
    if not has_order_identifier:
        return _NO_MATCH

    order_identifier = m_order.group(1) if m_order else ""

    # ── 2. Contract number ─────────────────────────────────────────────
    m_contract = _RE_CONTRACT_NO.search(raw)
    contract_no = m_contract.group(1) if m_contract else ""

    # ── 3. Cargo name detail ───────────────────────────────────────────
    m_cargo = _RE_CARGO_NAME.search(raw)
    cargo_name_detail = m_cargo.group(1) if m_cargo else ""

    # ── 4. Quantity (tons) ─────────────────────────────────────────────
    m_qty = _RE_QUANTITY.search(raw)
    quantity_tons = int(m_qty.group(1)) if m_qty else -1

    # ── 5. Port ────────────────────────────────────────────────────────
    m_port = _RE_PORT.search(raw)
    port = m_port.group(1) if m_port else ""

    # ── Determine status ───────────────────────────────────────────────
    has_other_fields = any([contract_no, cargo_name_detail, quantity_tons >= 0, port])
    status = "complete" if has_other_fields else "incomplete"

    return FreightDetailCandidate(
        message_id=message_id,
        group_id=group_id,
        message_time=message_time,
        raw_text=raw,
        port=port,
        cargo_name_detail=cargo_name_detail,
        quantity_tons=quantity_tons,
        contract_no=contract_no,
        order_identifier=order_identifier,
        status=status,
        binding_status="needs_manual_binding",
        source="freight_detail_extractor",
    )
