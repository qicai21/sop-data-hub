"""Source: all wagon_shipments rows for ctx['release_batch']['id'].

Required ctx keys:
    db_conn:        sqlite3.Connection
    release_batch:  dict with 'id'

Each row dict carries the same columns as the wagon_shipments table after
R76 migration (marked_weight, cargo_count, container_numbers_json, ...).
"""
NAME = "wagon_shipments_in_release_batch"


def fetch(ctx):
    conn = ctx["db_conn"]
    batch = ctx.get("release_batch") or {}
    batch_id = batch.get("id")
    if not batch_id:
        return []
    cur = conn.cursor()
    cur.row_factory = type(cur.row_factory)  # noqa: keep configured row_factory
    rows = conn.execute(
        "SELECT * FROM wagon_shipments WHERE batch_id=?",
        (batch_id,),
    ).fetchall()
    return [dict(r) for r in rows]
