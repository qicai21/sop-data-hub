"""检装通知单：按行级到站拆段 + 本项目 expected 对齐。

混装根因(2026-07-20 宝丽 46 车):OCR 已写出「汐子 16 + 乌兰浩特 29」,
但 ingest 整单 project=zhongtang/dest=汐子、expected=45,window_recover
16/45 永久 pending。

规则:
  1. 用 cargo_info_effective / cargo_info_raw 解析行到站(继承已由 OCR 写在 effective)。
  2. 连续同到站成段;每段独立 footer.zhuangche_jieshu = 本段非 defect 数。
  3. 非本项目 SOP 到站(无 open batch / 不在项目到站集合) → _split_group_unmatched,
     由 ingest 丢弃(同 #131 乌兰浩特未建项目)。
  4. ops mixed_load_ops_note / _authoritative_car_numbers 可强制权威车号子集。
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Mapping


# 长 token 优先,避免「凌源」误伤等
_DEST_TOKEN_CANON: list[tuple[str, str]] = [
    ("乌兰浩特", "乌兰浩特"),
    ("朝阳西", "朝阳西"),
    ("新台子", "新台子"),
    ("凌源东", "凌源东"),
    ("凌东", "凌源东"),
    ("四平", "四平"),
    ("马林", "马林"),
    ("汐子", "汐子"),
]


def infer_destination_from_text(text: str) -> str | None:
    t = str(text or "")
    if not t.strip():
        return None
    for token, canon in _DEST_TOKEN_CANON:
        if token in t:
            return canon
    return None


def infer_row_destination(row: Mapping[str, Any]) -> str | None:
    if not isinstance(row, Mapping):
        return None
    for key in ("cargo_info_effective", "cargo_info_raw", "remark", "ship_name"):
        d = infer_destination_from_text(str(row.get(key) or ""))
        if d:
            return d
    return None


def project_destination_aliases(
    conn: sqlite3.Connection | None,
    project_id: str,
) -> set[str]:
    """本项目开放批次到站 + 常见别名。"""
    out: set[str] = set()
    pid = (project_id or "").strip()
    if not pid or conn is None:
        return out
    try:
        rows = conn.execute(
            "SELECT DISTINCT destination_station FROM release_batches WHERE project=?",
            (pid,),
        ).fetchall()
    except Exception:
        return out
    for r in rows:
        raw = ""
        try:
            raw = str(r[0] if not hasattr(r, "keys") else r["destination_station"] or "")
        except Exception:
            raw = str(r[0] if r else "")
        d = infer_destination_from_text(raw) or raw.strip()
        if d:
            out.add(d)
    # 项目级兜底(批次 dest 空时)
    if pid == "zhongtang_special_steel":
        out.update({"汐子"})
    elif pid == "chaoyang_steel":
        out.update({"朝阳西"})
    elif pid == "jilin_jingang_jinzhou":
        out.update({"四平"})
    elif pid == "jiusan":
        out.update({"新台子"})
    return out


def _non_defect_car_count(rows: list[Any]) -> int:
    n = 0
    for r in rows:
        if not isinstance(r, Mapping):
            continue
        if r.get("defect"):
            continue
        if str(r.get("car_no") or "").strip():
            n += 1
    return n


def _car_nos(rows: list[Any], *, include_defect: bool = True) -> list[str]:
    out: list[str] = []
    for r in rows:
        if not isinstance(r, Mapping):
            continue
        if not include_defect and r.get("defect"):
            continue
        c = str(r.get("car_no") or "").strip()
        if c:
            out.append(c)
    return out


def _rewrite_segment_payload(
    payload: dict[str, Any],
    rows: list[Any],
    *,
    destination: str | None,
    unmatched: bool,
) -> dict[str, Any]:
    sub = dict(payload)
    sub["rows"] = rows
    sub["car_nos"] = _car_nos(rows, include_defect=True)
    real = _non_defect_car_count(rows)
    footer = dict(payload.get("footer") or {})
    footer["zhuangche_jieshu"] = real
    # 段内 defect 行数作排车观察
    defect_n = sum(
        1 for r in rows
        if isinstance(r, Mapping) and r.get("defect") and str(r.get("car_no") or "").strip()
    )
    if defect_n:
        footer["paiche_jieshu"] = defect_n
    sub["footer"] = footer
    if isinstance(payload.get("meta"), dict):
        meta = dict(payload["meta"])
        meta["jieshu"] = real + defect_n
        sub["meta"] = meta
    if destination:
        sub["destination"] = destination
        sub["_dest_segment"] = destination
    if unmatched:
        sub["_split_group_unmatched"] = True
        sub["_split_group_rule_id"] = ""
        sub["_split_group_ship_name"] = ""
    else:
        # 保留上游 ship 拆段标记;纯到站拆段不强制清 ship rule
        sub.pop("_split_group_unmatched", None)
    sub["rows_count"] = len(rows)
    if rows:
        last = rows[-1]
        if isinstance(last, Mapping):
            sub["last_car_no"] = str(last.get("car_no") or "")
    sub["_dest_segment_split"] = True
    return sub


def split_payload_by_destination(
    payload: dict[str, Any],
    *,
    conn: sqlite3.Connection | None = None,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """按行级到站连续段拆 payload。

    Returns:
      单段时返回 [原 payload](若无需拆)或 [改写后单段];
      多段时每段独立 payload。非本项目到站段带 _split_group_unmatched。
    """
    if not isinstance(payload, dict):
        return [payload]
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    if len(rows) < 2:
        return [payload]

    pid = (project_id or str(payload.get("project") or "").strip() or "").strip()
    scope = project_destination_aliases(conn, pid) if pid else set()

    # 为每行解析到站;空则继承上行。首个明确到站前的表头/排车前缀没有
    # 独立到站语义，应归入第一个明确到站段，不能单独建立候选。
    resolved: list[str | None] = []
    cur: str | None = None
    for row in rows:
        d = infer_row_destination(row) if isinstance(row, Mapping) else None
        if d:
            cur = d
        resolved.append(cur)

    # 若全程无到站 token,不拆
    if not any(resolved):
        return [payload]
    first_destination = next((d for d in resolved if d), None)
    if first_destination:
        for idx, destination in enumerate(resolved):
            if destination is not None:
                break
            resolved[idx] = first_destination

    # 连续同到站分段
    segments: list[tuple[str | None, list[Any]]] = []
    seg_dest = resolved[0]
    seg_rows: list[Any] = [rows[0]]
    for i in range(1, len(rows)):
        d = resolved[i]
        if d == seg_dest:
            seg_rows.append(rows[i])
        else:
            segments.append((seg_dest, seg_rows))
            seg_dest = d
            seg_rows = [rows[i]]
    segments.append((seg_dest, seg_rows))

    # 仅 1 段 → 仍改写 footer 为本段非 defect(与混装无关的单到站单也更干净)
    if len(segments) == 1:
        dest, seg_rows = segments[0]
        unmatched = bool(dest and scope and dest not in scope)
        # 单段且在 scope 内:不强制拆,但若 footer 虚高可收紧
        if not unmatched and dest:
            real = _non_defect_car_count(seg_rows)
            footer = payload.get("footer") or {}
            z = int(footer.get("zhuangche_jieshu") or 0) if isinstance(footer, dict) else 0
            if z > real > 0:
                return [_rewrite_segment_payload(
                    payload, seg_rows, destination=dest, unmatched=False)]
        if unmatched:
            return [_rewrite_segment_payload(
                payload, seg_rows, destination=dest, unmatched=True)]
        return [payload]

    out: list[dict[str, Any]] = []
    for dest, seg_rows in segments:
        unmatched = False
        if dest and scope and dest not in scope:
            unmatched = True
        elif dest is None and scope:
            # 无到站且项目已知:保守保留给主项目(前缀段)
            unmatched = False
        out.append(_rewrite_segment_payload(
            payload, seg_rows, destination=dest, unmatched=unmatched))
    return out


def filter_rows_for_project_destination(
    rows: list[Any],
    *,
    destination: str | None,
    project_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """链路上过滤:只保留与候选到站/项目 scope 一致的行。"""
    if not rows:
        return []
    dest = infer_destination_from_text(destination or "") or (destination or "").strip()
    scope = project_destination_aliases(conn, project_id or "") if project_id else set()
    if dest:
        scope = set(scope) | {dest}

    out: list[dict[str, Any]] = []
    cur_dest: str | None = dest or None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        d = infer_row_destination(row) or cur_dest
        if d:
            cur_dest = d
        if not scope:
            out.append(dict(row))
            continue
        if d and d in scope:
            out.append(dict(row))
        elif d is None and dest:
            # 继承候选到站
            out.append(dict(row))
        # else: foreign dest row dropped
    return out


def resolve_authoritative_loading_cars(
    *,
    payload: Mapping[str, Any] | None,
    candidate_car_numbers_json: str | None = None,
    loading_car_nos: list[str] | None = None,
) -> list[str] | None:
    """ops / 人工权威子集。有则返回;无则 None(调用方用默认逻辑)。"""
    payload = payload or {}
    # 1) mixed_load_ops_note
    note = payload.get("mixed_load_ops_note")
    if isinstance(note, Mapping):
        for key in ("zhongtang_cars", "cars", "authoritative_cars", "loading_cars"):
            raw = note.get(key)
            if isinstance(raw, list) and raw:
                cars = [str(c).strip() for c in raw if str(c or "").strip()]
                if cars:
                    return cars
    # 2) payload explicit
    for key in ("_authoritative_car_numbers", "authoritative_car_numbers"):
        raw = payload.get(key)
        if isinstance(raw, list) and raw:
            cars = [str(c).strip() for c in raw if str(c or "").strip()]
            if cars:
                return cars
    # 3) candidate.car_numbers_json 若与 payload 全量 rows 不同 → 权威子集
    if candidate_car_numbers_json:
        try:
            cars = [str(c).strip() for c in json.loads(candidate_car_numbers_json)
                    if str(c or "").strip()]
        except Exception:
            cars = []
        if cars:
            payload_cars = [
                str(r.get("car_no") or "").strip()
                for r in (payload.get("rows") or [])
                if isinstance(r, Mapping) and str(r.get("car_no") or "").strip()
            ]
            if payload_cars and cars != payload_cars:
                return cars
    return None


def segment_expected_count(rows: list[Any], footer: Mapping[str, Any] | None = None) -> int:
    """本段应装车数:优先非 defect 行数;footer 仅当不超过非 defect 时参考。"""
    real = _non_defect_car_count(rows)
    if not footer:
        return real
    try:
        z = int(footer.get("zhuangche_jieshu") or 0)
    except Exception:
        z = 0
    if z > 0 and real > 0:
        return min(z, real)
    return real or z
