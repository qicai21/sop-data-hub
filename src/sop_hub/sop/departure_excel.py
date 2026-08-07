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
# 业务文档归档根 — 跟 json/原图同根。用户业务侧查文档只会进 Documents/bussiness-
# artifacts,不该跑到 git 仓库里翻。生成 excel 走 business archive,保持跟
# VLM 抽出的 json(business/projects/<proj>/<dest>/<ship>/<lot>/json/<date>/)
# 对称的目录结构。
BUSINESS_ARCHIVE_ROOT = Path(
    "/Users/qicai21/Documents/bussiness-artifacts/wechat_images/business/projects"
)
# 仅作 fallback:business archive 根不可写时(测试 / 没挂卷)用仓库本地。
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
    # 项目特定 footer(2026-06-04 中唐:发运 excel 底部 5 行业务字段)。
    # 结构示例(全 yaml 驱动):
    #   footer:
    #     start_row_gap: 1          # 数据末行与 footer 之间空几行
    #     label_col: "A"
    #     value_col: "B"
    #     value_merge_end_col: "E"  # 可选,value 跨列合并
    #     rows:
    #       - label: "货物品名"
    #         from_release_batch: "cargo_product_name"
    #       - label: "进口船名"
    #         from_release_batch: "import_ship_name"
    # 不配 footer 的项目(朝阳/吉林)行为不变。
    footer: dict | None = None


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
        footer=tpl_dict.get("footer"),
    )


# ── Row extraction ──────────────────────────────────────────────────────


def _extract_rows(
    release_batch_id: str,
    *,
    db_path: str | Path | None = None,
    car_nos: list[str] | None = None,
    ydids: list[str] | None = None,
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

        if ydids:
            placeholders = ",".join("?" * len(ydids))
            ws_rows = conn.execute(
                f"SELECT * FROM wagon_shipments "
                f"WHERE batch_id=? AND ydid IN ({placeholders})",
                (release_batch_id, *ydids),
            ).fetchall()
            ydid_order = {str(y): i for i, y in enumerate(ydids)}
            car_order = {str(c): i for i, c in enumerate(car_nos or [])}
            ws_rows = sorted(
                ws_rows,
                key=lambda r: (
                    ydid_order.get(str(r["ydid"] or ""), 1_000_000),
                    car_order.get(str(r["car_no"] or ""), 1_000_000),
                    str(r["ticketed_at"] or ""),
                ),
            )
        elif car_nos:
            # 无 ydid 时每车号只保留最新 ticketed_at，防循环车号串入旧趟
            placeholders = ",".join("?" * len(car_nos))
            ws_rows = conn.execute(
                f"SELECT w.* FROM wagon_shipments w "
                f"WHERE w.batch_id=? AND w.car_no IN ({placeholders}) "
                f"AND w.ticketed_at = ("
                f"  SELECT MAX(w2.ticketed_at) FROM wagon_shipments w2 "
                f"  WHERE w2.batch_id=w.batch_id AND w2.car_no=w.car_no"
                f")",
                (release_batch_id, *car_nos),
            ).fetchall()
            # 按调用方给的 car_nos 顺序输出 — 对应检装车通知单的 seq,
            # 也就是列车物理排序(机车头到机车尾)。OCR 抽错的车号可改,
            # 但顺序不能断。2026-06-04 业务约定。
            order_index = {c: i for i, c in enumerate(car_nos)}
            ws_rows = sorted(
                ws_rows,
                key=lambda r: order_index.get(r["car_no"], 1_000_000),
            )
        else:
            # batch 历史累计场景,无 seq 上下文,fallback ticketed_at
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


def _render_footer(
    *,
    ws,
    footer_cfg: dict,
    release_batch_id: str,
    data_end_row: int,
    db_path: str | Path | None = None,
) -> None:
    """yaml 驱动的 footer 渲染。每条 row 拉一个 release_batches 字段值,
    label 在 label_col,value 在 value_col(可选 merge 到 value_merge_end_col)。

    例:中唐特钢底部
      货物品名:  纽曼粉
      计划号:    90260500008
      合同号:    ZLZT-2026050801
      进口船名:  丰收散运
      到港船名:  鞍子河
    """
    from openpyxl.styles import Font, Alignment
    from openpyxl.utils import column_index_from_string

    rows_cfg = footer_cfg.get("rows") or []
    if not rows_cfg:
        return
    gap = int(footer_cfg.get("start_row_gap", 1) or 0)
    label_col = footer_cfg.get("label_col", "A")
    value_col = footer_cfg.get("value_col", "B")
    value_merge_end = footer_cfg.get("value_merge_end_col")

    # 拉 release_batch 行
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rb = conn.execute(
            "SELECT * FROM release_batches WHERE id=?", (release_batch_id,)
        ).fetchone()
    finally:
        conn.close()
    if not rb:
        return  # 没行就别写 footer

    label_idx = column_index_from_string(label_col)
    value_idx = column_index_from_string(value_col)
    label_font = Font(bold=True)
    label_align = Alignment(horizontal="right", vertical="center")
    value_align = Alignment(horizontal="left", vertical="center")

    start_row = data_end_row + gap + 1
    for offset, r in enumerate(rows_cfg):
        excel_row = start_row + offset
        label = str(r.get("label") or "")
        field = r.get("from_release_batch") or ""
        try:
            value = rb[field] if field else ""
        except (IndexError, KeyError):
            value = ""
        if value is None:
            value = ""
        # 写标签
        c_lbl = ws.cell(row=excel_row, column=label_idx, value=label + ":")
        c_lbl.font = label_font
        c_lbl.alignment = label_align
        # 写值
        c_val = ws.cell(row=excel_row, column=value_idx, value=value)
        c_val.alignment = value_align
        # 可选:值合并跨列(footer 值通常较长)
        if value_merge_end:
            end_idx = column_index_from_string(value_merge_end)
            if end_idx > value_idx:
                ws.merge_cells(
                    start_row=excel_row, start_column=value_idx,
                    end_row=excel_row,   end_column=end_idx,
                )


def _resolve_filename(pattern: str, *, car_count: int, batch_id: str) -> str:
    today = datetime.now().strftime("%Y%m%d")
    return (pattern
            .replace("{car_count}", str(car_count))
            .replace("{yyyymmdd}", today)
            .replace("{batch_id}", batch_id)
            .replace("{date}", today))


# ── Public entry ─────────────────────────────────────────────────────────


def _resolve_archive_dir(
    project_id: str,
    release_batch_id: str,
    *,
    db_path: str | Path | None = None,
    date_override: str | None = None,
) -> Path:
    """业务归档目录:business/projects/<proj>/<dest>/<ship>/<lot>/excel/<date>/

    跟 json 归档结构对称(json 是 .../lot01/json/<date>/);excel 放 excel/
    子目录。<date> 用 batch_date(出港单上"第几次下达计划"那一行的日期);
    没有就 fallback notice_date,再没有 fallback 今天。

    business archive 根盘不可写时(测试 / 没挂卷)fallback 到 DEFAULT_OUTPUT_DIR。
    """
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    try:
        r = conn.execute(
            "SELECT ship_name, destination_station, batch_sequence, "
            "       batch_date, notice_date "
            "  FROM release_batches WHERE id=?",
            (release_batch_id,),
        ).fetchone()
    finally:
        conn.close()
    if not r:
        return DEFAULT_OUTPUT_DIR
    ship, dest, lot, batch_date, notice_date = r
    ship = (ship or "_unknown_ship").strip()
    dest = (dest or "_unknown_dest").strip()
    lot = (lot or "lot01").strip()
    # 优先用调用方给的发运日(发运 excel 该按发运事件日,不取可能解析错的 batch_date)
    date_seg = (date_override or batch_date or notice_date or "").strip()
    if not date_seg:
        from datetime import datetime
        date_seg = datetime.now().strftime("%Y-%m-%d")
    candidate = BUSINESS_ARCHIVE_ROOT / project_id / dest / ship / lot / "excel" / date_seg
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        return DEFAULT_OUTPUT_DIR


def generate_departure_excel(
    release_batch_id: str,
    *,
    project_id: str | None = None,
    output_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    car_nos: list[str] | None = None,
    ydids: list[str] | None = None,
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

    out_dir = (
        Path(output_dir) if output_dir
        else _resolve_archive_dir(project_id, release_batch_id, db_path=db_path)
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, ctx, err = _extract_rows(
        release_batch_id, db_path=db_path, car_nos=car_nos, ydids=ydids,
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

    # ── Footer(2026-06-04 中唐 海铁联运:底部加 货物品名/计划号/合同号/
    #            进口船名/到港船名 5 行)── 项目特定,yaml 驱动,不配则跳过 ──
    if tpl.footer:
        _render_footer(
            ws=ws,
            footer_cfg=tpl.footer,
            release_batch_id=release_batch_id,
            data_end_row=last_data_row,
            db_path=db_path,
        )

    wb.save(str(filepath))

    return ExcelGenerationResult(
        release_batch_id=release_batch_id,
        output_path=str(filepath),
        row_count=row_count,
        wagon_count=wagon_count,
        filename=filename,
    )


# ── Per-dispatch-event API(2026-06-06 #107)─────────────────────────────
# 业务事实:一次出港列车 = 一次发车事件,可能跨多个 release_batch(同一项目
# 同船,lotN 余尾 + lotN+1 新装)。发运 excel 必须 per-event 而非 per-batch:
# 行序按 ticketed_at(列车物理排序),每行各自携带其所属 batch 的合同/订单/
# 货名/船名 — 跨 batch 行同表共存,各填各的。详 [[dispatch-event-excel-rule]]。
#
# 入口:generate_dispatch_event_excel(wagon_ids=[...]) — 直接指定本次事件
# 涉及的 wagon_shipments.id 列表。caller 通常按某种业务规则(同一次检装车
# 通知单 + 余尾、ticketed_at 时间窗、人工汇总)拼出 wagon_ids。


@dataclass
class DispatchEventExcelResult:
    project_id: str = ""
    output_path: str = ""
    row_count: int = 0
    wagon_count: int = 0
    filename: str = ""
    release_batch_ids: list[str] = field(default_factory=list)
    error: str = ""


def _fetch_event_wagons_and_batches(
    wagon_ids: list[str] | None = None, *,
    container_ydids: list[str] | None = None,
    container_batch_id: str | None = None,
    db_path: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str]:
    """Return (wagon_rows ordered by ticketed_at, batch_id→batch_dict, error)."""
    wagon_ids = wagon_ids or []
    container_ydids = container_ydids or []
    if not wagon_ids and not container_ydids:
        return [], {}, "wagon_ids or container_ydids is empty"
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        if container_ydids:
            in_ph = ",".join("?" * len(container_ydids))
            batch_filter = " AND batch_id=?" if container_batch_id else ""
            box_rows = conn.execute(
                f"SELECT * FROM wagon_container_shipments WHERE ydid IN ({in_ph}){batch_filter} "
                f"ORDER BY ticketed_at ASC, car_no ASC, box_position ASC",
                [*container_ydids, *([container_batch_id] if container_batch_id else [])],
            ).fetchall()
            grouped: dict[tuple[str, str], list[sqlite3.Row]] = {}
            for row in box_rows:
                grouped.setdefault((row["car_no"] or "", row["ydid"] or ""), []).append(row)
            wagons = []
            for rows in grouped.values():
                first = dict(rows[0])
                first["container_numbers_json"] = __import__("json").dumps(
                    [r["box_no"] for r in rows]
                )
                first["container_no"] = "/".join(r["box_no"] for r in rows)
                # event renderer only needs wagon-shaped facts; source remains box-level.
                wagons.append(first)
        else:
            in_ph = ",".join("?" * len(wagon_ids))
            wagons = [dict(r) for r in conn.execute(
                f"SELECT * FROM wagon_shipments WHERE id IN ({in_ph}) "
                f"ORDER BY ticketed_at ASC, car_no ASC", wagon_ids,
            ).fetchall()]
        if not wagons:
            return [], {}, "no shipment facts found for this event"
        batch_ids = sorted({r["batch_id"] for r in wagons if r.get("batch_id")})
        if not batch_ids:
            return [], {}, "no batch_id on any wagon"
        b_ph = ",".join("?" * len(batch_ids))
        batches_rows = conn.execute(
            f"SELECT * FROM release_batches WHERE id IN ({b_ph})", batch_ids,
        ).fetchall()
        batches = {b["id"]: dict(b) for b in batches_rows}
        # 一致性:全部 wagon 必须属于同一项目(项目模板不同,无法混用)
        projects = {b.get("project") for b in batches.values() if b.get("project")}
        if len(projects) > 1:
            return [], {}, (
                f"per-event excel requires single-project wagons; got "
                f"{sorted(projects)}"
            )
        return wagons, batches, ""
    finally:
        conn.close()


def _build_event_row(
    *,
    wagon: dict[str, Any],
    batch: dict[str, Any],
    seq: int,
    earliest_ticketed_compact: str,
    container_override: str | None = None,
) -> dict[str, Any]:
    """Build one excel row for a wagon, with per-row batch context (event-aware).

    container_override:  传单个 box 号(#111 split 渲染用)→ 该行只填这 1 箱,
                         箱号2 列留空,合同/订单/船名走该 box 对应的 batch。
                         不传 → 整车 2 箱填同 1 行(常态)。
    """
    import json as _json
    containers: list[str] = []
    if container_override is not None:
        containers = [container_override]
    else:
        cnj = wagon.get("container_numbers_json")
        if cnj:
            try:
                containers = [b for b in _json.loads(cnj) if b]
            except Exception:
                containers = []
        if not containers and wagon.get("container_no"):
            containers = [b.strip() for b in str(wagon["container_no"]).split("/") if b.strip()]
    cont1 = containers[0] if len(containers) >= 1 else ""
    cont2 = containers[1] if len(containers) >= 2 else ""
    ta = (wagon.get("ticketed_at") or "").strip()
    loading_date = ta[:10] if ta and len(ta) >= 10 else ""
    entry_date = ""
    if loading_date:
        try:
            d = datetime.strptime(loading_date, "%Y-%m-%d")
            entry_date = (d + timedelta(days=30)).strftime("%Y-%m-%d")
        except ValueError:
            pass
    cargo = (
        batch.get("cargo_product_name")
        or batch.get("cargo_name", "")
        or ""
    )
    return {
        "seq": seq,
        "wagon_no": wagon.get("car_no") or "",
        "container_no_1": cont1,
        "container_no_2": cont2,
        # per-row batch fields — 这是 per-event 的关键:每行带各自 batch 的
        # 合同号/订单号/货名/船名,跨 batch 同表各填各的
        "cargo_name": cargo,
        "ship_name": batch.get("ship_name", ""),
        "entry_contract_no": batch.get("contract_no", ""),
        "order_identifier": batch.get("order_identifier", ""),
        "loading_date": loading_date,
        "entry_date": entry_date,
        "ticketed_at_raw": ta,
        "marked_weight": wagon.get("marked_weight") or "",
        "car_model": wagon.get("car_model") or "",
        "origin_name": wagon.get("origin_name") or "",
        "destination_name": wagon.get("destination_name") or "",
        "shipment_count_type": "单次",
        # 朝阳 yaml 用的"货票时间"用本次事件最早 ticketed_at 共享(yaml 注释
        # 明确"全列共用一个时间";per-event 也按这个共享语义)
        "ticketed_at_compact_text": earliest_ticketed_compact,
        "release_batch_id": batch.get("id", ""),
    }


def generate_dispatch_event_excel(
    wagon_ids: list[str] | None = None,
    *,
    project_id: str | None = None,
    output_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    filename_override: str | None = None,
    container_ydids: list[str] | None = None,
    container_batch_id: str | None = None,
) -> DispatchEventExcelResult:
    """Generate per-dispatch-event excel(跨 batch 合并一张表)。

    Args:
      wagon_ids: 本次事件涉及的 wagon_shipments.id 列表。caller 拼好传进来,
                 函数按 ticketed_at ASC 排,seq 1..N 重排。
      project_id: 不传则从首个 batch.project 读;若 wagons 跨多项目会报错。
      output_dir: 不传则按主 batch(车数最多那个)的 archive 路径落盘。
      filename_override: 不传则用 yaml file_naming.pattern + car_count。
    """
    wagons, batches, err = _fetch_event_wagons_and_batches(
        wagon_ids,
        container_ydids=container_ydids,
        container_batch_id=container_batch_id,
        db_path=db_path,
    )
    if err:
        return DispatchEventExcelResult(error=err)

    # 项目锁定(单一)
    resolved_project: str | None = project_id
    if not resolved_project:
        for b in batches.values():
            if b.get("project"):
                resolved_project = str(b["project"]).strip()
                break
    if not resolved_project:
        return DispatchEventExcelResult(
            error="cannot resolve project_id from any batch",
            release_batch_ids=sorted(batches.keys()),
        )

    try:
        tpl = _load_template(resolved_project)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return DispatchEventExcelResult(
            project_id=resolved_project,
            release_batch_ids=sorted(batches.keys()),
            error=f"template load failed: {exc}",
        )

    # 共享 ticketed_at_compact(取本次事件最早一条)
    earliest_compact = ""
    for w in wagons:
        ta = (w.get("ticketed_at") or "").strip()
        if ta:
            try:
                dt = datetime.fromisoformat(ta.replace(" ", "T"))
                earliest_compact = dt.strftime("%Y%m%d%H%M") + "00"
                break
            except ValueError:
                digits = "".join(ch for ch in ta if ch.isdigit())[:12]
                if len(digits) >= 12:
                    earliest_compact = digits + "00"
                break

    # #123 Phase 2 (2026-06-07):集装箱业务从 wagon_container_shipments 直接
    # SELECT box rows,SQL 自然,无需 cbm JSON 拆。整车业务保留老路径。
    import json as _json
    rows: list[dict[str, Any]] = []
    seq_counter = 0

    _use_container_table = False
    try:
        from sop_hub.sop.wagon_container_shipments import is_container_business_project
        _use_container_table = is_container_business_project(resolved_project)
    except Exception:
        _use_container_table = False

    if _use_container_table:
        # 新路径:用 (car_no, ydid) 查 box rows,batch_id 自带,合同走该行 batch
        import sqlite3 as _sql
        from pathlib import Path as _P
        db = _P(db_path) if db_path else SOP_DB
        car_ydid_pairs = [(w["car_no"], w.get("ydid") or "") for w in wagons
                          if w.get("car_no")]
        conn = _sql.connect(str(db))
        conn.row_factory = _sql.Row
        try:
            box_rows: list[dict[str, Any]] = []
            for car_no, ydid in car_ydid_pairs:
                if ydid:
                    batch_filter = " AND batch_id=?" if container_batch_id else ""
                    rs = conn.execute(
                        "SELECT * FROM wagon_container_shipments "
                        f"WHERE car_no=? AND ydid=?{batch_filter} ORDER BY box_position",
                        (car_no, ydid, *([container_batch_id] if container_batch_id else [])),
                    ).fetchall()
                else:
                    batch_filter = " AND batch_id=?" if container_batch_id else ""
                    rs = conn.execute(
                        "SELECT * FROM wagon_container_shipments "
                        f"WHERE car_no=?{batch_filter} ORDER BY box_position",
                        (car_no, *([container_batch_id] if container_batch_id else [])),
                    ).fetchall()
                for r in rs:
                    box_rows.append(dict(r))
        finally:
            conn.close()
        # 顺手补全 batches dict(box.batch_id 可能指向 wagons 不在的 lot)
        extra_batch_ids = sorted({r["batch_id"] for r in box_rows
                                  if r.get("batch_id") and r["batch_id"] not in batches})
        if extra_batch_ids:
            conn2 = _sql.connect(str(db)); conn2.row_factory = _sql.Row
            try:
                ph = ",".join("?" * len(extra_batch_ids))
                for br in conn2.execute(
                    f"SELECT * FROM release_batches WHERE id IN ({ph})", extra_batch_ids,
                ).fetchall():
                    batches[br["id"]] = dict(br)
            finally:
                conn2.close()
        # 按 (car_no, ydid, batch_id) 分组:整车同 lot → 1 行 N box;
        # split 车 → 2 行(每行 1 box,合同走该 box 对应 lot)。yaml 模板
        # 每行结构:1 个 car_no + box_no_1/box_no_2 两列。
        from collections import defaultdict, OrderedDict
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        # 维持原顺序:car_no 物理装载顺序 + box_position
        first_seen_order: dict[tuple[str, str], int] = OrderedDict()
        for br in box_rows:
            key_full = (br["car_no"], br.get("ydid") or "", br["batch_id"])
            groups[key_full].append(br)
            if (br["car_no"], br.get("ydid") or "") not in first_seen_order:
                first_seen_order[(br["car_no"], br.get("ydid") or "")] = len(first_seen_order)

        # 排序 keys: 按 car_no 在原 wagon 顺序的 idx,然后 batch_id 内 box_position
        def _grp_sort_key(k: tuple[str, str, str]) -> tuple[int, str]:
            cn, yd, bid = k
            return (first_seen_order.get((cn, yd), 9999), bid)

        for key in sorted(groups.keys(), key=_grp_sort_key):
            boxes_in_grp = sorted(groups[key], key=lambda r: r.get("box_position") or 0)
            cn, yd, bid = key
            b = batches.get(bid, {})
            sample = boxes_in_grp[0]
            wagon_like = {
                "car_no": cn,
                "ticketed_at": sample.get("ticketed_at"),
                "marked_weight": sample.get("marked_weight"),
                "car_model": sample.get("car_model"),
                "origin_name": sample.get("origin_name"),
                "destination_name": sample.get("destination_name"),
                "container_numbers_json": _json.dumps(
                    [bx["box_no"] for bx in boxes_in_grp]
                ),
                "container_no": "/".join(bx["box_no"] for bx in boxes_in_grp),
            }
            seq_counter += 1
            rows.append(_build_event_row(
                wagon=wagon_like, batch=b, seq=seq_counter,
                earliest_ticketed_compact=earliest_compact,
            ))
    else:
        # 老路径(整车业务):整车 1 行 2 箱,split 车看 cbm 拆
        for w in wagons:
            cbm_raw = w.get("container_batch_map")
            cbm: dict[str, str] | None = None
            if cbm_raw:
                try:
                    cbm = _json.loads(cbm_raw)
                    if not isinstance(cbm, dict) or len(set(cbm.values())) < 2:
                        cbm = None
                except Exception:
                    cbm = None
            if cbm:
                try:
                    ordered_boxes = [b for b in _json.loads(w.get("container_numbers_json") or "[]") if b]
                except Exception:
                    ordered_boxes = []
                if not ordered_boxes:
                    raw = w.get("container_no") or ""
                    ordered_boxes = [b.strip() for b in str(raw).split("/") if b.strip()]
                if not ordered_boxes:
                    ordered_boxes = list(cbm.keys())
                for box in ordered_boxes:
                    target_batch_id = cbm.get(box)
                    if not target_batch_id:
                        continue
                    b = batches.get(target_batch_id, {})
                    seq_counter += 1
                    rows.append(_build_event_row(
                        wagon=w, batch=b, seq=seq_counter,
                        earliest_ticketed_compact=earliest_compact,
                        container_override=box,
                    ))
            else:
                b = batches.get(w.get("batch_id"), {})
                seq_counter += 1
                rows.append(_build_event_row(
                    wagon=w, batch=b, seq=seq_counter,
                    earliest_ticketed_compact=earliest_compact,
                ))

    if not rows:
        return DispatchEventExcelResult(
            project_id=resolved_project,
            release_batch_ids=sorted(batches.keys()),
            error="no rows built",
        )

    # 主 batch = 车数最多者(归档目录用)
    from collections import Counter
    primary_batch_id = Counter(
        r["release_batch_id"] for r in rows if r.get("release_batch_id")
    ).most_common(1)[0][0]

    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = _resolve_archive_dir(
            resolved_project, primary_batch_id, db_path=db_path,
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    row_count = len(rows)
    wagon_count = len({r["wagon_no"] for r in rows if r["wagon_no"]})

    if filename_override:
        filename = filename_override
    elif tpl.file_pattern and "{car_count}" in tpl.file_pattern:
        # per-event 也走 yaml file_naming.pattern —— 业务侧文件名必须是项目正名
        # (如「吉林金钢_发运数据_{yyyymmdd}_{car_count}车.xlsx」),car_count = 本次
        # 事件车数。不再吐 {project}_event_… 内部名(2026-06-16 修:发运 excel
        # 文件名车数错 = 退化成整批 + 内部命名两个 bug 叠加)。
        filename = _resolve_filename(tpl.file_pattern,
                                      car_count=wagon_count, batch_id="")
    else:
        today = datetime.now().strftime("%Y%m%d")
        filename = f"{resolved_project}_event_{today}_{wagon_count}cars.xlsx"
    filepath = out_dir / filename

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = tpl.sheet_name

    if tpl.title_text and tpl.title_row > 0:
        ws.cell(row=tpl.title_row, column=1, value=tpl.title_text)

    for col_idx, col in enumerate(tpl.columns, 1):
        ws.cell(row=tpl.header_row, column=col_idx, value=col.header)

    for row_offset, row in enumerate(rows):
        excel_row = tpl.data_start_row + row_offset
        for col_idx, col in enumerate(tpl.columns, 1):
            value = _resolve_cell_value(col, row)
            cell = ws.cell(row=excel_row, column=col_idx, value=value)
            if col.cell_format:
                cell.number_format = col.cell_format

    last_data_row = tpl.data_start_row + row_count - 1
    _apply_style(ws, tpl, last_data_row)

    # footer 当前是 per-release-batch(从 batch 字段读)。per-event 跨 batch
    # 时 footer 取主 batch 的字段。若项目本来不配 footer(朝阳/吉林),跳过。
    if tpl.footer:
        _render_footer(
            ws=ws,
            footer_cfg=tpl.footer,
            release_batch_id=primary_batch_id,
            data_end_row=last_data_row,
            db_path=db_path,
        )

    wb.save(str(filepath))
    return DispatchEventExcelResult(
        project_id=resolved_project,
        output_path=str(filepath),
        row_count=row_count,
        wagon_count=wagon_count,
        filename=filename,
        release_batch_ids=sorted(batches.keys()),
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


def generate_multibatch_departure_excel(
    batch_specs: list[tuple[str, list[str] | None, list[str] | None]],
    *,
    project_id: str,
    output_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    filename_override: str | None = None,
) -> DispatchEventExcelResult:
    """多批次「分块」发运 excel(2026-06-27 中唐):一次发车涉及多船/多批次时,
    **按批次成块**——每块 = 船名小标题 + 列头 + 该批次记录 + 该批次货运 footer + 空行,
    批次间**不混排**。与 generate_dispatch_event_excel(跨 batch 按 ticketed_at 合并
    一张表)互补:整车多船(中唐贝拉+丰收散运)用本函数,集装箱单事件跨 lot 用那个。
    batch_specs: [(release_batch_id, car_nos|None, ydids|None), ...] 按展示顺序传入。
    """
    if not batch_specs:
        return DispatchEventExcelResult(error="batch_specs is empty")
    try:
        tpl = _load_template(project_id)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return DispatchEventExcelResult(project_id=project_id, error=f"template load failed: {exc}")

    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = tpl.sheet_name
    n_cols = max(1, len(tpl.columns))

    cur = 1
    total_rows = 0
    total_wagons: set[str] = set()
    used_batch_ids: list[str] = []
    ships_used: list[str] = []

    for rbid, car_nos, ydids in batch_specs:
        rows, ctx, err = _extract_rows(
            rbid, db_path=db_path, car_nos=car_nos, ydids=ydids
        )
        if err or not rows:
            continue
        ship = (ctx.get("ship_name") or "") if isinstance(ctx, dict) else ""
        if ship and ship not in ships_used:
            ships_used.append(ship)
        # 船名小标题(块首)
        ws.cell(row=cur, column=1, value=f"【{ship}】{len(rows)} 车").font = Font(bold=True)
        cur += 1
        # 列头
        for ci, col in enumerate(tpl.columns, 1):
            ws.cell(row=cur, column=ci, value=col.header).font = Font(bold=True)
        data_start = cur + 1
        # 数据行
        for ro, row in enumerate(rows):
            er = data_start + ro
            for ci, col in enumerate(tpl.columns, 1):
                cell = ws.cell(row=er, column=ci, value=_resolve_cell_value(col, row))
                if col.cell_format:
                    cell.number_format = col.cell_format
            if row.get("wagon_no"):
                total_wagons.add(row["wagon_no"])
        last_data_row = data_start + len(rows) - 1
        # 该批次货运 footer(计划号/合同号/进口船名/到港船名 等)
        footer_end = last_data_row
        if tpl.footer:
            _render_footer(ws=ws, footer_cfg=tpl.footer, release_batch_id=rbid,
                           data_end_row=last_data_row, db_path=db_path)
            gap = int(tpl.footer.get("start_row_gap", 1) or 0)
            footer_end = last_data_row + gap + len(tpl.footer.get("rows") or [])
        total_rows += len(rows)
        used_batch_ids.append(rbid)
        cur = footer_end + 2  # 批次间空 2 行

    if not used_batch_ids:
        return DispatchEventExcelResult(project_id=project_id, error="no rows for any batch in batch_specs")

    for ci in range(1, n_cols + 1):
        ws.column_dimensions[get_column_letter(ci)].width = 14

    # 发运日 = 这批车实际制票日(发运事件日);绕开可能解析错的 batch_date(如马兰幸福 8-27)。
    dispatch_date = ""
    try:
        _ph = ",".join("?" * len(used_batch_ids))
        _c = sqlite3.connect(str(Path(db_path) if db_path else SOP_DB))
        _row = _c.execute(
            f"SELECT max(substr(ticketed_at,1,10)) FROM wagon_shipments WHERE batch_id IN ({_ph})",
            used_batch_ids).fetchone()
        _c.close()
        dispatch_date = (_row[0] or "").strip() if _row else ""
    except Exception:
        dispatch_date = ""

    out_dir = Path(output_dir) if output_dir else _resolve_archive_dir(
        project_id, used_batch_ids[0], db_path=db_path, date_override=dispatch_date or None)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 文件名按船名(去掉"中唐发运"硬编码,全项目通用)+ 发运日
    _ships_label = "+".join(ships_used) or project_id
    filename = filename_override or f"{_ships_label}发运_{dispatch_date or '未知日'}_{total_rows}车.xlsx"
    filepath = out_dir / filename
    wb.save(str(filepath))

    return DispatchEventExcelResult(
        project_id=project_id, output_path=str(filepath),
        row_count=total_rows, wagon_count=len(total_wagons),
        filename=filename, release_batch_ids=used_batch_ids,
    )


if __name__ == "__main__":
    import sys
    sys.exit(main())
