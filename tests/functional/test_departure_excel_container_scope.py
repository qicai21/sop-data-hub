import sqlite3

from sop_hub.sop import departure_excel
from sop_hub.sop.departure_excel import (
    ExcelColumn,
    ExcelTemplate,
    _fetch_event_wagons_and_batches,
    generate_multibatch_departure_excel,
)


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


def test_multibatch_filename_uses_selected_event_date(monkeypatch, tmp_path):
    template = ExcelTemplate(
        project_id="chaoyang_steel",
        business_name="test",
        sheet_name="Sheet1",
        file_pattern="unused.xlsx",
        title_text="",
        title_row=0,
        title_range="",
        title_merge=False,
        header_row=1,
        data_start_row=2,
        bordered_range="",
        border_style="thin",
        apply_border_header=True,
        apply_border_body=True,
        columns=[ExcelColumn(header="车号", key="wagon_no")],
        footer=None,
    )
    monkeypatch.setattr(departure_excel, "_load_template", lambda _project: template)
    monkeypatch.setattr(
        departure_excel,
        "_extract_rows",
        lambda *_args, **_kwargs: (
            [{"wagon_no": "1234567", "ticketed_at_raw": "2026-09-07 19:46:00"}],
            {"ship_name": "欧律狄克"},
            "",
        ),
    )

    result = generate_multibatch_departure_excel(
        [("lot03", ["1234567"], ["ydid-1"])],
        project_id="chaoyang_steel",
        output_dir=tmp_path,
    )

    assert result.filename == "欧律狄克发运_2026-09-07_1车.xlsx"
