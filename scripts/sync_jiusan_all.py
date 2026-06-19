"""九三和谐1 发运自动同步(集装箱 + 散粮)—— 供 launchd 定时调度。

每跑一次:
  1. sync_jiusan_harmony_wagons.py  —— 集装箱循环列(默认 commit)
  2. sync_jiusan_bulk_wagons.py --apply —— 散粮整车

两个脚本都幂等(只新增/刷状态),重复跑安全。看板实时读 DB,跑完即新。
退出码:全成功 0;任一失败非 0(launchd 日志可查)。

手动:python scripts/sync_jiusan_all.py
定时:com.qicai21.sop-data-hub.jiusan-sync.plist(StartInterval)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

PY = sys.executable
STEPS = [
    ["scripts/sync_jiusan_harmony_wagons.py"],          # 集装箱
    ["scripts/sync_jiusan_bulk_wagons.py", "--apply"],  # 散粮
]


def main() -> int:
    stamp = now_iso_beijing()
    print(f"\n===== 九三自动同步 {stamp} =====", flush=True)
    rc = 0
    for step in STEPS:
        print(f"--- 跑 {' '.join(step)} ---", flush=True)
        r = subprocess.run([PY, *step], cwd=str(REPO),
                           capture_output=True, text=True)
        # 只回显结果尾部,避免日志膨胀
        tail = "\n".join((r.stdout or "").rstrip().splitlines()[-6:])
        if tail:
            print(tail, flush=True)
        if r.returncode != 0:
            rc = r.returncode
            print(f"!! 失败 rc={r.returncode}\n{(r.stderr or '').strip()[-800:]}",
                  flush=True)
    print(f"===== 完成 rc={rc} =====", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
