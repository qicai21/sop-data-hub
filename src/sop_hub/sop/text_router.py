"""Text message router for SOP Data Hub.

R67: Classifies text messages into SOP project/flow/node and writes back to
message_inbox. Replaces the old hard-coded keyword matching in
monitoring_plan_matcher._fallback_alignment_match and executor_runner's
`"四平" not in event.text` gate.

Rules (priority order):
  1. Departure text → jilin_jingang_jinzhou / departure_flow / detect_departure_message
  2. Chaoyang business context → chaoyang_steel / dispatch_flow / capture_business_context
  3. Freight detail → project_id inferred / freight_detail_flow / enrich_release_batch
  4. Everything else → ignored
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sop_hub.sop.departure_text_parser import parse_departure_text
from sop_hub.sop.monitoring_plan_matcher import MessageEvent

# ── Known destination → project mapping ──────────────────────────────────
_DEST_PROJECT = {
    "四平": "jilin_jingang_jinzhou",
    "朝阳西": "chaoyang_steel",
    "汐子": "zhongtang_special_steel",
}

# ── Departure text signals ──────────────────────────────────────────────
_DEPARTURE_DESTINATION_KEYWORDS = {"四平", "四平铁", "四平镍", "朝阳西", "朝阳铁", "汐子"}
_DEPARTURE_LANE_PATTERNS = {"道", "煤一", "煤二", "煤三", "煤四", "煤五", "煤六", "煤七", "煤八", "煤九"}
_DEPARTURE_CAR_PATTERNS = {"节", "车"}

# ── Chaoyang business context signals ────────────────────────────────────
_CHAOYANG_SHIP_KEYWORDS = {"木森17", "合远9", "宝腾海", "贝拉"}
_CHAOYANG_DEST_KEYWORDS = {"朝阳西", "朝阳铁", "朝钢", "朝阳钢铁"}
_CHAOYANG_CARGO_KEYWORDS = {"铁矿", "印粉", "PB粉", "麦克粉", "纽曼粉"}

# ── Freight detail signals ───────────────────────────────────────────────
_FREIGHT_STRUCTURED_KEYWORDS = {
    "合同号", "入场合同号", "入厂合同号", "订单标识", "标识号", "订单号",
    "计划号", "货名", "货品", "品名", "详细货名", "矿种", "船名",
    "数量", "港口", "放货",
}

# ── Ignored message patterns ─────────────────────────────────────────────
_IGNORE_PATTERNS = {"ok", "好的", "收到", "谢谢", "嗯", "好", "1", "OK"}


@dataclass
class TextRouteResult:
    """Result of classifying a single text message."""

    message_id: str
    group_id: str
    is_sop_msg: bool = False
    sop_project_id: str = ""
    sop_flow: str = ""
    sop_node: str = ""
    summary: str = ""
    processing_status: str = "ignored"
    departure_candidate: dict[str, Any] = field(default_factory=dict)

    def as_inbox_update(self) -> dict[str, Any]:
        """Return the fields to UPDATE on message_inbox."""
        return {
            "is_sop_msg": 1 if self.is_sop_msg else 0,
            "sop_project_id": self.sop_project_id,
            "sop_flow": self.sop_flow,
            "sop_node": self.sop_node,
            "summary": self.summary,
            "processing_status": self.processing_status,
        }


def _is_departure_text(text: str) -> tuple[bool, str, str]:
    """Check if text looks like a departure notification.

    Returns (is_departure, destination_canonical, project_id).

    Requires BOTH:
      - A known destination keyword (四平/朝阳西/汐子)
      - A lane marker ("道" or "煤N") OR a car count ("节"/"车")

    This prevents false positives on messages like
    "朝阳西，15997 吨，宝腾海，PB粉" which mention the destination
    but are freight detail, not departure notifications.
    """
    from sop_hub.sop.departure_text_parser import parse_departure_text

    candidate = parse_departure_text(text)
    if candidate.status == "no_match":
        return False, "", ""

    dest = candidate.destination
    project = _DEST_PROJECT.get(dest, "")
    if not project:
        return False, "", ""

    # Require at least one structural signal beyond the destination keyword
    has_lane = bool(candidate.lane_or_track)
    has_cars = candidate.car_count >= 0
    if not has_lane and not has_cars:
        return False, "", ""

    return True, dest, project


def _is_chaoyang_context(text: str) -> bool:
    """Check if text is a Chaoyang business context message."""
    # Ship name signals
    if any(kw in text for kw in _CHAOYANG_SHIP_KEYWORDS):
        return True
    # Destination signals
    if any(kw in text for kw in _CHAOYANG_DEST_KEYWORDS):
        # Need at least one more signal (cargo, ship, or structured)
        if any(kw in text for kw in _CHAOYANG_CARGO_KEYWORDS):
            return True
        if any(kw in text for kw in _CHAOYANG_SHIP_KEYWORDS):
            return True
        # "放货 + 朝阳" or "朝阳西 + 量"
        if "放货" in text:
            return True
    return False


def _is_freight_detail(text: str) -> bool:
    """Check if text is a structured freight detail message."""
    hit_count = sum(1 for kw in _FREIGHT_STRUCTURED_KEYWORDS if kw in text)
    if hit_count >= 2:
        return True
    # Single keyword + strong structure (colon/colon-like patterns)
    if hit_count >= 1 and ("：" in text or ":" in text):
        return True
    return False


def _is_ignorable(text: str) -> bool:
    """Check if text is an ignorable short message."""
    stripped = text.strip().lower()
    return stripped in _IGNORE_PATTERNS or len(stripped) <= 1


def _build_summary(
    rule: str, text: str, destination: str = ""
) -> str:
    """Build a human-readable summary of the routing decision."""
    preview = text.strip()[:60].replace("\n", " ")
    parts = [f"[{rule}]", preview]
    if destination:
        parts.append(f"→{destination}")
    return " ".join(parts)


def _infer_project_from_text(text: str) -> str:
    """Infer project_id from freight detail keywords."""
    if any(kw in text for kw in ("四平", "吉林金钢", "入场合同", "入场合同号", "红土镍矿")):
        return "jilin_jingang_jinzhou"
    if any(kw in text for kw in ("朝阳西", "朝阳", "合远9", "木森17", "宝腾海")):
        return "chaoyang_steel"
    if any(kw in text for kw in ("汐子", "中唐")):
        return "zhongtang_special_steel"
    return ""


def classify_text_message(event: MessageEvent) -> TextRouteResult:
    """Classify a single text message and return a routing result.

    Priority:
      1. Departure text (四平/朝阳西/汐子 + lane/car count patterns)
      2. Chaoyang business context
      3. Freight detail (structured cargo/contract info)
      4. Ignorable short messages
      5. Default → ignored (non-SOP)
    """
    text = (event.text or "").strip()
    group_id = event.group_id or ""
    message_id = event.message_id

    if not text:
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=False,
            processing_status="ignored",
            summary="[empty] no text content",
        )

    # Rule 1: Departure text
    is_dep, dest, project = _is_departure_text(text)
    if is_dep:
        candidate = parse_departure_text(event)
        summary = _build_summary("departure_text", text, dest)
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=True,
            sop_project_id=project,
            sop_flow="departure_flow",
            sop_node="detect_departure_message",
            summary=summary,
            processing_status="matched_sop",
            departure_candidate=candidate.to_dict(),
        )

    # Rule 2: Chaoyang business context
    if _is_chaoyang_context(text):
        summary = _build_summary("chaoyang_context", text)
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=True,
            sop_project_id="chaoyang_steel",
            sop_flow="dispatch_flow",
            sop_node="capture_business_context",
            summary=summary,
            processing_status="matched_sop",
        )

    # Rule 3: Freight detail
    if _is_freight_detail(text):
        project = _infer_project_from_text(text)
        if not project:
            # Mark as SOP but project unknown — needs later enrichment
            summary = _build_summary("freight_detail_unknown_project", text)
            return TextRouteResult(
                message_id=message_id,
                group_id=group_id,
                is_sop_msg=True,
                sop_project_id="",
                sop_flow="freight_detail_flow",
                sop_node="enrich_release_batch",
                summary=summary,
                processing_status="matched_sop",
            )
        summary = _build_summary("freight_detail", text)
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=True,
            sop_project_id=project,
            sop_flow="freight_detail_flow",
            sop_node="enrich_release_batch",
            summary=summary,
            processing_status="matched_sop",
        )

    # Rule 4: Ignorable
    if _is_ignorable(text):
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=False,
            processing_status="ignored",
            summary=f"[ignored_short] {text[:30]}",
        )

    # Rule 5: Default — not an SOP message
    return TextRouteResult(
        message_id=message_id,
        group_id=group_id,
        is_sop_msg=False,
        processing_status="ignored",
        summary=f"[no_match] {text[:50]}",
    )


# ── message_inbox write-back ────────────────────────────────────────────


def update_message_inbox_with_route(
    message_id: str,
    route: TextRouteResult,
    *,
    db_path: str | None = None,
) -> bool:
    """Write the text route result back to message_inbox."""
    import sqlite3
    from pathlib import Path

    if db_path:
        db = Path(db_path)
    else:
        from pathlib import Path as _Path
        db = _Path(__file__).resolve().parents[3] / "data" / "sop_agent.db"

    if not db.exists():
        return False

    try:
        conn = sqlite3.connect(str(db))
        updates = route.as_inbox_update()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [message_id]
        conn.execute(
            f"UPDATE message_inbox SET {set_clause} WHERE message_id = ?",
            values,
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False
