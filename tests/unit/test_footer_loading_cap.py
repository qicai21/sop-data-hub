"""Footer 实装 cap for inspection loading car list."""

from sop_hub.sop.inspection_window_recover import apply_footer_loading_cap


def test_footer_cap_trims_extra_non_defect_cars():
    cars = [f"c{i:02d}" for i in range(1, 57)]  # 56 rows
    out = apply_footer_loading_cap(cars, {"zhuangche_jieshu": 53, "paiche_jieshu": 3})
    assert len(out) == 53
    assert out[0] == "c01"
    assert out[-1] == "c53"


def test_footer_cap_noop_when_count_ok():
    cars = [f"c{i}" for i in range(10)]
    assert apply_footer_loading_cap(cars, {"zhuangche_jieshu": 10}) == cars


def test_footer_cap_noop_without_footer():
    cars = ["a", "b", "c"]
    assert apply_footer_loading_cap(cars, None) == cars
    assert apply_footer_loading_cap(cars, {}) == cars
