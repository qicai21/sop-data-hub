"""分票计划(release_batch_dispatch_plan)+ container_batch_map allocator。

2026-06-06 task #111。集装箱业务里一次 95306 制票批可能横跨多 lot。本模块:
  - 管理 plan 表(planned_box_count / allocated_box_count / priority_order)
  - 按 plan 给 wagon 的 container 分配 batch:整车同 lot → wagon.batch_id;
    跨 lot → wagon.batch_id(主)+ wagon.container_batch_map(JSON 副)
  - 提供 lot → boxes 反查(汇总 主 + 副)

模型约定:
- wagon.batch_id:整车都在该 lot 时填该 lot;跨 lot 拆箱时填"本车第一个被分
  到的 lot"(按 priority_order 取);
- wagon.container_batch_map:JSON ``{container_no: batch_id, ...}``。**只在跨
  lot 拆箱时设值**;整车同 lot 时 NULL,查时 fallback wagon.batch_id。

API:
  set_plan(project_id, ship_name, entries) — 写 plan 表(per release_batch)
  list_active_plans(project_id, ship_name) — 取活跃 plan,按 priority 升序
  allocate_wagons(wagon_ids, db_path)     — 给 wagon 赋 batch_id +
                                            container_batch_map,递减
                                            allocated_box_count,plan 满 → completed
  list_lot_containers(release_batch_id)   — 返回该 lot 的所有 (car_no, box_no)
  count_lot_containers(release_batch_id)  — 该 lot 实际分到的箱数
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[3]
SOP_DB = REPO_ROOT / "data" / "sop_agent.db"


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class PlanEntry:
    release_batch_id: str
    project_id: str
    ship_name: str
    planned_box_count: int
    allocated_box_count: int = 0
    priority_order: int = 1
    status: str = "active"
    notes: str = ""

    @property
    def remaining(self) -> int:
        return max(0, self.planned_box_count - self.allocated_box_count)


@dataclass
class WagonAllocation:
    wagon_id: str
    car_no: str
    primary_batch_id: str
    container_batch_map: dict[str, str] = field(default_factory=dict)
    # NULL-in-DB convention: empty map → wagon.batch_id 覆盖整车
    is_split: bool = False


@dataclass
class AllocationResult:
    wagons: list[WagonAllocation] = field(default_factory=list)
    allocations_by_batch: dict[str, int] = field(default_factory=dict)
    closed_batches: list[str] = field(default_factory=list)
    error: str = ""


# ── DB helpers ───────────────────────────────────────────────────────────


def _conn(db_path: str | Path | None = None) -> sqlite3.Connection:
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso() -> str:
    from sop_hub.utils.time import now_iso_beijing
    return now_iso_beijing()


# ── Plan management ─────────────────────────────────────────────────────


def set_plan(
    project_id: str,
    ship_name: str,
    entries: list[tuple[str, int, int]],
    *,
    notes: str = "",
    db_path: str | Path | None = None,
) -> list[PlanEntry]:
    """Set / replace plan rows for (project, ship).

    Args:
      entries: list of (release_batch_id, planned_box_count, priority_order).
      notes:   optional human-readable note (e.g. "今早港口分票").

    For each entry: INSERT OR REPLACE keeps allocated_box_count in sync with
    boxes already assigned to that release_batch. This matters when a dispatch
    plan is created after some wagons have already been ingested.
    Old plans on the same (project, ship) NOT in entries are NOT touched —
    caller is expected to know what to reset; if you need wholesale reset,
    call mark_status() first.
    """
    if not entries:
        return []
    conn = _conn(db_path)
    out: list[PlanEntry] = []
    try:
        for rb_id, planned, prio in entries:
            existing = conn.execute(
                "SELECT allocated_box_count FROM release_batch_dispatch_plan "
                "WHERE release_batch_id=?", (rb_id,),
            ).fetchone()
            actual_boxes = conn.execute(
                "SELECT COUNT(*) FROM wagon_container_shipments "
                "WHERE batch_id=?",
                (rb_id,),
            ).fetchone()[0]
            existing_allocated = int(existing["allocated_box_count"]) if existing else 0
            allocated = max(existing_allocated, int(actual_boxes or 0))
            status = "completed" if allocated >= int(planned) else "active"
            conn.execute(
                "INSERT OR REPLACE INTO release_batch_dispatch_plan "
                "(release_batch_id, project_id, ship_name, planned_box_count, "
                " allocated_box_count, priority_order, status, notes, "
                " created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?, "
                "        COALESCE((SELECT created_at FROM "
                "                 release_batch_dispatch_plan WHERE release_batch_id=?), ?), "
                "        ?)",
                (rb_id, project_id, ship_name, int(planned), allocated,
                 int(prio), status, notes, rb_id, _now_iso(), _now_iso()),
            )
            out.append(PlanEntry(
                release_batch_id=rb_id, project_id=project_id,
                ship_name=ship_name, planned_box_count=int(planned),
                allocated_box_count=allocated, priority_order=int(prio),
                status=status, notes=notes,
            ))
        conn.commit()
    finally:
        conn.close()
    return out


def list_active_plans(
    project_id: str, ship_name: str | None = None, *,
    db_path: str | Path | None = None,
) -> list[PlanEntry]:
    """Return active plans for (project, ship), priority ASC then remaining DESC."""
    conn = _conn(db_path)
    try:
        if ship_name:
            rows = conn.execute(
                "SELECT * FROM release_batch_dispatch_plan "
                "WHERE project_id=? AND ship_name=? AND status='active' "
                "ORDER BY priority_order ASC, release_batch_id ASC",
                (project_id, ship_name),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM release_batch_dispatch_plan "
                "WHERE project_id=? AND status='active' "
                "ORDER BY priority_order ASC, release_batch_id ASC",
                (project_id,),
            ).fetchall()
    finally:
        conn.close()
    return [
        PlanEntry(
            release_batch_id=r["release_batch_id"],
            project_id=r["project_id"], ship_name=r["ship_name"],
            planned_box_count=int(r["planned_box_count"] or 0),
            allocated_box_count=int(r["allocated_box_count"] or 0),
            priority_order=int(r["priority_order"] or 1),
            status=r["status"] or "active",
            notes=r["notes"] or "",
        ) for r in rows
    ]


def get_plan(release_batch_id: str, *, db_path: str | Path | None = None) -> PlanEntry | None:
    conn = _conn(db_path)
    try:
        r = conn.execute(
            "SELECT * FROM release_batch_dispatch_plan WHERE release_batch_id=?",
            (release_batch_id,),
        ).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    return PlanEntry(
        release_batch_id=r["release_batch_id"],
        project_id=r["project_id"], ship_name=r["ship_name"],
        planned_box_count=int(r["planned_box_count"] or 0),
        allocated_box_count=int(r["allocated_box_count"] or 0),
        priority_order=int(r["priority_order"] or 1),
        status=r["status"] or "active",
        notes=r["notes"] or "",
    )


def mark_status(
    release_batch_id: str, status: str, *,
    db_path: str | Path | None = None,
) -> bool:
    conn = _conn(db_path)
    try:
        cur = conn.execute(
            "UPDATE release_batch_dispatch_plan SET status=?, updated_at=? "
            "WHERE release_batch_id=?",
            (status, _now_iso(), release_batch_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Allocator core ──────────────────────────────────────────────────────


def _wagon_containers(row: dict[str, Any]) -> list[str]:
    """Read wagon row's containers (json_array preferred, fallback "/"-split)."""
    cnj = row.get("container_numbers_json")
    if cnj:
        try:
            data = json.loads(cnj)
            if isinstance(data, list):
                return [str(b).strip() for b in data if str(b).strip()]
        except Exception:
            pass
    raw = row.get("container_no") or ""
    return [b.strip() for b in str(raw).split("/") if b.strip()]


def allocate_wagons(
    wagon_ids: list[str],
    project_id: str,
    ship_name: str,
    *,
    db_path: str | Path | None = None,
    force_overwrite: bool = False,
) -> AllocationResult:
    """Consume active plans (priority asc) to assign each wagon's containers.

    For each wagon (ordered by ticketed_at ASC then car_no):
      1. read its containers (1..N boxes)
      2. drain priority queue: head plan with remaining > 0 takes the box,
         decrement, repeat until all boxes assigned or queue empty
      3. when boxes-of-this-wagon all to the same lot → wagon.batch_id = lot,
         container_batch_map = NULL
      4. when split across lots → wagon.batch_id = first lot in priority order
         this wagon contributed to, container_batch_map = {box: lot, ...}
      5. when a plan reaches planned_box_count → status='completed',
         dispatch_status of corresponding release_batch → consider closing
         elsewhere (NOT here — lifecycle close is #85's job)

    Returns AllocationResult. Caller should call this BEFORE the chain
    inserts wagon_shipments(default batch_id),or AFTER inserting with a
    placeholder batch_id which we then overwrite.

    Idempotency: re-calling allocate_wagons on the same wagon_ids when
    container_batch_map is already set will be a no-op for that wagon
    (we detect and skip).
    """
    result = AllocationResult()
    if not wagon_ids:
        result.error = "wagon_ids is empty"
        return result

    conn = _conn(db_path)
    try:
        in_ph = ",".join("?" * len(wagon_ids))
        wagons = conn.execute(
            f"SELECT * FROM wagon_shipments WHERE id IN ({in_ph}) "
            f"ORDER BY ticketed_at ASC, car_no ASC",
            wagon_ids,
        ).fetchall()
        if not wagons:
            result.error = "no wagon_shipments found for given ids"
            return result

        plans = conn.execute(
            "SELECT * FROM release_batch_dispatch_plan "
            "WHERE project_id=? AND ship_name=? AND status='active' "
            "ORDER BY priority_order ASC, release_batch_id ASC",
            (project_id, ship_name),
        ).fetchall()
        if not plans:
            result.error = (
                f"no active plans for project={project_id} ship={ship_name}"
            )
            return result

        # 工作集 — 全部活跃 plan 都参与 status 检查,**索引指针消费,不 pop**,
        # 避免丢失已耗尽 plan 的 newly_allocated 计数
        queue: list[dict[str, Any]] = []
        for p in plans:
            remaining = int(p["planned_box_count"] or 0) - int(p["allocated_box_count"] or 0)
            queue.append({
                "release_batch_id": p["release_batch_id"],
                "remaining": max(0, remaining),
                "newly_allocated": 0,
            })
        # 至少一个有 remaining 的 plan
        if all(q["remaining"] <= 0 for q in queue):
            result.error = "all active plans already full"
            return result

        head_idx = 0  # 当前消费指针
        # 幂等:wagon.batch_id 指向**本 project+ship 的任意 plan**(含 completed)
        # → 已分配,跳过。container_batch_map 也作辅助判据(整车同 lot 时 map=NULL
        # 但 batch_id 已设)。注意这里要查全状态 plan,不只 active —— 第二次调用
        # 时之前满了的 plan 已 completed,但 wagon 仍指它。
        all_plan_ids_rows = conn.execute(
            "SELECT release_batch_id FROM release_batch_dispatch_plan "
            "WHERE project_id=? AND ship_name=?",
            (project_id, ship_name),
        ).fetchall()
        all_plan_ids = {r["release_batch_id"] for r in all_plan_ids_rows}
        for w in wagons:
            w_dict = dict(w)
            if not force_overwrite:
                if w_dict.get("container_batch_map"):
                    continue  # split 已分配
                if w_dict.get("batch_id") and w_dict["batch_id"] in all_plan_ids:
                    continue  # 整车同 lot 已分配
            # force_overwrite=True 时跳过幂等 skip:chain step 4b 调用时,刚 INSERT
            # 的 wagon batch_id 是 step 3 的 placeholder(_find_release_batch
            # 取的第一个 in_progress lot),恰好也在 all_plan_ids 里 → 会被误判
            # 已分配。chain 知道这批 wagon 是新插入需重分,所以传 True。

            boxes = _wagon_containers(w_dict)
            if not boxes:
                # 散运/无箱号,整车按主路径走(不消费 plan)
                continue

            mapping: dict[str, str] = {}
            for box in boxes:
                # 指针前进到下一个 remaining > 0 的 plan
                while head_idx < len(queue) and queue[head_idx]["remaining"] <= 0:
                    head_idx += 1
                if head_idx >= len(queue):
                    result.error = (
                        f"plan exhausted while assigning wagon {w_dict['car_no']} "
                        f"box {box} — plan total < actual containers"
                    )
                    return result
                head = queue[head_idx]
                mapping[box] = head["release_batch_id"]
                head["remaining"] -= 1
                head["newly_allocated"] += 1

            assigned_lots = set(mapping.values())
            if len(assigned_lots) == 1:
                primary = next(iter(assigned_lots))
                conn.execute(
                    "UPDATE wagon_shipments SET batch_id=?, container_batch_map=NULL, "
                    "       updated_at=? WHERE id=?",
                    (primary, _now_iso(), w_dict["id"]),
                )
                wa = WagonAllocation(
                    wagon_id=w_dict["id"], car_no=w_dict["car_no"],
                    primary_batch_id=primary, container_batch_map={},
                    is_split=False,
                )
            else:
                # 跨 lot 拆箱;主 batch_id = boxes 顺序里第一个分到的 lot
                primary = mapping[boxes[0]]
                conn.execute(
                    "UPDATE wagon_shipments SET batch_id=?, container_batch_map=?, "
                    "       updated_at=? WHERE id=?",
                    (primary, json.dumps(mapping, ensure_ascii=False),
                     _now_iso(), w_dict["id"]),
                )
                wa = WagonAllocation(
                    wagon_id=w_dict["id"], car_no=w_dict["car_no"],
                    primary_batch_id=primary, container_batch_map=mapping,
                    is_split=True,
                )
            # #123 Phase 3 (2026-06-07):集装箱业务双写新表 box.batch_id —
            # per box UPDATE,完全无 cbm JSON 处理。下游消费方读新表 SQL 自然。
            for box, target_lot in mapping.items():
                conn.execute(
                    "UPDATE wagon_container_shipments SET batch_id=?, updated_at=? "
                    "WHERE car_no=? AND box_no=? AND ydid=?",
                    (target_lot, _now_iso(),
                     w_dict["car_no"], box, w_dict.get("ydid") or ""),
                )
            result.wagons.append(wa)

        # Flush — 所有 plan 都遍历一遍
        for q in queue:
            if q["newly_allocated"] > 0:
                conn.execute(
                    "UPDATE release_batch_dispatch_plan "
                    "SET allocated_box_count = allocated_box_count + ?, "
                    "    updated_at=? WHERE release_batch_id=?",
                    (q["newly_allocated"], _now_iso(), q["release_batch_id"]),
                )
                result.allocations_by_batch[q["release_batch_id"]] = q["newly_allocated"]
            if q["remaining"] == 0 and q["newly_allocated"] > 0:
                # 这一 plan 满了(且本轮刚好消费完)→ status=completed
                # release_batch.dispatch_status 让 #85 lifecycle 段决定是否关
                conn.execute(
                    "UPDATE release_batch_dispatch_plan "
                    "SET status='completed', updated_at=? "
                    "WHERE release_batch_id=? AND status='active'",
                    (_now_iso(), q["release_batch_id"]),
                )
                result.closed_batches.append(q["release_batch_id"])

        conn.commit()
        return result
    finally:
        conn.close()


def allocate_container_ydids(
    ydids: list[str],
    project_id: str,
    ship_name: str,
    *,
    db_path: str | Path | None = None,
    force_overwrite: bool = False,
) -> AllocationResult:
    """Allocate a container event directly from its box-level facts.

    This is the canonical allocator for 吉林金钢.  It deliberately never reads
    or writes ``wagon_shipments``: an event is identified by 95306 ``ydid`` and
    every ``ydid + box_no`` is assigned once in ``wagon_container_shipments``.
    """
    result = AllocationResult()
    ids = [str(value).strip() for value in ydids if str(value).strip()]
    if not ids:
        result.error = "ydids is empty"
        return result

    conn = _conn(db_path)
    try:
        placeholders = ",".join("?" * len(ids))
        boxes = conn.execute(
            f"SELECT id, ydid, car_no, box_no, batch_id FROM wagon_container_shipments "
            f"WHERE project_id=? AND ship_name=? AND ydid IN ({placeholders}) "
            f"ORDER BY ticketed_at ASC, car_no ASC, box_position ASC, box_no ASC",
            (project_id, ship_name, *ids),
        ).fetchall()
        if not boxes:
            result.error = "no wagon_container_shipments found for given ydids"
            return result

        plans = conn.execute(
            "SELECT * FROM release_batch_dispatch_plan "
            "WHERE project_id=? AND ship_name=? AND status='active' "
            "ORDER BY priority_order ASC, release_batch_id ASC",
            (project_id, ship_name),
        ).fetchall()
        if not plans:
            return result  # no explicit box plan: retain the matched release batch

        queue: list[dict[str, Any]] = []
        event_placeholders = ",".join("?" * len(ids))
        for plan in plans:
            if force_overwrite:
                # 新事件先按匹配 lot 落箱，随后才按 plan 重分。计算基线时必须
                # 排除这批 placeholder，否则首个 lot 会虚增到 plan 已超额。
                actual = conn.execute(
                    f"SELECT COUNT(*) FROM wagon_container_shipments WHERE batch_id=? "
                    f"AND ydid NOT IN ({event_placeholders})",
                    (plan["release_batch_id"], *ids),
                ).fetchone()[0]
            else:
                actual = conn.execute(
                    "SELECT COUNT(*) FROM wagon_container_shipments WHERE batch_id=?",
                    (plan["release_batch_id"],),
                ).fetchone()[0]
            recorded = int(plan["allocated_box_count"] or 0)
            planned = int(plan["planned_box_count"] or 0)
            # A stale plan must not silently consume another event. It needs an
            # explicit data repair, then the next trigger can allocate normally.
            if actual > planned:
                result.error = (
                    f"plan invariant violated: {plan['release_batch_id']} "
                    f"actual_boxes={actual} > planned_box_count={planned}"
                )
                return result
            queue.append({
                "release_batch_id": plan["release_batch_id"],
                "remaining": max(0, planned - max(recorded, actual)),
                "newly_allocated": 0,
            })
        if all(item["remaining"] <= 0 for item in queue):
            result.error = "all active plans already full"
            return result

        all_plan_ids = {p["release_batch_id"] for p in conn.execute(
            "SELECT release_batch_id FROM release_batch_dispatch_plan "
            "WHERE project_id=? AND ship_name=?", (project_id, ship_name)
        )}
        head = 0
        for row in boxes:
            if not force_overwrite and row["batch_id"] in all_plan_ids:
                continue
            while head < len(queue) and queue[head]["remaining"] <= 0:
                head += 1
            if head >= len(queue):
                result.error = (
                    f"plan exhausted while assigning ydid={row['ydid']} box={row['box_no']}"
                )
                return result
            target = queue[head]["release_batch_id"]
            conn.execute(
                "UPDATE wagon_container_shipments SET batch_id=?, updated_at=? WHERE id=?",
                (target, _now_iso(), row["id"]),
            )
            queue[head]["remaining"] -= 1
            queue[head]["newly_allocated"] += 1
            result.allocations_by_batch[target] = result.allocations_by_batch.get(target, 0) + 1

        for item in queue:
            bid = item["release_batch_id"]
            actual = conn.execute(
                "SELECT COUNT(*) FROM wagon_container_shipments WHERE batch_id=?", (bid,)
            ).fetchone()[0]
            status = "completed" if item["remaining"] == 0 else "active"
            conn.execute(
                "UPDATE release_batch_dispatch_plan SET allocated_box_count=?, status=?, updated_at=? "
                "WHERE release_batch_id=?",
                (actual, status, _now_iso(), bid),
            )
            if status == "completed" and item["newly_allocated"]:
                result.closed_batches.append(bid)
        conn.commit()
        return result
    finally:
        conn.close()


# ── Lot 反查(主 + 副)─────────────────────────────────────────────────


def list_lot_containers(
    release_batch_id: str, *, db_path: str | Path | None = None,
) -> list[tuple[str, str]]:
    """List (car_no, container_no) for a lot — primary path + secondary map.

    Two sources:
      - wagon.batch_id == lot AND container_batch_map IS NULL → ALL boxes of wagon
      - container_batch_map has entries pointing at this lot → just those boxes
    """
    conn = _conn(db_path)
    try:
        # primary: 整车都在本 lot
        primary_rows = conn.execute(
            "SELECT car_no, container_no, container_numbers_json "
            "FROM wagon_shipments WHERE batch_id=? AND container_batch_map IS NULL",
            (release_batch_id,),
        ).fetchall()
        out: list[tuple[str, str]] = []
        for r in primary_rows:
            for box in _wagon_containers(dict(r)):
                out.append((r["car_no"], box))

        # secondary: 跨 lot 拆箱 — json_each 抽出 map 里指向本 lot 的箱
        try:
            split_rows = conn.execute(
                "SELECT ws.car_no, j.key AS box "
                "  FROM wagon_shipments ws, json_each(ws.container_batch_map) j "
                " WHERE ws.container_batch_map IS NOT NULL AND j.value=?",
                (release_batch_id,),
            ).fetchall()
            for r in split_rows:
                out.append((r["car_no"], r["box"]))
        except sqlite3.OperationalError:
            # SQLite 没 json1 编译进去(罕见),fallback Python 解析
            map_rows = conn.execute(
                "SELECT car_no, container_batch_map FROM wagon_shipments "
                "WHERE container_batch_map IS NOT NULL"
            ).fetchall()
            for r in map_rows:
                try:
                    m = json.loads(r["container_batch_map"])
                except Exception:
                    continue
                for box, bid in m.items():
                    if bid == release_batch_id:
                        out.append((r["car_no"], box))
    finally:
        conn.close()
    return out


def count_lot_containers(
    release_batch_id: str, *, db_path: str | Path | None = None,
) -> int:
    return len(list_lot_containers(release_batch_id, db_path=db_path))
