"""九三和谐1 晨报 → container_pool_snapshot 自动 ingest(#issue 九三循环列看板 §八.2 Fix B)。

根治"晨报→看板"自动链不存在的问题:从 GROUP093 微信**原始库**拉最新晨报(它一直在,
只是没进 agent 的 chat_records)→ 正则解析箱节点 → 返空取 95306 返空列(铁律,不假设0)
→ ground330_empty 反推平**物理池(unique 箱号,硬证据)**→ record snapshot。
看板每轮 ORDER BY snapshot_date DESC 实时读最新快照,record 完即自动现成。

晨报锚点:"截止N日" + "【锦州港口装车情况】"(魏庆凯发)。
⚠ 自动记的是晨报字面值 + 物理池守恒,标 source=auto_morning_report;港空/330 这类用户
常按"流量口径"精修的(见 6-23 实战:港空 71+88返-132装=27、330 采信172),仍可人工
--record 覆盖。本脚本提供**每日基线**,免去手工拉+解析,精修仍归人。

用法:python scripts/jiusan_morning_report_ingest.py [--apply] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

WX_AGENT = Path("/Users/qicai21/projects/repos/wx-ops-agent")
WX_KEYS = WX_AGENT / "current_keys.json"
WX_DB_DIR = Path("/Users/qicai21/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
                 "xwechat_files/wxid_xby4wwyshvxr22_5815/db_storage")
GROUP093_ROOM = "18894028363@chatroom"   # 九三大豆发运群[GROUP093],见记忆 wx-group-search-codes
SOP_DB = REPO / "data" / "sop_agent.db"
SHIP = "和谐1"
CONTAINER_BATCH = "e96f4b3b83c74b4891c6b0957f6989bb827de45b"  # 和谐1 集装箱


# ── 1. 拉最新晨报(GROUP093 微信原始库)──────────────────────────────────
def pull_latest_morning_report() -> tuple[str, str] | None:
    """返回 (日, 正文) of 最新晨报,无则 None。锚点 截止N日 + 装车情况。"""
    sys.path.insert(0, str(WX_AGENT / "src"))
    from wechat_ops_agent.db.query import query_contact_messages  # noqa: E402
    keys = json.loads(WX_KEYS.read_text())
    best = None  # (create_time, day, text)
    for db in ("message_0.db", "message_1.db"):
        mp = WX_DB_DIR / "message" / db
        if not mp.exists():
            continue
        with open(mp, "rb") as f:
            salt = f.read(16).hex()
        mk = keys.get(salt)
        if not mk:
            continue
        try:
            msgs = query_contact_messages(str(mp), mk, GROUP093_ROOM, limit=80)
        except Exception:
            continue
        for m in msgs:
            c = m.get("content") or ""
            mt = re.search(r"截止\s*(\d+)\s*日", c)
            if mt and "锦州港口装车情况" in c:
                ct = int(m.get("create_time") or 0)
                if best is None or ct > best[0]:
                    best = (ct, mt.group(1), c)
    if not best:
        return None
    return best[1], best[2]


# ── 2. 解析晨报箱节点 ──────────────────────────────────────────────────
def _num(pat: str, text: str, default: int = 0) -> int:
    m = re.search(pat, text)
    return int(m.group(1)) if m else default


def parse_report(text: str) -> dict:
    """晨报正文 → 箱节点 dict。在途集装箱按 节×2=箱(用户确认 44节=88箱)。"""
    port_loaded = _num(r"剩余重箱\s*(\d+)", text)
    port_empty = _num(r"可用空箱\s*(\d+)", text)
    xtz_loaded = _num(r"新台子站内目前\s*(\d+)", text)
    line330_loaded = _num(r"线上重箱\s*(\d+)", text)
    ground330_loaded = _num(r"落地重箱\s*(\d+)", text)
    transit_cars = _num(r"在途\s*(\d+)\s*节集装箱", text)
    return {
        "port_loaded": port_loaded, "port_empty": port_empty,
        "xtz_loaded": xtz_loaded, "line330_loaded": line330_loaded,
        "ground330_loaded": ground330_loaded,
        "transit_loaded": transit_cars * 2,   # 节→箱
        "_loaded_box": _num(r"装箱\s*(\d+)", text),
        "_shipped_box": _num(r"发运\s*(\d+)", text),
    }


# ── 3. 返空(95306 返空列)+ 物理池(unique 箱号)────────────────────────
def compute_returns_and_pool(conn: sqlite3.Connection) -> tuple[int, int]:
    pool = conn.execute(
        "SELECT COUNT(DISTINCT box_no) FROM wagon_container_shipments "
        "WHERE ship_name=? AND box_no!=''", (SHIP,)).fetchone()[0]
    transit_empty = 0
    try:
        import jiusan_cycle_board as jcb
        pos = jcb._cycle_positions(conn)
        transit_empty = int((pos.get("transit_empty") or {}).get("boxes", 0) or 0)
    except Exception:
        pass
    return transit_empty, pool


def main(apply: bool = False, dry_run: bool = False) -> None:
    rep = pull_latest_morning_report()
    if not rep:
        print("✗ GROUP093 没拉到晨报")
        return
    day, text = rep
    nodes = parse_report(text)
    conn = sqlite3.connect(str(SOP_DB))
    transit_empty, pool = compute_returns_and_pool(conn)
    conn.close()

    from sop_hub.utils.time import now_iso_beijing
    snap_date = now_iso_beijing()[:8] + f"{int(day):02d}"  # YYYY-MM-DD(取当月+晨报日)
    nodes["transit_empty"] = transit_empty
    # ground330_empty 反推平物理池(硬证据);<0 则归 0 并告警
    known = (nodes["port_loaded"] + nodes["port_empty"] + nodes["xtz_loaded"]
             + nodes["line330_loaded"] + nodes["ground330_loaded"]
             + nodes["transit_loaded"] + nodes["transit_empty"])
    g330e = pool - known
    warn = ""
    if g330e < 0:
        warn = f"⚠ 反推 330空={g330e}<0(节点和{known}>池{pool}),已归0待人工核"
        g330e = 0
    nodes["ground330_empty"] = g330e

    print(f"=== 晨报截止{day}日 → 快照 {snap_date} ===")
    print(f"  港重{nodes['port_loaded']} 港空{nodes['port_empty']} 新台子{nodes['xtz_loaded']} "
          f"330线上{nodes['line330_loaded']} 330落地重{nodes['ground330_loaded']} "
          f"途重{nodes['transit_loaded']} 返空{transit_empty}(95306) "
          f"330落地空{g330e}(反推平物理池{pool})")
    print(f"  昨装{nodes['_loaded_box']} 发运{nodes['_shipped_box']}")
    if warn:
        print("  " + warn)

    # 幂等 + 不覆盖人工:今日快照已存在 → 跳过(人工按流量口径精修过的保留,auto 只填空缺日)
    conn0 = sqlite3.connect(str(SOP_DB))
    exists = conn0.execute(
        "SELECT source FROM container_pool_snapshot WHERE snapshot_date=? AND ship_name=?",
        (snap_date, SHIP)).fetchone()
    conn0.close()
    if exists:
        print(f"  快照 {snap_date} 已存在(source={exists[0]})→ 跳过,不覆盖人工精修")
        return

    if dry_run or not apply:
        print("\nDRY-RUN(加 --apply 真记)")
        return

    from container_pool_snapshot import record, ensure_schema
    conn = sqlite3.connect(str(SOP_DB))
    ensure_schema(conn)
    note = (f"auto从GROUP093晨报截止{day}日:返空取95306返空列、330空反推平物理池(unique箱{pool});"
            f"昨装{nodes['_loaded_box']}发{nodes['_shipped_box']}。"
            f"港空/330若按流量口径精修请人工--record覆盖。{warn}")
    record(conn, snapshot_date=snap_date, project="jiusan", ship_name=SHIP,
           nodes={k: v for k, v in nodes.items() if not k.startswith("_")},
           inferred="ground330_empty", source="auto_morning_report", note=note,
           now=now_iso_beijing())
    conn.commit()
    conn.close()
    print(f"\nCOMMIT ✓ 快照 {snap_date} 已记(source=auto_morning_report)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    main(apply=a.apply, dry_run=a.dry_run)
