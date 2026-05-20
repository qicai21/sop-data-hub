#!/usr/bin/env python3
"""
九三大豆看板 — 手动刷新 wrapper

Phase 2.5 阶段：无自动定时服务，需手动运行此脚本刷新看板。

Usage:
    python3 scripts/jiusan_refresh_board.py [--without-field] [--without-tracking]

Options:
    --without-field     跳过人工实况录入（仅首次初始化+追踪+看板）
    --without-tracking  跳过追踪状态更新
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_step(step_name: str, command: list[str], optional: bool = False) -> bool:
    print(f"\n{'=' * 60}")
    print(f"[{step_name}] {' '.join(command)}")
    print(f"{'=' * 60}")
    result = subprocess.run(command, cwd=str(REPO_ROOT), capture_output=False)
    if result.returncode != 0 and not optional:
        print(f"[ERROR] {step_name} 失败，退出码 {result.returncode}")
        return False
    if result.returncode != 0 and optional:
        print(f"[WARN] {step_name} 可选步骤失败（可忽略）")
    return True


def main():
    skip_field = "--without-field" in sys.argv
    skip_tracking = "--without-tracking" in sys.argv

    start = datetime.now()
    print(f"\n{'=' * 60}")
    print(f"九三大豆看板 — 手动刷新")
    print(f"开始时间: {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")

    # Step 1: 初始化 jiusan_cycle.db
    ok = run_step("1/4", ["python3", "scripts/jiusan_phase2_init.py", "--force"])
    if not ok:
        sys.exit(1)

    # Step 2: 人工实况录入（可选）
    if not skip_field:
        run_step("2/4", ["python3", "scripts/jiusan_phase2_5_field_update.py"], optional=True)
    else:
        print("\n[2/4] ⏭️ 跳过人工实况录入 (--without-field)")

    # Step 3: 追踪状态更新（可选）
    if not skip_tracking:
        run_step("3/4", ["python3", "scripts/jiusan_tracking_update.py"], optional=True)
    else:
        print("\n[3/4] ⏭️ 跳过追踪状态更新 (--without-tracking)")

    # Step 4: 生成看板
    ok = run_step("4/4", ["python3", "scripts/jiusan_board_generate.py", "--example"])
    if not ok:
        sys.exit(1)

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n{'=' * 60}")
    print(f"✅ 看板刷新完成（耗时 {elapsed:.0f} 秒）")
    print(f"刷新步骤：打开文件 → 重新加载浏览器")
    print(f"   dashboard/jiusan_dashboard.html")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
