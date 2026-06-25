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
    cols = ["port_empty", "port_loaded", "transit_loaded", "xtz_loaded",
            "line330_loaded", "ground330_loaded", "ground330_empty", "transit_empty",
            "total_loaded", "total_empty", "total_pool", "snapshot_date", "note"]
    rows = conn.execute(
        f"SELECT {','.join(cols)} FROM container_pool_snapshot WHERE ship_name=? "
        f"ORDER BY snapshot_date DESC LIMIT 2", (SHIP,)).fetchall()
    today = dict(zip(cols, rows[0])) if rows else {}
    yest = dict(zip(cols, rows[1])) if len(rows) > 1 else {}
    return today, yest


_CBATCH = "e96f4b3b83c74b4891c6b0957f6989bb827de45b"  # 和谐1 集装箱 batch


def _cycle_positions(conn) -> dict[str, dict]:
    """每号列当前位置 → {position: {cyc, boxes}}。位置键:port_loaded/transit_loaded/
    xtz/transit_empty。

    #issue-20260623 问题2 修复:原靠 95306 status_name(已制单→港重),但制票滞后,
    刚物理装车没出票的在装列认不到 → 港重列号常错。改用**节点时间戳推进**(港发车
    departed→新台子到达 arrived→三三0交付 delivered),按 home_cycle 取每列最近一趟:
      - 已交付:返港重装中。多列已交付时,**交付最早(返港最久)的 = 在装列(港重)**,
        其余 = 返空(返程在途)。这一步不依赖制票,解决在装列滞后失显。
      - 已到达未交付:新台子(到达/卸)。
      - 已发车未到达:在途去程(途重)。
    在装列精确时刻仍可由晨报「港内配车 N道M节」进一步校准(留待 §八 合并)。"""
    rows = conn.execute(
        """
        WITH t AS (
          SELECT wbp.home_cycle_no cyc, min(wcs.departed_at) dep,
                 min(wcs.arrived_at) arr, min(NULLIF(wcs.delivered_at,'')) deliv,
                 count(DISTINCT wcs.box_no) boxes
          FROM wagon_container_shipments wcs
          JOIN wagon_body_pool wbp ON wcs.car_no=wbp.car_no AND wbp.project='jiusan'
          WHERE wcs.batch_id=? AND wcs.departed_at!='' GROUP BY cyc, substr(wcs.departed_at,1,10)),
        lt AS (SELECT cyc, MAX(dep) md FROM t GROUP BY cyc)
        SELECT t.cyc, t.dep, t.arr, t.deliv, t.boxes
        FROM t JOIN lt ON t.cyc=lt.cyc AND t.dep=lt.md
        """, (_CBATCH,)).fetchall()
    pos: dict[str, dict] = {}
    delivered: list[tuple] = []  # (cyc, deliv, boxes)
    for cyc, dep, arr, deliv, boxes in rows:
        if deliv:
            delivered.append((cyc, deliv, boxes))
        elif arr:
            pos["xtz"] = {"cyc": cyc, "boxes": boxes}
        elif dep:
            pos["transit_loaded"] = {"cyc": cyc, "boxes": boxes}
    # 已交付的列:交付最早(返港最久)→ 在装列;其余 → 返空
    delivered.sort(key=lambda x: x[1])
    if delivered:
        pos["port_loaded"] = {"cyc": delivered[0][0], "boxes": delivered[0][2]}
        for cyc, deliv, boxes in delivered[1:]:
            pos["transit_empty"] = {"cyc": cyc, "boxes": boxes}
    return pos


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
        bulk = _bulk_active(conn)
    finally:
        conn.close()
    if not t:
        return cd._box(f"{SHIP} 箱循环",
                       [cd._dim("  (无 container_pool_snapshot 快照)")])

    g = lambda k: int(t.get(k) or 0)
    # 节点箱数一律取 container_pool_snapshot(晨报盘点 = 权威池底);95306 只用于
    # 叠加"哪号列在该节点"(hn)。此前混口径(箱数走95306在装列)致港重显100而非
    # 晨报165、新台子错读 line330_loaded——已收口为纯晨报池。
    n_portL = g("port_loaded")   # 港重(港内待发重箱)
    n_tranL = g("transit_loaded")  # 途重(在途去程)
    n_ret = g("transit_empty")   # 返空(返程)
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

    xtz_lbl = cd._bold("新台子") + f" {g('xtz_loaded')}"
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
    # §八 口径对齐:晨报"途重"有箱(已发车),但 95306 还显该列在港(已制单=制票滞后)
    # → 把港位列号**右移到途重位**、港重不再标"装·pm发"(那列其实发走了,95306没出票而已)。
    _plat_pos = pos.get("port_loaded")
    _tran_pos = pos.get("transit_loaded")
    if n_tranL > 0 and _tran_pos is None and _plat_pos is not None:
        tran_hn = cd._dim(f"#{_plat_pos['cyc']}号列发")   # 列号随实际发车右移到途重
        plat_hn = ""
        port_load_note = cd._dim("(在装列已发)")
    else:
        tran_hn = hn("transit_loaded") or cd._dim("(无在途列)")
        plat_hn = hn("port_loaded")
        port_load_note = cd._dim("装·pm发")
    g330_hn = hn("ground330")
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

    # 标题栏(a 口径:现状·循环列实时;落地池=最新晨报底)
    date = str(t.get("snapshot_date") or "")
    now_hm = cd.now_iso_beijing_compact()[:16].replace("T", " ")
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
    if stale_days >= 1:
        title = (f"{SHIP} 箱循环 · {now_hm}  "
                 f"{cd._red(f'⚠箱数=晨报{date}(已{stale_days}天·非现状)')}"
                 f" 池{g('total_pool')} · #列=95306实时")
    else:
        title = (f"{SHIP} 箱循环 · 现状 {now_hm}   "
                 f"箱数=晨报{date}盘点池 {g('total_pool')} · #号列=95306实时位置")

    legend_extra = (cd._red("  ⚠ 箱数节点(港重/港空/三三0…)停在 " + date +
                            " 手动快照,需补今日晨报 record;#号列/散粮状态=95306 实时")
                    if stale_days >= 1 else
                    cd._dim("  箱数=晨报盘点池;#N号列=95306循环列位置;散·待发/在途/到站=95306散粮车状态(非循环、不进箱池)"))
    legend = legend_extra
    body = [""] + L + ["", legend]
    return cd._box(title, body)


def render() -> str:
    return "\n".join(render_lines())


def main() -> None:
    print(render())


if __name__ == "__main__":
    main()
