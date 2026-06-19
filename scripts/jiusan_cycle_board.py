"""九三循环列「箱循环流水线」看板(样式①)。

issue: docs/issues/2026-06-19-九三循环列看板设计.md
只读 container_pool_snapshot / wagon_body_pool;**不写库**。复用 cli_dashboard
的框/对齐/配色风格(_box/_pad_disp/_disp_width/颜色/PANEL_WIDTH)。

对齐铁律:每个节点、每条竖线都用 _compose 钉在**固定显示列**;每行显示宽 <= 内容宽
(PANEL_WIDTH-4),由 _box 的 _fixed 收口,竖线绝不出框。

用法:python scripts/jiusan_cycle_board.py            # 渲染一次
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
import cli_dashboard as cd  # noqa: E402  复用渲染风格

SHIP = "和谐1"
INNER = cd.PANEL_WIDTH - 4  # 内容显示宽

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
    cols = ["port_empty", "port_loaded", "transit_loaded", "line330_loaded",
            "ground330_loaded", "ground330_empty", "transit_empty",
            "total_loaded", "total_empty", "total_pool", "snapshot_date", "note"]
    rows = conn.execute(
        f"SELECT {','.join(cols)} FROM container_pool_snapshot WHERE ship_name=? "
        f"ORDER BY snapshot_date DESC LIMIT 2", (SHIP,)).fetchall()
    today = dict(zip(cols, rows[0])) if rows else {}
    yest = dict(zip(cols, rows[1])) if len(rows) > 1 else {}
    return today, yest


_CBATCH = "e96f4b3b83c74b4891c6b0957f6989bb827de45b"  # 和谐1 集装箱 batch
# 95306 状态 → 循环位置(箱态推进)
_STATUS_POS = {
    "已制单": "port_loaded",      # 在港制票待发 = 港重(在装列)
    "已发车": "transit_loaded",   # 在途去程 = 途重
    "已到达": "ground330",        # 到站卸 = 三三0
    "货物已交付": "transit_empty",  # 已卸返程 = 返空
}


def _cycle_positions(conn) -> dict[str, dict]:
    """每号列最近一趟主导状态 → 当前位置。返回 {position: {cyc, boxes}}。
    位置键:port_loaded/transit_loaded/ground330/transit_empty。
    口径:95306 wagon_container_shipments 实时状态(循环列复用,按 home_cycle_no)。"""
    rows = conn.execute(
        """
        WITH t AS (
          SELECT wbp.home_cycle_no cyc, wcs.box_no, wcs.status_name,
                 substr(wcs.ticketed_at,1,10) d
          FROM wagon_container_shipments wcs
          JOIN wagon_body_pool wbp ON wcs.car_no=wbp.car_no AND wbp.project='jiusan'
          WHERE wcs.batch_id=? AND wcs.ticketed_at>='2026-06-15'),
        lt AS (SELECT cyc, MAX(d) md FROM t GROUP BY cyc)
        SELECT t.cyc, t.status_name, COUNT(DISTINCT t.box_no) boxes
        FROM t JOIN lt ON t.cyc=lt.cyc AND t.d=lt.md
        GROUP BY t.cyc, t.status_name
        """, (_CBATCH,)).fetchall()
    # 每号列取最近一趟里箱数最多的状态为主导
    dom: dict[int, tuple[str, int]] = {}
    for cyc, st, boxes in rows:
        if cyc not in dom or boxes > dom[cyc][1]:
            dom[cyc] = (st, boxes)
    pos: dict[str, dict] = {}
    for cyc, (st, boxes) in dom.items():
        node = _STATUS_POS.get(st)
        if node:
            pos[node] = {"cyc": cyc, "boxes": boxes}
    return pos


# 2026-06-19 厘清:就 3 列循环(无第4列)。列号 = home_cycle_no(车归属池),二者一致。
# 那"4号列"50车其实是 1号列(home_cycle 1)返空到港后重装的新一趟,非新增列。
# 故位置→列号直接读 95306 home_cycle 反推,无需 override(空表=全部走 _cycle_positions)。
# 前提:wagon_body_pool 的 9 车漂移(3→2)修正后,home_cycle 才是干净的 50/50/49。
_POS_LIE: dict[str, int] = {}


def _hn(pos: dict, key: str) -> str:
    """该位置的 #N列 标签:优先运营对齐表,回退 95306 home_cycle 反推。"""
    n = _POS_LIE.get(key)
    if n is None:
        d = pos.get(key)
        n = d["cyc"] if d else None
    return cd._dim(f"#{n}号列") if n else ""


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
        pos = _cycle_positions(conn)
    finally:
        conn.close()
    if not t:
        return cd._box(f"{SHIP} 箱循环",
                       [cd._dim("  (无 container_pool_snapshot 快照)")])

    g = lambda k: int(t.get(k) or 0)
    # a 口径(全 live):循环列在载/在途/返程的箱数取 95306 实时(每号列当前趟);
    # 港空/新台子/三三0 落地堆存池 95306 给不了实时,沿用最新晨报底(标 晨)。
    lb = lambda node, fb: int((pos.get(node) or {}).get("boxes", fb) or 0)
    n_portL = lb("port_loaded", g("port_loaded"))      # 港重=在装列(live)
    n_tranL = lb("transit_loaded", 0)                  # 途重=在途列(live,无则0)
    n_ret = lb("transit_empty", g("transit_empty"))    # 返空=返程列(live)
    g330 = g("ground330_loaded") + g("ground330_empty")
    y330 = (int(y.get("ground330_loaded") or 0) + int(y.get("ground330_empty") or 0)) if y else None
    d330 = ""
    if y330 is not None:
        dd = g330 - y330
        d330 = cd._dim("─0") if dd == 0 else (cd._green(f"▲{dd}") if dd > 0 else cd._red(f"▼{abs(dd)}"))

    def hbar(start, end, endchar):  # start→end 填 ─,end 处放 endchar(含)
        return (start, "─" * (end - start) + endchar)

    A = "──▶"   # 去程箭头
    hn = lambda k: _hn(pos, k)   # 该位置的 #N列 标签

    xtz_lbl = cd._bold("新台子") + f" {g('line330_loaded')}"
    xtz_end = COL_XTZ + _w(xtz_lbl) - 1
    ret_hn = hn("transit_empty")
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
    tran_hn = hn("transit_loaded") or cd._dim("(无在途列)")
    plat_hn = hn("port_loaded")
    g330_hn = hn("ground330")
    # R1 港空(晨报底)增量 + 途重#列 + 右竖线
    L.append(_compose([
        (COL_PORT_E, _delta(t, y, "port_empty")),
        (COL_TRAN_L, tran_hn),
        (RV, "│"),
    ]))
    # R2 港重在装列标记 + 右竖线
    L.append(_compose([
        (COL_PORT_L, (f"{plat_hn} " if plat_hn else "") + cd._dim("装·pm发")),
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

    # 标题栏(a 口径:现状·循环列实时;落地池=最新晨报底)
    date = str(t.get("snapshot_date") or "")
    now_hm = cd.now_iso_beijing_compact()[:16].replace("T", " ")
    title = (f"{SHIP} 箱循环 · 现状 {now_hm}   "
             f"循环列=95306实时 · 落地池=晨报{date}底(池 {g('total_pool')})")

    legend = cd._dim("  港空/新台子/三三0=晨报落地池底;港重/途重/返空=95306 列实时箱数")
    body = [""] + L + ["", legend]
    return cd._box(title, body)


def render() -> str:
    return "\n".join(render_lines())


def main() -> None:
    print(render())


if __name__ == "__main__":
    main()
