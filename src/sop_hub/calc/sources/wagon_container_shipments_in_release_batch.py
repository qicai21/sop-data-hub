"""Source: all wagon_container_shipments (box-level) rows for ctx batch.

集装箱业务每 box 一行(数据在 wagon_container_shipments,非 wagon_shipments)。
让集装箱装车重量也能走统一的 sum_over DSL:`per_box × box 数`,而不必在
sync 脚本里硬算 28.4×box(2026-06-15 补丁收敛)。

Required ctx keys:
    db_conn:        sqlite3.Connection
    release_batch:  dict with 'id'
"""
NAME = "wagon_container_shipments_in_release_batch"


def fetch(ctx):
    conn = ctx["db_conn"]
    batch = ctx.get("release_batch") or {}
    batch_id = batch.get("id")
    if not batch_id:
        return []
    rows = conn.execute(
        "SELECT * FROM wagon_container_shipments WHERE batch_id=?",
        (batch_id,),
    ).fetchall()
    return [dict(r) for r in rows]
