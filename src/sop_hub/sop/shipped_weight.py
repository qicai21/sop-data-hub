"""SOP-driven shipped-weight aggregation.

For a given release_batch:
  1. Read the project's yaml SOP (config/project_sops/<project>.yaml).
  2. Extract ``project_meta.shipped_weight_rule.per_wagon`` (a DSL expression).
  3. Wrap it in a sum_over and evaluate against the batch's wagon_shipments.
  4. Persist:
     - ``release_batches.shipped_weight_tons``       — total resolved
     - ``release_batches.remaining_weight_tons``     — planned − shipped
     - ``release_batches.unresolved_wagon_count``    — wagons that couldn't be calc'd
     - ``release_batches.shipped_weight_last_computed_at``
     - per-wagon: ``wagon_shipments.computed_loading_weight``,
                  ``wagon_shipments.weight_rule_basis`` (short audit string)

Called either:
  • after create_wagon_shipments writes new rows for a batch
  • from a CLI / one-shot script (backfill, recompute-all)
  • on demand from dashboard refresh
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from sop_hub.calc import evaluate

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO_ROOT / "data" / "sop_agent.db"
DEFAULT_SOP_DIR = REPO_ROOT / "config" / "project_sops"


def _open_conn(db_path: Path | str | None) -> sqlite3.Connection:
    db = Path(db_path) if db_path else DEFAULT_DB
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _scan_project_yamls(sop_dir: Path) -> dict[str, Path]:
    """Build {project_id: yaml_path} by scanning all *.yaml in sop_dir.

    Each yaml's own ``project_id:`` field is the source of truth — there is
    no naming convention between filename and project_id. New project = drop
    a yaml, no code change.
    """
    out: dict[str, Path] = {}
    if not sop_dir.exists():
        return out
    for path in sorted(sop_dir.glob("*.yaml")):
        try:
            sop = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        pid = sop.get("project_id")
        if pid:
            out[pid] = path
    return out


def _load_project_rule(project_id: str, sop_dir: Path | None = None) -> dict | None:
    sop_dir = sop_dir or DEFAULT_SOP_DIR
    file_map = _scan_project_yamls(sop_dir)
    path = file_map.get(project_id)
    if not path:
        return None
    sop = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return (sop.get("project_meta", {}) or {}).get("shipped_weight_rule")


def _format_basis(per_wagon_expr: dict, wagon_row: dict, value: Any) -> str:
    """Short audit string per wagon: rule shape + key inputs + result."""
    op = per_wagon_expr.get("op")
    if op == "lookup":
        return (f"lookup(marked_weight={wagon_row.get('marked_weight')!r},"
                f"car_model={wagon_row.get('car_model')!r})->{value}")
    if op == "multiply":
        return (f"multiply(cargo_count={wagon_row.get('cargo_count')!r}*"
                f"{per_wagon_expr.get('rhs')})->{value}")
    return f"{op}->{value}"


def compute_for_release_batch(
    release_batch_id: str,
    *,
    db_path: Path | str | None = None,
    sop_dir: Path | None = None,
    write_basis: bool = True,
) -> dict:
    """Recompute shipped_weight for ONE release_batch and persist results.

    Returns a summary dict:
      { release_batch_id, project, shipped_weight_tons, remaining_weight_tons,
        unresolved_wagon_count, total_wagons, ok }
    """
    conn = _open_conn(db_path)
    try:
        return _compute_inner(release_batch_id, conn, sop_dir, write_basis)
    finally:
        conn.close()


def _compute_inner(release_batch_id: str, conn: sqlite3.Connection,
                   sop_dir: Path | None, write_basis: bool) -> dict:
    batch_row = conn.execute(
        "SELECT * FROM release_batches WHERE id=?", (release_batch_id,)
    ).fetchone()
    if not batch_row:
        return {"ok": False, "error": "release_batch_not_found",
                "release_batch_id": release_batch_id}
    batch = dict(batch_row)
    project_id = batch.get("project") or ""

    rule = _load_project_rule(project_id, sop_dir)
    if not rule or "per_wagon" not in rule:
        return {"ok": False, "error": "no_shipped_weight_rule",
                "release_batch_id": release_batch_id, "project": project_id}

    per_wagon = rule["per_wagon"]
    sum_expr = {
        "op": "sum_over",
        "source": "wagon_shipments_in_release_batch",
        "each": per_wagon,
        "capture_basis": write_basis,
    }
    ctx = {"db_conn": conn, "release_batch": {"id": release_batch_id}}
    result = evaluate(sum_expr, ctx)
    if not isinstance(result, dict):
        return {"ok": False, "error": "calc_returned_non_dict",
                "release_batch_id": release_batch_id}

    shipped = float(result.get("value") or 0)
    unresolved = int(result.get("unresolved") or 0)
    total_items = int(result.get("total_items") or 0)
    # 剩余可发运 = 本 lot 放货量 - 本 lot 已发运。
    # 用 batch_quantity(本 lot 计划)优先;total_planned_quantity 是全船总计划
    # 不属于本 lot 的剩余口径(否则 lot1 的 remaining 会显示 9000-shipped 而非
    # 3000-shipped)。
    planned = batch.get("batch_quantity") or batch.get("total_planned_quantity") or 0
    try:
        planned = float(planned)
    except (ValueError, TypeError):
        planned = 0.0
    remaining = planned - shipped if planned else None

    # actual_wagon_count 与已发车数同步(去重 by car_no)。手动 re-link / 回填
    # 路径下 create_wagon_shipments hook 不会触发,这里兜底刷新。
    actual_wagons = conn.execute(
        "SELECT COUNT(DISTINCT car_no) FROM wagon_shipments WHERE batch_id=?",
        (release_batch_id,),
    ).fetchone()[0]

    conn.execute(
        "UPDATE release_batches SET "
        "  shipped_weight_tons=?, remaining_weight_tons=?, "
        "  unresolved_wagon_count=?, "
        "  actual_wagon_count=?, "
        "  shipped_weight_last_computed_at=datetime('now'), "
        "  updated_at=datetime('now') "
        "WHERE id=?",
        (round(shipped, 4), round(remaining, 4) if remaining is not None else None,
         unresolved, int(actual_wagons or 0), release_batch_id),
    )

    if write_basis:
        basis_list = result.get("basis_per_item") or []
        # Need to rebuild basis with wagon row for nice audit string
        wagons = {row["id"]: dict(row) for row in conn.execute(
            "SELECT * FROM wagon_shipments WHERE batch_id=?",
            (release_batch_id,)
        ).fetchall()}
        for entry in basis_list:
            wid = entry.get("item_id")
            wagon_row = wagons.get(wid) or {}
            val = entry.get("result")
            basis_str = _format_basis(per_wagon, wagon_row, val)
            conn.execute(
                "UPDATE wagon_shipments SET "
                "  computed_loading_weight=?, weight_rule_basis=? "
                "WHERE id=?",
                (val if isinstance(val, (int, float)) else None, basis_str, wid),
            )

    # #111: 跨 lot 拆箱场景下,box_count = primary(整车) + split-in(map 副)
    # 当前 shipped_weight_tons 还是按 wagon.batch_id 算的(split-out wagon 全计入
    # 主 lot,split-in wagon 一箱也不计),box_count 给 caller 用作对照锚点。
    try:
        from sop_hub.sop.dispatch_plan import count_lot_containers
        box_count = count_lot_containers(release_batch_id)
    except Exception:
        box_count = 0
    has_split = bool(conn.execute(
        "SELECT 1 FROM wagon_shipments "
        "WHERE container_batch_map IS NOT NULL "
        "  AND (batch_id=? OR EXISTS (SELECT 1 FROM json_each(container_batch_map) j "
        "                              WHERE j.value=?)) LIMIT 1",
        (release_batch_id, release_batch_id),
    ).fetchone())

    conn.commit()
    return {
        "ok": True,
        "release_batch_id": release_batch_id,
        "project": project_id,
        "shipped_weight_tons": round(shipped, 4),
        "remaining_weight_tons": round(remaining, 4) if remaining is not None else None,
        "planned_tons": planned,
        "unresolved_wagon_count": unresolved,
        "total_wagons": total_items,
        "box_count": box_count,            # #111 #boxes 主+副
        "has_split_wagons": has_split,     # #111 是否含 container_batch_map
    }


def compute_all(
    *,
    project_id: str | None = None,
    db_path: Path | str | None = None,
    sop_dir: Path | None = None,
    write_basis: bool = True,
) -> dict:
    """Recompute every release_batch (optionally filtered by project)."""
    conn = _open_conn(db_path)
    try:
        sql = "SELECT id, project FROM release_batches"
        params: tuple = ()
        if project_id:
            sql += " WHERE project=?"
            params = (project_id,)
        rows = conn.execute(sql, params).fetchall()
        results = []
        for r in rows:
            res = _compute_inner(r["id"], conn, sop_dir, write_basis)
            results.append(res)
        summary = {
            "total_batches": len(results),
            "ok": sum(1 for r in results if r.get("ok")),
            "errors": [r for r in results if not r.get("ok")],
            "results": results,
        }
        return summary
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--batch-id", help="single release_batch id")
    p.add_argument("--project", help="project to recompute (e.g. chaoyang_steel)")
    p.add_argument("--all", action="store_true", help="recompute every batch")
    p.add_argument("--no-basis", action="store_true",
                   help="skip per-wagon basis writeback")
    args = p.parse_args()

    if args.batch_id:
        r = compute_for_release_batch(args.batch_id, write_basis=not args.no_basis)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.project or args.all:
        r = compute_all(project_id=args.project, write_basis=not args.no_basis)
        # Don't dump huge per-wagon basis arrays in summary
        for entry in r.get("results", []):
            entry.pop("basis_per_item", None)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:5000])
    else:
        p.error("specify --batch-id, --project, or --all")
