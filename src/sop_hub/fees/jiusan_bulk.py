from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from docx import Document


REPO_ROOT = Path(__file__).resolve().parents[3]
JIUSAN_YAML = REPO_ROOT / "config" / "project_sops" / "jiusan.yaml"
DOC_ROOT = REPO_ROOT / "reports" / "fee_docs" / "jiusan"

_TRACK_PATTERN = re.compile(r"(煤[一二三四五六七八九])|([一二三四五六七八九十\d]+道)")


def stable_hash(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def load_route_c_config() -> dict[str, Any]:
    data = yaml.safe_load(JIUSAN_YAML.read_text(encoding="utf-8")) or {}
    return ((data.get("cost_structure") or {}).get("route_c_bulk") or {})


def is_real_track(track: str | None) -> bool:
    return bool(track and _TRACK_PATTERN.search(track))


def display_track(track: str | None) -> str:
    if is_real_track(track):
        return str(track).strip()
    return "待补录"


def build_docx_path(notice_date: str, ship_name: str, track: str | None, car_count: int) -> Path:
    month = notice_date[:7]
    safe_track = display_track(track).replace("/", "_")
    name = f"{notice_date}_{ship_name}_{safe_track}_{car_count}车_现场确认单.docx"
    return DOC_ROOT / month / name


def freight_fee_yuan(raw_fee: Any) -> float:
    try:
        return round(float(raw_fee) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def billing_weight(row: dict[str, Any]) -> float:
    v = row.get("computed_loading_weight")
    if v not in (None, ""):
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    model = str(row.get("car_model") or "")
    if model.startswith("L70"):
        return 69.0
    return 61.0


@dataclass
class FeeItemCalc:
    code: str
    name: str
    side: str
    qty: float
    qty_unit: str
    amount: float
    price: float | None
    settle_party: str
    counterparty: str
    pricing_basis: str
    pricing_basis_value: float
    tax_rate: float
    document_flow_type: str
    source_ref: str
    service_no: str = ""


def calc_fee_items(
    *,
    config: dict[str, Any],
    total_weight: float,
    freight_sum_yuan: float,
    car_count: int,
    source_ref: str,
) -> list[FeeItemCalc]:
    out: list[FeeItemCalc] = []
    for item in config.get("items") or []:
        code = item["code"]
        name = item["name"]
        side = item["side"]
        base = item.get("base", "")
        calc_mode = item.get("calc", "")
        tax = float(item.get("tax", 0) or 0)
        settle_party = str(item.get("settle_party") or "")
        counterparty = str(item.get("counterparty") or "")
        doc_type = str(item.get("doc_type") or "")
        service_no = str(item.get("service_no") or "")

        if calc_mode == "actual_freight_sum":
            qty = round(total_weight, 2)
            amount = round(freight_sum_yuan, 2)
            price = round(amount / qty, 4) if qty else 0.0
            qty_unit = "ton"
            pricing_basis = "actual_freight_fee"
            pricing_basis_value = round(freight_sum_yuan, 2)
        elif base == "railway_billing_weight":
            rate = float(item.get("rate", 0) or 0)
            qty = round(total_weight, 2)
            amount = round(rate * qty, 2)
            price = rate
            qty_unit = "ton"
            pricing_basis = "billing_weight"
            pricing_basis_value = round(total_weight, 2)
        elif base == "car_count":
            rate = float(item.get("rate", 0) or 0)
            qty = float(car_count)
            amount = round(rate * qty, 2)
            price = rate
            qty_unit = "car"
            pricing_basis = "car_count"
            pricing_basis_value = float(car_count)
        else:
            continue

        out.append(
            FeeItemCalc(
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
                document_flow_type=doc_type or "",
                source_ref=source_ref,
                service_no=service_no,
            )
        )
    return out


def render_onsite_confirm_docx(
    *,
    path: Path,
    notice_date: str,
    project_name: str,
    track: str | None,
    car_count: int,
    car_nos: list[str],
    fee_items: list[FeeItemCalc],
    ship_name: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_heading("锦州港装卸辅助作业现场确认单", level=1)
    doc.add_paragraph("-----------------------")
    doc.add_paragraph(f"发生日期: {notice_date}")
    doc.add_paragraph(f"项目名称: {project_name}")
    doc.add_paragraph(f"船名/批次: {ship_name}")
    doc.add_paragraph(f"道线场地: {display_track(track)}")
    if not is_real_track(track):
        doc.add_paragraph("备注: 当前台账来源为按船发运报表,真实作业股道待补录。")

    doc.add_paragraph("作业项目:")
    for item in fee_items:
        if item.code == "aux_bulk_loading":
            doc.add_paragraph(
                f"  -[ ] #{item.service_no} {item.name}/特殊平整、清扫归集及皮带机配合"
            )
        elif item.code == "aux_bulk_inspection":
            doc.add_paragraph(
                f"  -[ ] #{item.service_no} {item.name}/铁路及海关监管"
            )

    doc.add_paragraph(f"数量: {car_count} 车")
    doc.add_paragraph(f"车号/箱号: {', '.join(car_nos)}")
    doc.add_paragraph("")
    doc.add_paragraph("作业单位/班组:")
    doc.add_paragraph("")
    doc.add_paragraph("委托方(签字):")
    doc.add_paragraph("")
    doc.add_paragraph("作业方(签字):")
    doc.save(path)
