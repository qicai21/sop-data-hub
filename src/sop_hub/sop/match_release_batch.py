"""R78: 按船+到站+货物匹配可接收的 release_batch。

yaml `match_release_batch_by_ship_destination_cargo` action 的代码实现。
chaoyang.yaml inspection_notice_flow.match_release_batch 节点引用了
这个 action 名,但代码侧之前没人实现。

业务规则:
  1. 只接受 lifecycle 的 open batch;终态 batch 不接收新车
  2. 单一候选:直接采纳
  3. 多个候选:挂 pending_review,把候选 id 全数返回供人工指认
  4. 0 候选:返回 no_match,由调用方决定挂起 reason
  5. cargo_name 模糊匹配:同船同到站时 cargo 用 LIKE %xxx% 软比对
     (出港计划里 cargo 字段可能写 '铁矿' 而 95306 里 '铁矿粉')

完全不挂硬编码;给任何项目复用。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ReleaseBatchMatch:
    matched_release_batch_id: str | None = None
    candidate_release_batch_ids: list[str] = field(default_factory=list)
    reason: str = ""  # "single_match" | "multiple_candidates" | "no_open_batch" | "no_match"
    matched_dispatch_status: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_release_batch_id": self.matched_release_batch_id,
            "candidate_release_batch_ids": self.candidate_release_batch_ids,
            "reason": self.reason,
            "matched_dispatch_status": self.matched_dispatch_status,
            "notes": self.notes,
        }


# #125 lifecycle 8 phase:open 状态 = enriched/loading(可接装车),其他阶段拒
_OPEN_STATUSES = ("loading", "enriched")
_FACT_STATUSES = ("loading", "enriched", "pending_freight")


def match_release_batch_by_ship_destination_cargo(
    *,
    project_id: str,
    ship_name: str,
    destination_station: str,
    cargo_name: str = "",
    db_conn: sqlite3.Connection | None = None,
    db_path: str | Path | None = None,
    allow_pending_freight_for_facts: bool = False,
) -> ReleaseBatchMatch:
    """按 project + ship + destination (+ cargo soft match) 找匹配的 release_batch。

    Args:
      project_id: snake_case (e.g. "chaoyang_steel") 或中文名(向后兼容)
      ship_name:  船名(如 "宝腾海")
      destination_station: 到站(如 "朝阳西")
      cargo_name: 货物名;可空,用于在多候选时做软筛选
      db_conn:    已开的 sqlite 连接(优先)
      db_path:    DB 文件路径(没传 conn 时使用)

    Returns:
      ReleaseBatchMatch
    """
    if not project_id or not ship_name or not destination_station:
        return ReleaseBatchMatch(
            reason="bad_input",
            notes=[f"missing required: project_id={project_id!r} ship={ship_name!r} dest={destination_station!r}"],
        )

    own_conn = False
    if db_conn is None:
        if db_path is None:
            db_path = (Path(__file__).resolve().parents[3] / "data" / "sop_agent.db")
        db_conn = sqlite3.connect(str(db_path))
        own_conn = True
    db_conn.row_factory = sqlite3.Row

    try:
        # #125 lifecycle 8 phase open 集 = enriched/loading。
        # 检装车事实入库层可临时纳入 pending_freight;输出层仍需单独拦截。
        statuses = _FACT_STATUSES if allow_pending_freight_for_facts else _OPEN_STATUSES
        ph = ",".join("?" * len(statuses))
        rows = db_conn.execute(
            f"SELECT id, project, ship_name, destination_station, cargo_name, "
            f"       cargo_product_name, dispatch_status, notice_date, batch_date "
            f"FROM release_batches "
            f"WHERE COALESCE(project,'') = ? "
            f"  AND COALESCE(ship_name,'') = ? "
            f"  AND COALESCE(destination_station,'') = ? "
            f"  AND dispatch_status IN ({ph}) "
            f"ORDER BY notice_date DESC, batch_date DESC",
            (project_id, ship_name, destination_station, *statuses),
        ).fetchall()

        if not rows:
            return ReleaseBatchMatch(
                reason="no_open_batch",
                notes=[f"no {'/'.join(statuses)} batch for {project_id}/{ship_name}/{destination_station}"],
            )

        # Cargo soft filter only when more than one row
        candidates = [dict(r) for r in rows]
        if len(candidates) > 1 and cargo_name:
            tightened = [
                c for c in candidates
                if cargo_name in (c.get("cargo_name") or "")
                or cargo_name in (c.get("cargo_product_name") or "")
                or (c.get("cargo_name") or "") in cargo_name
            ]
            if tightened:
                candidates = tightened

        if len(candidates) == 1:
            return ReleaseBatchMatch(
                matched_release_batch_id=candidates[0]["id"],
                reason="single_match",
                matched_dispatch_status=candidates[0]["dispatch_status"] or "",
            )

        return ReleaseBatchMatch(
            candidate_release_batch_ids=[c["id"] for c in candidates],
            reason="multiple_candidates",
            notes=[
                f"{len(candidates)} open batches; needs human assignment",
                "do not prefer an older loading lot over a newer enriched lot",
            ],
        )

    finally:
        if own_conn:
            db_conn.close()


if __name__ == "__main__":
    import argparse, json
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--ship", required=True)
    p.add_argument("--dest", required=True)
    p.add_argument("--cargo", default="")
    p.add_argument("--db")
    args = p.parse_args()
    r = match_release_batch_by_ship_destination_cargo(
        project_id=args.project, ship_name=args.ship,
        destination_station=args.dest, cargo_name=args.cargo,
        db_path=args.db,
    )
    print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
