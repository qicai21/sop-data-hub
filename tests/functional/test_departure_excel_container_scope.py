import sqlite3

from sop_hub.sop.departure_excel import _fetch_event_wagons_and_batches


def test_container_event_scope_does_not_leak_other_batch_same_ydid(tmp_path):
    db_path = tmp_path / "sop.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE release_batches (id TEXT PRIMARY KEY, project TEXT);
        CREATE TABLE wagon_container_shipments (
          car_no TEXT, box_no TEXT, box_position INTEGER, ydid TEXT,
          batch_id TEXT, ticketed_at TEXT
        );
        INSERT INTO release_batches VALUES ('lot06', 'jilin_jingang_jinzhou');
        INSERT INTO release_batches VALUES ('lot07', 'jilin_jingang_jinzhou');
        INSERT INTO wagon_container_shipments VALUES
          ('1574879', 'TBJU3078142', 1, 'ydid-1', 'lot06', '2026-08-05 23:08:20'),
          ('1574879', 'TBCU0307126', 2, 'ydid-1', 'lot07', '2026-08-05 23:08:20');
        """
    )
    conn.commit()
    conn.close()

    wagons, batches, error = _fetch_event_wagons_and_batches(
        container_ydids=["ydid-1"],
        container_batch_id="lot07",
        db_path=db_path,
    )

    assert error == ""
    assert [wagon["batch_id"] for wagon in wagons] == ["lot07"]
    assert set(batches) == {"lot07"}
