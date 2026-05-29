"""Generate Jilin Jingang departure Excel from SOP template — R54.

Reads:
  - config/project_sops/jilin_jingang.yaml (output_templates.departure_excel)
  - data/sop_agent.db (release_batches + wagon_shipments)

Output:
  - Excel (.xlsx) following the 14-column SOP template
  - Field sources from release_batch (not hardcoded)
  - Dual container in same row: container_no_1 / container_no_2
  - Proper formatting: merged title, borders, alignment

Usage:
  PYTHONPATH=src python -m ops_hub.sop.departure_excel \\
    --release-batch-id <ID> [--output-dir <dir>]
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import openpyxl
import yaml
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ── Canonical paths ──────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[3]
SOP_YAML = REPO_ROOT / "config" / "project_sops" / "jilin_jingang.yaml"
SOP_DB = REPO_ROOT / "data" / "sop_agent.db"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "output" / "excel"


# ── Data models ──────────────────────────────────────────────────────────

@dataclass
class ExcelColumn:
    key: str
    header: str
    fill_rule: str


@dataclass
class ExcelTemplate:
    business_name: str
    sheet_name: str
    file_pattern: str
    title_text: str
    title_row: int
    title_range: str
    title_merge: bool
    header_row: int
    data_start_row: int
    columns: list[ExcelColumn]


@dataclass
class ExcelRow:
    """One row in the departure Excel."""

    wagon_no: str
    container_no: str  # container_no_1 (first container)
    container_no_2: str  # second container (empty if single)
    cargo_name: str
    ship_name: str
    contract_no: str
    order_identifier: str
    loading_date: str  # YYYY-MM-DD
    entry_date: str  # YYYY-MM-DD
    seq: int = 0


@dataclass
class ExcelGenerationResult:
    release_batch_id: str
    output_path: str = ""
    row_count: int = 0
    wagon_count: int = 0
    filename: str = ""
    error: str = ""


# ── Template loading ─────────────────────────────────────────────────────

def _load_template() -> ExcelTemplate:
    raw = yaml.safe_load(SOP_YAML.read_text())
    tpl = raw["flows"]["departure_flow"]["output_templates"]["departure_excel"]

    columns = [
        ExcelColumn(key=c["key"], header=c["header"], fill_rule=c.get("fill_rule", ""))
        for c in tpl["columns"]
    ]

    wb = tpl["workbook"]
    return ExcelTemplate(
        business_name=tpl.get("business_name", ""),
        sheet_name=wb["sheet_name"],
        file_pattern=tpl["file_naming"]["pattern"],
        title_text=wb["title"]["text"],
        title_row=wb["title"]["row"],
        title_range=wb["title"]["range"],
        title_merge=wb["title"].get("merge_cells", False),
        header_row=wb["header_row"],
        data_start_row=wb["data_start_row"],
        columns=columns,
    )


# ── Data extraction ──────────────────────────────────────────────────────

def _extract_rows(
    release_batch_id: str, *, db_path: str | Path | None = None
) -> tuple[list[ExcelRow], dict[str, str], str]:
    """Extract Excel rows from DB.

    Returns (rows, batch_info, error).
    """
    sop_path = Path(db_path) if db_path else SOP_DB
    if not sop_path.exists():
        return [], {}, f"DB not found: {sop_path}"

    conn = sqlite3.connect(str(sop_path))
    conn.row_factory = sqlite3.Row
    try:
        batch = conn.execute(
            "SELECT * FROM release_batches WHERE id = ?", (release_batch_id,)
        ).fetchone()
        if batch is None:
            return [], {}, f"release_batch not found: {release_batch_id}"

        b = dict(batch)
        batch_info = {
            "cargo_name": b.get("cargo_name_detail") or b.get("cargo_name") or "",
            "ship_name": b.get("ship_name") or "",
            "contract_no": b.get("contract_no") or "",
            "order_identifier": b.get("order_identifier") or "",
        }

        wagons = conn.execute(
            "SELECT * FROM wagon_shipments WHERE batch_id = ? ORDER BY car_no",
            (release_batch_id,),
        ).fetchall()

        if not wagons:
            return [], batch_info, (
                f"no wagon_shipments for release_batch {release_batch_id}"
            )

        rows: list[ExcelRow] = []
        for w in wagons:
            wd = dict(w)
            wagon_no = wd.get("car_no", "")
            container_raw = wd.get("container_no", "")
            ticketed = wd.get("ticketed_at") or ""

            # 双箱: 同车拆在一个 row 的两列
            containers = (
                [c.strip() for c in container_raw.split("/") if c.strip()]
                if container_raw
                else [""]
            )
            container_1 = containers[0] if len(containers) >= 1 else ""
            container_2 = containers[1] if len(containers) >= 2 else ""

            # loading_date: YYYY-MM-DD
            loading_str = ticketed[:10] if ticketed else ""
            # entry_date: loading_date + 30 days
            entry_str = ""
            if loading_str:
                try:
                    ld = datetime.strptime(loading_str, "%Y-%m-%d")
                    entry_str = (ld + timedelta(days=30)).strftime("%Y-%m-%d")
                except ValueError:
                    pass

            rows.append(
                ExcelRow(
                    wagon_no=wagon_no,
                    container_no=container_1,
                    container_no_2=container_2,
                    cargo_name=batch_info["cargo_name"],
                    ship_name=batch_info["ship_name"],
                    contract_no=batch_info["contract_no"],
                    order_identifier=batch_info["order_identifier"],
                    loading_date=loading_str,
                    entry_date=entry_str,
                    seq=0,  # filled later
                )
            )

        # Assign sequential numbers
        for i, r in enumerate(rows, 1):
            r.seq = i

    finally:
        conn.close()

    return rows, batch_info, ""


# ── Column value resolution ──────────────────────────────────────────────

def _resolve_cell_value(col: ExcelColumn, row: ExcelRow) -> str:
    """Resolve a cell value per the column fill_rule."""
    key = col.key

    if key == "seq":
        return str(row.seq)
    elif key == "supplier_name":
        return ""
    elif key == "detail_account":
        return ""
    elif key == "wagon_no":
        return row.wagon_no
    elif key == "container_no_1":
        return row.container_no
    elif key == "container_no_2":
        return row.container_no_2
    elif key == "loading_date":
        return row.loading_date
    elif key == "entry_date":
        return row.entry_date
    elif key == "cargo_name":
        return row.cargo_name
    elif key == "ship_name":
        return row.ship_name
    elif key == "entry_contract_no":
        return row.contract_no
    elif key == "order_identifier":
        return row.order_identifier
    elif key == "original_departure_weight":
        return ""
    elif key == "shipment_count_type":
        return "单次"

    return ""


# ── Excel generation ─────────────────────────────────────────────────────

def _apply_excel_style(ws, tpl: ExcelTemplate, last_data_row: int):
    """Apply formatting: merged title, borders, column widths."""
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # Title row
    if tpl.title_merge and tpl.title_range:
        ws.merge_cells(tpl.title_range)
    title_cell = ws.cell(row=tpl.title_row, column=1)
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    title_cell.font = Font(bold=True, size=14)

    # Header row
    header_fill = PatternFill(start_color="D9EAD3", end_color="D9EAD3", fill_type="solid")
    header_font = Font(bold=True)
    for col_idx in range(1, len(tpl.columns) + 1):
        cell = ws.cell(row=tpl.header_row, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    # Data rows: borders
    for row_idx in range(tpl.data_start_row, last_data_row + 1):
        for col_idx in range(1, len(tpl.columns) + 1):
            ws.cell(row=row_idx, column=col_idx).border = thin_border

    # Column widths
    for col_idx in range(1, len(tpl.columns) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 18


def generate_departure_excel(
    release_batch_id: str,
    *,
    output_dir: str | Path | None = None,
    db_path: str | Path | None = None,
) -> ExcelGenerationResult:
    """Generate departure Excel for one release_batch."""
    tpl = _load_template()
    out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, batch_info, error = _extract_rows(release_batch_id, db_path=db_path)
    if error:
        return ExcelGenerationResult(
            release_batch_id=release_batch_id, error=error
        )

    row_count = len(rows)
    wagon_count = len(set(r.wagon_no for r in rows))

    today = datetime.now().strftime("%Y%m%d")
    filename = f"吉林金钢_发运数据_{today}_{wagon_count}车.xlsx"
    filepath = out_dir / filename

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = tpl.sheet_name

    # Title
    ws.cell(row=tpl.title_row, column=1, value=tpl.title_text)

    # Headers
    for col_idx, col in enumerate(tpl.columns, 1):
        ws.cell(row=tpl.header_row, column=col_idx, value=col.header)

    # Data
    for row_offset, row in enumerate(rows):
        excel_row = tpl.data_start_row + row_offset
        for col_idx, col in enumerate(tpl.columns, 1):
            value = _resolve_cell_value(col, row)
            ws.cell(row=excel_row, column=col_idx, value=value)

    last_data_row = tpl.data_start_row + row_count - 1
    _apply_excel_style(ws, tpl, last_data_row)

    wb.save(str(filepath))

    return ExcelGenerationResult(
        release_batch_id=release_batch_id,
        output_path=str(filepath),
        row_count=row_count,
        wagon_count=wagon_count,
        filename=filename,
    )


# ── CLI ──────────────────────────────────────────────────────────────────

def _build_cli_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Jilin Jingang departure Excel from SOP template."
    )
    parser.add_argument("--release-batch-id", required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--db-path", type=Path, default=None)
    return parser


def main():
    parser = _build_cli_parser()
    args = parser.parse_args()

    result = generate_departure_excel(
        args.release_batch_id,
        output_dir=args.output_dir,
        db_path=args.db_path,
    )

    if result.error:
        print(f"ERROR: {result.error}")
        return 1

    print(f"Excel generated: {result.output_path}")
    print(f"  Wagons: {result.wagon_count}")
    print(f"  Rows: {result.row_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
