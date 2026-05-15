from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from html import escape
import json
from pathlib import Path
import sqlite3
from typing import Any


STATUS_LABELS = {
    "in_progress": "发运中",
    "active": "发运中",
    "completed": "已发完",
    "suspended": "暂停",
    "cancelled": "不发运",
    "pending": "待人工确认",
    "ambiguous": "待人工确认",
}

MANUAL_CANDIDATE_STATUSES = {"pending", "ambiguous"}


def render_dispatch_board(
    *,
    business_db_path: str | Path,
    output_path: str | Path,
    rail_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Render a static dispatch board from the business DB and optional read-only 95306 linkage DB."""
    business_db_path = Path(business_db_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(business_db_path) as business_db:
        business_db.row_factory = sqlite3.Row
        release_rows = _fetch_release_batches(business_db)
        candidate_summary = _fetch_candidate_summary(business_db)
        audit_paths = _fetch_audit_paths(business_db)

    formal_summary: dict[str, dict[str, Any]] = {}
    if rail_db_path:
        formal_summary = _fetch_formal_summary_read_only(Path(rail_db_path))

    html = _build_html(
        business_db_path=business_db_path,
        rail_db_path=Path(rail_db_path) if rail_db_path else None,
        release_rows=release_rows,
        candidate_summary=candidate_summary,
        formal_summary=formal_summary,
        audit_paths=audit_paths,
    )
    output_path.write_text(html, encoding="utf-8")
    return {
        "output_path": str(output_path),
        "release_batch_count": len(release_rows),
        "active_release_batch_count": sum(1 for row in release_rows if row.get("dispatch_status") == "in_progress"),
        "candidate_count": sum(item.get("matched_candidate_count", 0) for item in candidate_summary.values()),
        "manual_pending_candidate_count": _manual_pending_count(candidate_summary),
        "formal_match_count": sum(item.get("formal_match_count", 0) for item in formal_summary.values()),
        "formal_weight": sum(float(item.get("formal_weight") or 0) for item in formal_summary.values()),
    }


def _fetch_release_batches(db: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(db, "release_batches"):
        return []
    rows = db.execute(
        """
        SELECT id, project, ship_name, destination_station, batch_sequence,
               batch_quantity, batch_date, cargo_name, cargo_product_name,
               dispatch_status, dispatch_status_note, source_file_name,
               source_json, updated_at, plan_id, order_id, contract_no
        FROM release_batches
        ORDER BY
          CASE dispatch_status
            WHEN 'in_progress' THEN 1
            WHEN 'suspended' THEN 2
            WHEN 'completed' THEN 3
            WHEN 'cancelled' THEN 4
            ELSE 9
          END,
          COALESCE(batch_date, notice_date, updated_at) DESC,
          ship_name ASC,
          batch_sequence ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_candidate_summary(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = defaultdict(_empty_candidate_summary)
    if not _table_exists(db, "inspection_ingestion_candidates"):
        return {}
    grouped = db.execute(
        """
        SELECT release_batch_id, status, COUNT(*) AS count, COALESCE(SUM(wagon_count), 0) AS wagon_count
        FROM inspection_ingestion_candidates
        GROUP BY release_batch_id, status
        """
    ).fetchall()
    for row in grouped:
        key = row["release_batch_id"] or "__unassigned__"
        item = summary[key]
        status = row["status"] or ""
        count = int(row["count"] or 0)
        item["by_status"][status] = item["by_status"].get(status, 0) + count
        item["wagon_count"] += int(row["wagon_count"] or 0)
        if status == "candidate":
            item["matched_candidate_count"] += count
        if status in MANUAL_CANDIDATE_STATUSES:
            item["manual_pending_candidate_count"] += count
    return dict(summary)


def _empty_candidate_summary() -> dict[str, Any]:
    return {
        "matched_candidate_count": 0,
        "manual_pending_candidate_count": 0,
        "wagon_count": 0,
        "by_status": {},
    }


def _manual_pending_count(summary: dict[str, dict[str, Any]]) -> int:
    return sum(int(item.get("manual_pending_candidate_count") or 0) for item in summary.values())


def _fetch_audit_paths(db: sqlite3.Connection) -> dict[str, dict[str, str]]:
    if not _table_exists(db, "image_ingestion_audit"):
        return {}
    rows = db.execute(
        """
        SELECT raw_image_path, classified_image_path, extraction_json_path, project_archive_paths
        FROM image_ingestion_audit
        ORDER BY created_at DESC
        """
    ).fetchall()
    paths: dict[str, dict[str, str]] = {}
    for row in rows:
        source_candidates = [row["classified_image_path"], row["raw_image_path"]]
        keys = {Path(str(item)).name for item in source_candidates if item}
        keys.update({Path(str(item)).stem for item in source_candidates if item})
        archive_paths = _json_object(row["project_archive_paths"])
        status_path = str(archive_paths.get("status_path") or archive_paths.get("status") or "")
        path_info = {
            "raw_image_path": str(row["raw_image_path"] or ""),
            "classified_image_path": str(row["classified_image_path"] or ""),
            "json_path": str(row["extraction_json_path"] or ""),
            "status_path": status_path,
        }
        for key in keys:
            paths.setdefault(key, path_info)
    return paths


def _fetch_formal_summary_read_only(rail_db_path: Path) -> dict[str, dict[str, Any]]:
    if not rail_db_path.exists():
        return {}
    uri = f"file:{rail_db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        if not _table_exists(db, "shipment_release_batch_matches"):
            return {}
        rows = db.execute(
            """
            SELECT release_batch_id,
                   COUNT(*) AS formal_match_count,
                   COALESCE(SUM(COALESCE(planned_weight, marked_weight, 0)), 0) AS formal_weight
            FROM shipment_release_batch_matches
            WHERE release_batch_id IS NOT NULL AND release_batch_id <> ''
            GROUP BY release_batch_id
            """
        ).fetchall()
    return {
        row["release_batch_id"]: {
            "formal_match_count": int(row["formal_match_count"] or 0),
            "formal_weight": float(row["formal_weight"] or 0),
        }
        for row in rows
    }


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_paths(row: dict[str, Any], audit_paths: dict[str, dict[str, str]]) -> dict[str, str]:
    source_json = _json_object(row.get("source_json"))
    source_file = str(row.get("source_file_name") or "")
    lookup_keys = [Path(source_file).name, Path(source_file).stem] if source_file else []
    audit_info: dict[str, str] = {}
    for key in lookup_keys:
        if key in audit_paths:
            audit_info = audit_paths[key]
            break
    image_path = str(
        source_json.get("image_path")
        or source_json.get("raw_image_path")
        or source_json.get("classified_image_path")
        or audit_info.get("classified_image_path")
        or audit_info.get("raw_image_path")
        or source_file
        or ""
    )
    json_path = str(
        source_json.get("json_path")
        or source_json.get("extraction_json_path")
        or audit_info.get("json_path")
        or ""
    )
    status_path = str(
        source_json.get("status_path")
        or source_json.get("ops_data_hub_status_path")
        or audit_info.get("status_path")
        or ""
    )
    return {
        "image_path": image_path or "待补充",
        "json_path": json_path or "待补充",
        "status_path": status_path or "待补充",
    }


def _build_html(
    *,
    business_db_path: Path,
    rail_db_path: Path | None,
    release_rows: list[dict[str, Any]],
    candidate_summary: dict[str, dict[str, Any]],
    formal_summary: dict[str, dict[str, Any]],
    audit_paths: dict[str, dict[str, str]],
) -> str:
    active_count = sum(1 for row in release_rows if row.get("dispatch_status") == "in_progress")
    matched_candidate_count = sum(item.get("matched_candidate_count", 0) for item in candidate_summary.values())
    manual_pending_count = _manual_pending_count(candidate_summary)
    formal_count = sum(item.get("formal_match_count", 0) for item in formal_summary.values())
    formal_weight = sum(float(item.get("formal_weight") or 0) for item in formal_summary.values())
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows_html = []
    for row in release_rows:
        release_id = str(row.get("id") or "")
        candidates = candidate_summary.get(release_id, _empty_candidate_summary())
        formal = formal_summary.get(release_id, {"formal_match_count": 0, "formal_weight": 0.0})
        paths = _source_paths(row, audit_paths)
        status = str(row.get("dispatch_status") or "")
        status_label = STATUS_LABELS.get(status, status or "待人工确认")
        remaining = _remaining_text(row.get("batch_quantity"), formal.get("formal_weight"))
        rows_html.append(
            "<tr>"
            f"<td class='mono'>{_h(release_id)}</td>"
            f"<td>{_h(row.get('project'))}</td>"
            f"<td>{_h(row.get('ship_name'))}</td>"
            f"<td>{_h(row.get('destination_station'))}</td>"
            f"<td>{_h(row.get('batch_sequence'))}</td>"
            f"<td>{_fmt_num(row.get('batch_quantity'))}</td>"
            f"<td>{_h(row.get('batch_date'))}</td>"
            f"<td>{_h(row.get('cargo_product_name') or row.get('cargo_name'))}</td>"
            f"<td>{_h(status_label)}</td>"
            f"<td>{candidates.get('matched_candidate_count', 0)}</td>"
            f"<td>{candidates.get('manual_pending_candidate_count', 0)}</td>"
            f"<td>{formal.get('formal_match_count', 0)}</td>"
            f"<td>{_fmt_num(formal.get('formal_weight'))}</td>"
            f"<td>{_h(remaining)}</td>"
            f"<td class='path'>{_h(paths['image_path'])}</td>"
            f"<td class='path'>{_h(paths['json_path'])}</td>"
            f"<td class='path'>{_h(paths['status_path'])}</td>"
            "</tr>"
        )
    if not rows_html:
        rows_html.append("<tr><td colspan='17' class='empty'>暂无 release_batch 数据</td></tr>")

    unassigned = candidate_summary.get("__unassigned__", _empty_candidate_summary())
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>放货 / 发运 / 图片识别 / 匹配入库看板</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 24px; color: #1f2937; background: #f8fafc; }}
h1 {{ margin-bottom: 4px; }}
.meta {{ color: #64748b; margin-bottom: 20px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 18px 0; }}
.card {{ background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; box-shadow: 0 1px 2px rgba(15,23,42,.05); }}
.card .label {{ color: #64748b; font-size: 13px; }}
.card .value {{ font-size: 28px; font-weight: 700; margin-top: 6px; }}
table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #e2e8f0; }}
th, td {{ border: 1px solid #e2e8f0; padding: 8px; vertical-align: top; font-size: 13px; }}
th {{ background: #e0f2fe; position: sticky; top: 0; }}
.path {{ max-width: 260px; word-break: break-all; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
.empty {{ text-align: center; color: #64748b; padding: 24px; }}
.section-title {{ margin-top: 24px; }}
</style>
</head>
<body>
<h1>放货记录 / 发运记录 / 图片下载识别 / 匹配入库看板</h1>
<div class="meta">生成时间：{_h(generated_at)}；业务库：{_h(str(business_db_path))}；95306正式匹配库（只读）：{_h(str(rail_db_path) if rail_db_path else '未配置')}</div>
<div class="cards">
  <div class="card"><div class="label">当前发运中批次数</div><div class="value">{active_count}</div></div>
  <div class="card"><div class="label">release_batch 总数</div><div class="value">{len(release_rows)}</div></div>
  <div class="card"><div class="label">已匹配检装车候选数 candidate</div><div class="value">{matched_candidate_count}</div></div>
  <div class="card"><div class="label">待人工匹配候选数</div><div class="value">{manual_pending_count}</div></div>
  <div class="card"><div class="label">未分配待人工候选数</div><div class="value">{unassigned.get('manual_pending_candidate_count', 0)}</div></div>
  <div class="card"><div class="label">已正式入库车数</div><div class="value">{formal_count}</div></div>
  <div class="card"><div class="label">已正式入库重量</div><div class="value">{_fmt_num(formal_weight)}</div></div>
</div>
<h2 class="section-title">正式匹配汇总 / release_batch 明细</h2>
<table>
<thead><tr>
<th>release_batch_id</th><th>项目</th><th>船名</th><th>到站</th><th>lot</th><th>计划吨数</th><th>批次日期</th><th>货物品名</th><th>当前状态</th><th>已匹配候选数</th><th>待人工候选数</th><th>已正式入库车数</th><th>已正式入库重量</th><th>理论剩余货量/车数</th><th>原始图片路径</th><th>JSON 路径</th><th>状态文件路径</th>
</tr></thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>
</body>
</html>
"""


def _remaining_text(batch_quantity: Any, formal_weight: Any) -> str:
    if batch_quantity is None or formal_weight is None:
        return "待计算"
    try:
        return _fmt_num(float(batch_quantity) - float(formal_weight))
    except Exception:
        return "待计算"


def _fmt_num(value: Any) -> str:
    if value is None or value == "":
        return "待计算"
    try:
        number = float(value)
    except Exception:
        return _h(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _h(value: Any) -> str:
    if value is None or value == "":
        return ""
    return escape(str(value), quote=True)
