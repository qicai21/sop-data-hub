from __future__ import annotations

import sqlite3


def test_empty_container_batch_map_is_not_sent_to_json_each():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE release_batches (id TEXT PRIMARY KEY);
        CREATE TABLE wagon_shipments (id TEXT, batch_id TEXT, container_batch_map TEXT);
        INSERT INTO release_batches VALUES ('lot-a');
        INSERT INTO wagon_shipments VALUES ('w-empty', 'lot-a', '');
        INSERT INTO wagon_shipments VALUES ('w-null', 'lot-a', NULL);
        INSERT INTO wagon_shipments VALUES (
          'w-valid', 'other', '{"TBJU1":"lot-a"}'
        );
        """
    )

    rows = conn.execute(
        """
        SELECT COUNT(*)
        FROM wagon_shipments ws, json_each(ws.container_batch_map) j
        WHERE ws.container_batch_map IS NOT NULL
          AND ws.container_batch_map <> ''
          AND json_valid(ws.container_batch_map)
          AND j.value = 'lot-a'
        """
    ).fetchone()

    assert rows[0] == 1
