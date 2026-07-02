from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
JIUSAN_YAML = REPO_ROOT / "config" / "project_sops" / "jiusan.yaml"


def stable_hash(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def load_route_a_config() -> dict[str, Any]:
    data = yaml.safe_load(JIUSAN_YAML.read_text(encoding="utf-8")) or {}
    return ((data.get("cost_structure") or {}).get("route_a_container") or {})


def container_billing_weight(box_count: int, weight_per_box: float) -> float:
    return round(float(box_count) * float(weight_per_box), 2)


def load_contract_terms(conn: sqlite3.Connection, project_id: str, route_code: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT fee_code, fee_name, charge_side, pricing_basis, pricing_unit, default_rate,
               tax_rate, counterparty, settle_party, note
        FROM contract_fee_terms
        WHERE project_id=? AND route_code=? AND enabled=1
        """,
        (project_id, route_code),
    ).fetchall()
    return {str(r["fee_code"]): dict(r) for r in rows}


def load_line_rates(
    conn: sqlite3.Connection,
    project_id: str,
    route_code: str,
    fee_code: str,
) -> dict[str, float]:
    rows = conn.execute(
        """
        SELECT line_name, rate
        FROM contract_line_rates
        WHERE project_id=? AND route_code=? AND fee_code=? AND enabled=1
        """,
        (project_id, route_code, fee_code),
    ).fetchall()
    return {str(r["line_name"]).strip(): float(r["rate"]) for r in rows}


def load_weight_confirmation(
    conn: sqlite3.Connection,
    release_batch_id: str,
    route_code: str,
    weight_type: str = "port_weighing_departure",
) -> float | None:
    row = conn.execute(
        """
        SELECT confirmed_weight
        FROM shipment_weight_confirmation
        WHERE release_batch_id=? AND route_code=? AND weight_type=?
        """,
        (release_batch_id, route_code, weight_type),
    ).fetchone()
    if not row:
        return None
    try:
        return float(row["confirmed_weight"])
    except (TypeError, ValueError):
        return None


def resolve_line_rate(
    line_name: str | None,
    rate_map: dict[str, float],
    default_rate: float | None = None,
) -> tuple[float | None, str]:
    name = str(line_name or "").strip()
    if name and name in rate_map:
        return rate_map[name], name
    return default_rate, name


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
    terms: dict[str, dict[str, Any]],
    box_trip_count: int,
    confirmed_weight: float,
    railway_weight: float,
    freight_sum_yuan: float,
    metro_amount: float,
    wagon_occupancy_amount: float,
    transfer_amount: float,
    tarpaulin_amount: float,
    item9_amount: float,
    item11_amount: float,
    source_ref: str,
) -> list[ContainerFeeItemCalc]:
    out: list[ContainerFeeItemCalc] = []
    amount_overrides = {
        "route_a_metro_fee": metro_amount,
        "route_a_wagon_occupancy": wagon_occupancy_amount,
        "route_a_transfer_fee": transfer_amount,
        "route_a_tarpaulin": tarpaulin_amount,
        "route_a_item9": item9_amount,
        "route_a_item11": item11_amount,
    }
    qty_overrides = {
        "route_a_metro_fee": ("ton", railway_weight, "marked_weight_x_line_rate", railway_weight),
        "route_a_wagon_occupancy": ("ton", railway_weight, "marked_weight_tiered", railway_weight),
        "route_a_transfer_fee": ("box", float(box_trip_count), "box_trip_count", float(box_trip_count)),
        "route_a_tarpaulin": ("box", float(box_trip_count), "formula", float(box_trip_count)),
        "route_a_item9": ("box", float(box_trip_count), "box_trip_count", float(box_trip_count)),
        "route_a_item11": ("box", float(box_trip_count), "box_trip_count", float(box_trip_count)),
    }

    for code, item in terms.items():
        name = str(item["fee_name"])
        side = str(item["charge_side"])
        basis = str(item.get("pricing_basis") or "")
        tax = float(item.get("tax_rate", 0) or 0)
        settle_party = str(item.get("settle_party") or "")
        counterparty = str(item.get("counterparty") or "")
        rate = float(item.get("default_rate", 0) or 0)

        if basis == "freight_fee_sum":
            qty = round(railway_weight, 2)
            amount = round(freight_sum_yuan, 2)
            price = round(amount / qty, 4) if qty else 0.0
            qty_unit = "ton"
            pricing_basis = "actual_freight_fee"
            pricing_basis_value = amount
        elif basis == "confirmed_weight":
            qty = round(confirmed_weight, 2)
            amount = round(rate * qty, 2)
            price = rate
            qty_unit = "ton"
            pricing_basis = basis
            pricing_basis_value = qty
        elif code in amount_overrides:
            qty_unit, qty, pricing_basis, pricing_basis_value = qty_overrides[code]
            amount = round(amount_overrides[code], 2)
            price = rate
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
    "load_contract_terms",
    "load_line_rates",
    "load_weight_confirmation",
    "load_route_a_config",
    "resolve_line_rate",
    "stable_hash",
]
