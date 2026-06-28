"""九三大豆对账 Spec(通用引擎的第一个实例)。

额外源 = 港方『大豆 X 货票.xlsx』(WeChat 文件夹自动落地),新台子 sheet=集装箱、
散粮车 sheet=散粮。归属维度:九三外贸船不重名、一船一趟 → **船名 → lot01 batch** 即可。

scope:origin=高桥镇、dest=新台子、ticketed>=2026-06-10。
集装箱 key=(ydid, box_no);散粮 key=ydid。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "scripts"))
import reconcile_jiusan_containers as _rec  # noqa: E402  复用 RAIL 常量

from .engine import ReconcileSpec  # noqa: E402

RAIL = _rec.RAIL
PROJECT = "jiusan"
ORIGIN = "高桥镇"
DEST = "新台子"
START = "2026-06-10"

# 一票两船 box 级例外:货票号 -> {箱号尾段: 应归船}
BOX_EXCEPTIONS = {"GZDJW0496546": {"1512630": "诚信"}}


# ── 额外源:WeChat 文件夹各船最新版货票 ──────────────────────────────
def _wx_file_dirs():
    base = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    return sorted(base.glob("*/msg/file/2026-*"), reverse=True)


def _parse_ship_version(fname):
    m = re.search(r"大豆\s*(\S+?)\s*货票", fname)
    if not m:
        return None, 0
    v = re.search(r"\((\d+)\)", fname)
    return m.group(1).strip(), (int(v.group(1)) if v else 0)


def find_latest_tickets():
    best = {}
    for d in _wx_file_dirs():
        for f in d.glob("*大豆*货票*.xlsx"):
            ship, ver = _parse_ship_version(f.name)
            if not ship:
                continue
            key = (ver, f.stat().st_mtime)
            if ship not in best or key > best[ship][1]:
                best[ship] = (f, key)
    return {s: p for s, (p, _k) in best.items()}


def _read_sheet_hph(path, sheet):
    try:
        ws = openpyxl.load_workbook(path, data_only=True)[sheet]
    except KeyError:
        return set()
    out = set()
    for r in ws.iter_rows(min_row=4, values_only=True):
        h = r[3] if len(r) > 3 else None
        if h and str(h).strip().startswith("GZD"):
            out.add(str(h).strip())
    return out


def _ydid2hph(rail):
    """ydid -> 货票号(高桥镇→新台子,集装箱+散粮同到站,>=START)。"""
    m = {}
    for ydid, raw in rail.execute(
        "SELECT ydid, raw_core_json FROM shipments "
        "WHERE origin_name=? AND destination_name LIKE ? AND substr(ticketed_at,1,10)>=?",
        (ORIGIN, f"%{DEST}%", START)):
        if raw:
            g = re.findall(r"GZD[A-Z]{2}[0-9]{6,}", raw)
            if g:
                m[ydid] = g[0]
    return m


def _ship_to_batch(hub, seq):
    # 九三每船:集装箱=lot01、散粮=lot02(两个独立 release_batch)
    return {r[0]: r[1] for r in hub.execute(
        "SELECT ship_name, id FROM release_batches "
        "WHERE project=? AND batch_sequence=?", (PROJECT, seq))}


class _JiusanBase(ReconcileSpec):
    project_id = PROJECT
    transport_mode = ""   # 子类填:集装箱运输 / 整车运输
    sheet = ""            # 新台子 / 散粮车
    batch_seq = ""        # 集装箱 lot01 / 散粮 lot02

    def _hph2ship(self):
        h2s = {}
        for ship, path in find_latest_tickets().items():
            for h in _read_sheet_hph(path, self.sheet):
                h2s[h] = ship
        return h2s

    def leg_ydids_all_dates(self, rail):
        """本 leg 在 95306 的全部 ydid(不卡日期)——grandfather 判历史真车。"""
        return {r[0] for r in rail.execute(
            "SELECT ydid FROM shipments WHERE origin_name=? AND destination_name LIKE ? "
            "AND transport_mode_name=?", (ORIGIN, f"%{DEST}%", self.transport_mode))}


class JiusanContainerSpec(_JiusanBase):
    leg = "container"
    transport_mode = "集装箱运输"
    sheet = "新台子"
    batch_seq = "lot01"
    table = "wagon_container_shipments"
    key_cols = ("ydid", "box_no")

    def universe(self, rail):
        out = {}
        for ydid, cj, tk in rail.execute(
            "SELECT ydid, container_numbers_json, ticketed_at FROM shipments "
            "WHERE origin_name=? AND destination_name LIKE ? AND transport_mode_name=? "
            "AND substr(ticketed_at,1,10)>=?", (ORIGIN, f"%{DEST}%", self.transport_mode, START)):
            for b in (json.loads(cj) if cj else []):
                out[(ydid, str(b).strip())] = {"date": str(tk)[:10]}
        return out

    def db_rows(self, hub):
        return {(r[0], r[1]): r[2] for r in hub.execute(
            "SELECT t.ydid, t.box_no, t.batch_id FROM wagon_container_shipments t "
            "JOIN release_batches rb ON t.batch_id=rb.id WHERE rb.project=?", (PROJECT,))}

    def source_batch(self, rail, hub):
        h2s = self._hph2ship()
        y2h = _ydid2hph(rail)
        s2b = _ship_to_batch(hub, self.batch_seq)
        out = {}
        for (ydid, box) in self.universe(rail):
            hph = y2h.get(ydid)
            ship = h2s.get(hph)
            if not ship:
                continue  # 货票号不在任何文件 → 源未到 → 引擎判 NEW_UNATTR
            # box 级例外覆盖
            for suf, eship in BOX_EXCEPTIONS.get(hph, {}).items():
                if box.endswith(suf):
                    ship = eship
            bid = s2b.get(ship)
            if bid:
                out[(ydid, box)] = bid
        return out


class JiusanBulkSpec(_JiusanBase):
    leg = "bulk"
    transport_mode = "整车运输"
    sheet = "散粮车"
    batch_seq = "lot02"
    table = "wagon_shipments"
    key_cols = ("ydid",)

    def universe(self, rail):
        out = {}
        for ydid, tk in rail.execute(
            "SELECT ydid, ticketed_at FROM shipments "
            "WHERE origin_name=? AND destination_name LIKE ? AND transport_mode_name=? "
            "AND substr(ticketed_at,1,10)>=?", (ORIGIN, f"%{DEST}%", self.transport_mode, START)):
            out[ydid] = {"date": str(tk)[:10]}
        return out

    def db_rows(self, hub):
        return {r[0]: r[1] for r in hub.execute(
            "SELECT t.ydid, t.batch_id FROM wagon_shipments t "
            "JOIN release_batches rb ON t.batch_id=rb.id WHERE rb.project=?", (PROJECT,))}

    def source_batch(self, rail, hub):
        h2s = self._hph2ship()
        y2h = _ydid2hph(rail)
        s2b = _ship_to_batch(hub, self.batch_seq)
        out = {}
        for ydid in self.universe(rail):
            ship = h2s.get(y2h.get(ydid))
            if ship and s2b.get(ship):
                out[ydid] = s2b[ship]
        return out


SPECS = {"container": JiusanContainerSpec, "bulk": JiusanBulkSpec}
