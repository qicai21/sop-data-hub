"""九三历史分票与完成闸的边界。"""
from __future__ import annotations

import sqlite3

from openpyxl import Workbook

from sop_hub.reconcile.engine import NEW_UNATTR, ReconcileResult
from sop_hub.reconcile.jiusan_spec import JiusanContainerSpec, _parse_ship_version, _read_sheet_hph


def _hub() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE release_batches (
            id TEXT, project TEXT, batch_sequence TEXT, ship_name TEXT,
            dispatch_status TEXT, batch_date TEXT, notice_date TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO release_batches VALUES ('US','jiusan','lot01','美国','loading','2026-07-02','2026-07-02')"
    )
    return conn


def _rail() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE shipments (
            ydid TEXT, container_numbers_json TEXT, ticketed_at TEXT,
            origin_name TEXT, destination_name TEXT, transport_mode_name TEXT
        )"""
    )
    conn.executemany(
        "INSERT INTO shipments VALUES (?,?,?,?,?,?)",
        [
            ("old", '["TBJU0000001"]', "2026-06-10 10:00:00", "高桥镇", "新台子", "集装箱运输"),
            ("new", '["TBJU0000002"]', "2026-07-02 10:00:00", "高桥镇", "新台子", "集装箱运输"),
        ],
    )
    return conn


def test_parse_ship_version_accepts_direct_download_filename():
    assert _parse_ship_version("诚信货票.xlsx") == ("诚信", 0)
    assert _parse_ship_version("大豆 美国货票(2).xlsx") == ("美国", 2)


def test_read_sheet_hph_accepts_xintai_prefix_sheet_name(tmp_path):
    path = tmp_path / "诚信货票.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "新台子330"
    sheet.append(["", "", "", ""])
    sheet.append(["", "", "", ""])
    sheet.append(["", "", "", ""])
    sheet.append([1, "", "", "GZDJW0501693"])
    workbook.save(path)

    assert _read_sheet_hph(path, "新台子") == {"GZDJW0501693"}


def test_container_gate_excludes_history_before_active_release_date():
    result = ReconcileResult(
        project_id="jiusan",
        leg="container",
        by_cat={NEW_UNATTR: [(("old", "TBJU0000001"), "H1"), (("new", "TBJU0000002"), "H1")]},
    )

    keys = JiusanContainerSpec().gateable_new_unattributed_keys(result, _rail(), _hub())

    assert keys == {("new", "TBJU0000002")}
