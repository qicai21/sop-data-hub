"""鞍钢门户:回运计划查询(wmwm01_inq_v1)— 单一查询入口。

入参极简(4 个字段),返回完整明细。
- TRANSPORT_PLAN_NO: 可空,空 = 所有
- BEGIN_DATE: YYYYMMDD
- END_DATE: YYYYMMDD
- UNIT_CODE: 可空

返回 Table TWMWM01,每行 ~170 列。业务关键字段(命名见 query_wmwm19.py 注释):
  ALLOT_PLAN_NO / TRANSPORT_PLAN_NO / WAYBILL_NO
  SHIP_CNAME / BOAT_NO
  GOODS_NAME / SUPPLIER_DES
  TOTAL_QUANTITY / REAL_TOTAL_QUANTITY / REMAIN_QUANTITY (吨)
  PORT_WT (港口实重) / FIN_SHIP_WT (终船重)
  CARRY_COMPANY_NAME (承运单位)
  RECEIVE_UNIT (收货单位)
  PLAN_STATUS / FIN_FLAG
  UNIT_PRICE
  DATE_BEGIN / DATE_END (单据时间)
  REAL_START_WORK_TIME / REAL_END_WORK_TIME / REAL_ARRI_TIME
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any

import requests

from sop_hub.external.chaoyang_ansteel.login import login


QUERY_URL = "https://56.ansteel.com.cn/api/api/CallService"
SVC_NAME = "AGM4M.wmwm01_inq_v1"
COMPANY_CODE = "00047800"
COMPANY_NAME = "鞍钢集团朝阳钢铁有限公司"


# ── 简化业务行(只挑常用列,从 ~170 列里抽)──────────────────────────


@dataclass
class PlanRow:
    allot_plan_no: str = ""           # 分配计划号
    transport_plan_no: str = ""       # 运输计划号
    waybill_no: str = ""              # 运单号
    ship_cname: str = ""              # 船名
    boat_no: str = ""                 # 船号
    goods_name: str = ""              # 货物品名(澳BHP麦克粉)
    supplier_des: str = ""            # 供应商(收货前的卖方)
    receive_unit: str = ""            # 收货单位(朝阳钢铁等)
    carry_company_name: str = ""      # 承运单位(鞍钢汽车运输等)
    total_quantity: float = 0.0       # 计划吨
    real_total_quantity: float = 0.0  # 已发吨
    remain_quantity: float = 0.0      # 剩吨
    port_wt: float = 0.0              # 港口实重(贴近我们的 batch_quantity)
    fin_ship_wt: float = 0.0          # 终船重
    unit_price: float = 0.0           # 单价(元/吨)
    plan_status: str = ""             # 状态
    fin_flag: str = ""                # 完结标志
    date_begin: str = ""              # 计划开始
    date_end: str = ""                # 计划结束
    real_arri_time: str = ""          # 实际到达时间
    raw: dict[str, Any] = field(default_factory=dict)  # 整行字典


def _row_to_plan(cols: list[str], row: list[Any]) -> PlanRow:
    idx = {c: i for i, c in enumerate(cols)}
    def g(name: str, default: Any = "") -> Any:
        if name not in idx:
            return default
        v = row[idx[name]]
        return v if v is not None else default
    raw = {c: row[i] for i, c in enumerate(cols)}
    return PlanRow(
        allot_plan_no=str(g("ALLOT_PLAN_NO")).strip(),
        transport_plan_no=str(g("TRANSPORT_PLAN_NO")).strip(),
        waybill_no=str(g("WAYBILL_NO")).strip(),
        ship_cname=str(g("SHIP_CNAME")).strip(),
        boat_no=str(g("BOAT_NO")).strip(),
        goods_name=str(g("GOODS_NAME")).strip(),
        supplier_des=str(g("SUPPLIER_DES")).strip(),
        receive_unit=str(g("RECEIVE_UNIT")).strip(),
        carry_company_name=str(g("CARRY_COMPANY_NAME")).strip(),
        total_quantity=float(g("TOTAL_QUANTITY", 0) or 0),
        real_total_quantity=float(g("REAL_TOTAL_QUANTITY", 0) or 0),
        remain_quantity=float(g("REMAIN_QUANTITY", 0) or 0),
        port_wt=float(g("PORT_WT", 0) or 0),
        fin_ship_wt=float(g("FIN_SHIP_WT", 0) or 0),
        unit_price=float(g("UNIT_PRICE", 0) or 0),
        plan_status=str(g("PLAN_STATUS")).strip(),
        fin_flag=str(g("FIN_FLAG")).strip(),
        date_begin=str(g("DATE_BEGIN")).strip(),
        date_end=str(g("DATE_END")).strip(),
        real_arri_time=str(g("REAL_ARRI_TIME")).strip(),
        raw=raw,
    )


def query_wmwm01(
    *,
    session: requests.Session,
    date_begin: str,
    date_end: str,
    transport_plan_no: str = "",
    unit_code: str = "",
    timeout: int = 60,
) -> tuple[list[PlanRow], dict[str, Any]]:
    """按时间窗查回运计划。返回 (rows, raw_body)。"""
    payload = {
        "SysInfo": {
            "CompanyCode": COMPANY_CODE,
            "CompanyName": COMPANY_NAME,
            "SvcName": SVC_NAME,
            "Msg": "",
            "Flag": 0,
            "Sender": "CWL20085",
            "UserName": "朱峰",
            "ForeIP": "",
            "ForeMac": "",
            "UUID": "",
        },
        "ExtendedProperties": {},
        "Tables": [
            {
                "Name": "Table1",
                "Columns": [
                    {"Name": "TRANSPORT_PLAN_NO", "Caption": "", "DataType": "S"},
                    {"Name": "BEGIN_DATE", "Caption": "", "DataType": "S"},
                    {"Name": "END_DATE", "Caption": "", "DataType": "S"},
                    {"Name": "UNIT_CODE", "Caption": "", "DataType": "S"},
                ],
                "ExtendedProperties": {},
                "Rows": [[transport_plan_no, date_begin, date_end, unit_code]],
            }
        ],
    }
    resp = session.post(QUERY_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    rows_out: list[PlanRow] = []
    for tbl in (body.get("Tables") or []):
        if tbl.get("Name") != "TWMWM01":
            continue
        cols = [c["Name"] for c in (tbl.get("Columns") or [])]
        for row in (tbl.get("Rows") or []):
            rows_out.append(_row_to_plan(cols, row))
    return rows_out, body


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="鞍钢门户:按时间窗查询回运计划(wmwm01)")
    p.add_argument("--from", dest="date_begin", default="20260529")
    p.add_argument("--to", dest="date_end", default="20260602")
    p.add_argument("--save", type=str, default=None,
                   help="把完整 response 存到文件(JSON)")
    p.add_argument("--full", action="store_true",
                   help="打印每行 ~170 列完整 raw,不只 PlanRow 简化字段")
    args = p.parse_args(argv)

    print(f"→ 登录...")
    res, session = login()
    if not res.success:
        print(f"  ❌ {res.error}")
        return 1
    print(f"  ✅ {res.user_name_cn}")

    print(f"\n→ 查 wmwm01 [{args.date_begin} ~ {args.date_end}]")
    rows, body = query_wmwm01(
        session=session,
        date_begin=args.date_begin,
        date_end=args.date_end,
    )
    sys_info = body.get("SysInfo") or {}
    print(f"  flag={sys_info.get('Flag')} msg={(sys_info.get('Msg') or '').strip()!r}")
    print(f"  TWMWM01 返回 {len(rows)} 行\n")

    for i, r in enumerate(rows):
        print(f"  ━━ 行 {i + 1} ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"    船名 / 船号:    {r.ship_cname} / {r.boat_no}")
        print(f"    货物 / 供应商:  {r.goods_name} / {r.supplier_des}")
        print(f"    承运 → 收货:    {r.carry_company_name} → {r.receive_unit}")
        print(f"    计划 / 已发 / 剩: {r.total_quantity} / {r.real_total_quantity} / {r.remain_quantity} t")
        print(f"    港口实重 / 终船重: {r.port_wt} / {r.fin_ship_wt} t")
        print(f"    单价 / 状态 / 完结: {r.unit_price} 元/吨 / status={r.plan_status} / fin_flag={r.fin_flag}")
        print(f"    单据时间窗:    {r.date_begin} ~ {r.date_end}")
        print(f"    实际到达:      {r.real_arri_time}")
        print(f"    计划号:        allot={r.allot_plan_no} / transport={r.transport_plan_no} / waybill={r.waybill_no}")

    if args.save:
        from pathlib import Path
        Path(args.save).write_text(
            json.dumps(body, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n  完整 response 存到 {args.save}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
