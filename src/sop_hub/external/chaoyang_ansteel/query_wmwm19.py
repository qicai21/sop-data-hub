"""鞍钢门户业务查询:车辆运单和订单(wmwm19_inq_v)。

测试目的:验证用户的假设 — 能不能跳过 wmwm01_inq_v1,直接打 wmwm19_inq_v,
只填 DATE_BEGIN/DATE_END,其他 150 个字段空着,服务端是否返回时间窗内全部明细。

如果能,我们就用 1 个 API 拿到细到车号+重量+船名级别的数据,跟 release_batches
/ wagon_shipments 直接对应。

业务上下文(从查询 2 返回的 row 可以看到字段语义):
  - REC_ID/MT_ID: 内部主键
  - ALLOT_PLAN_NO: 分配计划号(e.g. HY2605290015)
  - TRANSPORT_PLAN_NO: 运输计划号(e.g. HY26053002)
  - WAYBILL_NO: 运单号(e.g. DL000751173)
  - GOODS_NAME: 货物品名(e.g. 澳BHP麦克粉)— 对应我们的 cargo_name 详细品名
  - SUPPLIER_DES: 供应商(e.g. 青岛中资钢联国际贸易有限公司)
  - SHIP_CNAME / BOAT_NO: 船名 / 船号(e.g. 宝腾海 / NK260529006)
  - CARRY_COMPANY_NAME: 承运单位(e.g. 鞍钢汽车运输有限责任公司朝阳钢铁分公司)
  - RECEIVE_UNIT: 收货单位(e.g. 鞍钢集团朝阳钢铁有限公司)
  - PLAN_STATUS: 状态(数字码)
  - TOTAL_QUANTITY / REAL_TOTAL_QUANTITY / REMAIN_QUANTITY: 计划/已发/剩余量
  - TRANS_TYPE: 运输类型(D=?)
  - TRANS_FORM: 运输方式(T=铁路?)
  - DATE_BEGIN/DATE_END: 查询时间窗
"""
from __future__ import annotations

import json
import sys
from typing import Any

import requests

from sop_hub.external.chaoyang_ansteel.login import login


QUERY_URL = "https://56.ansteel.com.cn/api/api/CallService"
SVC_NAME = "AGM4M.wmwm19_inq_v"
COMPANY_CODE = "00047800"
COMPANY_NAME = "鞍钢集团朝阳钢铁有限公司"


# wmwm19 完整列结构(从抓包 payload 里抄出来的 ~165 列)。
# 实际查询时绝大部分留空,只填 DATE_BEGIN / DATE_END。
WMWM19_COLUMNS: list[str] = [
    "_events", "_handlers",
    "REC_ID", "MT_ID", "REC_CREATOR", "REC_CREATE_TIME",
    "REC_REVISOR", "REC_REVISE_TIME", "REC_DELETOR", "REC_DELETE_TIME",
    "DELETE_FLAG", "ARCHIVE_FLAG", "ARCHIVE_STAMP_NO", "REMARK",
    "COMPANY_CODE", "COMPANY_NAME",
    "ALLOT_PLAN_NO", "TRANSPORT_PLAN_NO", "WAYBILL_NO", "PLAN_STATUS",
    "GOODS_CODE", "GOODS_NAME", "SUPPLIER_DES", "SUPPLIER_ID",
    "DATE_TIME", "TIME_END", "TRANS_TYPE", "TRANS_FORM",
    "SHIP_CNAME", "BOAT_NO", "CARRY_COMPANY_NAME", "CARRY_COMPANY_CODE",
    "REMAIN_QUANTITY", "REAL_TOTAL_QUANTITY", "TOTAL_QUANTITY",
    "OUTSTORE_ID", "STOCK_NAME", "DG_UNIT_NAME", "DG_UNIT_CODE",
    "PONDER_MARK", "RECEIVE_UNIT", "RECEIVE_UNIT_CODE",
    "SHIFTID", "STOCK_ID", "UNLOAD_CODE", "UNLOAD_NAME",
    "SCRAP_STEEL_DIVI", "BELT_WT", "SERVICE_CODE", "SERVICE_DESC",
    "ORDER_NO", "AG_CON_ID", "AG_CON_NO",
    # 同表的二次 JOIN(订单子表前缀 _1)
    "REC_ID_1", "MT_ID_1", "REC_CREATOR_1", "REC_CREATE_TIME_1",
    "REC_REVISOR_1", "REC_REVISE_TIME_1", "REC_DELETOR_1", "REC_DELETE_TIME_1",
    "DELETE_FLAG_1", "ARCHIVE_FLAG_1", "ARCHIVE_STAMP_NO_1", "REMARK_1",
    "COMPANY_CODE_1", "COMPANY_NAME_1",
    "WAYBILL_NO_1", "BOAT_NO_1", "SHIP_CNAME_1",
    "TRADE_MODE", "DIV_FLAG_1", "TRADE_IN_OUT_FLAG",
    "STA_HARBOR_NAME", "STA_HAVEN_CODE", "AIM_HARBOR_NAME", "AIM_PORT_ID",
    "MAT_CODE", "MAT_NAME", "GOODS_CODE_1", "GOODS_NAME_1",
    "UNIT_PRICE", "LOAD_PORT_DRAFT_WT", "UNLOADING_PORT_DRAFT_WT",
    "PLANWT", "BL_TOTAL_AMOUNT",
    "SECOND_BELT_WT", "STEEL_SCRAP_WT", "BELT_WT_1", "PORT_WT",
    "RW_IN_WT", "RW_OUT_WT", "AT_IN_WT", "AT_OUT_WT",
    "REAL_START_WORK_TIME", "REAL_END_WORK_TIME", "REAL_ARRI_TIME", "REAL_UNBERTH_TIME",
    "RECEIVE_UNIT_CODE_1", "RECEIVE_UNIT_1",
    "SHIP_COMPANY_CODE", "SHIP_COMPANY_CDES",
    "CUNIT_CODE", "CUNIT_NAME", "DRAFT_UNIT_CODE", "DRAFT_UNIT_NAME",
    "AT_ARRI_NUM", "AT_SEND_NUM", "RW_ARRI_NUM", "RW_SEND_NUM",
    "SELL_WT", "IS_CONTAINER", "IS_PORT_RATE",
    "RW_POND_WT", "RW_STACK_WT", "RW_DIRECT_WT", "RW_LOAD_WT",
    "AT_STACK_WT", "AT_DIRECT_WT", "PORT_SECOND_BELT_WT",
    "AT_TAKEOUT_WT_G", "AT_TAKEOUT_WT_S", "RW_TAKEOUT_WT_G", "RW_TAKEOUT_WT_S",
    "PORT_SETTLE_WEIGHT", "FIN_SHIP_WT", "FIN_FLAG", "BAL_FLAG",
    "FLAG_1", "FLAG_2", "FLAG_3", "FLAG_4",
    "BL_WATER", "SEND_WATER", "UNLOAD_WATER", "ARRI_WATER",
    "RW_LOAD_BELT_WT", "PO_ARRI_WT", "BELT_PORT_WT",
    "RW_DIRECT_WT_G", "RW_STACK_WT_G", "RW_DIRECT_WT_S", "RW_STACK_WT_S",
    "AT_DIRECT_WT_S", "AT_STACK_WT_S", "AT_DIRECT_WT_G", "AT_STACK_WT_G",
    "RW_WT_1", "RW_WT_2", "RW_WT_3", "RW_WT_4",
    "FIN_SHIP_WT_G", "SHIP_NO", "RW_WT_ALL", "UNIT",
    "DISSENT_FLAG", "INSU_COMPANY_CODE", "SHIP_SEQ_NO",
    "REMARK0", "REMARK1", "REMARK2", "REMARK3", "REMARK4", "REMARK5", "REMARK6",
    "DATE_TIME_1", "SUPPLIER_ID_1", "SUPPLIER_NAME",
    # ★ 这两个是真正的查询条件
    "DATE_BEGIN", "DATE_END",
    "CONTRACT_FLAG", "DELIVERY_TYPE", "REAL_SHANGJIANTIME", "AT_TZQ_IN_NUM",
    "CLOSE_ORDER_DATE", "ZCJ_POND_WT",
    "BACKN0", "BACKN1", "BACKN10",
    "BACK_C1", "BACK_C2", "BACK_C3",
    # 前端 model 框架字段(末尾 6 个)
    "uuid", "uid", "dirty", "dirtyFields", "id", "parent",
]


def _build_minimal_row(date_begin: str, date_end: str) -> list[Any]:
    """构造最简化的 row — 绝大部分留空 / 0 / None,只填 DATE_BEGIN/DATE_END。"""
    row: list[Any] = []
    for col in WMWM19_COLUMNS:
        if col == "_events":
            row.append({"change": [None]})
        elif col == "_handlers":
            row.append({})
        elif col == "DATE_BEGIN":
            row.append(date_begin)
        elif col == "DATE_END":
            row.append(date_end)
        # 框架字段尽量按原样
        elif col == "dirty":
            row.append(False)
        elif col == "dirtyFields":
            row.append({})
        elif col == "parent":
            row.append(None)
        elif col in ("uuid", "uid", "id"):
            # 随便填一个 placeholder uuid;服务端通常不校验
            row.append("00000000-0000-0000-0000-000000000000")
        else:
            row.append("")
    return row


def query_wmwm19(
    *,
    session: requests.Session,
    date_begin: str,
    date_end: str,
    timeout: int = 60,
) -> dict[str, Any]:
    """跳过 wmwm01,直接打 wmwm19 拿明细。"""
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
                    {"Name": c, "Caption": "", "DataType": "S"}
                    for c in WMWM19_COLUMNS
                ],
                "ExtendedProperties": {},
                "Rows": [_build_minimal_row(date_begin, date_end)],
            }
        ],
    }
    resp = session.post(QUERY_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="测试:跳过 wmwm01,直接打 wmwm19")
    p.add_argument("--from", dest="date_begin", default="20260529")
    p.add_argument("--to", dest="date_end", default="20260602")
    p.add_argument("--save", type=str, default=None,
                   help="把完整 response 存到文件(JSON)")
    args = p.parse_args(argv)

    print(f"→ 1. 登录...")
    res, session = login()
    if not res.success:
        print(f"  ❌ login failed: {res.error}")
        return 1
    print(f"  ✅ {res.user_name_cn} / signature 过期 {res.signature_expires_at}")

    print(f"\n→ 2. 直接查 wmwm19(只填 DATE_BEGIN/END,其他全空)...")
    print(f"  时间窗 {args.date_begin} ~ {args.date_end}")
    try:
        body = query_wmwm19(
            session=session, date_begin=args.date_begin, date_end=args.date_end
        )
    except Exception as exc:
        print(f"  ❌ 异常: {exc}")
        return 1

    sys_info = body.get("SysInfo") or {}
    flag = int(sys_info.get("Flag", -1))
    msg = (sys_info.get("Msg") or "").strip()
    print(f"  SysInfo: flag={flag}, msg={msg!r}")

    tables = body.get("Tables") or []
    for tbl in tables:
        rows = tbl.get("Rows") or []
        cols = [c["Name"] for c in (tbl.get("Columns") or [])]
        print(f"\n  📋 表 {tbl.get('Name')} 返回 {len(rows)} 行 × {len(cols)} 列")
        # 印前 3 行简要(挑业务关键列)
        key_cols = ["WAYBILL_NO", "BOAT_NO", "SHIP_CNAME",
                    "GOODS_NAME", "SUPPLIER_DES",
                    "TOTAL_QUANTITY", "REAL_TOTAL_QUANTITY",
                    "PLAN_STATUS", "DATE_TIME"]
        idx_map = {c: i for i, c in enumerate(cols)}
        for r_i, row in enumerate(rows[:5]):
            print(f"\n  row#{r_i}:")
            for k in key_cols:
                if k in idx_map:
                    v = row[idx_map[k]]
                    print(f"    {k:<22} = {v}")

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
