"""Functional tests for departure text parser — R33."""

from sop_hub.sop.departure_text_parser import DepartureCandidate, parse_departure_text
from sop_hub.sop.monitoring_plan_matcher import MessageEvent


# ── Jilin Jingang / 四平 ─────────────────────────────────────────────


def test_py_6dao_siping_46cars():
    """'6道, 四平铁, 46车' → complete with destination=四平"""
    c = parse_departure_text("6道，四平铁，46车")
    assert c.status == "complete"
    assert c.destination == "四平"
    assert c.car_count == 46
    assert c.lane_or_track == "6道"
    assert c.project_id == "jilin_jingang_jinzhou"


def test_py_14dao_siping_lanqi():
    """'十四道, 四平铁, 蓝鳍, 18车' → ship + destination"""
    c = parse_departure_text("十四道，四平铁，蓝鳍，18车")
    assert c.status == "complete"
    assert c.destination == "四平"
    assert c.car_count == 18
    assert c.lane_or_track == "十四道"
    assert c.optional_ship_name == "蓝鳍"
    assert c.project_id == "jilin_jingang_jinzhou"


def test_py_6dao_siping_direction_changhang():
    """'6道, 四平方向, 长航滨海, 46车' → destination + ship"""
    c = parse_departure_text("6道，四平方向，长航滨海，46车")
    assert c.status == "complete"
    assert c.destination == "四平"
    assert c.car_count == 46
    assert c.optional_ship_name == "长航滨海"
    assert c.project_id == "jilin_jingang_jinzhou"


def test_py_siping_only_keyword():
    """Just '四平' in text → detected as departure (no lane, has destination)"""
    c = parse_departure_text("四平，46车")
    assert c.status == "complete"
    assert c.destination == "四平"
    assert c.car_count == 46
    assert c.project_id == "jilin_jingang_jinzhou"


# ── Missing car_count → incomplete ───────────────────────────────────


def test_py_missing_car_count_incomplete():
    """'6道，四平方向' → destination but no car_count → incomplete"""
    c = parse_departure_text("6道，四平方向")
    assert c.status == "incomplete"
    assert c.destination == "四平"
    assert c.car_count == -1
    assert c.lane_or_track == "6道"
    assert c.project_id == "jilin_jingang_jinzhou"


def test_py_no_count_no_lane_still_incomplete():
    """Just '四平方向' → incomplete (no car_count)"""
    c = parse_departure_text("四平方向")
    assert c.status == "incomplete"
    assert c.destination == "四平"
    assert c.car_count == -1


# ── Non-departure text → no_match ─────────────────────────────────────


def test_py_irrelevant_text_no_match():
    """'今天天气不错' → no_match"""
    c = parse_departure_text("今天天气不错")
    assert c.status == "no_match"


def test_py_freight_detail_no_match():
    """Freight detail text should not be caught as departure."""
    c = parse_departure_text("订单标识 CGR20260518174420，合同号 JGCG-SFY-HTNK20260501")
    assert c.status == "no_match"


# ── Chaoyang / ChaoXi ────────────────────────────────────────────────


def test_py_chaoxi_departure():
    """'十四道，朝阳西铁矿，木森17，装55节' → chaoyang_steel"""
    c = parse_departure_text("十四道，朝阳西铁矿，木森17，装55节")
    assert c.status == "complete"
    assert c.destination == "朝阳西"
    assert c.car_count == 55
    assert c.lane_or_track == "十四道"
    assert c.optional_ship_name == "木森17"
    assert c.project_id == "chaoyang_steel"


def test_py_chaotie_alias():
    """'朝阳铁' → chaoyang_steel"""
    c = parse_departure_text("6道，朝阳铁，32车")
    assert c.status == "complete"
    assert c.destination == "朝阳西"
    assert c.car_count == 32
    assert c.project_id == "chaoyang_steel"


# ── Shizi / 汐子 ──────────────────────────────────────────────────────


def test_py_shizi_departure():
    """'汐子，贝拉，48车' → zhongtang"""
    c = parse_departure_text("汐子，贝拉，48车")
    assert c.status == "complete"
    assert c.destination == "汐子"
    assert c.car_count == 48
    assert c.optional_ship_name == "贝拉"
    assert c.project_id == "zhongtang_special_steel"


def test_py_shazi_alias():
    """'沙子' → 汐子 (OCR alias)"""
    c = parse_departure_text("沙子，28车")
    assert c.status == "complete"
    assert c.destination == "汐子"
    assert c.car_count == 28
    assert c.project_id == "zhongtang_special_steel"


# ── MessageEvent integration ──────────────────────────────────────────


def test_py_from_message_event():
    """Parser accepts MessageEvent directly."""
    event = MessageEvent(
        message_id="wx_001",
        channel="wechat",
        group_id="GROUP001",
        message_type="text",
        received_at="2026-05-28T08:00:00Z",
        text="6道，四平铁，46车",
    )
    c = parse_departure_text(event)
    assert c.status == "complete"
    assert c.message_id == "wx_001"
    assert c.group_id == "GROUP001"
    assert c.message_time == "2026-05-28T08:00:00Z"
    assert c.destination == "四平"
    assert c.car_count == 46


def test_py_empty_text_no_match():
    c = parse_departure_text("")
    assert c.status == "no_match"


# ── to_dict() ─────────────────────────────────────────────────────────


def test_py_to_dict():
    c = parse_departure_text("6道，四平铁，46车")
    d = c.to_dict()
    assert d["destination"] == "四平"
    assert d["car_count"] == 46
    assert d["status"] == "complete"
    assert d["source"] == "departure_text_parser"
    assert d["project_id"] == "jilin_jingang_jinzhou"


# ── Car count edge cases ──────────────────────────────────────────────


def test_py_53_cars():
    """SOP pattern: 53车"""
    c = parse_departure_text("十四道，四平铁，53车")
    assert c.status == "complete"
    assert c.car_count == 53
    assert c.destination == "四平"


def test_py_55_cars():
    """SOP pattern: 55车"""
    c = parse_departure_text("6道，四平铁，55车")
    assert c.status == "complete"
    assert c.car_count == 55


def test_py_zhuang_55_jie():
    """'装55节' format"""
    c = parse_departure_text("十四道，四平铁，装55节")
    assert c.status == "complete"
    assert c.car_count == 55


def test_py_ship_name_without_cars_incomplete():
    """Ship name alone does not make it a departure text → needs car count or destination"""
    c = parse_departure_text("长航滨海")
    assert c.status == "no_match"


# ── R40: New departure text patterns ──────────────────────────────────

def test_py_28jie_siping_lanqi():
    """'28节四平铁，蓝鳍（26-60位）' — car before dest, ship, paren range"""
    c = parse_departure_text("28节四平铁，蓝鳍（26-60位）")
    assert c.status == "complete"
    assert c.destination == "四平"
    assert c.car_count == 28
    assert c.optional_ship_name == "蓝鳍"
    assert c.project_id == "jilin_jingang_jinzhou"


def test_py_mei6_siping_lanqi_18jie():
    """'煤六   四平铁"蓝鳍"18节' — lane, Chinese-quoted ship, 节"""
    text = "煤六   四平铁\u201c蓝鳍\u201d18节"
    c = parse_departure_text(text)
    assert c.status == "complete"
    assert c.destination == "四平"
    assert c.car_count == 18
    assert c.optional_ship_name == "蓝鳍"
    assert "煤六" in c.lane_or_track
    assert c.project_id == "jilin_jingang_jinzhou"


def test_py_9dao_sipingnie_changhang_46jie():
    """'九道   四平镍"长航滨海"46节' — 四平镍, Chinese-quoted ship"""
    text = "九道   四平镍\u201c长航滨海\u201d46节"
    c = parse_departure_text(text)
    assert c.status == "complete"
    assert c.destination == "四平"
    assert c.car_count == 46
    assert c.optional_ship_name == "长航滨海"
    assert "九道" in c.lane_or_track
    assert c.project_id == "jilin_jingang_jinzhou"


def test_py_14dao_41jie_siping_xiamenshiji():
    """'十四道 41节 四平铁 厦门世纪' — lane, car, dest, unknown ship"""
    c = parse_departure_text("十四道 41节 四平铁 厦门世纪")
    assert c.status == "complete"
    assert c.destination == "四平"
    assert c.car_count == 41
    assert c.optional_ship_name == "厦门世纪"
    assert "十四道" in c.lane_or_track
    assert c.project_id == "jilin_jingang_jinzhou"


def test_py_mei6_39jie_siping_zhihui():
    """'煤六 39节 四平铁 智慧' — lane, car, dest, unknown ship"""
    c = parse_departure_text("煤六 39节 四平铁 智慧")
    assert c.status == "complete"
    assert c.destination == "四平"
    assert c.car_count == 39
    assert c.optional_ship_name == "智慧"
    assert "煤六" in c.lane_or_track
    assert c.project_id == "jilin_jingang_jinzhou"

