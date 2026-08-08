import sqlite3

import pytest

from sop_hub.sop.shipped_weight import compute_for_release_batch


def test_jilin_container_weight_uses_box_rows_not_stale_wagon_rows(tmp_path):
    db = tmp_path / "sop.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE release_batches (
        id TEXT PRIMARY KEY, project TEXT, batch_quantity REAL,
        total_planned_quantity REAL, shipped_weight_tons REAL,
        remaining_weight_tons REAL, unresolved_wagon_count INTEGER,
        actual_wagon_count INTEGER, shipped_weight_last_computed_at TEXT,
        updated_at TEXT, transport_mode TEXT
    )""")
    conn.execute("CREATE TABLE wagon_shipments (id TEXT PRIMARY KEY, batch_id TEXT, container_batch_map TEXT)")
    conn.execute("CREATE TABLE wagon_container_shipments (id TEXT PRIMARY KEY, batch_id TEXT, ydid TEXT, box_no TEXT)")
    conn.execute("INSERT INTO release_batches (id,project,batch_quantity) VALUES ('lot4','jilin_jingang_jinzhou',10000)")
    conn.executemany("INSERT INTO wagon_shipments (id, batch_id) VALUES (?, 'lot4')", [(f'w{i}',) for i in range(179)])
    conn.executemany(
        "INSERT INTO wagon_container_shipments VALUES (?, 'lot4', ?, ?)",
        [(f'b{i}', f'ydid-{i // 2}', f'TBJU{i:07d}') for i in range(264)],
    )
    conn.commit()
    conn.close()

    result = compute_for_release_batch('lot4', db_path=db)

    assert result['shipped_weight_tons'] == pytest.approx(264 * 32.28)
    assert result['unresolved_wagon_count'] == 0
    assert result['total_wagons'] == 264

    conn = sqlite3.connect(db)
    stored = conn.execute("SELECT actual_wagon_count FROM release_batches WHERE id='lot4'").fetchone()[0]
    assert stored == 132
