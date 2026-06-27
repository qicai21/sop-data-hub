"""统一「全行」wagon 入库口 —— 手工补车 / backfill / 一次性回填都走这里。

背景:正式发运链(create_wagon_shipments / workflow_task_executor)写的是
**精简行**(只落基础字段,marked_weight / 95306 状态由后续 sync 补),且它们
自带重算 hook。但散落在 scripts/ 下的 backfill_*/oneoff_*/r73_* 手工脚本各自
手搓 INSERT:列集 16~32 列不一、cargo_count 算法不一、**落库后忘了重算装车
重量** —— 2026-06-15 宝腾海 15 车救火漏重算、看板偏小(14942.5 vs 15946)就是
这么来的。

本模块把「从 95306 货票直接取全行写 wagon_shipments」这一类收敛成一个口子,
保证三件事:
  1. 统一列集(canonical 32 列,与 oneoff_seed 全行口径一致)
  2. cargo_count 统一规则(集装箱=箱数;整车/散粮=0)
  3. 落库后**必重算** compute_for_release_batch —— 想漏都漏不了

id 配方对齐生产:sha1(f"{ydid}|{batch_id}")[:24],与 create_wagon_shipments
的 _gen_wagon_id 同源,手工入库行与正式行可互换、不重复。

用法:
    from sop_hub.sop.wagon_ingest import ingest_wagons
    res = ingest_wagons(
        batch_id, tickets,          # tickets: 95306 shipments 行 dict 列表
        project_id="chaoyang_steel", ship_name="宝腾海",
        source_message_id="manual_补车_20260615",
        db_path=DB_PATH,
    )
    # res = {"new": 15, "refreshed": 0, "shipped_weight_tons": 15946.0, ...}
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

# 95306 ticket dict → wagon_shipments 列的映射(canonical 全行)。
# ticket 用 95306 shipments 表原始列名;缺列用 None/'' 兜。
CANONICAL_COLUMNS = [
    "id", "departure_id", "batch_id", "car_no", "car_model", "cargo_name",
    "shipper_name", "consignee_name", "origin_name", "destination_name",
    "accepted_at", "loaded_at", "ticketed_at", "departed_at", "arrived_at",
    "delivered_at", "confirmed_received_at", "status_name", "latest_stage_key",
    "latest_stage_name", "latest_event_time", "marked_weight", "freight_fee",
    "container_no", "waybill_no", "ydid", "czydid", "cargo_count",
    "transport_mode_code", "transport_mode_name", "project_id", "ship_name",
    "dispatch_status", "source_message_id", "source_group_id",
    "loading_line", "hph", "created_at", "updated_at",
]

# 落库后用这些字段在「已有行」上刷新 95306 状态(幂等 re-sync,保留 created_at)
_REFRESH_FIELDS = [
    "status_name", "latest_stage_key", "latest_stage_name", "latest_event_time",
    "departed_at", "arrived_at", "delivered_at", "confirmed_received_at",
    "marked_weight", "dispatch_status", "updated_at",
]


def gen_wagon_id(ydid: str, batch_id: str) -> str:
    """与 create_wagon_shipments._gen_wagon_id 同源,手工/正式行可互换。"""
    return hashlib.sha1(f"{ydid}|{batch_id}".encode()).hexdigest()[:24]


def compute_cargo_count(ticket: dict) -> int:
    """集装箱业务=该车箱数;整车/散粮=0。
    优先 container_numbers_json 数组长度,fallback container_no 按 '/' 拆。
    """
    raw = ticket.get("container_numbers_json") or ""
    if raw:
        try:
            return len([b for b in json.loads(raw) if b])
        except Exception:
            pass
    cn = ticket.get("container_no") or ""
    if cn:
        return len([b for b in str(cn).split("/") if b.strip()])
    return 0


def _f(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def extract_hph(ticket: dict) -> str:
    """货票号 hph:优先 ticket 顶层,否则从 raw_core_json/detail_json 取。"""
    h = ticket.get("hph")
    if h:
        return str(h)
    for k in ("raw_core_json", "detail_json"):
        raw = ticket.get(k)
        if raw:
            try:
                v = (json.loads(raw) if isinstance(raw, str) else raw).get("hph")
                if v:
                    return str(v)
            except Exception:
                pass
    return ""


def marked_load_from_car_model(car_model: str, is_container: bool) -> int | None:
    """标载(标记载重)从**车型**推,不取 95306 的 marked_weight(那是计费/装载重量,
    同型号随货变,不是标载)。规则(2026-06-27 用户确认):
      - 集装箱业务 / 平板车(X70·NX70 必拉箱)→ 64(敞顶箱)
      - C70 系敞车 → 70    C64 系(C64K/C64H/C64T)→ 61    C60/C62 系 → 60
      - 散粮车 L 系(L18/L70)→ 60
    返回 None = 未知车型(调用方自行兜底/告警)。"""
    cm = (car_model or "").upper().strip()
    if is_container or cm.startswith("X70") or cm.startswith("NX70"):
        return 64
    if cm.startswith("C70"):
        return 70
    if cm.startswith("C64"):
        return 61
    if cm.startswith("C62") or cm.startswith("C60"):
        return 60
    if cm.startswith("L"):
        return 60
    return None


def backfill_event_fields_from_95306(
    wagon_ids: list[str],
    *,
    db_path: str | Path,
    rail_db_path: str | Path | None = None,
) -> dict[str, int]:
    """正式发运链(create_wagon_shipments)只写精简行,不落 car_model / hph / 标重;
    upload 前校验缺 hph/标重 会挂起。本函数在链条 create_wagon 之后**立即按 ydid 补齐**:
      - car_model、hph ← 95306(rail);
      - **marked_weight = 车型推标载**(marked_load_from_car_model,**不取 rail 的计费/装载
        重量**;2026-06-27 起 marked_weight 语义统一为「标载」)。
    **idempotent**:只补缺,已有值不动。返回 {"marked_weight","hph","car_model"} 各补几条。
    """
    out = {"marked_weight": 0, "hph": 0, "car_model": 0}
    if not wagon_ids:
        return out
    from sop_hub.sop.query_95306_shipments import _resolve_rail_db_path
    rail_path = _resolve_rail_db_path(rail_db_path)
    if not Path(rail_path).exists():
        return out
    hub = sqlite3.connect(str(db_path))
    hub.row_factory = sqlite3.Row
    try:
        in_ph = ",".join("?" * len(wagon_ids))
        wagons = hub.execute(
            f"SELECT id, ydid, car_model, marked_weight, hph FROM wagon_shipments WHERE id IN ({in_ph})",
            wagon_ids,
        ).fetchall()
        need = [
            w for w in wagons
            if w["ydid"] and (
                w["marked_weight"] in (None, 0, "")
                or not (w["hph"] or "").strip()
                or not (w["car_model"] or "").strip()
            )
        ]
        if not need:
            return out
        ydids = [w["ydid"] for w in need]
        cy_ph = ",".join("?" * len(ydids))
        container_ydids = {
            r["ydid"] for r in hub.execute(
                f"SELECT DISTINCT ydid FROM wagon_container_shipments WHERE ydid IN ({cy_ph})", ydids
            )
        }
        rail = sqlite3.connect(f"file:{rail_path}?mode=ro", uri=True)
        rail.row_factory = sqlite3.Row
        try:
            yph = ",".join("?" * len(ydids))
            rmap = {
                r["ydid"]: (r["car_model"], extract_hph(dict(r)))
                for r in rail.execute(
                    f"SELECT * FROM shipments WHERE ydid IN ({yph})", ydids
                )
            }
        finally:
            rail.close()
        for w in need:
            rail_cm, hph = rmap.get(w["ydid"], ("", ""))
            car_model = (w["car_model"] or rail_cm or "").strip()
            # car_model 回填(标载推断的依据)
            if not (w["car_model"] or "").strip() and rail_cm:
                hub.execute("UPDATE wagon_shipments SET car_model=? WHERE id=?", (rail_cm, w["id"]))
                out["car_model"] += 1
            # marked_weight = **车型推标载**(不取 rail 计费重量;见 marked_load_from_car_model)
            if w["marked_weight"] in (None, 0, ""):
                bz = marked_load_from_car_model(car_model, w["ydid"] in container_ydids)
                if bz is not None:
                    hub.execute("UPDATE wagon_shipments SET marked_weight=? WHERE id=?", (bz, w["id"]))
                    out["marked_weight"] += 1
            # hph from 95306
            if not (w["hph"] or "").strip() and hph:
                hub.execute("UPDATE wagon_shipments SET hph=? WHERE id=?", (hph, w["id"]))
                out["hph"] += 1
        hub.commit()
    finally:
        hub.close()
    return out


def build_wagon_row(
    ticket: dict,
    *,
    batch_id: str,
    project_id: str,
    ship_name: str,
    now: str,
    dispatch_status: str = "loading",
    source_message_id: str = "",
    source_group_id: str = "",
    departure_id: str = "",
    loading_line: str = "",
) -> dict:
    """从一张 95306 货票 dict 构造一行 canonical wagon_shipments。"""
    ydid = ticket.get("ydid") or ""
    status = ticket.get("status_name") or ""
    # 九三/full_track 口径:95306 已交付 → confirmed_received + 落交付时间
    ds = dispatch_status
    confirmed_at = None
    if status and "交付" in status:
        ds = "confirmed_received"
        confirmed_at = ticket.get("delivered_at")
    return {
        "id": gen_wagon_id(ydid, batch_id),
        "departure_id": departure_id,
        "batch_id": batch_id,
        "car_no": ticket.get("car_no") or "",
        "car_model": ticket.get("car_model") or "",
        "cargo_name": ticket.get("cargo_name") or "",
        "shipper_name": ticket.get("shipper_name") or "",
        "consignee_name": ticket.get("consignee_name") or "",
        "origin_name": ticket.get("origin_name") or "",
        "destination_name": ticket.get("destination_name") or "",
        "accepted_at": ticket.get("accepted_at") or "",
        "loaded_at": ticket.get("loaded_at") or "",
        "ticketed_at": ticket.get("ticketed_at") or "",
        "departed_at": ticket.get("departed_at") or "",
        "arrived_at": ticket.get("arrived_at") or "",
        "delivered_at": ticket.get("delivered_at") or "",
        "confirmed_received_at": confirmed_at,
        "status_name": status,
        "latest_stage_key": ticket.get("latest_stage_key") or "",
        "latest_stage_name": ticket.get("latest_stage_name") or "",
        "latest_event_time": ticket.get("latest_event_time") or "",
        "marked_weight": _f(ticket.get("marked_weight")),
        "freight_fee": ticket.get("freight_fee"),
        "container_no": ticket.get("container_no_raw") or ticket.get("container_no") or "",
        "waybill_no": ticket.get("waybill_no") or "",
        "ydid": ydid,
        "czydid": ticket.get("czydid") or "",
        "cargo_count": compute_cargo_count(ticket),
        "transport_mode_code": ticket.get("transport_mode_code") or "",
        "transport_mode_name": ticket.get("transport_mode_name") or "",
        "project_id": project_id,
        "ship_name": ship_name,
        "dispatch_status": ds,
        "source_message_id": source_message_id,
        "source_group_id": source_group_id,
        # 装车线路:调用方归一好传进来(canonicalize_loading_line),或 ticket 自带
        "loading_line": loading_line or (ticket.get("loading_line") or ""),
        "hph": extract_hph(ticket),   # 货票号(全项目必备,2026-06-17)
        "created_at": now,
        "updated_at": now,
    }


def upsert_wagons(conn: sqlite3.Connection, rows: list[dict], batch_id: str) -> tuple[int, int]:
    """新行 INSERT;已有行只刷 95306 状态(幂等)。返回 (新增, 刷新)。"""
    # 老库迁移:确保新列存在(loading_line 2026-06-16;hph/trip_seq 2026-06-17)
    _cols = {r[1] for r in conn.execute("PRAGMA table_info(wagon_shipments)")}
    if "loading_line" not in _cols:
        conn.execute("ALTER TABLE wagon_shipments ADD COLUMN loading_line TEXT")
    if "hph" not in _cols:
        conn.execute("ALTER TABLE wagon_shipments ADD COLUMN hph TEXT")
    if "trip_seq" not in _cols:
        conn.execute("ALTER TABLE wagon_shipments ADD COLUMN trip_seq INTEGER")
    existing = {r[0] for r in conn.execute(
        "SELECT id FROM wagon_shipments WHERE batch_id=?", (batch_id,))}
    new_n = 0
    for r in rows:
        if r["id"] in existing:
            conn.execute(
                f"UPDATE wagon_shipments SET {','.join(f'{c}=?' for c in _REFRESH_FIELDS)} "
                f"WHERE id=?",
                [r[c] for c in _REFRESH_FIELDS] + [r["id"]],
            )
        else:
            conn.execute(
                f"INSERT INTO wagon_shipments ({','.join(CANONICAL_COLUMNS)}) "
                f"VALUES ({','.join('?' * len(CANONICAL_COLUMNS))})",
                [r[c] for c in CANONICAL_COLUMNS],
            )
            new_n += 1
    return new_n, len(rows) - new_n


def recompute_trip_seq(conn: sqlite3.Connection, batch_id: str) -> int:
    """重算该 batch 各趟「列序号」trip_seq:按各 source 的 min(ticketed_at) 排序。

    每次入库重算(像装车重量一样自愈),修「上传时临时算列序号会算错」。落到
    每行 wagon_shipments.trip_seq,excel/对账/报送直接带,不再临时算。
    """
    srcs = [r[0] for r in conn.execute(
        "SELECT source_message_id FROM wagon_shipments "
        "WHERE batch_id=? AND source_message_id IS NOT NULL AND source_message_id!='' "
        "GROUP BY source_message_id ORDER BY MIN(ticketed_at) ASC", (batch_id,))]
    n = 0
    for i, src in enumerate(srcs, 1):
        n += conn.execute(
            "UPDATE wagon_shipments SET trip_seq=? WHERE batch_id=? AND source_message_id=?",
            (i, batch_id, src)).rowcount
    return n


def ingest_wagons(
    batch_id: str,
    tickets: Iterable[dict],
    *,
    project_id: str,
    ship_name: str,
    db_path: Path | str,
    dispatch_status: str = "loading",
    source_message_id: str = "",
    source_group_id: str = "",
    loading_line: str = "",
    recompute: bool = True,
    now: str | None = None,
) -> dict:
    """一站式:构行 → upsert → 重算装车重量。手工/backfill 入库唯一入口。

    recompute=True(默认)落库后必调 compute_for_release_batch —— 这正是散落
    脚本最常漏的一步。无 yaml shipped_weight_rule 的项目 compute 会安全跳过。
    loading_line:装车线路,调用方先 canonicalize_loading_line 归一(煤六/七道…)。
    """
    if now is None:
        from sop_hub.utils.time import now_iso_beijing_compact
        now = now_iso_beijing_compact()
    rows = [build_wagon_row(
        t, batch_id=batch_id, project_id=project_id, ship_name=ship_name, now=now,
        dispatch_status=dispatch_status, source_message_id=source_message_id,
        source_group_id=source_group_id, loading_line=loading_line,
    ) for t in tickets]

    conn = sqlite3.connect(str(db_path))
    try:
        new_n, ref_n = upsert_wagons(conn, rows, batch_id)
        trip_n = recompute_trip_seq(conn, batch_id)   # 列序号自愈重算
        conn.commit()
    finally:
        conn.close()

    out: dict = {"new": new_n, "refreshed": ref_n, "total": len(rows),
                 "batch_id": batch_id, "trip_seq_rows": trip_n}
    if recompute:
        from sop_hub.sop.shipped_weight import compute_for_release_batch
        sw = compute_for_release_batch(batch_id, db_path=db_path)
        out["shipped_weight_tons"] = sw.get("shipped_weight_tons")
        out["recompute_ok"] = sw.get("ok")
        out["recompute_note"] = sw.get("error")  # no_shipped_weight_rule 等
    return out
