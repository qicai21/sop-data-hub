"""Generate per-project departure Excel from yaml output_templates — R54 + R78.

完全 yaml 驱动,**不挂任何项目硬编码**:
  - yaml 给列定义(header/key/可选 constant/可选 cell_format)
  - yaml 给文件名 pattern(支持 {car_count} {yyyymmdd} 占位)
  - yaml 给样式 hints(标题合并范围、边框范围)

读:
  - config/project_sops/<找 project_id 对应的 yaml>
  - data/sop_agent.db (release_batches + wagon_shipments)

写:
  - 默认 output/excel/<yaml 命名 pattern>.xlsx

新项目接入:在 yaml 加 output_templates.departure_excel 即可,不改这里。

Usage:
  PYTHONPATH=src python -m sop_hub.sop.departure_excel \\
    --release-batch-id <ID> [--project chaoyang_steel] [--output-dir <dir>]
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import openpyxl
import yaml
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


REPO_ROOT = Path(__file__).resolve().parents[3]
SOP_DIR = REPO_ROOT / "config" / "project_sops"
SOP_DB = REPO_ROOT / "data" / "sop_agent.db"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "output" / "excel"


# ── Data models ──────────────────────────────────────────────────────────


@dataclass
class ExcelColumn:
    key: str
    header: str
    fill_rule: str = ""
    constant: str | None = None          # yaml: constant: "高桥镇"
    cell_format: str | None = None       # yaml: cell_format: "@" (text)


@dataclass
class ExcelTemplate:
    project_id: str
    business_name: str
    sheet_name: str
    file_pattern: str
    title_text: str
    title_row: int
    title_range: str
    title_merge: bool
    header_row: int
    data_start_row: int
    bordered_range: str       # e.g. "A1:E{last_data_row}" or "A2:N{last_data_row}"
    border_style: str
    apply_border_header: bool
    apply_border_body: bool
    columns: list[ExcelColumn]


@dataclass
class ExcelGenerationResult:
    release_batch_id: str
    output_path: str = ""
    row_count: int = 0
    wagon_count: int = 0
    filename: str = ""
    error: str = ""


# ── Template loading: yaml-driven ───────────────────────────────────────


def _find_yaml_for_project(project_id: str) -> Path:
    for yp in sorted(SOP_DIR.glob("*.yaml")):
        try:
            d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if d.get("project_id") == project_id:
            return yp
    raise FileNotFoundError(f"no yaml found with project_id={project_id!r} in {SOP_DIR}")


_FLOW_KEYS_TO_PROBE = ("departure_flow", "report_delivery_flow")


def _load_template(project_id: str) -> ExcelTemplate:
    yp = _find_yaml_for_project(project_id)
    raw = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    flows = raw.get("flows", {}) or {}

    tpl_dict: dict | None = None
    for flow_key in _FLOW_KEYS_TO_PROBE:
        candidate = ((flows.get(flow_key) or {}).get("output_templates") or {}).get("departure_excel")
        if candidate:
            tpl_dict = candidate
            break
    if tpl_dict is None:
        raise ValueError(
            f"no flows.<departure|report_delivery>_flow.output_templates.departure_excel in {yp}"
        )

    cols: list[ExcelColumn] = []
    for c in tpl_dict.get("columns") or []:
        cols.append(ExcelColumn(
            key=c["key"],
            header=c["header"],
            fill_rule=c.get("fill_rule", ""),
            constant=str(c["constant"]) if c.get("constant") is not None else None,
            cell_format=c.get("cell_format"),
        ))

    wb = tpl_dict.get("workbook") or {}
    title = wb.get("title") or {}
    fnaming = tpl_dict.get("file_naming") or {}
    style = (tpl_dict.get("style") or {}).get("table") or {}

    return ExcelTemplate(
        project_id=project_id,
        business_name=tpl_dict.get("business_name", ""),
        sheet_name=wb.get("sheet_name", "Sheet1"),
        file_pattern=fnaming.get("pattern", f"{project_id}_{{car_count}}cars.xlsx"),
        title_text=title.get("text", ""),
        title_row=int(title.get("row", 0)) if title else 0,
        title_range=title.get("range", ""),
        title_merge=bool(title.get("merge_cells", False)),
        header_row=int(wb.get("header_row", 1)),
        data_start_row=int(wb.get("data_start_row", 2)),
        bordered_range=style.get("bordered_range", ""),
        border_style=style.get("border_style", "thin"),
        apply_border_header=bool(style.get("apply_border_to_header", True)),
        apply_border_body=bool(style.get("apply_border_to_body", True)),
        columns=cols,
    )


# ── Row extraction ──────────────────────────────────────────────────────


def _extract_rows(
    release_batch_id: str,
    *,
    db_path: str | Path | None = None,
    car_nos: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Return (per-wagon dicts, batch_context dict, error_str).

    car_nos: 如果给了,只取这些车号的 wagon_shipments(用于"本次单子"
    范围,而不是 batch 历史累计)。
    """
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rb = conn.execute(
            "SELECT * FROM release_batches WHERE id=?", (release_batch_id,)
        ).fetchone()
        if rb is None:
            return [], {}, f"release_batch not found: {release_batch_id}"
        batch = dict(rb)

        if car_nos:
            placeholders = ",".join("?" * len(car_nos))
            ws_rows = conn.execute(
                f"SELECT * FROM wagon_shipments "
                f"WHERE batch_id=? AND car_no IN ({placeholders}) "
                f"ORDER BY ticketed_at ASC, car_no ASC",
                (release_batch_id, *car_nos),
            ).fetchall()
        else:
            ws_rows = conn.execute(
                "SELECT * FROM wagon_shipments WHERE batch_id=? "
                "ORDER BY ticketed_at ASC, car_no ASC",
                (release_batch_id,),
            ).fetchall()

        # batch context
        # 全列共用一个"货票时间":取最早 ticketed_at,format yyyymmddhhmm00
        ticketed_compact = ""
        for r in ws_rows:
            ta = (r["ticketed_at"] or "").strip()
            if ta:
                try:
                    dt = datetime.fromisoformat(ta.replace(" ", "T"))
                    ticketed_compact = dt.strftime("%Y%m%d%H%M") + "00"
                except ValueError:
                    # fallback: 取数字部分
                    digits = "".join(ch for ch in ta if ch.isdigit())[:12]
                    if len(digits) >= 12:
                        ticketed_compact = digits + "00"
                break

        ctx = {
            "release_batch_id": release_batch_id,
            "project": batch.get("project", ""),
            "ship_name": batch.get("ship_name", ""),
            "cargo_name": batch.get("cargo_product_name") or batch.get("cargo_name", ""),
            "destination_station": batch.get("destination_station", ""),
            "contract_no": batch.get("contract_no", ""),
            "order_identifier": batch.get("order_identifier", ""),
            "ticketed_at_compact_text": ticketed_compact,
        }

        import json as _json
        rows: list[dict[str, Any]] = []
        for i, r in enumerate(ws_rows, start=1):
            # 箱号:container_numbers_json 优先(稳定源),fallback container_no("A/B")
            containers: list[str] = []
            r_dict = dict(r)
            cnj = r_dict.get("container_numbers_json")
            if cnj:
                try:
                    containers = [b for b in _json.loads(cnj) if b]
                except Exception:
                    containers = []
            if not containers and r_dict.get("container_no"):
                containers = [b.strip() for b in str(r_dict["container_no"]).split("/") if b.strip()]
            cont1 = containers[0] if len(containers) >= 1 else ""
            cont2 = containers[1] if len(containers) >= 2 else ""
            ta = (r["ticketed_at"] or "").strip()
            loading_date = ta[:10] if ta and len(ta) >= 10 else ""
            entry_date = ""
            if loading_date:
                try:
                    d = datetime.strptime(loading_date, "%Y-%m-%d")
                    entry_date = (d + timedelta(days=30)).strftime("%Y-%m-%d")
                except ValueError:
                    pass
            rows.append({
                "seq": i,
                "wagon_no": r["car_no"] or "",
                "container_no_1": cont1,
                "container_no_2": cont2,
                "cargo_name": ctx["cargo_name"],
                "ship_name": ctx["ship_name"],
                "entry_contract_no": ctx["contract_no"],
                "order_identifier": ctx["order_identifier"],
                "loading_date": loading_date,
                "entry_date": entry_date,
                "ticketed_at_raw": ta,
                "marked_weight": r["marked_weight"] or "",
                "car_model": r["car_model"] or "",
                "origin_name": r["origin_name"] or "",
                "destination_name": r["destination_name"] or "",
                "shipment_count_type": "单次",
                # batch-level constants/derived also injected for direct lookup
                "ticketed_at_compact_text": ctx["ticketed_at_compact_text"],
                "release_batch_id": release_batch_id,
            })
        return rows, ctx, ""
    finally:
        conn.close()


# ── Column value resolution ─────────────────────────────────────────────


def _resolve_cell_value(col: ExcelColumn, row: dict[str, Any]) -> Any:
    """Resolve a cell value for one column on one row.

    Priority:
      1. yaml `constant` (literal, no lookup)
      2. row dict[key] lookup
      3. empty string
    """
    if col.constant is not None:
        return col.constant
    if col.key == "seq":
        return row.get("seq", "")
    return row.get(col.key, "")


# ── Styling ──────────────────────────────────────────────────────────────


def _apply_style(ws, tpl: ExcelTemplate, last_data_row: int):
    """Apply yaml-driven styling."""
    thin_border = Border(
        left=Side(style=tpl.border_style),
        right=Side(style=tpl.border_style),
        top=Side(style=tpl.border_style),
        bottom=Side(style=tpl.border_style),
    )

    # Optional title
    if tpl.title_text and tpl.title_row > 0:
        if tpl.title_merge and tpl.title_range:
            try:
                ws.merge_cells(tpl.title_range)
            except Exception:
                pass
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
        if tpl.apply_border_header:
            cell.border = thin_border

    # Data rows borders
    if tpl.apply_border_body:
        for row_idx in range(tpl.data_start_row, last_data_row + 1):
            for col_idx in range(1, len(tpl.columns) + 1):
                ws.cell(row=row_idx, column=col_idx).border = thin_border

    # Column widths
    for col_idx in range(1, len(tpl.columns) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 18


def _resolve_filename(pattern: str, *, car_count: int, batch_id: str) -> str:
    today = datetime.now().strftime("%Y%m%d")
    return (pattern
            .replace("{car_count}", str(car_count))
            .replace("{yyyymmdd}", today)
            .replace("{batch_id}", batch_id)
            .replace("{date}", today))


# ── Public entry ─────────────────────────────────────────────────────────


def generate_departure_excel(
    release_batch_id: str,
    *,
    project_id: str | None = None,
    output_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    car_nos: list[str] | None = None,
) -> ExcelGenerationResult:
    """Generate departure Excel for one release_batch.

    project_id 可以不传 — 自动从 release_batches.project 读。
    car_nos: 如果给了,只导出这些车号(对应"本次检装车通知单"),
    避免 batch 多次发车时累计输出。
    """
    # Resolve project_id from DB if not given
    if not project_id:
        db = Path(db_path) if db_path else SOP_DB
        conn = sqlite3.connect(str(db))
        try:
            r = conn.execute(
                "SELECT project FROM release_batches WHERE id=?", (release_batch_id,)
            ).fetchone()
        finally:
            conn.close()
        if not r or not r[0]:
            return ExcelGenerationResult(
                release_batch_id=release_batch_id,
                error="cannot resolve project_id from release_batch",
            )
        project_id = str(r[0]).strip()

    try:
        tpl = _load_template(project_id)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return ExcelGenerationResult(
            release_batch_id=release_batch_id,
            error=f"template load failed: {exc}",
        )

    out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, ctx, err = _extract_rows(
        release_batch_id, db_path=db_path, car_nos=car_nos,
    )
    if err:
        return ExcelGenerationResult(release_batch_id=release_batch_id, error=err)
    if not rows:
        return ExcelGenerationResult(
            release_batch_id=release_batch_id,
            error="no wagon_shipments found for this release_batch",
        )

    row_count = len(rows)
    wagon_count = len({r["wagon_no"] for r in rows if r["wagon_no"]})

    filename = _resolve_filename(tpl.file_pattern,
                                  car_count=wagon_count,
                                  batch_id=release_batch_id[:8])
    filepath = out_dir / filename

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = tpl.sheet_name

    # Title (optional)
    if tpl.title_text and tpl.title_row > 0:
        ws.cell(row=tpl.title_row, column=1, value=tpl.title_text)

    # Headers
    for col_idx, col in enumerate(tpl.columns, 1):
        ws.cell(row=tpl.header_row, column=col_idx, value=col.header)

    # Data
    for row_offset, row in enumerate(rows):
        excel_row = tpl.data_start_row + row_offset
        for col_idx, col in enumerate(tpl.columns, 1):
            value = _resolve_cell_value(col, row)
            cell = ws.cell(row=excel_row, column=col_idx, value=value)
            # 文本格式列(如朝阳的"货票时间")强制文本,避免 Excel 科学计数
            if col.cell_format:
                cell.number_format = col.cell_format

    last_data_row = tpl.data_start_row + row_count - 1
    _apply_style(ws, tpl, last_data_row)

    wb.save(str(filepath))

    return ExcelGenerationResult(
        release_batch_id=release_batch_id,
        output_path=str(filepath),
        row_count=row_count,
        wagon_count=wagon_count,
        filename=filename,
    )


# ── CLI ──────────────────────────────────────────────────────────────────


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Generate departure Excel from yaml SOP.")
    p.add_argument("--release-batch-id", required=True)
    p.add_argument("--project", default=None, help="optional; default reads from DB")
    p.add_argument("--output-dir", type=Path, default=None)
    p.add_argument("--db-path", type=Path, default=None)
    args = p.parse_args()

    r = generate_departure_excel(
        args.release_batch_id,
        project_id=args.project,
        output_dir=args.output_dir,
        db_path=args.db_path,
    )
    if r.error:
        print(f"ERROR: {r.error}")
        return 1
    print(f"Excel: {r.output_path}")
    print(f"  rows: {r.row_count}  wagons: {r.wagon_count}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
