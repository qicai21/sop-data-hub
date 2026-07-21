"""九三循环列「箱循环流水线」看板(样式①)。

issue: docs/issues/2026-06-19-九三循环列看板设计.md
只读 container_pool_snapshot / wagon_body_pool;**不写库**。复用 cli_dashboard
的框/对齐/配色风格(_box/_pad_disp/_disp_width/颜色/PANEL_WIDTH)。

对齐铁律:每个节点、每条竖线都用 _compose 钉在**固定显示列**;每行显示宽 <= 内容宽
(PANEL_WIDTH-4),由 _box 的 _fixed 收口,竖线绝不出框。

用法:python scripts/jiusan_cycle_board.py            # 渲染一次
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
import cli_dashboard as cd  # noqa: E402  复用渲染风格

POOL_KEY = "九三大豆"  # snapshot.ship_name 的项目级 key(工单 2026-06-29 §3a,已迁移);箱数节点=本阶段港总池
PHASE_START_SHIP = "和谐1"  # 本阶段锚(工单 §3b):集装箱循环从和谐1 起算——总池跨船(和谐1→诚信→…)
#                            但 departed_at 不回溯到和谐1 之前(项目曾停发,3 月旧船不计)。可调:换船名即换锚。
DISPLAY = "九三大豆·锦州港集装箱总池"  # 看板口径:项目整体集装箱循环,不拘单船(工单 §二)
INNER = cd.PANEL_WIDTH - 4  # 内容显示宽
TRACKING_CACHE = REPO / "runtime" / "jiusan_cycle_tracking_latest.json"
TRACKING_CACHE_MAX_AGE_HOURS = 3

# ── 运营覆盖(仅人工纠偏用,默认空) ─────────────────────────────────────
# 列位置默认走 95306 实时自动状态机(_cycle_state);「调出」列由 TRANSFERRED_CYCLES
# 独立驱动,自动状态机已认(不再依赖本表)。
#
# OPS_CYCLE_NODES 只在 95306 自动推断确实错/滞后时,人工临时钉某列位置用;**留空即纯自动**。
# 历史教训(工单 2026-07-17 类):本表曾硬编码 5 列位置且 3 周无人更新,导致 1 号列
# 明明已返空却长期显示「港装货」。除非当天确需纠偏,**保持空**;纠偏后请及时清回。
# 注意:_apply_ops_cycle_override 是「全量替换」——一旦非空,未列入的列位置会被清掉,
# 故若要钉,须把当天所有在途/在港列一并列全。docs/business-rules/jiusan_cycle_ops_log.md
TRANSFERRED_CYCLES: frozenset[int] = frozenset({4})  # 调出列,不计入「循环车组N列」
# node_key → 看板流水线位置; node_label → 列表明细「当前节点」
OPS_CYCLE_NODES: dict[int, dict] = {}


def _phase_start_date(conn) -> str:
    """本阶段起始时刻 = PHASE_START_SHIP 的首次发车(工单 §3b)。总池跨船但 departed_at
    须 >= 此值,绝不回溯到锚船之前。锚船无数据则返回 ''(=不加下限)。"""
    r = conn.execute(
        "SELECT MIN(departed_at) FROM wagon_container_shipments "
        "WHERE ship_name=? AND departed_at!=''", (PHASE_START_SHIP,)).fetchone()
    return (r[0] if r and r[0] else "") or ""

# ── 固定列布局(显示列,半角=1 全角=2)。横向铺开、竖线钉死同列 ──────
LV = 4            # 左竖线列(返程上行边 / 左直角)
RV = 90           # 右竖线列(新台子下落边 / 右直角)
COL_PORT_E = 6    # 港空(左上角节点)
COL_PORT_L = 30   # 港重
COL_TRAN_L = 54   # 途重
COL_XTZ = 74      # 新台子(右上角节点)
COL_330 = 74      # 三三0总(右下区;留够到右竖线的空隙,避免标签顶到 │)
COL_RET = 32      # 返空(底行中)


def _w(s: str) -> int:
    return cd._disp_width(cd._strip_ansi(s))


def _compose(parts: list[tuple[int, str]]) -> str:
    """parts: (显示列, 文本)。把每段文本左端钉在指定显示列,中间补空格。
    文本可含 ANSI(宽度按可见字符算)。"""
    line, cur = "", 0
    for col, text in sorted(parts, key=lambda p: p[0]):
        if col < cur:
            col = cur  # 防重叠(设计应避免)
        line += " " * (col - cur) + text
        cur = col + _w(text)
    return line


def _fetch(conn) -> tuple[dict, dict]:
    cols = ["port_empty", "port_loaded", "transit_loaded", "xtz_loaded",
            "line330_loaded", "ground330_loaded", "ground330_empty", "transit_empty",
            "total_loaded", "total_empty", "total_pool", "snapshot_date", "note"]
    rows = conn.execute(
        f"SELECT {','.join(cols)} FROM container_pool_snapshot WHERE ship_name=? "
        f"ORDER BY snapshot_date DESC LIMIT 2", (POOL_KEY,)).fetchall()
    today = dict(zip(cols, rows[0])) if rows else {}
    yest = dict(zip(cols, rows[1])) if len(rows) > 1 else {}
    return today, yest


RETURN_HOME_HOURS = 5  # 全部交付后≥5h视为返空回港(返程≈去程~5h);见工单 2026-06-29 §返空口径


def _col_latest_trips(conn) -> list[dict]:
    """每条循环列(home_cycle_no)的**最近一趟**(按发车日),across 九三大豆全部集装箱船。
    **总池口径**(工单 2026-06-29 §三):不锁单船/单 batch——和谐1/诚信等共用同一车体池
    (140/180 诚信车命中 jiusan wagon_body_pool),循环列由车体识别,故 JOIN
    wagon_body_pool(project=jiusan) 即把同一组循环列在所有船的趟次统一,取真正最近一趟
    (诚信的新趟会盖过和谐1旧趟,根治"看板锁死和谐1致循环列停在旧船"的失显)。

    返回每列 dict:cyc/ship/dep/arr/total(箱)/deliv(已交付箱)/last_deliv/full。"""
    phase = _phase_start_date(conn)   # 阶段锚:departed_at >= 此值(总池跨船,不回溯锚船之前)
    rows = conn.execute(
        """
        WITH t AS (
          SELECT wbp.home_cycle_no cyc, wcs.ship_name ship,
                 min(wcs.departed_at) dep, max(wcs.arrived_at) arr,
                 count(DISTINCT wcs.car_no) cars,
                 count(DISTINCT wcs.box_no) total,
                 count(DISTINCT CASE WHEN COALESCE(wcs.delivered_at,'')!='' THEN wcs.box_no END) deliv,
                 max(wcs.delivered_at) last_deliv
          FROM wagon_container_shipments wcs
          JOIN wagon_body_pool wbp ON wcs.car_no=wbp.car_no AND wbp.project='jiusan'
          WHERE wcs.departed_at!='' AND wcs.departed_at>=? GROUP BY cyc, substr(wcs.departed_at,1,10)),
        lt AS (SELECT cyc, MAX(dep) md FROM t GROUP BY cyc)
        SELECT t.cyc, t.ship, t.dep, t.arr, t.cars, t.total, t.deliv, t.last_deliv
        FROM t JOIN lt ON t.cyc=lt.cyc AND t.dep=lt.md
        ORDER BY t.cyc
        """, (phase,)).fetchall()
    out = []
    for cyc, ship, dep, arr, cars, total, deliv, last_deliv in rows:
        total, deliv = int(total or 0), int(deliv or 0)
        out.append({"cyc": cyc, "ship": ship or "", "dep": dep or "", "arr": arr or "",
                    "cars": int(cars or 0), "total": total, "deliv": deliv, "last_deliv": last_deliv or "",
                    "full": total > 0 and deliv == total})
    return out


def _pool_cycles(conn) -> dict[int, dict]:
    try:
        rows = conn.execute(
            """
            SELECT home_cycle_no, COUNT(DISTINCT car_no) cars,
                   MIN(first_seen_date), MAX(last_seen_date)
            FROM wagon_body_pool
            WHERE project='jiusan' AND status='active' AND home_cycle_no IS NOT NULL
            GROUP BY home_cycle_no
            ORDER BY home_cycle_no
            """
        ).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(
            """
            SELECT home_cycle_no, COUNT(DISTINCT car_no) cars, '' AS first_seen, '' AS last_seen
            FROM wagon_body_pool
            WHERE project='jiusan' AND home_cycle_no IS NOT NULL
            GROUP BY home_cycle_no
            ORDER BY home_cycle_no
            """
        ).fetchall()
    return {
        int(cyc): {"cycle_no": int(cyc), "pool_cars": int(cars or 0),
                   "first_seen": first or "", "last_seen": last or ""}
        for cyc, cars, first, last in rows
    }


def _current_ticketed_trips(conn) -> dict[int, dict]:
    """当前已制票但未发车的列。用于弥补 95306 departed_at 之前的港内待发状态。"""
    try:
        rows = conn.execute(
            """
            WITH latest_departed AS (
              SELECT wbp.home_cycle_no cyc, MAX(wcs.departed_at) latest_departed_at
              FROM wagon_container_shipments wcs
              JOIN wagon_body_pool wbp ON wcs.car_no=wbp.car_no AND wbp.project='jiusan'
              WHERE wcs.project_id='jiusan' AND COALESCE(wcs.departed_at,'')!=''
              GROUP BY wbp.home_cycle_no
            )
            SELECT wbp.home_cycle_no cyc, wcs.ship_name,
                   COUNT(DISTINCT wcs.car_no) cars,
                   COUNT(DISTINCT wcs.box_no) boxes,
                   COUNT(DISTINCT wcs.ydid) ydids,
                   MIN(wcs.ticketed_at) first_ticketed,
                   MAX(wcs.ticketed_at) last_ticketed
            FROM wagon_container_shipments wcs
            JOIN wagon_body_pool wbp ON wcs.car_no=wbp.car_no AND wbp.project='jiusan'
            LEFT JOIN latest_departed ld ON ld.cyc=wbp.home_cycle_no
            WHERE wcs.project_id='jiusan'
              AND COALESCE(wcs.ticketed_at,'')!=''
              AND COALESCE(wcs.departed_at,'')=''
              AND wcs.ticketed_at > COALESCE(ld.latest_departed_at, '')
              AND COALESCE(wcs.transport_mode_name,'') LIKE '%集装箱%'
            GROUP BY wbp.home_cycle_no, wcs.ship_name
            ORDER BY wbp.home_cycle_no
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[int, dict] = {}
    for cyc, ship, cars, boxes, ydids, first_ticketed, last_ticketed in rows:
        out[int(cyc)] = {
            "cyc": int(cyc), "ship": ship or "", "cars": int(cars or 0),
            "boxes": int(boxes or 0), "ydids": int(ydids or 0),
            "first_ticketed": first_ticketed or "", "last_ticketed": last_ticketed or "",
        }
    return out


def _hours_since(ts: str, now) -> float | None:
    if not ts:
        return None
    from datetime import datetime
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%dT%H:%M:%S", 19),
                   ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%dT%H:%M", 16)):
        try:
            return (now - datetime.strptime(ts[:n], fmt)).total_seconds() / 3600.0
        except Exception:
            pass
    return None


def _cycle_state(conn, now=None) -> dict:
    """循环列状态机 → {pos, transit_empty_boxes, transit_empty_cycs, confirms, trips}。

    返空口径(工单 2026-06-29,用户铁律 §返空):
      - 已发车未到站            → 途重(transit_loaded,在途去程)。
      - 已到站·部分交付          → 新台子(xtz,在330卸),**绝不计返空**;若该列拖长
        (已开卸但未交付完)且已有**更新进站列开卸** → 提示人工确认(规则3:是否主体
        已返空、少量车随下趟返港)。
      - 已到站·全部交付:
          · 交付>5h(RETURN_HOME_HOURS) → 返空回港(规则1);或
          · 已有更新进站列开卸           → 返空回港(规则2,旧列即便<5h也算回港);
            两者满足其一即视为回港(returned)。最早交付(回港最久)= 在装列(港重 #列)。
          · 否则                          → 返空在途(transit_empty,计入返空箱)。

    transit_empty_boxes = 所有"返空在途"列的箱数**之和**(根治旧版循环里被覆盖、只剩
    最后一列致返空漏算的 bug)。"""
    if now is None:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    trips = _col_latest_trips(conn)
    pool = _pool_cycles(conn)
    ticketed = _current_ticketed_trips(conn)

    def has_newer_delivering(arr: str) -> bool:
        # 是否存在"到站更晚且已开卸(deliv>=1)"的列 → 用于规则2/3 的 supersede 判断
        return any(t["arr"] and arr and t["arr"] > arr and t["deliv"] >= 1 for t in trips)

    pos: dict[str, dict] = {}
    pos_multi: dict[str, list[dict]] = {}
    te_cycs: list = []
    te_boxes = 0
    returned: list[tuple] = []   # (last_deliv, cyc, boxes)
    confirms: list[str] = []

    def add_pos(key: str, item: dict) -> None:
        pos_multi.setdefault(key, []).append(item)
        pos[key] = item

    cycle_rows: dict[int, dict] = {}
    for t in trips:
        cyc, boxes = t["cyc"], t["total"]
        cycle_rows[int(cyc)] = {
            "cycle_no": int(cyc), "pool_cars": pool.get(int(cyc), {}).get("pool_cars", 0),
            "trip_cars": int(t.get("cars") or 0), "trip_boxes": boxes, "ship": t["ship"],
            "latest_departed": t["dep"], "latest_arrived": t["arr"],
            "latest_delivered": t["last_deliv"], "delivered_boxes": t["deliv"],
            "node_key": "", "node_label": "", "note": "",
        }
        # 调出列不参与循环位置分配(港重/新台子/返空),避免其旧趟被误推为「在装列」。
        if int(cyc) in TRANSFERRED_CYCLES:
            cycle_rows[int(cyc)].update({
                "node_key": "transferred", "node_label": "调出",
                "note": "不计入运行中循环列"})
            continue
        if t["dep"] and not t["arr"]:
            add_pos("transit_loaded", {"cyc": cyc, "boxes": boxes})     # 途重
            cycle_rows[int(cyc)].update({"node_key": "transit_loaded", "node_label": "途重"})
        elif t["arr"] and not t["full"]:
            add_pos("xtz", {"cyc": cyc, "boxes": boxes})                # 新台子(部分卸)
            cycle_rows[int(cyc)].update({"node_key": "xtz", "node_label": "新台子到站未交付"})
            if t["deliv"] >= 1 and has_newer_delivering(t["arr"]):       # 规则3
                confirms.append(
                    f"#{cyc}号列到站后仅交付 {t['deliv']}/{t['total']} 箱,且已有更新进站列开卸"
                    f"——是否主体已返空回港、少量车随下趟返港?需人工确认")
        elif t["full"]:
            hrs = _hours_since(t["last_deliv"], now)
            home = (hrs is not None and hrs > RETURN_HOME_HOURS) or has_newer_delivering(t["arr"])
            if home:
                returned.append((t["last_deliv"], cyc, boxes))          # 返空回港
                cycle_rows[int(cyc)].update({"node_key": "returned_home", "node_label": "已交付返港/港内待装"})
            else:
                te_cycs.append(cyc)                                     # 返空在途
                te_boxes += boxes
                cycle_rows[int(cyc)].update({"node_key": "transit_empty", "node_label": "返空在途"})
    if te_cycs:
        add_pos("transit_empty", {"cyc": te_cycs[0], "boxes": te_boxes, "cycs": te_cycs})
    if returned:
        returned.sort(key=lambda x: x[0] or "")     # 交付最早(回港最久)= 在装列(港重)
        for last_deliv, cyc, boxes in returned:
            add_pos("returned_home", {"cyc": cyc, "boxes": boxes, "last_deliv": last_deliv})
        if "port_loaded" not in pos:
            add_pos("port_loaded", {"cyc": returned[0][1], "boxes": returned[0][2]})

    for cyc, tick in ticketed.items():
        if int(cyc) in TRANSFERRED_CYCLES:
            continue
        if "returned_home" in pos_multi:
            pos_multi["returned_home"] = [
                item for item in pos_multi["returned_home"] if item.get("cyc") != cyc
            ]
        add_pos("port_loaded", {"cyc": cyc, "boxes": tick["boxes"], "cars": tick["cars"],
                                "ticketed": True})
        row = cycle_rows.setdefault(cyc, {
            "cycle_no": cyc, "pool_cars": pool.get(cyc, {}).get("pool_cars", 0),
            "trip_cars": 0, "trip_boxes": 0, "ship": tick["ship"],
            "latest_departed": "", "latest_arrived": "", "latest_delivered": "",
            "delivered_boxes": 0, "node_key": "", "node_label": "", "note": "",
        })
        row.update({
            "ship": tick["ship"], "trip_cars": tick["cars"], "trip_boxes": tick["boxes"],
            "latest_ticketed": tick["last_ticketed"],
            "node_key": "port_loaded", "node_label": "港内制票/待发",
        })
        if row["pool_cars"] and tick["cars"] != row["pool_cars"]:
            row["note"] = f"池{row['pool_cars']}车,已制票{tick['cars']}车"

    for cyc, info in pool.items():
        cycle_rows.setdefault(cyc, {
            "cycle_no": cyc, "pool_cars": info["pool_cars"], "trip_cars": 0,
            "trip_boxes": 0, "ship": "", "latest_departed": "", "latest_arrived": "",
            "latest_delivered": "", "delivered_boxes": 0, "node_key": "unknown",
            "node_label": "无近期趟次", "note": "",
        })
    rows = [cycle_rows[k] for k in sorted(cycle_rows)]
    return {"pos": pos, "pos_multi": pos_multi, "cycle_rows": rows,
            "transit_empty_boxes": te_boxes, "transit_empty_cycs": te_cycs,
            "confirms": confirms, "trips": trips,
            "transferred_cycles": sorted(TRANSFERRED_CYCLES)}


def _apply_ops_cycle_override(state: dict) -> dict:
    """将 OPS_CYCLE_NODES / TRANSFERRED_CYCLES 叠到自动状态机结果上(仅渲染用)。"""
    if not OPS_CYCLE_NODES:
        return state
    rows_by = {int(r["cycle_no"]): r for r in (state.get("cycle_rows") or [])}
    pos: dict = {}
    pos_multi: dict = {}
    te_cycs: list = []
    te_boxes = 0
    for cyc, ov in OPS_CYCLE_NODES.items():
        cyc = int(cyc)
        row = rows_by.setdefault(cyc, {
            "cycle_no": cyc, "pool_cars": 0, "trip_cars": 0, "trip_boxes": 0,
            "ship": "", "latest_departed": "", "latest_arrived": "",
            "latest_delivered": "", "delivered_boxes": 0,
            "node_key": "", "node_label": "", "note": "",
        })
        nk = ov.get("node_key") or "unknown"
        row["node_key"] = nk
        row["node_label"] = ov.get("node_label") or nk
        if "trip_cars" in ov:
            row["trip_cars"] = int(ov["trip_cars"] or 0)
        if "trip_boxes" in ov:
            row["trip_boxes"] = int(ov["trip_boxes"] or 0)
        if nk == "transferred":
            row["note"] = ov.get("note") or "不计入运行中循环列"
            continue
        boxes = int(ov.get("trip_boxes") or row.get("trip_boxes") or 0)
        item = {"cyc": cyc, "boxes": boxes}
        pos_multi.setdefault(nk, []).append(item)
        pos[nk] = item
        if nk == "transit_empty":
            te_cycs.append(cyc)
            te_boxes += boxes
    if te_cycs:
        pos["transit_empty"] = {"cyc": te_cycs[0], "boxes": te_boxes, "cycs": te_cycs}
    state = dict(state)
    state["pos"] = pos
    state["pos_multi"] = pos_multi
    state["cycle_rows"] = [rows_by[k] for k in sorted(rows_by)]
    state["transit_empty_boxes"] = te_boxes
    state["transit_empty_cycs"] = te_cycs
    state["transferred_cycles"] = sorted(TRANSFERRED_CYCLES)
    return state


def _load_tracking_cache(path: Path = TRACKING_CACHE, now: datetime | None = None) -> dict:
    """Load a recent four-probe cache; stale data is metadata only, never a node override."""
    now = now or datetime.now()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "fresh": False, "reason": "轨迹缓存不存在"}
    generated = _hours_since(str(payload.get("generated_at") or ""), now)
    fresh = generated is not None and 0 <= generated <= TRACKING_CACHE_MAX_AGE_HOURS
    payload["available"] = True
    payload["fresh"] = fresh
    payload["age_hours"] = generated
    if not fresh:
        payload["reason"] = "轨迹缓存超过3小时"
    return payload


def _apply_tracking_cache(state: dict, cache: dict) -> dict:
    """Overlay fresh per-cycle 95306 probe states onto the legacy inferred state."""
    state = dict(state)
    state["tracking_cache"] = {
        "available": bool(cache.get("available")),
        "fresh": bool(cache.get("fresh")),
        "generated_at": cache.get("generated_at") or "",
        "age_hours": cache.get("age_hours"),
        "reason": cache.get("reason") or "",
        "query_count": int(cache.get("query_count") or 0),
        "error_count": int(cache.get("error_count") or 0),
    }
    if not cache.get("fresh"):
        return state

    rows_by = {int(r["cycle_no"]): dict(r) for r in (state.get("cycle_rows") or [])}
    pos_multi = {
        key: [dict(item) for item in items]
        for key, items in (state.get("pos_multi") or {}).items()
    }
    tracked_cycles: set[int] = set()
    for train in cache.get("trains") or []:
        cyc = int(train.get("cycle_no") or 0)
        if not cyc or cyc in TRANSFERRED_CYCLES:
            continue
        tracked_cycles.add(cyc)
        row = rows_by.setdefault(cyc, {
            "cycle_no": cyc, "pool_cars": 0, "trip_cars": 0, "trip_boxes": 0,
            "ship": "", "latest_departed": "", "latest_arrived": "",
            "latest_delivered": "", "delivered_boxes": 0, "note": "",
        })
        node_key = str(train.get("node_key") or "unknown")
        row.update({
            "ship": train.get("ship_name") or row.get("ship") or "",
            "trip_cars": int(train.get("car_count") or row.get("trip_cars") or 0),
            "node_key": node_key,
            "node_label": train.get("node_label") or node_key,
            "tracking_updated_at": cache.get("generated_at") or "",
            "tracking_state_at": train.get("state_at") or "",
            "tracking_sample_count": int(train.get("sample_count") or 0),
            "tracking_strategy": train.get("sample_strategy") or "",
            "tracking_next_transition_at": train.get("next_transition_at") or "",
        })

    if not tracked_cycles:
        state["cycle_rows"] = [rows_by[k] for k in sorted(rows_by)]
        return state

    for key in list(pos_multi):
        pos_multi[key] = [item for item in pos_multi[key] if int(item.get("cyc") or 0) not in tracked_cycles]
        if not pos_multi[key]:
            pos_multi.pop(key)
    for cyc in sorted(tracked_cycles):
        row = rows_by[cyc]
        node_key = row.get("node_key") or "unknown"
        if node_key == "unknown":
            continue
        boxes = int(row.get("trip_boxes") or 0)
        pos_multi.setdefault(node_key, []).append({"cyc": cyc, "boxes": boxes})

    pos = {key: items[-1] for key, items in pos_multi.items() if items}
    te_items = pos_multi.get("transit_empty") or []
    te_cycs = [int(item["cyc"]) for item in te_items]
    te_boxes = sum(int(item.get("boxes") or 0) for item in te_items)
    if te_items:
        pos["transit_empty"] = {"cyc": te_cycs[0], "boxes": te_boxes, "cycs": te_cycs}
    state.update({
        "pos": pos,
        "pos_multi": pos_multi,
        "cycle_rows": [rows_by[k] for k in sorted(rows_by)],
        "transit_empty_boxes": te_boxes,
        "transit_empty_cycs": te_cycs,
    })
    return state


def _cycle_positions(conn) -> dict[str, dict]:
    """back-compat 包装:仅返回 position→{cyc,boxes} 映射(口径见 _cycle_state)。
    transit_empty.boxes 为所有返空在途列之**和**。"""
    return _cycle_state(conn)["pos"]


# 2026-06-19 厘清:就 3 列循环(无第4列)。列号 = home_cycle_no(车归属池),二者一致。
# 那"4号列"50车其实是 1号列(home_cycle 1)返空到港后重装的新一趟,非新增列。
# 故位置→列号直接读 95306 home_cycle 反推,无需 override(空表=全部走 _cycle_positions)。
# 前提:wagon_body_pool 的 9 车漂移(3→2)修正后,home_cycle 才是干净的 50/50/49。
_POS_LIE: dict[str, int] = {}


def _bulk_active(conn) -> dict[str, int]:
    """九三散粮(整车,诚信+和谐1)未交付活跃车,按 95306 status_name 计 distinct ydid。
    已制单=在港待发 / 已发车=在途 / 已到达=到新台子站。散粮非循环、不进箱池,仅标状态。"""
    rows = conn.execute(
        "SELECT status_name, COUNT(DISTINCT ydid) FROM wagon_shipments "
        "WHERE project_id='jiusan' AND cargo_name LIKE '%豆%' "
        "AND transport_mode_name LIKE '%整车%' "
        "AND status_name IN ('已制单','已发车','已到达') GROUP BY status_name",
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def _hn(pos: dict, key: str) -> str:
    """该位置的 #N列 标签:优先运营对齐表,回退 95306 home_cycle 反推。"""
    n = _POS_LIE.get(key)
    if n is None:
        d = pos.get(key)
        n = d["cyc"] if d else None
    return cd._dim(f"#{n}号列") if n else ""


def _hn_multi(state: dict, key: str) -> str:
    items = state.get("pos_multi", {}).get(key) or []
    cycs = []
    for item in items:
        cyc = item.get("cyc")
        if cyc is not None and cyc not in cycs:
            cycs.append(cyc)
    if not cycs:
        return ""
    return cd._dim("+".join(f"#{c}号列" for c in cycs))


def _cycle_detail_lines(state: dict) -> list[str]:
    rows = state.get("cycle_rows") or []
    if not rows:
        return []
    out = [cd._dim("  列状态  列号  池车  本趟        当前节点              关键时间/提示")]
    transferred = set(state.get("transferred_cycles") or TRANSFERRED_CYCLES)
    for r in rows:
        cyc_no = int(r["cycle_no"])
        cyc = f"#{cyc_no}"
        pool = str(r.get("pool_cars") or "-")
        trip = f"{r.get('trip_cars') or 0}车/{r.get('trip_boxes') or 0}箱"
        node = r.get("node_label") or "-"
        is_out = cyc_no in transferred or r.get("node_key") == "transferred"
        if is_out:
            node = cd._red(node if node and node != "-" else "调出")
            trip = cd._dim("-")
        if r.get("latest_ticketed"):
            when = f"制票 {r['latest_ticketed']}"
        elif r.get("tracking_state_at"):
            when = f"轨迹 {r['tracking_state_at']}"
        elif r.get("latest_departed") and not is_out:
            when = f"发 {r['latest_departed']}"
        else:
            when = ""
        if r.get("note"):
            when = (when + " " if when else "") + r["note"]
        if is_out and when:
            when = cd._dim(when)
        line = (
            "  "
            + cd._fixed(cyc, 6)
            + cd._fixed(pool, 5, ">")
            + "  "
            + cd._fixed(trip, 13)
            + cd._fixed(node, 22)
            + when
        )
        out.append(cd._fixed(line, INNER))
    return out


def _delta(today, yest, key) -> str:
    """净变化 ▲N/▼N(快照今昨差;gross +进/-出 待 95306/晨报流量子系统,暂用净)。"""
    if not yest or today.get(key) is None or yest.get(key) is None:
        return ""
    d = int(today[key]) - int(yest[key])
    if d == 0:
        return cd._dim("─0")
    return cd._green(f"▲{d}") if d > 0 else cd._red(f"▼{abs(d)}")


def render_lines() -> list[str]:
    """返回箱循环流水线看板的行列表(供 cli_dashboard 主看板嵌入 / 独立打印)。"""
    conn = cd._connect(cd.DB_PATH)
    if conn is None:
        return [cd._dim("(DB 连接失败)")]
    try:
        t, y = _fetch(conn)
        tracking_cache = _load_tracking_cache()
        state = _apply_ops_cycle_override(_apply_tracking_cache(_cycle_state(conn), tracking_cache))
        pos = state["pos"]
        bulk = _bulk_active(conn)
    finally:
        conn.close()
    if not t:
        return cd._box(f"{DISPLAY} 箱循环",
                       [cd._dim("  (无 container_pool_snapshot 快照)")])

    g = lambda k: int(t.get(k) or 0)
    # 节点箱数一律取 container_pool_snapshot(晨报盘点 = 权威池底);95306 只用于
    # 叠加"哪号列在该节点"(hn)。此前混口径(箱数走95306在装列)致港重显100而非
    # 晨报165、新台子错读 line330_loaded——已收口为纯晨报池。
    n_portL = g("port_loaded")   # 港重(港内待发重箱,晨报港总池)
    # 途重:有运营覆盖「在途重/到站等待」列时,优先用该列箱数;否则晨报/快照
    n_tranL = g("transit_loaded")  # 途重(在途去程,晨报港总池)
    if state.get("pos_multi", {}).get("xtz") and not n_tranL:
        # #2 在新台子站等待时,途重位可空;新台子节点挂列号
        pass
    # 若运营把在途重挂在 xtz 但晨报途重有数,保留晨报途重显示
    # 返空:快照 + 运营覆盖列箱数取大(95306 实时可能滞后)
    n_ret = max(g("transit_empty"), int(state.get("transit_empty_boxes") or 0))
    g330 = g("ground330_loaded") + g("ground330_empty")
    y330 = (int(y.get("ground330_loaded") or 0) + int(y.get("ground330_empty") or 0)) if y else None
    d330 = ""
    if y330 is not None:
        dd = g330 - y330
        d330 = cd._dim("─0") if dd == 0 else (cd._green(f"▲{dd}") if dd > 0 else cd._red(f"▼{abs(dd)}"))

    def hbar(start, end, endchar):  # start→end 填 ─,end 处放 endchar(含)
        return (start, "─" * (end - start) + endchar)

    A = "──▶"   # 去程箭头
    hn = lambda k: _hn_multi(state, k) or _hn(pos, k)   # 该位置的 #N列 标签

    xtz_lbl = cd._bold("新台子") + f" {g('xtz_loaded')}"
    xtz_end = COL_XTZ + _w(xtz_lbl) - 1
    ret_hn = hn("transit_empty") or hn("returned_home")
    ret_lbl = cd._bold("返空") + f" {n_ret}" + (f" {ret_hn}" if ret_hn else "")
    ret_end = COL_RET + _w(ret_lbl) - 1

    L = []  # 流水线各行(纯排版,颜色已内嵌)
    # R0 顶行:去程四节点 + 右上直角 ─┐
    L.append(_compose([
        (COL_PORT_E, cd._bold("港空") + f" {g('port_empty')}"),
        (COL_PORT_L - 4, A),
        (COL_PORT_L, cd._bold("港重") + f" {n_portL}"),
        (COL_TRAN_L - 4, A),
        (COL_TRAN_L, cd._bold("途重") + f" {n_tranL}"),
        (COL_XTZ - 4, A),
        (COL_XTZ, xtz_lbl),
        hbar(xtz_end + 2, RV, "┐"),
    ]))
    # §八 口径对齐:晨报"途重"有箱(已发车),但 95306 还显该列在港(已制单=制票滞后)
    # → 把港位列号**右移到途重位**、港重不再标"装·pm发"(那列其实发走了,95306没出票而已)。
    _plat_pos = pos.get("port_loaded")
    _tran_pos = pos.get("transit_loaded")
    if n_tranL > 0 and _tran_pos is None and _plat_pos is not None and not OPS_CYCLE_NODES:
        tran_hn = cd._dim(f"#{_plat_pos['cyc']}号列发")   # 列号随实际发车右移到途重
        plat_hn = ""
        port_load_note = cd._dim("(在装列已发)")
    else:
        tran_hn = hn("transit_loaded") or (cd._dim("(无在途列)") if n_tranL == 0 else "")
        plat_hn = hn("port_loaded")
        # 运营覆盖时港重旁注用列节点文案
        _ops1 = OPS_CYCLE_NODES.get(1) or {}
        if _ops1.get("node_key") == "port_loaded":
            port_load_note = cd._dim(_ops1.get("node_label") or "港装货")
        else:
            port_load_note = cd._dim("装·pm发")
    g330_hn = hn("ground330") or hn("xtz")
    # R1 附属①:集装箱列号(港重在装列 / 途重在途列 / 新台子到达列)+ 港空增量 + 右竖线
    L.append(_compose([
        (COL_PORT_E, _delta(t, y, "port_empty")),
        (COL_PORT_L, (f"{plat_hn} " if plat_hn else "") + port_load_note),
        (COL_TRAN_L, tran_hn),
        (COL_XTZ, hn("xtz")),
        (RV, "│"),
    ]))
    # R2 附属②:散粮车状态(非循环/不进箱池,仅标)——港=待发 途=在途 新台子=到站
    bk = lambda lbl, k: cd._dim(f"散·{lbl}{bulk.get(k, 0)}")
    L.append(_compose([
        (COL_PORT_L, bk("待发", "已制单")),
        (COL_TRAN_L, bk("在途", "已发车")),
        (COL_XTZ, bk("到站", "已到达")),
        (RV, "▼"),
    ]))
    # R3 左▲ + 三三0总
    L.append(_compose([(LV, "▲"), (COL_330, cd._bold("三三0总") + f" {g330}"), (RV, "│")]))
    # R4 左│ + 330增量
    L.append(_compose([(LV, "│"), (COL_330, d330), (RV, "│")]))
    # R5 左│ + 330作业列状态
    L.append(_compose([
        (LV, "│"),
        (COL_330, (f"{g330_hn} " if g330_hn else "") + cd._dim("卸重装空")),
        (RV, "│"),
    ]))
    # R6 底行:└──── 返空 ◀────┘
    L.append(_compose([
        (LV, "└" + "─" * (COL_RET - LV - 1)),
        (COL_RET, ret_lbl),
        (ret_end + 2, "◀"),
        hbar(ret_end + 3, RV, "┘"),
    ]))

    # 标题栏:压缩顶栏文案,详细口径留在 legend。
    date = str(t.get("snapshot_date") or "")
    now_hm = cd.now_iso_beijing_compact()[:16].replace("T", " ")
    now_short = now_hm[2:4] + now_hm[5:7] + now_hm[8:16]
    today = cd.now_iso_beijing_compact()[:10]
    # §八.2 自保:箱数取最新手动快照,若快照不是今天→别拿旧数冒充"现状",醒目标过时。
    stale_days = 0
    try:
        from datetime import date as _d
        sy, sm, sd = (int(x) for x in date.split("-"))
        ty, tm, td = (int(x) for x in today.split("-"))
        stale_days = (_d(ty, tm, td) - _d(sy, sm, sd)).days
    except Exception:
        stale_days = 0
    transferred = set(state.get("transferred_cycles") or TRANSFERRED_CYCLES)
    active_rows = [
        r for r in (state.get("cycle_rows") or [])
        if int(r.get("cycle_no") or 0) not in transferred
        and r.get("node_key") != "transferred"
    ]
    cycle_count = len(active_rows) if active_rows else max(
        0, len(state.get("cycle_rows") or []) - len(transferred))
    stale_mark = cd._red(f" ⚠晨报{date}已{stale_days}天") if stale_days >= 1 else ""
    title = (
        f"大豆循环现状 {now_short} | 总箱量: {g('total_pool')} | "
        f"循环车组{cycle_count}列{stale_mark}"
    )

    tracking_meta = state.get("tracking_cache") or {}
    tracking_stamp = str(tracking_meta.get("generated_at") or "")[5:16]
    tracking_note = (
        cd._dim(f"列节点=95306轨迹4车/列·2h更新({tracking_stamp},查询{tracking_meta.get('query_count', 0)}次)")
        if tracking_meta.get("fresh") else
        cd._red("⚠ 列轨迹缓存不可用/过期,已回退本地95306状态推断")
    )
    legend_extra = (cd._red("  ⚠ 箱数节点(港重/途重/港空/三三0…)停在 " + date +
                            " 手动快照,需补今日晨报 record;" + cd._strip_ansi(tracking_note))
                    if stale_days >= 1 else
                    cd._dim("  箱数=晨报港总池;") + tracking_note +
                    cd._dim(";散·待发/在途/到站=95306散粮(非循环)"))
    detail_lines = _cycle_detail_lines(state)
    legend_lines = [*detail_lines, legend_extra]
    # 规则3:返空口径无法自动判定时,醒目挂人工确认(见 _cycle_state)
    for c in state.get("confirms", []):
        legend_lines.append(cd._red("  ⚠ 待确认:" + c))
    body = [""] + L + ["", *legend_lines]
    return cd._box(title, body)


def render() -> str:
    return "\n".join(render_lines())


def main() -> None:
    print(render())


if __name__ == "__main__":
    main()
