"""Station-aware segmentation for shared workgroup departure text.

The text trigger is only a rendezvous hint for a later inspection slip.  It
must nevertheless preserve an explicit station boundary: a car count written
for a non-SOP destination must never be inherited by a neighbouring SOP ship.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping


_COUNT_RE = re.compile(r"(?<![.．])(?P<count>\d{1,3})\s*(?:节|车)")


@dataclass(frozen=True)
class StationAlias:
    canonical: str
    project_id: str = ""


# Includes non-SOP destinations deliberately.  They are segmentation barriers,
# not trigger targets.  Longer aliases are matched first below.
STATION_ALIASES: dict[str, StationAlias] = {
    "朝阳西铁矿": StationAlias("朝阳西", "chaoyang_steel"),
    "朝阳西铁": StationAlias("朝阳西", "chaoyang_steel"),
    "朝阳西": StationAlias("朝阳西", "chaoyang_steel"),
    "朝阳铁": StationAlias("朝阳西", "chaoyang_steel"),
    "四平方向": StationAlias("四平", "jilin_jingang_jinzhou"),
    "四平镍": StationAlias("四平", "jilin_jingang_jinzhou"),
    "四平铁": StationAlias("四平", "jilin_jingang_jinzhou"),
    "四平": StationAlias("四平", "jilin_jingang_jinzhou"),
    "汐子铁矿": StationAlias("汐子", "zhongtang_special_steel"),
    "汐子铁": StationAlias("汐子", "zhongtang_special_steel"),
    "汐子": StationAlias("汐子", "zhongtang_special_steel"),
    "沙子铁": StationAlias("汐子", "zhongtang_special_steel"),
    "沙子": StationAlias("汐子", "zhongtang_special_steel"),
    "新台子": StationAlias("新台子", "jiusan"),
    "新台": StationAlias("新台子", "jiusan"),
    "凌源东铁": StationAlias("凌源东"),
    "凌源东煤": StationAlias("凌源东"),
    "凌源东": StationAlias("凌源东"),
    "凌东铁": StationAlias("凌源东"),
    "凌东煤": StationAlias("凌源东"),
    "凌东": StationAlias("凌源东"),
    "乌兰浩特铁": StationAlias("乌兰浩特"),
    "乌兰浩特": StationAlias("乌兰浩特"),
    "乌铁": StationAlias("乌兰浩特"),
    "草市": StationAlias("草市"),
    "瓢儿屯": StationAlias("瓢儿屯"),
    "瓢屯": StationAlias("瓢儿屯"),
    "马林": StationAlias("马林"),
    "林西": StationAlias("林西"),
}


@dataclass(frozen=True)
class StationTextSegment:
    """One explicit station segment and its textual boundaries."""

    destination: str
    project_id: str
    alias: str
    marker_start: int
    marker_end: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class StationShipCount:
    destination: str
    project_id: str
    ship: str
    car_count: int
    segment: str


def _normalise(text: str) -> str:
    return (text or "").replace("“", "").replace("”", "").replace('"', "")


def _station_matches(text: str) -> list[tuple[int, int, str, StationAlias]]:
    """Return non-overlapping station markers, preferring the longest alias."""
    matches: list[tuple[int, int, str, StationAlias]] = []
    occupied_until = -1
    for match in re.finditer(
        "|".join(re.escape(alias) for alias in sorted(STATION_ALIASES, key=len, reverse=True)),
        text,
    ):
        if match.start() < occupied_until:
            continue
        alias = match.group(0)
        matches.append((match.start(), match.end(), alias, STATION_ALIASES[alias]))
        occupied_until = match.end()
    return matches


def extract_station_segments(text: str) -> list[StationTextSegment]:
    """Split a message at explicit station markers.

    A segment begins after the prior station marker (or message start) and ends
    before the next station marker.  This retains a count immediately before a
    station (``53节汐子铁宝丽``) with the station that follows it.
    """
    normalized = _normalise(text)
    markers = _station_matches(normalized)
    segments: list[StationTextSegment] = []
    for index, (start, end, alias, station) in enumerate(markers):
        previous_end = markers[index - 1][1] if index else 0
        next_start = markers[index + 1][0] if index + 1 < len(markers) else len(normalized)
        # Keep a directly-adjacent prefix count (``53节汐子铁``) with this
        # marker.  A punctuated tail (``鞍子河5节，朝阳西``) remains with the
        # preceding marker instead.
        segment_start = start
        prefix = normalized[previous_end:start]
        prefix_counts = list(_COUNT_RE.finditer(prefix))
        if index == 0:
            segment_start = 0
        elif prefix_counts and prefix[prefix_counts[-1].end():].strip() == "":
            segment_start = previous_end + prefix_counts[-1].start()
        segments.append(
            StationTextSegment(
                destination=station.canonical,
                project_id=station.project_id,
                alias=alias,
                marker_start=start,
                marker_end=end,
                start=segment_start,
                end=next_start,
                text=normalized[segment_start:next_start].strip(),
            )
        )
    return segments


def _is_prefix_for_next_station(
    text: str, count_start: int, count_end: int, segment_end: int, ship_end: int,
) -> bool:
    """Whether a count is the ``N节<next station>`` prefix, not this segment.

    ``实装22节乌铁`` is different from ``宝丽，3节乌铁``: the former has an
    explicit loading verb tied to the preceding ship, so its count remains in
    the current station segment even when the next station follows directly.
    """
    if any(word in text[ship_end:count_start] for word in ("实装", "装车", "发出")):
        return False
    return text[count_end:segment_end].strip() == ""


def extract_station_ship_counts(
    text: str,
    ship_projects: Mapping[str, str],
) -> list[StationShipCount]:
    """Extract known ships only after station segmentation.

    Explicit station and project must agree.  An explicit non-SOP station is a
    hard exclusion even if the message contains a ship known to an SOP project.
    """
    normalized = _normalise(text)
    segments = extract_station_segments(normalized)
    result: list[StationShipCount] = []
    ships = sorted((ship for ship in ship_projects if ship), key=len, reverse=True)
    for segment in segments:
        if not segment.project_id:
            continue
        occurrence_start = segment.start if segment.start == 0 else segment.marker_end
        occurrences = [
            (match.start(), match.end(), ship)
            for ship in ships
            for match in re.finditer(re.escape(ship), normalized[occurrence_start:segment.end])
        ]
        occurrences.sort()
        for index, (relative_start, relative_end, ship) in enumerate(occurrences):
            if ship_projects[ship] != segment.project_id:
                continue
            ship_start = occurrence_start + relative_start
            ship_end = occurrence_start + relative_end
            next_ship_start = (
                occurrence_start + occurrences[index + 1][0]
                if index + 1 < len(occurrences) else segment.end
            )
            previous_ship_end = (
                occurrence_start + occurrences[index - 1][1]
                if index else segment.start
            )
            forward = list(_COUNT_RE.finditer(normalized, ship_end, next_ship_start))
            forward = [
                count for count in forward
                if not (
                    segment.end < len(normalized)
                    and _is_prefix_for_next_station(
                        normalized, count.start(), count.end(), segment.end, ship_end,
                    )
                )
            ]
            backward = list(_COUNT_RE.finditer(normalized, previous_ship_end, ship_start))
            count_match = forward[0] if forward else (backward[-1] if backward else None)
            if not count_match:
                continue
            count = int(count_match.group("count"))
            if count <= 0:
                continue
            result.append(StationShipCount(
                destination=segment.destination,
                project_id=segment.project_id,
                ship=ship,
                car_count=count,
                segment=segment.text,
            ))
    return result
