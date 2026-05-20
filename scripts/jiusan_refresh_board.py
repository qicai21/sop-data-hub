#!/usr/bin/env python3
"""
九三大豆看板 — 手动刷新 wrapper（V3 架构）

Phase 1 V3 阶段：看板已改为直接映射 95306 实时数据，无冲突检测。

Usage:
    python3 scripts/jiusan_refresh_board.py [--without-tracking]

Options:
    --without-tracking  跳过追踪状态更新（仅生成看板）
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
    skip_tracking = "--without-tracking" in sys.argv

    start = datetime.now()
    print(f"\n{'=' * 60}")
    print(f"九三大豆看板 V3 — 手动刷新")
    print(f"开始时间: {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"运行状态直接映射 95306，无冲突检测")
    print(f"{'=' * 60}")

    # Step 1: 追踪状态更新（可选）
    if not skip_tracking:
        run_step("1/2", ["python3", "scripts/jiusan_tracking_update.py"], optional=True)
    else:
        print("\n[1/2] ⏭️ 跳过追踪状态更新 (--without-tracking)")

    # Step 2: 生成看板
    ok = run_step("2/2", ["python3", "scripts/jiusan_board_generate.py", "--example"])
    if not ok:
        sys.exit(1)

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n{'=' * 60}")
    print(f"✅ 看板刷新完成（耗时 {elapsed:.0f} 秒）")
    print(f"打开 dashboard/jiusan_dashboard.html 查看最新数据")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
