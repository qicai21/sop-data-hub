"""#125 (2026-06-07) — release_batches.dispatch_status 老 5 值 → 新 8 值 lifecycle 枚举。

CHECK 约束已在 db.py 改成新 8 值。**已有的 release_batches 表是没 CHECK 约束的**
(老 db 建表时还没强制 enum),所以这里只 UPDATE 数据,不重建表。

迁移规则(见 lifecycle.LEGACY_MIGRATION_MAP):
  pending_completion → pending_freight
  in_progress        → loading
  completed          → confirmed_received
  suspended          → loading
  cancelled          → closed
  delivered          → delivered   (新值 placeholder,已存在 1 行)

后续 #127/#128 状态机触发器自动把 loading 推到 all_loaded/tracking/delivered。
"""
from __future__ import annotations
import sqlite3, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
DB = REPO / "data" / "sop_agent.db"

from sop_hub.sop.lifecycle import LEGACY_MIGRATION_MAP


def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    before = {r["dispatch_status"]: r["c"] for r in conn.execute(
        "SELECT dispatch_status, COUNT(*) c FROM release_batches GROUP BY dispatch_status"
    ).fetchall()}
    print("=== 迁移前 ===")
    for k, v in before.items():
        print(f"  {k:25s} {v}")

    total_updated = 0
    for old, new in LEGACY_MIGRATION_MAP.items():
        cur = conn.execute(
            "UPDATE release_batches SET dispatch_status=?, "
            "dispatch_status_note=COALESCE(dispatch_status_note,'') || ' | #125 migrated from ' || ?, "
            "dispatch_status_updated_at=datetime('now') "
            "WHERE dispatch_status=?",
            (new, old, old),
        )
        if cur.rowcount:
            print(f"  {old:25s} → {new:20s} ({cur.rowcount} rows)")
            total_updated += cur.rowcount
    conn.commit()

    after = {r["dispatch_status"]: r["c"] for r in conn.execute(
        "SELECT dispatch_status, COUNT(*) c FROM release_batches GROUP BY dispatch_status"
    ).fetchall()}
    print("\n=== 迁移后 ===")
    for k, v in sorted(after.items()):
        print(f"  {k:25s} {v}")
    print(f"\nTotal updated: {total_updated}")
    conn.close()


if __name__ == "__main__":
    main()
