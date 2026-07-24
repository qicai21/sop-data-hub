from sop_hub.sop.release_dispatch_priority import (
    release_dispatch_rule_priority,
    sequential_lot_priority_projects,
)


def test_configured_projects_use_lot_number_priority():
    projects = sequential_lot_priority_projects()
    assert {
        "zhongtang_special_steel",
        "chaoyang_steel",
        "jilin_jingang_jinzhou",
    }.issubset(projects)
    assert release_dispatch_rule_priority(
        "zhongtang_special_steel", "lot05"
    ) == (500, True)
    assert release_dispatch_rule_priority(
        "zhongtang_special_steel", "lot6"
    ) == (600, True)


def test_jiusan_parallel_transport_lots_do_not_get_sequential_priority():
    assert "jiusan" not in sequential_lot_priority_projects()
    assert release_dispatch_rule_priority("jiusan", "lot02") == (100, False)


def test_invalid_or_missing_sequence_is_not_derived():
    assert release_dispatch_rule_priority(
        "zhongtang_special_steel", "pending_lot"
    ) == (100, False)
    assert release_dispatch_rule_priority(
        "zhongtang_special_steel", None
    ) == (100, False)
