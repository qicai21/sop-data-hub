"""Shared 95306 shipment query window models — R42.

Public, cross-project data models for the 95306 query window executor.
Future consumers (吉林金钢, 朝阳钢铁, 中唐特钢, 九三大豆) all use these.

These models are intentionally minimal:
- they describe what the 95306 DB contains;
- they do NOT encode project-specific business logic;
- they are read-only — no writes, no DB mutations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


# ── QueryWindow ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class QueryWindow:
    """A time window for querying 95306 shipments.

    Fields:
      start_time: ISO-8601 string, inclusive lower bound.
      end_time: ISO-8601 string, inclusive upper bound.
      reference_time: original reference time used to build the window.
      window_before_minutes: minutes before reference_time.
      window_after_minutes: minutes after reference_time.
    """

    start_time: str
    end_time: str
    reference_time: str
    window_before_minutes: int
    window_after_minutes: int

    @classmethod
    def from_reference(
        cls,
        reference_time: str,
        *,
        window_before_minutes: int = 60,
        window_after_minutes: int = 60,
    ) -> QueryWindow:
        """Build a QueryWindow from a reference time string.

        Args:
          reference_time: ISO-8601 or 'YYYY-MM-DD HH:MM:SS' string.
          window_before_minutes: minutes before reference_time.
          window_after_minutes: minutes after reference_time.

        Returns:
          QueryWindow with computed start_time and end_time.
        """
        ref = _parse_time(reference_time)
        start = ref - timedelta(minutes=window_before_minutes)
        end = ref + timedelta(minutes=window_after_minutes)
        return cls(
            start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
            reference_time=ref.strftime("%Y-%m-%d %H:%M:%S"),
            window_before_minutes=window_before_minutes,
            window_after_minutes=window_after_minutes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "reference_time": self.reference_time,
            "window_before_minutes": self.window_before_minutes,
            "window_after_minutes": self.window_after_minutes,
        }


@dataclass(frozen=True)
class ShipmentCandidate:
    """A single shipment row from the 95306 collection DB.

    Fields map directly to 95306 shipments table columns:
      ydid: 运单ID.
      wagon_no: 车号 (car_no).
      waybill_no: 运单号 (czydid, may be empty).
      container_no: 箱号 (container_no_raw).
      origin_station: 发站 (origin_name).
      destination_station: 到站 (destination_name).
      cargo_name: 货物品名.
      ticketed_at: 制票时间.
      departed_at: 发车时间.
      arrived_at: 到站时间.
      delivered_at: 交付时间.
      current_status: 当前状态 (status_name or latest_stage_name).
    """

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "ydid": self.ydid,
            "wagon_no": self.wagon_no,
            "waybill_no": self.waybill_no,
            "container_no": self.container_no,
            "origin_station": self.origin_station,
            "destination_station": self.destination_station,
            "cargo_name": self.cargo_name,
            "ticketed_at": self.ticketed_at,
            "departed_at": self.departed_at,
            "arrived_at": self.arrived_at,
            "delivered_at": self.delivered_at,
            "current_status": self.current_status,
        }


@dataclass
class ShipmentQueryResult:
    """Result of a 95306 shipment query by time window.

    Fields:
      window: the QueryWindow used.
      origin_station: queried origin station.
      destination_station: queried destination station.
      cargo_name: optional cargo_name filter.
      expected_car_count: optional expected car count.
      total_candidates: total number of candidates returned.
      exact_match_count: number of candidates matching all criteria exactly.
      ambiguous_count: candidates beyond the primary match.
      candidates: list of ShipmentCandidate.
    """

    window: QueryWindow
    origin_station: str
    destination_station: str
    cargo_name: str = ""
    expected_car_count: int = -1

    total_candidates: int = 0
    exact_match_count: int = 0
    ambiguous_count: int = 0

    candidates: list[ShipmentCandidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window.to_dict(),
            "origin_station": self.origin_station,
            "destination_station": self.destination_station,
            "cargo_name": self.cargo_name,
            "expected_car_count": self.expected_car_count if self.expected_car_count >= 0 else None,
            "total_candidates": self.total_candidates,
            "exact_match_count": self.exact_match_count,
            "ambiguous_count": self.ambiguous_count,
            "candidates": [c.to_dict() for c in self.candidates],
        }


# ── Helpers ─────────────────────────────────────────────────────────────

def _parse_time(time_str: str) -> datetime:
    """Parse a time string in common formats.

    Supports: 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DDTHH:MM:SS', ISO-8601.
    """
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    # Last resort: try ISO
    try:
        return datetime.fromisoformat(time_str)
    except ValueError:
        pass
    raise ValueError(f"Cannot parse time string: {time_str!r}")
