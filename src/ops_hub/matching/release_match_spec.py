from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping


@dataclass(frozen=True)
class ReleaseBatchMatchSpec:
    release_batch_id: str
    ship_name: str
    cargo_name: str
    destination_station: str
    project: str = ""
    batch_sequence: str = ""
    batch_date: str = ""
    station_aliases: tuple[str, ...] = field(default_factory=tuple)
    cargo_aliases: tuple[str, ...] = field(default_factory=tuple)
    ship_aliases: tuple[str, ...] = field(default_factory=tuple)
    patterns: tuple[str, ...] = field(default_factory=tuple)


def build_match_spec(release_batch: Mapping[str, Any] | Any) -> ReleaseBatchMatchSpec:
    """Build an auditable matching spec from one release_batches row.

    The spec is intentionally data/config shaped. It describes matching intent;
    it does not write shipment_release_batch_matches by itself.
    """
    batch_id = _get(release_batch, "id")
    ship_name = _get(release_batch, "ship_name")
    cargo_name = _get(release_batch, "cargo_name")
    destination = _get(release_batch, "destination_station")
    project = _get(release_batch, "project")
    batch_sequence = _get(release_batch, "batch_sequence")
    batch_date = _get(release_batch, "batch_date") or _get(release_batch, "notice_date")

    station_aliases = _unique([destination, *_station_aliases(destination, project)])
    cargo_aliases = _unique([cargo_name, *_cargo_aliases(cargo_name, project)])
    ship_aliases = _unique([ship_name, _normalize_fullwidth_digits(ship_name)])
    patterns = _build_patterns(station_aliases, cargo_aliases, ship_aliases)

    return ReleaseBatchMatchSpec(
        release_batch_id=batch_id,
        ship_name=ship_name,
        cargo_name=cargo_name,
        destination_station=destination,
        project=project,
        batch_sequence=batch_sequence,
        batch_date=batch_date,
        station_aliases=tuple(station_aliases),
        cargo_aliases=tuple(cargo_aliases),
        ship_aliases=tuple(ship_aliases),
        patterns=tuple(patterns),
    )


def row_matches_spec(row: Mapping[str, Any], spec: ReleaseBatchMatchSpec) -> bool:
    text = _normalize_text(
        " ".join(
            str(row.get(key) or "")
            for key in ("cargo_info_effective", "cargo_info_raw", "remark", "ship_name")
        )
    )
    if not text:
        return False

    station_hit = any(_normalize_text(alias) in text for alias in spec.station_aliases if alias)
    cargo_hit = any(_normalize_text(alias) in text for alias in spec.cargo_aliases if alias)
    if station_hit and cargo_hit:
        return True

    # Some projects use shorthand like "朝阳铁" to encode station+cargo.
    if any(_normalize_text(alias) in text for alias in spec.station_aliases if alias):
        if any(_normalize_text(ship) in text for ship in spec.ship_aliases if ship):
            return True

    return any(_pattern_matches(pattern, text) for pattern in spec.patterns)


def _get(obj: Mapping[str, Any] | Any, key: str) -> str:
    if isinstance(obj, Mapping):
        value = obj.get(key)
    else:
        value = getattr(obj, key, None)
    return str(value or "").strip()


def _station_aliases(destination: str, project: str = "") -> list[str]:
    aliases: list[str] = []
    if destination == "朝阳西" or "朝钢" in project or "朝阳钢" in project:
        aliases.extend(["朝阳西", "朝阳铁"])
    if destination == "汐子":
        aliases.append("汐子")
    # 中唐特钢铁路放货单里常以“沙子”作为放货目的地口径，95306 与检装车单
    # 发运校验口径使用“汐子”。这是项目级 SOP 站名映射，不绑定具体船名/批次。
    if destination == "沙子" and "中唐特钢" in project:
        aliases.append("汐子")
    return aliases


def _cargo_aliases(cargo_name: str, project: str = "") -> list[str]:
    text = cargo_name or ""
    aliases: list[str] = []
    if "铁" in text or "铁矿" in project:
        aliases.extend(["铁矿粉", "铁矿", "铁"])
    return aliases


def _build_patterns(stations: list[str], cargos: list[str], ships: list[str]) -> list[str]:
    patterns: list[str] = []
    for station in stations:
        if station == "汐子":
            patterns.extend(["汐子/铁", "汐子铁矿(粉)?"])
        if station == "朝阳西":
            for ship in ships:
                if ship:
                    patterns.append(f"朝阳西/铁矿粉/*?/{ship}")
        if station == "朝阳铁":
            for ship in ships:
                if ship:
                    patterns.append(f"朝阳铁/*?/{ship}")
        for cargo in cargos:
            if station and cargo:
                patterns.append(f"{station}/{cargo}")
                patterns.append(f"{station}{cargo}")
                for ship in ships:
                    if ship:
                        patterns.append(f"{station}/{cargo}/*?/{ship}")
                        patterns.append(f"{station}.*{cargo}.*{ship}")
    return _unique(patterns)


def _pattern_matches(pattern: str, normalized_text: str) -> bool:
    regex = re.escape(pattern)
    regex = regex.replace(re.escape("*?"), ".*?")
    regex = regex.replace(re.escape(".*"), ".*")
    regex = regex.replace(re.escape("(粉)?"), "(粉)?")
    regex = regex.replace(re.escape("/"), ".*")
    return re.search(regex, normalized_text) is not None


def _normalize_text(value: str) -> str:
    return re.sub(r"[\s/／、，,：:（）()\-]+", "", _normalize_fullwidth_digits(str(value or "")))


def _normalize_fullwidth_digits(value: str) -> str:
    table = str.maketrans("０１２３４５６７８９", "0123456789")
    return str(value or "").translate(table)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
