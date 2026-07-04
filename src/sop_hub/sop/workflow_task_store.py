"""
R68: workflow_task DB table — executable tasks from matched_sop messages.

Table: workflow_task_db in sop_agent.db.
Provides: schema, DAO, and CLI for creating/looking up tasks
from message_inbox matched_sop records.

Kept separate from sop_hub.sop.workflow_task (which is the planner/model layer)
to avoid conflating the in-memory dataclass WorkflowTask with the DB table.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _get_db_path(db_path: str | Path | None = None) -> Path:
    if db_path:
        return Path(db_path)
    import os
    env = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "data" / "sop_agent.db"


def _now_iso() -> str:
    from sop_hub.utils.time import now_iso_beijing
    return now_iso_beijing()


WORKFLOW_TASK_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_task_db (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    message_inbox_id    INTEGER NOT NULL,
    message_id          TEXT    NOT NULL,
    project_id          TEXT    NOT NULL,
    flow_name           TEXT    NOT NULL,
    node_name           TEXT    NOT NULL,
    task_type           TEXT    NOT NULL,
    task_status         TEXT    NOT NULL DEFAULT 'pending',
    input_json          TEXT,
    output_json         TEXT,
    error_message       TEXT,
    retry_count         INTEGER DEFAULT 0,
    created_at          TEXT,
    updated_at          TEXT,
    last_run_at         TEXT,
    UNIQUE(message_inbox_id, task_type)
);
"""

WORKFLOW_TASK_DB_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_wt_db_project_id ON workflow_task_db(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_wt_db_flow_name ON workflow_task_db(flow_name);",
    "CREATE INDEX IF NOT EXISTS idx_wt_db_task_status ON workflow_task_db(task_status);",
    "CREATE INDEX IF NOT EXISTS idx_wt_db_message_id ON workflow_task_db(message_id);",
    "CREATE INDEX IF NOT EXISTS idx_wt_db_created_at ON workflow_task_db(created_at);",
]


def ensure_workflow_task_db_schema(db_path: str | Path | None = None) -> None:
    db = _get_db_path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(WORKFLOW_TASK_DB_SCHEMA)
    for idx_sql in WORKFLOW_TASK_DB_INDEXES:
        try:
            conn.execute(idx_sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


# 2026-06-07 #121:yaml 项目层"无此 flow"守门
# 项目 yaml 写 has_inspection_slip: false 但消息分类时被误归到这个项目 +
# inspection_notice_flow → 老路由会建出 generic_sop_task 孤儿(无 executor)→
# 永远 pending。返回这个 sentinel 让上游 create_task 跳过建任务。
SKIP_TASK_TYPE_SENTINEL = "skipped_by_yaml_no_such_flow"


def _project_has_inspection_slip(project_id: str) -> bool:
    """Read project yaml `project_meta.has_inspection_slip`(default True)。"""
    if not project_id:
        return True
    try:
        import yaml as _yaml
        from sop_hub.sop.departure_excel import _find_yaml_for_project
        yp = _find_yaml_for_project(project_id)
        raw = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    except Exception:
        return True  # 找不到 yaml 兜底放行(老行为)
    pm = raw.get("project_meta") or {}
    v = pm.get("has_inspection_slip")
    if isinstance(v, bool):
        return v
    return True  # 字段缺省视为有(老行为)


def _project_has_flow(project_id: str, flow_name: str) -> bool:
    """yaml flows.<flow_name> 是否声明 — 项目没声明的 flow 直接拒绝建 task。
    #129 (2026-06-08):防朝阳/乌兰浩特/中唐 text 路由到 detect_departure_message
    落 generic_sop_task 永挂(只 jilin yaml 有 departure_flow)。
    """
    if not project_id or not flow_name:
        return False
    try:
        import yaml as _yaml
        from sop_hub.sop.departure_excel import _find_yaml_for_project
        yp = _find_yaml_for_project(project_id)
        raw = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    except Exception:
        return True  # 找不到 yaml 兜底放行(避免误杀)
    return flow_name in (raw.get("flows") or {})


def _resolve_task_type(project_id: str, flow_name: str, node_name: str) -> str:
    # #121 守门:项目 yaml 显式声明无检车单时,拒绝建 inspection_*_flow task
    if (flow_name in ("inspection_flow", "inspection_notice_flow")
            and not _project_has_inspection_slip(project_id)):
        return SKIP_TASK_TYPE_SENTINEL

    # #129 守门:项目 yaml 没声明该 flow → 拒绝(防 generic_sop_task 永挂孤儿)
    # 只对会落到 generic_sop_task 的 flow 守门,已知有 chain 的 flow 放行
    _KNOWN_HANDLED_FLOWS = {
        "freight_detail_flow",                    # 通用 enrichment
        "inspection_flow", "inspection_notice_flow",  # 朝阳/中唐
        "inspection_text_trigger_flow",           # #143 文本触发器(多项目,project 可空)
    }
    if (flow_name not in _KNOWN_HANDLED_FLOWS
            and not _project_has_flow(project_id, flow_name)):
        return SKIP_TASK_TYPE_SENTINEL

    # #143:复合/单船检装车文本触发器。task 创建时按段扇出(见
    # create_task_from_message_inbox),这里只标 task_type。
    if (flow_name == "inspection_text_trigger_flow"
            and node_name == "inspection_text_trigger"):
        return "inspection_text_trigger"

    if (project_id == "jilin_jingang_jinzhou"
            and flow_name == "departure_flow"
            and node_name == "detect_departure_message"):
        return "jljg_departure_text_chain"
    if (project_id == "jiusan"
            and flow_name == "departure_flow"
            and node_name == "detect_departure_message"):
        return "jiusan_departure_text_reconcile"
    # R78: 朝阳检装车通知单 → 全链(match → 95306 → wagons → excel)
    # 节点名:live_service 分类管线实际产出 create_inspection_candidate
    # (run_live_service.py),repair 路径用 extract_inspection_notice —— 两个都收,
    # 否则生产者/消费者节点名不一致会把检装车链卡死(2026-06-03 宝腾海漏触发根因)。
    if (project_id == "chaoyang_steel"
            and flow_name in ("inspection_flow", "inspection_notice_flow")
            and node_name in ("create_inspection_candidate",
                              "detect_inspection_notice",
                              "extract_inspection_notice",
                              "extract_inspection_notice_fields")):
        return "chaoyang_inspection_chain"
    # 中唐特钢复用同一套检装车链(汐子站铁矿粉),只是没有鞍钢门户上传段。
    # 共用 _execute_chaoyang_inspection_chain 执行器,内部 project_id 门控。
    if (project_id == "zhongtang_special_steel"
            and flow_name in ("inspection_flow", "inspection_notice_flow")
            and node_name in ("create_inspection_candidate",
                              "detect_inspection_notice",
                              "extract_inspection_notice",
                              "extract_inspection_notice_fields")):
        return "zhongtang_inspection_chain"
    if node_name == "create_release_batch":
        return "create_release_batch"
    if project_id == "chaoyang_steel" and flow_name == "dispatch_flow":
        return "chaoyang_dispatch_context"
    if flow_name == "freight_detail_flow":
        return "freight_detail_enrichment"
    return "generic_sop_task"


def _build_input_json(row: dict[str, Any]) -> dict[str, Any]:
    inp: dict[str, Any] = {
        "message_inbox_id": row.get("id"),
        "message_id": row.get("message_id"),
        "group_name": row.get("group_name"),
        "received_datetime": row.get("received_datetime"),
        "text_content": row.get("text_content"),
        "sop_project_id": row.get("sop_project_id"),
        "sop_flow": row.get("sop_flow"),
        "sop_node": row.get("sop_node"),
        "summary": row.get("summary"),
        "media_status": row.get("media_status"),
        "source_file": row.get("source_file"),
        "extraction_json_path": row.get("extraction_json_path"),
        "raw_standard_image_path": row.get("raw_standard_image_path"),
        "classification_label": row.get("classification_label"),
        "business_archive_image_path": row.get("business_archive_image_path"),
        "business_archive_json_path": row.get("business_archive_json_path"),
    }
    return {k: v for k, v in inp.items() if v is not None}


def create_task_from_message_inbox(
    message_inbox_id: int,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    db = _get_db_path(db_path)
    ensure_workflow_task_db_schema(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    row = conn.execute("SELECT * FROM message_inbox WHERE id = ?", (message_inbox_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"message_inbox_id {message_inbox_id} not found"}

    row_dict = dict(row)
    project_id = row_dict.get("sop_project_id") or ""
    flow_name = row_dict.get("sop_flow") or ""
    node_name = row_dict.get("sop_node") or ""
    task_type = _resolve_task_type(project_id, flow_name, node_name)
    # #121:守门标记 — 项目 yaml 无此 flow,直接跳过不建任务
    if task_type == SKIP_TASK_TYPE_SENTINEL:
        conn.close()
        return {
            "action": "skipped", "reason": "yaml_has_no_such_flow",
            "project_id": project_id, "flow_name": flow_name,
            "node_name": node_name, "message_inbox_id": message_inbox_id,
        }
    input_json = _build_input_json(row_dict)
    now = _now_iso()

    # #143:检装车文本触发器 → 按段扇出成 N 个 task(复合多船一条文本拆 N 个),
    # 每个 task 自带 {project, ship, dest, expected_count}。各段与检装车通知单
    # 候选 rendezvous(执行器 _execute_inspection_text_trigger)。文本只提供触发
    # + 预期车数;车号顺序 / lot 归属仍归通知单。
    if task_type == "inspection_text_trigger":
        from sop_hub.sop.text_router import extract_inspection_text_triggers
        segs = extract_inspection_text_triggers(row_dict.get("text_content") or "")
        if not segs:
            conn.close()
            return {
                "action": "skipped", "reason": "no_inspection_segment",
                "message_inbox_id": message_inbox_id, "task_type": task_type,
            }
        created, skipped = [], []
        for seg in segs:
            # UNIQUE(message_inbox_id, task_type) 要求每船 task_type 唯一 →
            # 后缀船名;dispatch 侧按 "inspection_text_trigger" 前缀匹配。
            seg_task_type = f"{task_type}:{seg['ship']}"
            dup = conn.execute(
                "SELECT id, task_status FROM workflow_task_db "
                "WHERE message_inbox_id = ? AND task_type = ?",
                (message_inbox_id, seg_task_type),
            ).fetchone()
            if dup:
                skipped.append({"ship": seg["ship"], "id": dup["id"],
                                "task_status": dup["task_status"]})
                continue
            seg_input = {
                **input_json,
                "trigger_project": seg["project_id"],
                "trigger_ship": seg["ship"],
                "trigger_dest": seg["destination"],
                "trigger_expected_count": seg["expected_count"],
                "trigger_segment": seg["segment"],
            }
            conn.execute(
                """INSERT INTO workflow_task_db
                   (message_inbox_id, message_id, project_id, flow_name, node_name,
                    task_type, task_status, input_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (message_inbox_id, row_dict.get("message_id", ""),
                 seg["project_id"], flow_name, node_name, seg_task_type,
                 json.dumps(seg_input, ensure_ascii=False), now, now),
            )
            created.append({"ship": seg["ship"],
                            "id": conn.execute("SELECT last_insert_rowid()").fetchone()[0],
                            "expected_count": seg["expected_count"]})
        conn.commit()
        conn.close()
        return {
            "action": "created_multi" if created else "skipped",
            "reason": "all_duplicate" if not created else "",
            "message_inbox_id": message_inbox_id, "task_type": task_type,
            "ids": [c["id"] for c in created],
            "created": created, "skipped": skipped,
        }

    existing = conn.execute(
        "SELECT id, task_status, created_at FROM workflow_task_db "
        "WHERE message_inbox_id = ? AND task_type = ?",
        (message_inbox_id, task_type),
    ).fetchone()

    if existing:
        conn.close()
        return {
            "action": "skipped", "reason": "duplicate",
            "id": existing["id"], "message_inbox_id": message_inbox_id,
            "task_type": task_type, "task_status": existing["task_status"],
        }

    conn.execute(
        """INSERT INTO workflow_task_db
           (message_inbox_id, message_id, project_id, flow_name, node_name,
            task_type, task_status, input_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (message_inbox_id, row_dict.get("message_id", ""), project_id,
         flow_name, node_name, task_type,
         json.dumps(input_json, ensure_ascii=False), now, now),
    )
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {
        "action": "created", "id": new_id,
        "message_inbox_id": message_inbox_id,
        "message_id": row_dict.get("message_id"),
        "task_type": task_type, "task_status": "pending",
    }


def create_tasks_for_matched_messages(
    *, db_path: str | Path | None = None, limit: int | None = None,
) -> dict[str, Any]:
    db = _get_db_path(db_path)
    ensure_workflow_task_db_schema(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    query = (
        "SELECT id FROM message_inbox "
        "WHERE processing_status = 'matched_sop' AND is_sop_msg = 1 ORDER BY id"
    )
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()
    conn.close()

    created, skipped, errors = [], [], []
    for row in rows:
        mid = row["id"]
        try:
            result = create_task_from_message_inbox(mid, db_path=db)
            if result.get("action") == "created":
                created.append(result)
            else:
                skipped.append(result)
        except Exception as exc:
            errors.append({"message_inbox_id": mid, "error": str(exc)})
    return {
        "created_count": len(created), "skipped_count": len(skipped),
        "error_count": len(errors), "created": created, "skipped": skipped,
        "errors": errors,
    }


def get_workflow_task_db_by_message_id(
    message_id: str, *, db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    db = _get_db_path(db_path)
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM workflow_task_db WHERE message_id = ? ORDER BY id",
        (message_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_workflow_task_db(
    *, task_status: str | None = None, limit: int = 20,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    db = _get_db_path(db_path)
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    if task_status:
        rows = conn.execute(
            "SELECT id, message_id, project_id, flow_name, node_name, "
            "task_type, task_status, created_at "
            "FROM workflow_task_db WHERE task_status = ? ORDER BY id DESC LIMIT ?",
            (task_status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, message_id, project_id, flow_name, node_name, "
            "task_type, task_status, created_at "
            "FROM workflow_task_db ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── CLI ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="workflow_task DB management CLI")
    p.add_argument("--init-db", action="store_true")
    p.add_argument("--create-for-matched", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--get-message", type=str)
    p.add_argument("--list", action="store_true")
    p.add_argument("--status", type=str)
    p.add_argument("--db", type=str, default=None)

    args = p.parse_args()
    _db = Path(args.db) if args.db else None

    if args.init_db:
        ensure_workflow_task_db_schema(_db)
        print(json.dumps({"action": "init_db", "ok": True}, ensure_ascii=False))
    elif args.create_for_matched:
        result = create_tasks_for_matched_messages(db_path=_db, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.get_message:
        tasks = get_workflow_task_db_by_message_id(args.get_message, db_path=_db)
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
    elif args.list:
        tasks = list_workflow_task_db(task_status=args.status, limit=args.limit or 20, db_path=_db)
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
    else:
        p.print_help()
