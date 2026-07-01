from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from .jiusan_bulk import JIUSAN_YAML, stable_hash


def load_route_a_config() -> dict[str, Any]:
    data = yaml.safe_load(JIUSAN_YAML.read_text(encoding="utf-8")) or {}
    return ((data.get("cost_structure") or {}).get("route_a_container") or {})


def container_billing_weight(box_count: int, weight_per_box: float) -> float:
    return round(float(box_count) * float(weight_per_box), 2)


@dataclass
class ContainerFeeItemCalc:
    code: str
    name: str
    side: str
    qty: float
    qty_unit: str
    amount: float
    price: float
    settle_party: str
    counterparty: str
    pricing_basis: str
    pricing_basis_value: float
    tax_rate: float
    source_ref: str


def calc_route_a_fee_items(
    *,
    config: dict[str, Any],
    box_count: int,
    total_weight: float,
    freight_sum_yuan: float,
    source_ref: str,
) -> list[ContainerFeeItemCalc]:
    out: list[ContainerFeeItemCalc] = []
    for item in config.get("items") or []:
        code = str(item["code"])
        name = str(item["name"])
        side = str(item["side"])
        base = str(item.get("base") or "")
        calc_mode = str(item.get("calc") or "")
        tax = float(item.get("tax", 0) or 0)
        settle_party = str(item.get("settle_party") or "")
        counterparty = str(item.get("counterparty") or "")

        if calc_mode == "actual_freight_sum":
            qty = round(total_weight, 2)
            amount = round(freight_sum_yuan, 2)
            price = round(amount / qty, 4) if qty else 0.0
            qty_unit = "ton"
            pricing_basis = "actual_freight_fee"
            pricing_basis_value = amount
        elif base == "railway_billing_weight":
            rate = float(item.get("rate", 0) or 0)
            qty = round(total_weight, 2)
            amount = round(rate * qty, 2)
            price = rate
            qty_unit = "ton"
            pricing_basis = "billing_weight"
            pricing_basis_value = qty
        elif base == "box_count":
            rate = float(item.get("rate", 0) or 0)
            qty = float(box_count)
            amount = round(rate * qty, 2)
            price = rate
            qty_unit = "box"
            pricing_basis = "box_count"
            pricing_basis_value = qty
        else:
            continue

        out.append(
            ContainerFeeItemCalc(
                code=code,
                name=name,
                side=side,
                qty=qty,
                qty_unit=qty_unit,
                amount=amount,
                price=price,
                settle_party=settle_party,
                counterparty=counterparty,
                pricing_basis=pricing_basis,
                pricing_basis_value=pricing_basis_value,
                tax_rate=tax,
                source_ref=source_ref,
            )
        )
    return out


__all__ = [
    "ContainerFeeItemCalc",
    "calc_route_a_fee_items",
    "container_billing_weight",
    "load_route_a_config",
    "stable_hash",
]
