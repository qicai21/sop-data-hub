#!/usr/bin/env python3.14
"""定期清理巡检 — 读 cleanup_ledger.toml,把"到期且验证通过"的延迟清理项主动提醒出来。

设计原则:
- **只提示,绝不自动删**。删除永远人工确认(由人/Claude session 复核 verify 再删)。
- 零三方依赖:tomllib(stdlib)解析台账,osascript 发 macOS 通知。
- 幂等可重复跑:每次重新评估,不改台账(状态由人手动改 status=done)。

由 launchd(com.qicai.cleanup-check)每周跑一次,也可手动:
    /opt/homebrew/bin/python3.14 maintenance/cleanup_check.py
退出码:0=无到期项 / 有但已巡检完;2=有"可删"项在等你确认(方便脚本串联)。
"""
from __future__ import annotations

import datetime as _dt
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "cleanup_ledger.toml"
REPORT = HERE / "last_cleanup_report.md"


def _today() -> _dt.date:
    return _dt.date.today()


def _parse_date(s: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(str(s).strip())
    except Exception:
        return None


def _run_verify(cmd: str) -> bool:
    """一条 verify 通过 = exit 0。"""
    try:
        r = subprocess.run(["bash", "-lc", cmd], capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _du(paths: list[str]) -> str:
    existing = [p for p in paths if Path(p).exists()]
    if not existing:
        return "0(路径已不存在)"
    try:
        out = subprocess.run(
            ["du", "-shc", *existing], capture_output=True, text=True, timeout=60
        ).stdout.strip().splitlines()
        return out[-1].split("\t")[0] if out else "?"
    except Exception:
        return "?"


def _notify(title: str, message: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {message!r} with title {title!r}'],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


def main() -> int:
    if not LEDGER.exists():
        print(f"[cleanup] 台账不存在: {LEDGER}")
        return 0

    data = tomllib.loads(LEDGER.read_text(encoding="utf-8"))
    items = data.get("items", [])
    today = _today()

    ripe_clean: list[dict] = []     # 到期 + verify 全过 → 可删
    ripe_blocked: list[dict] = []   # 到期 + 有 verify 失败 → 先别删
    waiting: list[dict] = []        # 未到期
    lines: list[str] = [f"# 清理巡检报告 — {today.isoformat()}", ""]

    for it in items:
        if it.get("status") != "pending":
            continue
        ripe_after = _parse_date(it.get("ripe_after", ""))
        item_id = it.get("id", "?")
        desc = it.get("description", "")
        paths = it.get("paths", [])
        if ripe_after is None:
            lines.append(f"- ⚠️ `{item_id}`:ripe_after 日期无法解析,跳过")
            continue
        if today < ripe_after:
            waiting.append(it)
            days = (ripe_after - today).days
            lines.append(f"- ⏳ `{item_id}`:还有 {days} 天到期({ripe_after.isoformat()})")
            continue

        # 到期 → 跑 verify
        checks = it.get("verify", [])
        results = [(c.get("desc", ""), _run_verify(c.get("cmd", "false"))) for c in checks]
        all_pass = all(ok for _, ok in results)
        size_now = _du(paths)
        if all_pass:
            ripe_clean.append(it)
            lines.append(f"- ✅ **可删** `{item_id}`(回收 {size_now})— {desc}")
            for d, ok in results:
                lines.append(f"    - ✓ {d}")
            for p in paths:
                lines.append(f"    - 路径:`{p}`")
        else:
            ripe_blocked.append(it)
            lines.append(f"- 🚫 **到期但还有引用** `{item_id}` — {desc}")
            for d, ok in results:
                lines.append(f"    - {'✓' if ok else '✗ 未通过'} {d}")

    lines += [
        "",
        f"小结:可删 {len(ripe_clean)} · 被阻 {len(ripe_blocked)} · 等待 {len(waiting)}",
        "",
        "可删项需人工确认:跟 Claude 说\"复核并清理 cleanup ledger\","
        "我会重跑 verify 再删,删后把该项 status 改 done。",
    ]
    report = "\n".join(lines)
    REPORT.write_text(report + "\n", encoding="utf-8")
    print(report)

    if ripe_clean:
        ids = ", ".join(i.get("id", "?") for i in ripe_clean)
        _notify(
            "清理台账:有可删项",
            f"{len(ripe_clean)} 项到期且验证通过({ids})。打开 maintenance/last_cleanup_report.md 查看,跟 Claude 说清理。",
        )
        return 2
    if ripe_blocked:
        _notify(
            "清理台账:到期但被阻",
            f"{len(ripe_blocked)} 项到期但仍有引用,见 last_cleanup_report.md。",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
