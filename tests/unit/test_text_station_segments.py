"""Regression cases from the July 铁晟业务工作群 station-text corpus."""

import json
from pathlib import Path

from sop_hub.sop.text_router import extract_inspection_text_triggers
from sop_hub.sop.text_station_segments import extract_station_segments


def _by_ship(text: str) -> dict[str, dict]:
    return {item["ship"]: item for item in extract_inspection_text_triggers(text)}


def test_july_station_text_corpus_is_versioned_and_deduplicated():
    path = Path(__file__).resolve().parents[1] / "fixtures" / "workgroup_station_text_corpus_2026-07.json"
    corpus = json.loads(path.read_text(encoding="utf-8"))
    assert corpus["source"] == "message_inbox/铁晟业务工作群"
    assert corpus["month"] == "2026-07"
    assert corpus["matched_message_count"] == 202
    assert corpus["unique_pattern_count"] == 138
    assert any(item["example"]["message_id"] == "wx_2026-07_2482" for item in corpus["patterns"])


def test_mixed_sop_and_wulan_segment_does_not_leak_count_to_baoli():
    triggers = _by_ship("煤四，53节汐子铁宝丽，3节乌铁春日莲花")
    assert triggers == {
        "宝丽": {
            "project_id": "zhongtang_special_steel",
            "ship": "宝丽",
            "destination": "汐子",
            "expected_count": 53,
            "segment": "煤四，53节汐子铁宝丽，3节",
        }
    }


def test_full_wulan_name_has_the_same_non_sop_barrier_effect():
    triggers = _by_ship("煤四，53节汐子铁宝丽，3节乌兰浩特铁春日莲花")
    assert triggers["宝丽"]["expected_count"] == 53
    assert triggers["宝丽"]["destination"] == "汐子"


def test_lingdong_and_full_lingyuandong_are_non_sop_barriers():
    for text in ("煤五凌东铁宝腾海51节", "煤五凌源东铁宝腾海51节"):
        assert extract_inspection_text_triggers(text) == []


def test_chaoyangxi_still_creates_chaoyang_trigger():
    triggers = _by_ship("煤五朝阳西铁宝腾海51节")
    assert triggers["宝腾海"]["project_id"] == "chaoyang_steel"
    assert triggers["宝腾海"]["destination"] == "朝阳西"
    assert triggers["宝腾海"]["expected_count"] == 51


def test_same_station_multiple_sop_ships_keep_their_own_counts():
    triggers = _by_ship("煤五 汐子铁 宝丽19节 鞍子河10节共29节")
    assert triggers["宝丽"]["expected_count"] == 19
    assert triggers["鞍子河"]["expected_count"] == 10
    assert {item["destination"] for item in triggers.values()} == {"汐子"}


def test_explicit_loaded_count_before_next_station_belongs_to_previous_ship():
    triggers = _by_ship("煤四 汐子铁 鞍子河 实装22节乌铁春日莲花25节")
    assert triggers == {
        "鞍子河": {
            "project_id": "zhongtang_special_steel",
            "ship": "鞍子河",
            "destination": "汐子",
            "expected_count": 22,
            "segment": "煤四 汐子铁 鞍子河 实装22节",
        }
    }


def test_aliases_are_canonicalised_and_unknown_sop_ship_is_not_inferred():
    segments = extract_station_segments("煤四 乌铁春日莲花25节，沙子铁鞍子河22节")
    assert [(item.alias, item.destination, item.project_id) for item in segments] == [
        ("乌铁", "乌兰浩特", ""),
        ("沙子铁", "汐子", "zhongtang_special_steel"),
    ]
    triggers = _by_ship("煤四 乌铁春日莲花25节，沙子铁鞍子河22节")
    assert triggers == {
        "鞍子河": {
            "project_id": "zhongtang_special_steel",
            "ship": "鞍子河",
            "destination": "汐子",
            "expected_count": 22,
            "segment": "沙子铁鞍子河22节",
        }
    }
