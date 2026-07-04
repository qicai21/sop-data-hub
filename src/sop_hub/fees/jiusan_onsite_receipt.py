from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, JpegImagePlugin  # noqa: F401


PROJECT = "jiusan"
LOT = "lot02"
PROJECT_NAME = "九三大豆铁运项目(散粮车)"
TEAM_NAME = "优瑞达物流"
DEFAULT_FONT = Path("/System/Library/Fonts/STHeiti Medium.ttc")

# 80mm receipt paper at common 203dpi thermal-printer density.
RECEIPT_DPI = 203
PAGE_WIDTH_CM = 8.0
PAGE_WIDTH_PX = round(PAGE_WIDTH_CM / 2.54 * RECEIPT_DPI)
FONT_SIZE_PT = 10.5  # Word 五号字
FONT_SIZE_PX = round(FONT_SIZE_PT / 72 * RECEIPT_DPI)
LINE_HEIGHT_PX = round(FONT_SIZE_PX * 1.48)
MARGIN_LEFT_PX = 34
MARGIN_TOP_PX = 28
MARGIN_BOTTOM_PX = 14


@dataclass(frozen=True)
class BulkTrainGroup:
    ship_name: str
    notice_date: str
    track: str
    total_cars: int
    rows: list[dict]

    @property
    def car_count(self) -> int:
        return len(self.rows)


def safe_filename_part(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\\s]+", "_", str(value or "").strip())
    return cleaned.strip("_") or "未命名"


def display_rows(rows: list[dict]) -> list[dict | None]:
    """Show first 2 and last 2 car rows; omit the middle on receipts."""
    if len(rows) <= 4:
        return list(rows)
    return [*rows[:2], None, *rows[-2:]]


def fetch_bulk_train_groups(
    conn: sqlite3.Connection,
    *,
    ship_name: str = "",
    notice_date: str = "",
    track: str = "",
    project: str = PROJECT,
    lot: str = LOT,
) -> list[BulkTrainGroup]:
    sql = """
        SELECT notice_date, track, ship_name, lot, total_cars, COUNT(*) AS cars
        FROM bulk_loading_notice_wagon
        WHERE project=? AND lot=?
    """
    params: list[str] = [project, lot]
    if ship_name:
        sql += " AND ship_name=?"
        params.append(ship_name)
    if notice_date:
        sql += " AND notice_date=?"
        params.append(notice_date)
    if track:
        sql += " AND track=?"
        params.append(track)
    sql += """
        GROUP BY notice_date, track, ship_name, lot, total_cars
        ORDER BY notice_date, ship_name, track
    """
    groups = conn.execute(sql, tuple(params)).fetchall()
    out: list[BulkTrainGroup] = []
    for g in groups:
        rows = conn.execute(
            """
            SELECT notice_date, track, ship_name, total_cars, car_seq, car_no, car_model, ydid
            FROM bulk_loading_notice_wagon
            WHERE project=? AND lot=? AND ship_name=? AND notice_date=? AND track=?
            ORDER BY car_seq, car_no
            """,
            (project, lot, g["ship_name"], g["notice_date"], g["track"]),
        ).fetchall()
        out.append(
            BulkTrainGroup(
                ship_name=str(g["ship_name"]),
                notice_date=str(g["notice_date"]),
                track=str(g["track"]),
                total_cars=int(g["total_cars"] or g["cars"] or 0),
                rows=[dict(r) for r in rows],
            )
        )
    return out


def receipt_lines(group: BulkTrainGroup) -> list[tuple[str, str]]:
    """Return (text, align) lines for one receipt page."""
    lines: list[tuple[str, str]] = [
        ("锦州港装卸辅助作业现场确认单", "center"),
        ("-" * 25, "left"),
        (f"发生日期: {group.notice_date}", "left"),
        (f"项目名称: {PROJECT_NAME}", "left"),
        (f"船名/批次: {group.ship_name} / {LOT}", "left"),
        (f"道线场地: {group.track}", "left"),
        (f"数量: {group.car_count} 车", "left"),
        ("-" * 25, "left"),
        ("作业项目:", "left"),
        ("[ ] #19 散粮车装卸辅助作业服务", "left"),
        ("    特殊平整、清扫归集及皮带机配合", "left"),
        ("[ ] #20 散粮车车体检查及作业现场检查服务", "left"),
        ("-" * 25, "left"),
        ("车号/车型:", "left"),
    ]
    for row in display_rows(group.rows):
        if row is None:
            lines.append(("... 中间车号略 ...", "center"))
        else:
            seq = int(row.get("car_seq") or 0)
            car_no = str(row.get("car_no") or "")
            model = str(row.get("car_model") or "")
            lines.append((f"{seq:02d} {car_no} {model}", "left"))
    lines.extend(
        [
            ("-" * 25, "left"),
            (f"作业单位/班组: {TEAM_NAME}", "left"),
            ("", "left"),
            ("委托方(签字):", "left"),
            ("", "left"),
            ("", "left"),
            ("", "left"),
            ("作业方(签字):", "left"),
            ("", "left"),
            ("", "left"),
            ("", "left"),
            ("备注:", "left"),
            ("", "left"),
            ("-" * 25, "left"),
        ]
    )
    return lines


def render_receipt_page(
    lines: Iterable[tuple[str, str]],
    *,
    font_path: Path = DEFAULT_FONT,
) -> Image.Image:
    font = ImageFont.truetype(str(font_path), FONT_SIZE_PX)
    line_list = list(lines)
    height = MARGIN_TOP_PX + len(line_list) * LINE_HEIGHT_PX + MARGIN_BOTTOM_PX
    image = Image.new("RGB", (PAGE_WIDTH_PX, height), "white")
    draw = ImageDraw.Draw(image)
    y = MARGIN_TOP_PX
    for text, align in line_list:
        if text:
            if align == "center":
                bbox = draw.textbbox((0, 0), text, font=font)
                x = max(0, (PAGE_WIDTH_PX - (bbox[2] - bbox[0])) // 2)
            else:
                x = MARGIN_LEFT_PX
            draw.text((x, y), text, font=font, fill="black")
        y += LINE_HEIGHT_PX
    return image


def render_groups_to_pdf(
    groups: list[BulkTrainGroup],
    output_path: Path,
    *,
    font_path: Path = DEFAULT_FONT,
    dpi: int = RECEIPT_DPI,
) -> Path:
    if not groups:
        raise ValueError("no bulk train groups to render")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pages = [render_receipt_page(receipt_lines(g), font_path=font_path) for g in groups]
    if output_path.exists():
        output_path.unlink()
    pages[0].save(output_path, save_all=True, append_images=pages[1:], resolution=dpi)
    return output_path


def default_output_path(groups: list[BulkTrainGroup], output_dir: Path) -> Path:
    if len(groups) == 1:
        g = groups[0]
        name = (
            f"{g.notice_date}_{safe_filename_part(g.ship_name)}_"
            f"{safe_filename_part(g.track)}_{g.car_count}车_辅助作业现场确认单.pdf"
        )
    else:
        dates = sorted({g.notice_date for g in groups})
        start = dates[0]
        end = dates[-1]
        period = start if start == end else f"{start}_至_{end}"
        name = f"{period}_九三散粮_{len(groups)}列_辅助作业现场确认单.pdf"
    return output_dir / name


__all__ = [
    "BulkTrainGroup",
    "default_output_path",
    "display_rows",
    "fetch_bulk_train_groups",
    "receipt_lines",
    "render_groups_to_pdf",
    "render_receipt_page",
]
