"""Local departure-text parser for SOP departure_flow detect_departure_message.

Parses Chinese freight departure messages such as:
  - "6道，四平铁，46车"
  - "十四道，朝阳西铁矿，木森17，装55节"
  - "6道，四平方向，长航滨海，46车"
  - "28节四平铁，蓝鳍（26-60位）"
  - "煤六   四平铁"蓝鳍"18节"
  - "九道   四平镍"长航滨海"46节"
  - "十四道 41节 四平铁 厦门世纪"
  - "煤六 39节 四平铁 智慧"

Extracts: message_time, destination, car_count, lane_or_track, optional_ship_name.

This module stays local-only:
- accepts a MessageEvent or text + metadata;
- returns a DepartureCandidate dataclass;
- does not call 95306, write DB, generate Excel/JSON, or send reports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sop_hub.sop.monitoring_plan_matcher import MessageEvent

# ── known ship names │ SOP projects ─────────────────────────────────────
_KNOWN_SHIPS = {
    "长航滨海",
    "蓝鳍",
    "木森17",
    "贝拉",
    "合远9",
    "玛格丽特",
    "萨哈林",
    "阿芙拉",
    "厦门世纪",
    "智慧",
    # ── chaoyang_steel 朝阳铁矿发运船 ───────────────────────
    "宝腾海",
}


def get_known_ships() -> set[str]:
    """暴露给 infer_candidate_context 用,合并 yaml 中 project_meta.known_ships。"""
    ships = set(_KNOWN_SHIPS)
    try:
        import yaml
        from pathlib import Path
        yp = Path(__file__).resolve().parents[3] / "config" / "project_sops"
        for f in yp.glob("*.yaml"):
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            pm = d.get("project_meta") or {}
            for s in pm.get("known_ships") or []:
                if s:
                    ships.add(str(s).strip())
    except Exception:
        pass
    return ships

# ── destination aliases → canonical form ───────────────────────────────
_DESTINATION_MAP = {
    "四平镍": "四平",
    "四平铁": "四平",
    "四平方向": "四平",
    "四平": "四平",
    "朝阳西": "朝阳西",
    "朝阳铁": "朝阳西",
    "汐子": "汐子",
    "沙子": "汐子",
}
_DESTINATION_MAP_REVERSED: dict[str, list[str]] = {}
for _alias, _canonical in _DESTINATION_MAP.items():
    _DESTINATION_MAP_REVERSED.setdefault(_canonical, []).append(_alias)

# ── project mapping by destination ─────────────────────────────────────
_DESTINATION_PROJECT = {
    "四平": "jilin_jingang_jinzhou",
    "朝阳西": "chaoyang_steel",
    "汐子": "zhongtang_special_steel",
}

# ── regex fragments ────────────────────────────────────────────────────
# Lane/track: 煤六, 九道, 十四道, 6道, etc.
_RE_LANE = re.compile(r"((?:煤[一二三四五六七八九])|(?:[一二三四五六七八九十百千]+)|(?:\d+))?\s*(?:道)")
_RE_CAR_COUNT = re.compile(r"(?:装\s*)?(\d{1,3})\s*(?:车|节)")
_RE_NUMBER = re.compile(r"\d+")

# Chinese quotes: \u201c\u201d (left/right double), \u2018\u2019 (single),
# \uff02 (fullwidth), \u300c\u300d (corner brackets)
_CN_QUOTE_CHARS = "\u201c\u201d\u2018\u2019\uff02\u300c\u300d"


@dataclass(frozen=True)
class DepartureCandidate:
    """Parsed departure-text result.

    Fields:
      message_id: source MessageEvent.message_id.
      group_id: source MessageEvent.group_id.
      message_time: MessageEvent.received_at (or metadata time).
      raw_text: original message text.
      destination: canonical destination name (四平/朝阳西/汐子).
      car_count: number of cars. -1 if unparseable → incomplete status.
      lane_or_track: e.g. "6道" or "十四道". "" if None.
      optional_ship_name: known ship name from text. "" if None.
      project_id: SOP project_id mapped from destination.
      source: "departure_text_parser".
      status: "complete" | "incomplete" | "no_match".

    status = "no_match"  → text is not a departure message.
    status = "incomplete" → departure text detected but car_count < 0.
    status = "complete"   → car_count >= 0, ready for next node.
    """

    message_id: str
    group_id: str
    message_time: str
    raw_text: str
    destination: str = ""
    car_count: int = -1
    lane_or_track: str = ""
    optional_ship_name: str = ""
    project_id: str = ""
    source: str = "departure_text_parser"
    status: str = "no_match"

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "group_id": self.group_id,
            "message_time": self.message_time,
            "raw_text": self.raw_text,
            "destination": self.destination,
            "car_count": self.car_count,
            "lane_or_track": self.lane_or_track,
            "optional_ship_name": self.optional_ship_name,
            "project_id": self.project_id,
            "source": self.source,
            "status": self.status,
        }


_NO_MATCH = DepartureCandidate(
    message_id="", group_id="", message_time="", raw_text="", status="no_match"
)


def _strip_chinese_quotes(s: str) -> str:
    """Remove Chinese quote characters from a string."""
    result = s
    for ch in _CN_QUOTE_CHARS:
        result = result.replace(ch, "")
    # Also strip ASCII quotes
    result = result.replace('"', "").replace("'", "")
    return result.strip()


def _find_ship_in_text(raw: str) -> str:
    """Find a known ship name in text. Handles Chinese-quoted ship names."""
    ships = get_known_ships()
    # Try raw text first
    for known in ships:
        if known in raw:
            return known
    # Try stripping Chinese quotes first (e.g. "蓝鳍" → 蓝鳍)
    stripped = _strip_chinese_quotes(raw)
    if stripped != raw:
        for known in ships:
            if known in stripped:
                return known
    return ""


def parse_departure_text(
    event_or_text: MessageEvent | str,
    *,
    group_id: str = "",
    message_id: str = "",
    message_time: str = "",
) -> DepartureCandidate:
    """Parse a departure text message into a DepartureCandidate.

    Args:
      event_or_text: a MessageEvent, or a plain string.
      group_id: only used when event_or_text is str.
      message_id: only used when event_or_text is str.
      message_time: only used when event_or_text is str.

    Returns:
      DepartureCandidate with status "complete", "incomplete", or "no_match".
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

    # ── detect departure template ──────────────────────────────────────
    lane = ""
    car_count = -1
    destination = ""

    # lane / track (match "道" suffix: 煤六道→"煤六道", 九道→"九道", 十四道→"十四道", 6道→"6道")
    # "煤六" etc. can appear without "道"; digit/Chinese-number lanes require "道"
    m_lane = re.search(r"(煤[一二三四五六七八九](?:\s*道)?)", raw)
    if not m_lane:
        m_lane = re.search(r"([\d一二三四五六七八九十百千]+\s*道)", raw)
    if m_lane and m_lane.group(1).strip():
        lane = m_lane.group(1).strip().replace(" ", "")

    # car count ("节" and "车" are equivalent)
    m_cars = _RE_CAR_COUNT.search(raw)
    if m_cars:
        car_count = int(m_cars.group(1))

    # destination (longest match first for "四平铁"/"四平镍" before "四平")
    dest_keys = sorted(_DESTINATION_MAP.keys(), key=len, reverse=True)
    for alias in dest_keys:
        if alias in raw:
            destination = _DESTINATION_MAP[alias]
            break

    # ship name (handles Chinese quotes)
    ship = _find_ship_in_text(raw)

    # If no destination AND no lane AND no car_count → not a departure text
    has_destination = bool(destination)
    has_lane = bool(lane)
    has_cars = car_count >= 0
    if not has_destination and not has_lane and not has_cars:
        return _NO_MATCH

    # If we detected destination or lane but still can't determine project
    if not has_destination:
        # try fallback keyword matching
        if "四平" in raw:
            destination = "四平"
        elif "朝阳" in raw:
            destination = "朝阳西"
        elif any(kw in raw for kw in ("汐子", "沙子")):
            destination = "汐子"

    project = _DESTINATION_PROJECT.get(destination, "")

    status = "complete" if car_count >= 0 else "incomplete"

    return DepartureCandidate(
        message_id=message_id,
        group_id=group_id,
        message_time=message_time,
        raw_text=raw,
        destination=destination,
        car_count=car_count,
        lane_or_track=lane,
        optional_ship_name=ship,
        project_id=project,
        source="departure_text_parser",
        status=status,
    )
