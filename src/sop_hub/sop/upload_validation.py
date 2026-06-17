"""上传前校验 —— 堵「整批误传」+ 核对车数/箱数(2026-06-17 用户要求)。

吉林金钢 06-16 发错复盘:链子算不出本次事件就退化成整批上传(181车)。
本模块在**真上传之前**强制校验,任一不过就拒绝上传:
  1. 本次 wagon_ids 必须属于**同一个发车事件**(单一 source_message_id)——
     跨多趟 = 疑似整批 → 拒。
  2. 列序号(第N列):按 batch 各趟首次入库时间排序定位,给出来对账。
  3. 车数 = 本次唯一车号数。
  4. 箱数 = 本地箱表数,且**必须等于 95306 该批车的箱数之和**(自动算,
     95306 为权威)——对不上说明漏箱/串箱 → 拒。

设计:校验只认「一个完整发车事件」。手工补几箱这种部分上传不走这里
(调用方传 validate=False 绕过)。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RAIL_DB = Path(
    "/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3"
)


@dataclass
class UploadValidation:
    ok: bool = False
    trip_label: str = ""           # 第N列
    car_count: int = 0
    box_count_local: int = 0
    box_count_95306: int = 0
    batch_total_cars: int = 0
    source_message_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "trip_label": self.trip_label,
            "car_count": self.car_count, "box_count_local": self.box_count_local,
            "box_count_95306": self.box_count_95306,
            "batch_total_cars": self.batch_total_cars,
            "source_message_ids": self.source_message_ids, "errors": self.errors,
        }


def _trip_label(conn: sqlite3.Connection, batch_id: str, source_id: str) -> str:
    """按 batch 各 source 首次入库时间排序,定位 source 的列序号。"""
    rows = conn.execute(
        "SELECT source_message_id, MIN(created_at) c FROM wagon_shipments "
        "WHERE batch_id=? AND source_message_id IS NOT NULL AND source_message_id!='' "
        "GROUP BY source_message_id ORDER BY c ASC",
        (batch_id,),
    ).fetchall()
    order = [r[0] for r in rows]
    cn = "一二三四五六七八九十十一十二十三十四十五"
    if source_id in order:
        i = order.index(source_id)
        return f"第{cn[i] if i < len(cn) else i+1}列"
    return "第?列"


def validate_dispatch_event_upload(
    wagon_ids: list[str],
    *,
    db_path: str | Path,
    rail_db_path: str | Path | None = None,
) -> UploadValidation:
    """真上传前调用。返回 UploadValidation;ok=False 时调用方必须拒绝上传。"""
    v = UploadValidation()
    if not wagon_ids:
        v.errors.append("wagon_ids 为空,无可上传事件")
        return v

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        in_ph = ",".join("?" * len(wagon_ids))
        rows = conn.execute(
            f"SELECT car_no, ydid, hph, marked_weight, batch_id, source_message_id "
            f"FROM wagon_shipments WHERE id IN ({in_ph})", wagon_ids,
        ).fetchall()
        if not rows:
            v.errors.append(f"{len(wagon_ids)} 个 wagon_id 在库里查不到")
            return v

        # 必备字段完整性(2026-06-17):缺字段 → 挂起,不上传。95306 一定会补齐,
        # 只是早晚 —— 等下一轮 sync 填上再放行。
        _REQUIRED = {"car_no": "车号", "ydid": "需求号", "hph": "货票号",
                     "marked_weight": "标重"}
        _missing: dict[str, int] = {}
        for r in rows:
            for f in _REQUIRED:
                val = r[f]
                if val is None or (isinstance(val, str) and not val.strip()):
                    _missing[f] = _missing.get(f, 0) + 1
        if _missing:
            detail = ",".join(f"{_REQUIRED[f]}缺{n}" for f, n in _missing.items())
            v.errors.append(f"必备字段不全({detail})→ 挂起待 95306 补齐,暂不上传")

        cars = {r["car_no"] for r in rows if r["car_no"]}
        ydids = {r["ydid"] for r in rows if r["ydid"]}
        batch_ids = {r["batch_id"] for r in rows if r["batch_id"]}
        sources = sorted({r["source_message_id"] for r in rows if r["source_message_id"]})
        v.car_count = len(cars)
        v.source_message_ids = sources

        # ① 必须单一发车事件(单 source)——跨多趟 = 整批
        if len(sources) > 1:
            v.errors.append(
                f"跨 {len(sources)} 趟({sources})= 疑似整批上传,拒绝;"
                f"发运上传必须 per-event")
        # batch 上下文
        if len(batch_ids) == 1:
            bid = next(iter(batch_ids))
            v.batch_total_cars = conn.execute(
                "SELECT COUNT(DISTINCT car_no) FROM wagon_shipments "
                "WHERE batch_id=? AND car_no!=''", (bid,)).fetchone()[0]
            if sources:
                v.trip_label = _trip_label(conn, bid, sources[0])
            # 本次=整批全部车(且 batch 有多趟)→ 也拒
            if (v.batch_total_cars and v.car_count == v.batch_total_cars
                    and len(sources) <= 1):
                n_trips = conn.execute(
                    "SELECT COUNT(DISTINCT source_message_id) FROM wagon_shipments "
                    "WHERE batch_id=? AND source_message_id!=''", (bid,)).fetchone()[0]
                if n_trips > 1:
                    v.errors.append(
                        f"本次车数={v.car_count} 等于整批({v.batch_total_cars},"
                        f"{n_trips}趟)→ 疑似整批,拒绝")

        # ② 本地箱数
        if ydids:
            yph = ",".join("?" * len(ydids))
            v.box_count_local = conn.execute(
                f"SELECT COUNT(*) FROM wagon_container_shipments WHERE ydid IN ({yph})",
                tuple(ydids)).fetchone()[0]
    finally:
        conn.close()

    # ③ 95306 权威箱数(本次各 ydid 的 container 数之和)
    rail = Path(rail_db_path) if rail_db_path else RAIL_DB
    if ydids and rail.exists():
        rc = sqlite3.connect(str(rail))
        try:
            yph = ",".join("?" * len(ydids))
            for (cnj,) in rc.execute(
                f"SELECT container_numbers_json FROM shipments WHERE ydid IN ({yph})",
                tuple(ydids)):
                if cnj and cnj not in ("", "[]"):
                    try:
                        v.box_count_95306 += len([b for b in json.loads(cnj) if b])
                    except Exception:
                        pass
        finally:
            rc.close()
        if v.box_count_local != v.box_count_95306:
            v.errors.append(
                f"箱数对不上:本地 {v.box_count_local} vs 95306 {v.box_count_95306}"
                f"(漏箱/串箱),拒绝")
    elif not rail.exists():
        v.errors.append(f"95306 库不可达({rail}),无法核箱数,保守拒绝")

    v.ok = not v.errors
    return v
