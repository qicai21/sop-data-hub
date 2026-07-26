"""混装检装单到站拆段 + 权威车号 expected。"""
from __future__ import annotations

import sqlite3

from sop_hub.sop.inspection_destination_split import (
    filter_rows_for_project_destination,
    resolve_authoritative_loading_cars,
    segment_expected_count,
    split_payload_by_destination,
)


def _row(seq: int, car: str, raw: str, effective: str, defect: bool = False) -> dict:
    return {
        "seq": seq,
        "car_no": car,
        "cargo_info_raw": raw,
        "cargo_info_effective": effective,
        "defect": defect,
        "global_index": seq,
    }


def _baoli_mixed_payload() -> dict:
    """宝丽混装:汐子 16 装 + 1 临修 + 乌兰浩特 29。"""
    rows = []
    # 汐子 1-9
    xizi = [
        "1805202", "1839171", "1820709", "4869418", "4894582", "1823273",
        "1838402", "1683294", "1867537",
    ]
    for i, c in enumerate(xizi, 1):
        raw = "汐子铁矿粉" if i == 1 else ("16节" if i == 4 else "")
        eff = "汐子铁矿粉" if i == 1 else "汐子铁矿粉/16节"
        if i == 2:
            raw, eff = "沈阳盛京颐昇代", "汐子铁矿粉/沈阳盛京颐昇代"
        if i == 3:
            raw, eff = "宝丽", "汐子铁矿粉/宝丽"
        rows.append(_row(i, c, raw, eff))
    rows.append(_row(10, "1624392", "临修", "汐子铁矿粉/临修", defect=True))
    for i, c in enumerate(
        ["1881303", "1764236", "1783096", "4915265", "1809565", "4876173", "4969819"],
        11,
    ):
        rows.append(_row(i, c, "", "汐子铁矿粉/临修"))
    # 乌兰 18-46 = 29 车
    wulan = [f"W{i:06d}" for i in range(1, 30)]
    for j, c in enumerate(wulan):
        seq = 18 + j
        raw = "乌兰浩特铁矿粉" if j == 0 else ("29节" if j == 3 else "")
        eff = "乌兰浩特铁矿粉" if j == 0 else "乌兰浩特铁矿粉/29节"
        if j == 1:
            raw, eff = "沈阳盛京颐昇代", "乌兰浩特铁矿粉/沈阳盛京颐昇代"
        rows.append(_row(seq, c, raw, eff))
    return {
        "project": "zhongtang_special_steel",
        "destination": "汐子",
        "rows": rows,
        "footer": {"zhuangche_jieshu": 45, "paiche_jieshu": 1},
        "meta": {"jieshu": 46, "daoxian": "煤五"},
        "car_nos": [r["car_no"] for r in rows],
    }


def test_split_baoli_mixed_drops_wulan_keeps_xizi16():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE release_batches("
        "id TEXT, project TEXT, destination_station TEXT, dispatch_status TEXT)"
    )
    conn.execute(
        "INSERT INTO release_batches VALUES ('b1','zhongtang_special_steel','汐子','loading')"
    )
    payload = _baoli_mixed_payload()
    groups = split_payload_by_destination(
        payload, conn=conn, project_id="zhongtang_special_steel",
    )
    assert len(groups) == 2
    xizi = [g for g in groups if not g.get("_split_group_unmatched")]
    wulan = [g for g in groups if g.get("_split_group_unmatched")]
    assert len(xizi) == 1 and len(wulan) == 1
    assert xizi[0]["destination"] == "汐子"
    assert xizi[0]["footer"]["zhuangche_jieshu"] == 16
    assert len([r for r in xizi[0]["rows"] if not r.get("defect")]) == 16
    assert wulan[0]["destination"] == "乌兰浩特"
    assert len(wulan[0]["rows"]) == 29


def test_leading_rows_before_first_destination_do_not_create_extra_candidate():
    """排车/表头前缀应并入首个明确到站段，不得拆出第二条候选。"""
    rows = [
        _row(1, "4902260", "空排", "未知到站/未知货名/空排", defect=True),
        _row(2, "1891463", "空排", "未知到站/未知货名/空排", defect=True),
        _row(3, "4903368", "收货人/船名/到站", "未知到站/未知货名/收货人/船名/到站"),
        _row(4, "1741438", "汐子铁矿粉", "汐子铁矿粉"),
        _row(5, "4932193", "宝丽", "汐子铁矿粉/宝丽"),
    ]
    payload = {
        "project": "zhongtang_special_steel",
        "destination": "汐子",
        "rows": rows,
        "footer": {"zhuangche_jieshu": 3, "paiche_jieshu": 2},
    }

    groups = split_payload_by_destination(
        payload, project_id="zhongtang_special_steel",
    )

    assert len(groups) == 1
    assert groups[0] is payload


def test_filter_rows_keeps_only_xizi_for_zhongtang():
    payload = _baoli_mixed_payload()
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE release_batches("
        "id TEXT, project TEXT, destination_station TEXT, dispatch_status TEXT)"
    )
    conn.execute(
        "INSERT INTO release_batches VALUES ('b1','zhongtang_special_steel','汐子','loading')"
    )
    scoped = filter_rows_for_project_destination(
        payload["rows"],
        destination="汐子",
        project_id="zhongtang_special_steel",
        conn=conn,
    )
    assert len(scoped) == 17  # 16 load + 1 defect
    assert all(
        "汐子" in (r.get("cargo_info_effective") or "") for r in scoped
    )


def test_ops_note_authoritative_cars():
    cars = ["1805202", "1839171"]
    payload = {
        "rows": [_row(1, "1805202", "汐子", "汐子"), _row(2, "9999999", "乌兰浩特", "乌兰浩特")],
        "mixed_load_ops_note": {"zhongtang_cars": cars},
    }
    auth = resolve_authoritative_loading_cars(payload=payload)
    assert auth == cars


def test_segment_expected_uses_non_defect():
    rows = [
        _row(1, "1", "汐子", "汐子"),
        _row(2, "2", "临修", "汐子", defect=True),
        _row(3, "3", "", "汐子"),
    ]
    assert segment_expected_count(rows, {"zhuangche_jieshu": 45}) == 2
