"""守门:发运对齐延迟门 + 装车线路归一(2026-06-16 设定)。

这两条是 06-16 马兰希望"发错 excel"复盘后加的:
  1. 延迟门:发运文本到群时 95306 还在制单,延迟 N 小时再对齐,避免抓空车号。
  2. 装车线路归一:六道/6道/煤6 等多写法收敛成中文大写正名(煤六/七道…)。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sop_hub.sop.departure_text_parser import canonicalize_loading_line
from sop_hub.sop.executor_runner import _dispatch_delay_gate, _dispatch_delay_hours
from sop_hub.utils.time import now_iso_beijing_compact


# ── 装车线路归一 ────────────────────────────────────────────────────────

def test_loading_line_merges_variants_to_canonical():
    # 1-6 号 → 煤<中文>;六道/6道/煤6/港6 全归 煤六(用户确认)
    for v in ("六道", "6道", "煤6", "煤六", "港6", "煤6道"):
        assert canonicalize_loading_line(v) == "煤六", v
    # 7 号及以上 → <中文>道;港7/7道/七道 全归 七道
    for v in ("港7", "7道", "七道"):
        assert canonicalize_loading_line(v) == "七道", v


def test_loading_line_handles_two_digit_and_edge():
    assert canonicalize_loading_line("十四道") == "十四道"
    assert canonicalize_loading_line("14道") == "十四道"
    assert canonicalize_loading_line("港14") == "十四道"
    assert canonicalize_loading_line("十道") == "十道"
    # 1~6 边界:五→煤五,六→煤六,七→七道
    assert canonicalize_loading_line("5道") == "煤五"
    assert canonicalize_loading_line("煤五") == "煤五"
    assert canonicalize_loading_line("七道") == "七道"


def test_loading_line_unparseable_preserved_not_dropped():
    assert canonicalize_loading_line("") == ""
    assert canonicalize_loading_line(None) == ""
    # 解析不出线号 → 留痕原文(去空格),不丢
    assert canonicalize_loading_line("未知线") == "未知线"


# ── 发运对齐延迟门 ──────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.strptime(
        now_iso_beijing_compact()[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S"
    )


def test_jilin_delay_hours_from_yaml():
    assert _dispatch_delay_hours("jilin_jingang_jinzhou") == 2.0


def test_recent_dispatch_is_deferred():
    ref = _now().strftime("%Y-%m-%d %H:%M:%S")  # 刚发
    ok_at, defer = _dispatch_delay_gate("jilin_jingang_jinzhou", ref)
    assert defer is True
    assert ok_at  # 给出可跑时刻


def test_old_dispatch_runs_immediately():
    ref = (_now() - timedelta(hours=2, minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    _ok_at, defer = _dispatch_delay_gate("jilin_jingang_jinzhou", ref)
    assert defer is False


def test_bad_time_fails_open_never_stuck():
    # 解析不了文本时间 → 不延迟(宁可跑,绝不永久挂起)
    _ok_at, defer = _dispatch_delay_gate("jilin_jingang_jinzhou", "not-a-time")
    assert defer is False
