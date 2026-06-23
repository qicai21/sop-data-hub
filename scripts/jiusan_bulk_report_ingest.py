"""九三散粮报表(薛雪红 `<船> 散粮车<日>.xlsx`)自动 ingest → sync(#issue 散粮sync按船拆分 运维自动化)。

跟"晨报自动ingest"配套,消掉九三第二个手工日更:扫微信文件目录 → 找比上次处理更新的
散粮车报表 → 每船取最新一份(报表累计,后版含全 sheet,supersede 旧版)→ 调
`ingest_bulk_loading_notice.py` 落台账 → 跑一次 `sync_jiusan_bulk_wagons.py` 按船重路由。

幂等:状态文件记上次处理的最大 mtime,无更新文件直接跳过;ingest 本身 delete-then-insert
per(船+日)。⚠ 95306 权威,报表只供分票(见记忆 jiusan-bulk-multiship-routing)。

用法:python scripts/jiusan_bulk_report_ingest.py [--apply] [--dry-run]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = "/opt/homebrew/bin/python3.14"
WX_FILE_GLOB = ("/Users/qicai21/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
                "xwechat_files/wxid_xby4wwyshvxr22_5815/msg/file/*/* 散粮车*.xlsx")
STATE = REPO / "runtime" / "jiusan_bulk_report_state.json"
SHIPS = ("诚信", "和谐1")   # 九三循环散粮船;只跟这俩


def _latest_per_ship() -> dict[str, str]:
    """每船的最新散粮车报表文件(按 mtime;累计报表后版 supersede)。"""
    out: dict[str, str] = {}
    for f in glob.glob(WX_FILE_GLOB):
        name = Path(f).name
        ship = next((s for s in SHIPS if name.startswith(s)), None)
        if not ship:
            continue
        if ship not in out or os.path.getmtime(f) > os.path.getmtime(out[ship]):
            out[ship] = f
    return out


def _load_state() -> float:
    try:
        return float(json.loads(STATE.read_text()).get("last_mtime", 0))
    except Exception:
        return 0.0


def main(apply: bool = False, dry_run: bool = False) -> None:
    latest = _latest_per_ship()
    if not latest:
        print("✗ 没找到任何散粮车报表文件")
        return
    last = _load_state()
    # 哪些船有"比上次新"的报表
    todo = {s: f for s, f in latest.items() if os.path.getmtime(f) > last}
    if not todo:
        print(f"无新散粮报表(上次处理 mtime={last:.0f});各船最新="
              + ", ".join(f"{s}:{Path(f).name}" for s, f in latest.items()))
        return

    print("=== 待 ingest 的新报表 ===")
    for s, f in todo.items():
        print(f"  {s}: {Path(f).name}")
    if dry_run or not apply:
        print("\nDRY-RUN(加 --apply 真跑 ingest + sync)")
        return

    env = {**os.environ, "PYTHONPATH": "src"}
    for s, f in todo.items():
        print(f"\n--- ingest {Path(f).name} ---")
        r = subprocess.run([PY, "scripts/ingest_bulk_loading_notice.py", f, "--apply"],
                           cwd=str(REPO), env=env, capture_output=True, text=True)
        print(r.stdout.strip()[-400:] or r.stderr.strip()[-300:])
    print("\n--- sync_jiusan_bulk_wagons --apply ---")
    r = subprocess.run([PY, "scripts/sync_jiusan_bulk_wagons.py", "--apply"],
                       cwd=str(REPO), env=env, capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if any(k in line for k in ("诚信", "和谐1", "改动", "新增", "WARN")):
            print("  " + line)
    # 推进状态(取所有报表的最大 mtime,避免反复处理)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"last_mtime": max(os.path.getmtime(f) for f in latest.values())}))
    print("\nCOMMIT ✓ 散粮报表已 ingest + sync,状态推进")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    main(apply=a.apply, dry_run=a.dry_run)
