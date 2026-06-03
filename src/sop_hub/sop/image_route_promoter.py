"""Image-side promoter: classified → matched_sop.

Background: text messages are routed by ``text_router.classify_text_message`` which
sets ``processing_status='matched_sop'`` and ``sop_project_id`` in one step. Image
messages currently get ``classification_label``, ``is_sop_msg=1``, ``sop_flow`` and
``sop_node`` populated by the extraction pipeline, but nothing advances them to
``matched_sop`` nor copies the inferred ``project`` field from the extraction JSON
to ``message_inbox.sop_project_id``. As a result, ``workflow_task_store``'s
``WHERE processing_status='matched_sop' AND is_sop_msg=1`` filter never picks them
up, and the create_release_batch / inspection chains never fire for image inputs.

This module closes that gap: it scans ``message_inbox`` for image rows stuck at
``classified`` with an extraction JSON, reads the inferred ``project`` field, and
promotes the row to ``matched_sop`` so the standard
``create_tasks_for_matched_messages`` flow can take over.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "sop_agent.db"


def _read_extraction_project(extraction_json_path: str) -> tuple[str | None, dict[str, Any] | None]:
    """Return (project, full_json) from an extraction JSON file, or (None, None) on failure."""
    if not extraction_json_path:
        return None, None
    p = Path(extraction_json_path)
    if not p.exists():
        return None, None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    if not isinstance(data, dict):
        return None, None
    # The VLM pipeline writes project under the top-level "project" key. Some older
    # payloads use "项目"; accept either, and require SOP authorisation.
    project = data.get("project") or data.get("项目")
    if data.get("_agent_sop_authorized") is False:
        return None, data
    return (project or None), data


def promote_classified_image_messages(
    db_path: str | Path | None = None,
    *,
    limit: int | None = None,
    only_message_inbox_id: int | None = None,
) -> dict[str, Any]:
    """Promote image-side classified messages to matched_sop.

    Selection criteria:
      - ``processing_status='classified'``
      - ``is_sop_msg=1``
      - ``sop_flow`` and ``sop_node`` are non-empty (set by classification pipeline)
      - ``extraction_json_path`` is non-empty and the file exists

    Project resolution: 若 ``sop_project_id`` 已有值,直接用它升 matched_sop;若为空,
    从 extraction JSON 的 ``project`` 字段推断(推断不到则 skip)。
    —— 2026-06-03 修:原来只处理空 project 的行,导致已带 project 的检装车图片(如
    inbox 270 chaoyang_steel)永远停在 classified、进不了流程。

    Returns: ``{promoted: int, skipped: int, errors: int, details: [...]}``.
    """
    db = Path(db_path) if db_path else _default_db_path()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    base_sql = (
        "SELECT id, message_id, group_name, sop_project_id, extraction_json_path, sop_flow, sop_node "
        "FROM message_inbox "
        "WHERE processing_status='classified' AND is_sop_msg=1 "
        "  AND COALESCE(sop_flow,'')<>'' AND COALESCE(sop_node,'')<>'' "
        "  AND COALESCE(extraction_json_path,'')<>''"
    )
    if only_message_inbox_id is not None:
        base_sql += " AND id = ?"
        params: tuple = (only_message_inbox_id,)
    else:
        params = ()
    base_sql += " ORDER BY id"
    if limit:
        base_sql += f" LIMIT {int(limit)}"
    rows = conn.execute(base_sql, params).fetchall()

    promoted = 0
    skipped = 0
    errors = 0
    details: list[dict[str, Any]] = []

    from sop_hub.models.project_sop import PROJECT_ID_ALIASES

    for row in rows:
        rid = row["id"]
        ext_path = row["extraction_json_path"]
        # 已有 project → 直接用;否则从 extraction JSON 推断
        project = (row["sop_project_id"] or "").strip()
        if not project:
            project, _payload = _read_extraction_project(ext_path)
        # extraction JSON 里常是中文显示名(如"朝阳钢铁铁矿发运项目"),归一到规范 id
        # (chaoyang_steel),否则 _resolve_task_type 认不出 → 落 generic_sop_task。
        project = PROJECT_ID_ALIASES.get(project, project)
        if not project:
            skipped += 1
            details.append(
                {"id": rid, "message_id": row["message_id"], "action": "skipped",
                 "reason": "no_project_in_extraction", "extraction_path": ext_path}
            )
            continue
        try:
            conn.execute(
                "UPDATE message_inbox "
                "SET sop_project_id=?, processing_status='matched_sop', updated_at=datetime('now') "
                "WHERE id=?",
                (project, rid),
            )
            conn.commit()
            promoted += 1
            details.append(
                {"id": rid, "message_id": row["message_id"], "action": "promoted",
                 "sop_project_id": project, "sop_flow": row["sop_flow"], "sop_node": row["sop_node"]}
            )
        except Exception as exc:
            errors += 1
            details.append({"id": rid, "message_id": row["message_id"], "action": "error",
                            "error": str(exc)})

    conn.close()
    return {"promoted": promoted, "skipped": skipped, "errors": errors, "details": details}


def repair_misclassified_ignored(
    db_path: str | Path | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Repair image rows misclassified as ``ignored`` when they should have been routed.

    The fallback monitoring_plan_matcher only matches against ``event.text`` for
    image-only messages (which have empty text), so image messages get tagged
    ``ignored`` even when their downstream extraction has a clear ``project``.
    This repair scans for ``processing_status='ignored'`` with a usable extraction
    JSON and project, and reattaches them to the SOP flow.
    """
    db = Path(db_path) if db_path else _default_db_path()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, message_id, group_name, extraction_json_path, classification_label "
        "FROM message_inbox "
        "WHERE processing_status='ignored' "
        "  AND COALESCE(extraction_json_path,'')<>'' "
        "  AND COALESCE(classification_label,'')<>'' "
        "ORDER BY id"
        + (f" LIMIT {int(limit)}" if limit else "")
    ).fetchall()

    repaired = 0
    skipped = 0
    details: list[dict[str, Any]] = []

    # Map known classification labels to (sop_flow, sop_node). Keep small and explicit;
    # broader routing should move to project-SOP-driven matching, not this repair.
    label_route = {
        "出港计划通知单": ("departure_flow", "create_release_batch"),
        "检装车通知单": ("inspection_flow", "extract_inspection_notice"),
    }

    for row in rows:
        rid = row["id"]
        label = row["classification_label"]
        if label not in label_route:
            skipped += 1
            details.append({"id": rid, "message_id": row["message_id"], "action": "skipped",
                            "reason": "label_not_in_route_map", "label": label})
            continue
        project, _ = _read_extraction_project(row["extraction_json_path"])
        if not project:
            skipped += 1
            details.append({"id": rid, "message_id": row["message_id"], "action": "skipped",
                            "reason": "no_project_in_extraction"})
            continue
        flow, node = label_route[label]
        conn.execute(
            "UPDATE message_inbox SET "
            "  is_sop_msg=1, sop_project_id=?, sop_flow=?, sop_node=?, "
            "  processing_status='matched_sop', updated_at=datetime('now') "
            "WHERE id=?",
            (project, flow, node, rid),
        )
        conn.commit()
        repaired += 1
        details.append({"id": rid, "message_id": row["message_id"], "action": "repaired",
                        "sop_project_id": project, "sop_flow": flow, "sop_node": node, "label": label})

    conn.close()
    return {"repaired": repaired, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Promote classified image messages to matched_sop")
    parser.add_argument("--db", help="path to sop_agent.db (default: data/sop_agent.db)")
    parser.add_argument("--limit", type=int, help="max rows to process")
    parser.add_argument("--repair-ignored", action="store_true",
                        help="also repair image rows wrongly marked as ignored")
    parser.add_argument("--only-id", type=int, help="only promote a specific message_inbox.id")
    args = parser.parse_args()

    out = promote_classified_image_messages(
        db_path=args.db, limit=args.limit, only_message_inbox_id=args.only_id,
    )
    print(json.dumps({"promote": out}, ensure_ascii=False, indent=2))
    if args.repair_ignored:
        rep = repair_misclassified_ignored(db_path=args.db, limit=args.limit)
        print(json.dumps({"repair": rep}, ensure_ascii=False, indent=2))
