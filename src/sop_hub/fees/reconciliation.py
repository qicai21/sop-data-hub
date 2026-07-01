from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class ReconciliationGroup:
    charge_side: str
    party_name: str
    total_amount: float
    fee_codes: list[str]
    batch_ids: list[str]
    wagon_count: int
    container_count: int
    total_weight: float


def group_ship_fee_rows(rows: Iterable[dict]) -> list[ReconciliationGroup]:
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        charge_side = str(row.get("charge_side") or "")
        party_name = str(
            row.get("counterparty") if charge_side == "income" else row.get("settle_party") or ""
        ).strip()
        if not party_name:
            continue
        key = (charge_side, party_name)
        cur = grouped.setdefault(
            key,
            {
                "total_amount": 0.0,
                "fee_codes": set(),
                "batch_ids": [],
                "batch_seen": set(),
                "wagon_count": 0,
                "container_count": 0,
                "total_weight": 0.0,
            },
        )
        cur["total_amount"] += float(row.get("amount") or 0)
        fee_code = str(row.get("fee_code") or "").strip()
        if fee_code:
            cur["fee_codes"].add(fee_code)
        batch_id = str(row.get("fee_batch_id") or "").strip()
        if batch_id and batch_id not in cur["batch_seen"]:
            cur["batch_seen"].add(batch_id)
            cur["batch_ids"].append(batch_id)
            cur["wagon_count"] += int(row.get("wagon_count") or 0)
            cur["container_count"] += int(row.get("container_count") or 0)
            cur["total_weight"] += float(row.get("total_weight") or 0)

    out: list[ReconciliationGroup] = []
    for (charge_side, party_name), cur in sorted(grouped.items()):
        out.append(
            ReconciliationGroup(
                charge_side=charge_side,
                party_name=party_name,
                total_amount=round(cur["total_amount"], 2),
                fee_codes=sorted(cur["fee_codes"]),
                batch_ids=cur["batch_ids"],
                wagon_count=cur["wagon_count"],
                container_count=cur["container_count"],
                total_weight=round(cur["total_weight"], 2),
            )
        )
    return out


__all__ = ["ReconciliationGroup", "group_ship_fee_rows"]
