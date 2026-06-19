"""中唐特钢 到站装卸费 费用生成(#P001-billing,2026-06-18 v2=DB驱动)。

v1 以手帐为 seed;v2 改为**从 DB 直接生成,手帐只当验收常数**(数据自洽、不再依赖手帐)。

铁路计费重量来源(自洽):
  - 历史 16 船:zhongtang_dispatch_map.actual_weight(2026-06-16 跟踪表对齐的权威标载和)
  - 鞍子河 6月 lot(plan90260600006,比 map 新):wagon_shipments 按 car_model 套 yaml 标载规则
    (C70系→70 / C64→61 / C62→60),实测正好 147车/9783吨,与手帐吻合
费率 6.5 元/吨 从 yaml cost_structure.arrival_handling 读。

结算方(业务规则,配置化):铁发=早期船(联合/非凡/宝腾海/旺达97/德邻惠航/德邻惠海plan170/
运达7非改派部分);其余=中唐特钢。运达7 装卸费结算方变更的 23 车走 billing_settle_override
(从 ~/Downloads/运达7.xlsx 读真实车号)。

验收:settle_party=中唐特钢 合计须 = 4724车 / 318246吨 / 2,068,599元(手帐校验,不参与生成)。

用法:python scripts/billing_generate_zhongtang_handling.py [--apply]
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
import yaml  # noqa: E402
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

SOP_DB = REPO / "data" / "sop_agent.db"
YAML = REPO / "config" / "project_sops" / "zhongtang.yaml"
RUNDA7_XLSX = Path("/Users/qicai21/Downloads/运达7.xlsx")
PROJECT = "zhongtang_special_steel"
TIEFA, ZT = "铁发", "中唐特钢"
ANZIHE_JUNE_PLAN = "90260600006"   # 鞍子河 6月,不在 dispatch_map

# 结算方规则(配置化):整船结铁发的早期船;德邻惠海按 plan 拆;运达7 按车 override。
TIEFA_FULL_SHIPS = {"联合", "非凡", "宝腾海", "旺达97", "德邻惠航"}
TIEFA_SHIP_PLAN = {("德邻惠海", "90260100170")}   # 德邻惠海早期 plan → 铁发
RUNDA7 = "运达7"
EXPECT_ZT = (4724, 318246.0, 2068599.0)   # 手帐验收常数


def _h(*p) -> str:
    return hashlib.sha1("|".join(str(x) for x in p).encode()).hexdigest()[:24]


def biaozai(model) -> int | None:
    """car_model → 标载(铁路计费吨)。yaml shipped_weight_rule.prefix_map 同口径。"""
    m = str(model or "")
    if m.startswith("C70") or m in ("70", "70E", "70H"):
        return 70
    if m.startswith("C64") or m.startswith("64"):
        return 61
    if m.startswith("C62") or m.startswith("62"):
        return 60
    return None


def load_rate() -> tuple[float, float, str]:
    cs = yaml.safe_load(open(YAML))["cost_structure"]
    it = next(i for i in cs["items"] if i["code"] == "arrival_handling")
    return float(it["rate"]), float(it.get("tax", 0)), it["base"]


def read_runda7_override_cars() -> list[dict]:
    if not RUNDA7_XLSX.exists():
        return [{"car_no": None, "hph": None, "w": 1556 / 23} for _ in range(23)]
    import openpyxl
    ws = openpyxl.load_workbook(RUNDA7_XLSX, data_only=True)["Sheet1"]
    out = []
    for r in range(2, ws.max_row + 1):
        seq, _d, car_no, hph, marked, *_ = [ws.cell(r, col).value for col in range(1, 9)]
        if seq is None or marked is None:
            continue
        out.append({"car_no": str(car_no), "hph": hph, "w": float(marked)})
    return out


def collect_groups(conn) -> list[dict]:
    """从 DB 拉 (ship, plan, 车, 计费重量),并定 settle_party。运达7 拆两组。"""
    groups = []
    # 铁路计费重量 per-car = 取非零的那个(actual 优先,回退 marked;
    # 丰收散运等船计费吨存在 marked_weight、actual_weight=0,反之亦然,不可只取一列)
    rows = conn.execute(
        "SELECT ship_name, plan_no, count(*), "
        "ROUND(COALESCE(SUM(COALESCE(NULLIF(actual_weight,0), marked_weight, 0)),0),2) "
        "FROM zhongtang_dispatch_map GROUP BY ship_name, plan_no").fetchall()
    ovr = read_runda7_override_cars()
    ovr_c, ovr_w = len(ovr), round(sum(o["w"] for o in ovr), 2)
    for ship, plan, cars, w in rows:
        if ship == RUNDA7:
            # 拆:override 23车→中唐;剩余→铁发
            groups.append({"ship": ship, "plan": plan, "cars": ovr_c, "w": ovr_w,
                           "party": ZT, "note": "运达7车级改派(override)"})
            groups.append({"ship": ship, "plan": plan, "cars": cars - ovr_c,
                           "w": round(w - ovr_w, 2), "party": TIEFA, "note": "运达7非改派部分"})
            continue
        if ship in TIEFA_FULL_SHIPS or (ship, plan) in TIEFA_SHIP_PLAN:
            party = TIEFA
        else:
            party = ZT
        groups.append({"ship": ship, "plan": plan, "cars": cars, "w": w, "party": party, "note": ""})

    # 鞍子河 6月(dispatch_map 没有)→ wagon_shipments 按车型算标载
    june = conn.execute(
        "SELECT ws.car_model, COUNT(*) FROM wagon_shipments ws JOIN release_batches rb ON ws.batch_id=rb.id "
        "WHERE rb.project=? AND rb.ship_name='鞍子河' AND rb.plan_id=? GROUP BY ws.car_model",
        (PROJECT, ANZIHE_JUNE_PLAN)).fetchall()
    jc = sum(n for _, n in june)
    jw = sum((biaozai(m) or 0) * n for m, n in june)
    if jc:
        groups.append({"ship": "鞍子河", "plan": ANZIHE_JUNE_PLAN, "cars": jc, "w": float(jw),
                       "party": ZT, "note": "6月lot,wagon车型算标载补全"})
    return groups, ovr


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    rate, tax, base = load_rate()
    now = now_iso_beijing()
    conn = sqlite3.connect(str(SOP_DB))
    conn.execute("DELETE FROM billing_cost_item WHERE project=? AND cost_code='arrival_handling'", (PROJECT,))
    conn.execute("DELETE FROM billing_settle_override WHERE project=? AND cost_code='arrival_handling'", (PROJECT,))

    groups, ovr = collect_groups(conn)
    for g in groups:
        amt = round(rate * g["w"], 2)
        cid = _h(PROJECT, "arrival_handling", g["ship"], g["plan"], g["party"])
        conn.execute(
            """INSERT OR REPLACE INTO billing_cost_item
            (id,project,batch_ref,ship_name,period,cost_code,cost_name,side,settle_party,
             rate,base_kind,base_qty,car_count,amount,tax_rate,status,source,note,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, PROJECT, g["ship"], g["ship"], g["plan"], "arrival_handling", "到站装卸费",
             "passthrough", g["party"], rate, base, g["w"], g["cars"], amt, tax,
             "生成费用", "DB:dispatch_map+wagon车型", g["note"], now, now))
    for o in ovr:
        oid = _h(PROJECT, RUNDA7, "arrival_handling", o["car_no"], o["hph"])
        conn.execute(
            """INSERT OR REPLACE INTO billing_settle_override
            (id,project,ship_name,cost_code,car_no,ydid,hph,from_party,to_party,billing_weight,note,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (oid, PROJECT, RUNDA7, "arrival_handling", o["car_no"], None, o["hph"],
             TIEFA, ZT, o["w"], "运达7装卸费结算方变更", now))

    def agg(party):
        return conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(car_count),0),ROUND(COALESCE(SUM(base_qty),0),2),"
            "ROUND(COALESCE(SUM(amount),0),2) FROM billing_cost_item "
            "WHERE project=? AND cost_code='arrival_handling' AND settle_party=?", (PROJECT, party)).fetchone()
    zt, tf = agg(ZT), agg(TIEFA)
    print(f"费率 {rate}/吨(yaml)× 铁路计费重量;源=DB(dispatch_map + wagon车型补6月)")
    print(f"  结中唐特钢: 组{zt[0]:>2} 车{zt[1]:>5} 重{zt[2]:>10} 金额{zt[3]:>12}")
    print(f"  结铁发    : 组{tf[0]:>2} 车{tf[1]:>5} 重{tf[2]:>10} 金额{tf[3]:>12}  (含早期联合/非凡/德邻惠海170,手帐未涵盖)")
    ok = (zt[1] == EXPECT_ZT[0] and abs(zt[2]-EXPECT_ZT[1]) < 0.5 and abs(zt[3]-EXPECT_ZT[2]) < 0.5)
    print(f"\n验收 中唐特钢应 4724车/318246吨/2068599元: {'✓ 对平' if ok else '✗ 不符'}")
    if args.apply and ok:
        conn.commit(); print("COMMIT ✓")
    elif args.apply:
        conn.rollback(); print("ROLLBACK(未对平)"); sys.exit(1)
    else:
        conn.rollback(); print("DRY-RUN(加 --apply 落库)")
    conn.close()


if __name__ == "__main__":
    main()
