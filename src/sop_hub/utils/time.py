"""统一时间戳工具 — 2026-06-02 时区修复(TZ.2)。

历史问题:仓库里同时混着至少 5 种 timestamp 风格(naive UTC / naive
Beijing / ISO UTC +00:00 / ISO UTC Z / ISO Beijing),消费端常常猜不
出哪种,造成 dashboard 显示偏 8h、verifier elapsed 算偏 8h 等 bug。

本模块定下统一格式:**ISO 8601 with explicit Beijing offset**
  `2026-06-02T14:30:00+08:00`

理由:
- 人看就是北京时间,不用脑补 UTC+8
- 机器 parse 也精确:`datetime.fromisoformat(s)` 自带 tz 信息
- 跨子系统流转(DB / JSON / API)都不丢失语义

写入端:用 `now_iso_beijing()` 替代所有 datetime.now() / datetime.utcnow()
   / datetime.now(timezone.utc).isoformat() 的 DB/JSON 写入。
   (文件名/目录路径用的 strftime 不必改,纯字符串无 tz 问题)

读取端:用 `parse_any_timestamp(s)` 容错解析任意旧格式 → 返回 tz-aware
   datetime,再 .astimezone(BEIJING_TZ) 转 Beijing。

迁移:`to_iso_beijing(any)` 把任意旧字符串归一成新格式(给 migration 用)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


# 北京时区(UTC+8,无夏令时)
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def now_iso_beijing() -> str:
    """返回当前北京时间的 ISO 字符串,带 +08:00 offset。

    例: '2026-06-02T14:30:25.123456+08:00'

    秒以下保留 microsecond,方便排序/去重。如不需要,调用方自己 ::-N 截。
    """
    return datetime.now(BEIJING_TZ).isoformat()


def now_iso_beijing_compact() -> str:
    """带秒不带 microsecond 的版本,适合写 SQLite TEXT 列(更短、可读)。

    例: '2026-06-02T14:30:25+08:00'
    """
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def parse_any_timestamp(s: str | None) -> datetime | None:
    """容错解析任意 timestamp 字符串 → 返回 tz-aware datetime(北京时区)。

    支持的输入格式:
    - ISO 带 tz: '2026-06-02T00:23:25+00:00' / '...Z' / '...+08:00'
    - ISO naive: '2026-06-02T00:23:25' → 假设是 UTC(SQLite 默认/历史 utcnow)
    - 空格 naive: '2026-06-02 00:23:25' → 假设是 UTC(SQLite CURRENT_TIMESTAMP)
    - 空字符串 / None → 返回 None

    所有 naive 输入都按 **UTC** 解读,因为仓库历史里 naive 来源
    几乎全是 SQLite CURRENT_TIMESTAMP / datetime.utcnow()。**唯一例外**
    是 wx-ops-agent 写的 jsonl 里的 received_datetime,它是 naive Beijing
    — 调用方如知道来源是 wx,应直接传 assume_tz=BEIJING_TZ。
    """
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    # 兜底:Z → +00:00
    s = s.replace("Z", "+00:00")
    # 空格分隔的 → ISO 化
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        # naive → 假设 UTC(SQLite/utcnow 历史默认)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING_TZ)


def parse_any_timestamp_assume(s: str | None, assume_tz: timezone) -> datetime | None:
    """parse_any_timestamp 的 explicit-tz 版,naive 输入按给定 tz 解读。

    适用场景:wx-ops-agent 的 received_datetime(naive Beijing)。
    """
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=assume_tz)
    return dt.astimezone(BEIJING_TZ)


def to_iso_beijing(s: str | None, assume_naive_utc: bool = True) -> str | None:
    """把任意旧格式字符串归一成 ISO Beijing 格式。给 migration 用。

    返回 None 仅当输入空。其他情况输出 `2026-06-02T14:30:25+08:00`。
    """
    if assume_naive_utc:
        dt = parse_any_timestamp(s)
    else:
        dt = parse_any_timestamp_assume(s, BEIJING_TZ)
    if dt is None:
        return None
    return dt.isoformat(timespec="seconds")
