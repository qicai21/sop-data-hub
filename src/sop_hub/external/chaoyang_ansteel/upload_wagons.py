"""鞍钢门户上传 wagon list + 立即反查 — 一体化。

SvcName: AGM4M.wmwm19_ins_v(_ins_v = insert)

业务流程:
1. 拿 wagon_shipments 里的某 batch 的车号清单(同时也是发 excel 用的 53 车)
2. 调 wmwm01 找对应 plan(按 SHIP_CNAME 匹配),拿 plan 级常量
   REC_ID / MT_ID / TRANSPORT_PLAN_NO / GOODS_CODE / WAYBILL_NO
3. 拼 53 行 payload,字段:
   - WAGONNO ← wagon.car_no
   - WAYBILL_TIME ← wagon.ticketed_at 转 YYYYMMDDHHMMSS
   - FS_NAME ← yaml chaoyang.project_meta.origin_station_name(高桥镇)
   - ANTIFREEZE_FLAG ← yaml 常量(1)
   - uuid ← uuid4()
   - BUY_ORDER_NO/REC_ID/SHIP_CNAME/TRANSPORT_PLAN_NO/GOODS_CODE/MT_ID
     ← 从 plan 复制
4. POST wmwm19_ins_v → 服务端返回新 REC_ID 列表
5. 立即调 wmwm19_inq_v 反查:用同一 plan row 入参,看 WAYBILL_TIME=今天的车
   集合是否跟上传车号集合完全一致(业务铁律:上传后必须反查)
6. 返回 UploadResult(success, uploaded_count, verified_count, missing, extra)

⚠️ 业务铁律:每次上传必须紧跟反查,这条规则写进 yaml SOP +
  内置在本脚本。不允许"只上传不反查"或"反查失败照样标 success"。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import uuid as _uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from sop_hub.external.chaoyang_ansteel.login import login
from sop_hub.external.chaoyang_ansteel.query_wmwm01 import query_wmwm01
from sop_hub.external.chaoyang_ansteel.query_wmwm19 import WMWM19_COLUMNS


UPLOAD_URL = "https://56.ansteel.com.cn/api/api/CallService"
SVC_NAME_INSERT = "AGM4M.wmwm19_ins_v"
SVC_NAME_QUERY = "AGM4M.wmwm19_inq_v"
COMPANY_CODE = "00047800"
COMPANY_NAME = "鞍钢集团朝阳钢铁有限公司"


# yaml chaoyang 常量(目前硬编码,后续接 yaml)
FS_NAME_DEFAULT = "高桥镇"
ANTIFREEZE_FLAG_DEFAULT = "1"


@dataclass
class WagonForUpload:
    car_no: str
    waybill_time: str  # YYYYMMDDHHMMSS


@dataclass
class UploadResult:
    success: bool
    uploaded_count: int = 0
    server_returned_count: int = 0
    verified_count: int = 0     # 反查命中数
    missing_car_nos: list[str] = field(default_factory=list)  # 上传了但反查不到
    # 2026-06-29 口径:本次上传车号在门户返回里出现多于一条(应唯一却重复)
    duplicate_car_nos: list[str] = field(default_factory=list)
    extra_car_nos: list[str] = field(default_factory=list)    # 反查多出的(意外)
    error: str = ""
    plan_summary: str = ""      # plan 摘要(船名/运单/计划吨)
    upload_response_msg: str = ""
    verify_response_msg: str = ""


# ── DB 拉车数据 ────────────────────────────────────────────────────────


def fetch_wagons(
    *,
    db_path: str | Path,
    batch_id: str,
    car_nos: list[str] | None = None,
    ydids: list[str] | None = None,
    date_prefix: str | None = None,
) -> list[WagonForUpload]:
    """从 wagon_shipments 拉某 batch 的车。

    优先 ydids(本次运单唯一集合)；其次 car_nos(明确车号列表 — 给 chain 用,跟 excel 同一批);
    fallback date_prefix(如 '2026-06-02',按 ticketed_at LIKE — 给 CLI 用)。
    都没给 = 拉整个 batch。
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if ydids:
            placeholders = ",".join("?" * len(ydids))
            sql = (
                f"SELECT car_no, ticketed_at, ydid FROM wagon_shipments "
                f"WHERE batch_id = ? AND ydid IN ({placeholders}) "
                f"ORDER BY ticketed_at, car_no"
            )
            args = [batch_id, *ydids]
        elif car_nos:
            # 循环车号同 batch 会多趟：无 ydid 时只取每个 car_no 最新 ticketed_at
            # 一行，避免 07-08 马兰幸福 22 车串 07-05 旧趟（32 行/错上传口径）。
            placeholders = ",".join("?" * len(car_nos))
            sql = (
                f"SELECT w.car_no, w.ticketed_at, w.ydid FROM wagon_shipments w "
                f"WHERE w.batch_id = ? AND w.car_no IN ({placeholders}) "
                f"AND w.ticketed_at = ("
                f"  SELECT MAX(w2.ticketed_at) FROM wagon_shipments w2 "
                f"  WHERE w2.batch_id = w.batch_id AND w2.car_no = w.car_no"
                f") "
                f"ORDER BY w.ticketed_at, w.car_no"
            )
            args = [batch_id, *car_nos]
        elif date_prefix:
            sql = (
                "SELECT car_no, ticketed_at, ydid FROM wagon_shipments "
                "WHERE batch_id = ? AND ticketed_at LIKE ? "
                "ORDER BY ticketed_at, car_no"
            )
            args = [batch_id, f"{date_prefix}%"]
        else:
            sql = (
                "SELECT car_no, ticketed_at, ydid FROM wagon_shipments "
                "WHERE batch_id = ? ORDER BY ticketed_at, car_no"
            )
            args = [batch_id]
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()

    if ydids:
        order = {str(y): i for i, y in enumerate(ydids)}
        rows = sorted(rows, key=lambda r: order.get(str(r["ydid"] or ""), 1_000_000))

    out: list[WagonForUpload] = []
    for r in rows:
        car_no = str(r["car_no"] or "").strip()
        ticketed = str(r["ticketed_at"] or "").strip()
        if not car_no:
            continue
        out.append(WagonForUpload(
            car_no=car_no,
            waybill_time=_to_yyyymmddhhmmss(ticketed),
        ))
    return out


def _to_yyyymmddhhmmss(s: str) -> str:
    """'2026-06-02 09:13:00' / '2026-06-02T09:13:00' → '20260602091300'。"""
    if not s:
        return ""
    cleaned = (s.replace("-", "").replace(":", "")
                .replace(" ", "").replace("T", ""))
    return cleaned[:14]


# ── 上传 ────────────────────────────────────────────────────────────────


def build_upload_payload(
    *,
    plan_raw: dict[str, Any],
    wagons: list[WagonForUpload],
    fs_name: str = FS_NAME_DEFAULT,
    antifreeze_flag: str = ANTIFREEZE_FLAG_DEFAULT,
) -> dict[str, Any]:
    """根据 plan 常量 + 53 wagon,拼 wmwm19_ins_v payload。"""
    plan_rec_id = plan_raw.get("REC_ID", "")
    plan_mt_id = plan_raw.get("MT_ID", "")
    plan_transport_no = plan_raw.get("TRANSPORT_PLAN_NO", "")
    plan_ship_cname = plan_raw.get("SHIP_CNAME", "")
    plan_waybill_no = plan_raw.get("WAYBILL_NO", "")
    plan_goods_code = plan_raw.get("GOODS_CODE", "")

    rows: list[list[Any]] = []
    for w in wagons:
        rows.append([
            w.car_no,                          # WAGONNO
            w.waybill_time,                    # WAYBILL_TIME
            fs_name,                           # FS_NAME
            antifreeze_flag,                   # ANTIFREEZE_FLAG
            str(_uuid.uuid4()),                # uuid
            plan_waybill_no,                   # BUY_ORDER_NO(等于 WAYBILL_NO)
            None,                              # FS_CODE
            plan_rec_id,                       # REC_ID
            plan_ship_cname,                   # SHIP_CNAME
            plan_transport_no,                 # TRANSPORT_PLAN_NO
            plan_goods_code,                   # GOODS_CODE
            plan_mt_id,                        # MT_ID
        ])

    return {
        "SysInfo": {
            "CompanyCode": COMPANY_CODE,
            "CompanyName": COMPANY_NAME,
            "SvcName": SVC_NAME_INSERT,
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
                "Name": "rows",
                "Columns": [
                    {"Name": "WAGONNO", "Caption": "", "DataType": "S"},
                    {"Name": "WAYBILL_TIME", "Caption": "", "DataType": "S"},
                    {"Name": "FS_NAME", "Caption": "", "DataType": "S"},
                    {"Name": "ANTIFREEZE_FLAG", "Caption": "", "DataType": "S"},
                    {"Name": "uuid", "Caption": "", "DataType": "S"},
                    {"Name": "BUY_ORDER_NO", "Caption": "", "DataType": "S"},
                    {"Name": "FS_CODE", "Caption": "", "DataType": "S"},
                    {"Name": "REC_ID", "Caption": "", "DataType": "S"},
                    {"Name": "SHIP_CNAME", "Caption": "", "DataType": "S"},
                    {"Name": "TRANSPORT_PLAN_NO", "Caption": "", "DataType": "S"},
                    {"Name": "GOODS_CODE", "Caption": "", "DataType": "S"},
                    {"Name": "MT_ID", "Caption": "", "DataType": "S"},
                ],
                "ExtendedProperties": {},
                "Rows": rows,
            }
        ],
    }


def call_upload(
    *, session: requests.Session, payload: dict[str, Any], timeout: int = 60,
) -> dict[str, Any]:
    resp = session.post(UPLOAD_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ── 反查(业务铁律)──────────────────────────────────────────────────


def call_query_wmwm19(
    *, session: requests.Session, plan_raw: dict[str, Any], timeout: int = 60,
) -> dict[str, Any]:
    """重放 wmwm19_inq_v,入参用 plan 的原始 row(从 wmwm01 拿的 ~180 列)。"""
    # 注意:wmwm19_inq_v 的 Table 名字是 "Table1",入参列必须跟用户抓包一致
    # 但我们这里只能用 plan_raw(180 列 wmwm01 的列名),它跟 wmwm19_inq_v 入参列
    # 有重叠但不完全相同。为了对账,我们直接重放抓包里的固定 payload — 因为
    # 服务端只看几个关键字段(REC_ID/TRANSPORT_PLAN_NO/MT_ID),其他默认。
    # 简化:从 plan_raw 抽 12 个关键字段,其他全空。
    return _query_wagons_for_plan(session, plan_raw, timeout=timeout)


def _query_wagons_for_plan(
    session: requests.Session, plan_raw: dict[str, Any], timeout: int = 60,
) -> dict[str, Any]:
    """从 plan_raw 自己拼 wmwm19_inq_v payload — 不再依赖任何桌面/外部文件。

    入参 row 的列结构跟 wmwm01 response 列高度重合,加上 6 个前端框架字段
    (uuid/uid/dirty/dirtyFields/id/parent)。从 plan_raw 直接取值。
    """
    row: list[Any] = []
    for col in WMWM19_COLUMNS:
        if col == "_events":
            row.append({"change": [None]})
        elif col == "_handlers":
            row.append({})
        elif col == "dirty":
            row.append(False)
        elif col == "dirtyFields":
            row.append({})
        elif col == "parent":
            row.append(None)
        elif col in ("uuid", "uid", "id"):
            row.append(str(_uuid.uuid4()))
        else:
            # 从 plan_raw 取(wmwm01 response 列名)
            row.append(plan_raw.get(col, ""))

    payload = {
        "SysInfo": {
            "CompanyCode": COMPANY_CODE,
            "CompanyName": COMPANY_NAME,
            "SvcName": SVC_NAME_QUERY,
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
                "Rows": [row],
            }
        ],
    }
    resp = session.post(UPLOAD_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _wagons_from_query_response(body: dict[str, Any]) -> list[dict[str, str]]:
    """从 wmwm19_inq_v 响应里抽 (WAGONNO, WAYBILL_TIME) 列。"""
    out: list[dict[str, str]] = []
    for tbl in (body.get("Tables") or []):
        if tbl.get("Name") != "TWMWM19":
            continue
        cols = [c["Name"] for c in (tbl.get("Columns") or [])]
        idx = {c: i for i, c in enumerate(cols)}
        for r in (tbl.get("Rows") or []):
            out.append({
                "wagonno": str(r[idx["WAGONNO"]] if "WAGONNO" in idx else "").strip(),
                "waybill_time": str(r[idx["WAYBILL_TIME"]] if "WAYBILL_TIME" in idx else "").strip(),
            })
    return out


def _today_site_car_counts(
    site_wagons: list[dict[str, str]],
    today_yyyymmdd: str,
) -> Counter[str]:
    """门户返回是整单累计;朝钢只看"今天写入门户"的车号集合。

    现场核对已确认:门户 `WAYBILL_TIME` 不是铁路 `ticketed_at`,更接近门户写入时间。
    因此不能拿 `car_no + 铁路制票时间` 做跨系统唯一键,否则会把真正已上传的本次车
    全部判成 missing。对朝钢门户来说,当前能稳定落地的口径是:

    - presence: 本次 car_no 是否出现在**今天的门户返回**里
    - unique:   今天的门户返回里,该 car_no 是否只出现 1 次
    """
    return Counter(
        str(w.get("wagonno", "")).strip()
        for w in site_wagons
        if str(w.get("wagonno", "")).strip()
        and str(w.get("waybill_time", "")).startswith(today_yyyymmdd)
    )


# ── 反查对账(2026-06-29 口径:present + unique,按 car_no)──────────────


def reconcile_uploaded_cars(
    uploaded_wagons: list[WagonForUpload],
    site_wagons: list[dict[str, str]],
    today_yyyymmdd: str,
) -> tuple[set[str], list[str], list[str], list[str]]:
    """朝钢反查对账。门户按 plan 反查返回整单全量累计 + 收货端偶发删数,
    故**不做整批对齐**,只校验本次上传车号在"今天门户返回"里的状态:
      present: `car_no` 出现在今天返回里(否则计入 missing)
      unique : `car_no` 在今天返回里仅一条(否则计入 duplicate)
    extra 仅取今天多出的车号,作告警/观测,不参与成败判定。
    返回 (verified, missing, duplicate, extra)。
    """
    site_counts = _today_site_car_counts(site_wagons, today_yyyymmdd)
    uploaded_cars = {str(w.car_no or "").strip() for w in uploaded_wagons if str(w.car_no or "").strip()}
    site_cars = set(site_counts)
    verified = uploaded_cars & site_cars
    missing = sorted(uploaded_cars - site_cars)
    duplicate = sorted(car for car in uploaded_cars if site_counts[car] > 1)
    extra = sorted(site_cars - uploaded_cars)
    return verified, missing, duplicate, extra


# ── 一体化入口 ────────────────────────────────────────────────────────


def upload_and_verify(
    *,
    db_path: str | Path,
    batch_id: str,
    ship_name: str,
    transport_plan_no: str | None = None,
    car_nos: list[str] | None = None,
    ydids: list[str] | None = None,
    date_prefix: str | None = None,
    verify_sleep_seconds: int = 2,
) -> UploadResult:
    """全流程:登录 → wmwm01 找 plan → 拉车 → 上传 → 反查 → 对账。

    ydids / car_nos / date_prefix 三选一(优先 ydid,给 chain 用)。
    """
    # 1. 登录
    auth, session = login()
    if not auth.success:
        return UploadResult(success=False, error=f"login failed: {auth.error}")

    # 2. wmwm01 找 plan
    #   优先回运计划号(transport_plan_no)直查 —— 木森17 这类按船名+日期窗口
    #   查不到的,用回运计划号精确定位(2026-06-18 新增)。否则按 30 天窗口 + 船名匹配。
    from datetime import datetime, timedelta
    if transport_plan_no:
        plans, _ = query_wmwm01(session=session, date_begin="", date_end="",
                                transport_plan_no=transport_plan_no)
        # 回运计划号唯一定位;有多条再按船名收敛
        target_plan = next((p for p in plans if p.ship_cname == ship_name), None) or (plans[0] if plans else None)
        if target_plan is None:
            return UploadResult(
                success=False,
                error=f"wmwm01 按回运计划号 {transport_plan_no!r} 查不到 plan",
            )
    else:
        # 船名窗口兜底极危险:同船多 lot / 多回运计划时 next() 取第一条会串计划号
        # (2026-07-19 马兰幸福 lot3 52 车误挂 lot2 计划)。无 transport_plan_no 时
        # 若同船命中 >1 条 plan → 直接失败,要求调用方传回运计划号。
        today = datetime.now()
        begin = (today - timedelta(days=30)).strftime("%Y%m%d")
        end = (today + timedelta(days=1)).strftime("%Y%m%d")
        plans, _ = query_wmwm01(session=session, date_begin=begin, date_end=end)
        ship_plans = [p for p in plans if p.ship_cname == ship_name]
        if not ship_plans:
            return UploadResult(
                success=False,
                error=f"wmwm01 找不到 ship_name={ship_name!r} 的 plan,只有 "
                      f"{[p.ship_cname for p in plans]}",
            )
        if len(ship_plans) > 1:
            plan_nos = [getattr(p, "transport_plan_no", "") or getattr(p, "allot_plan_no", "")
                        for p in ship_plans]
            return UploadResult(
                success=False,
                error=(
                    f"wmwm01 ship_name={ship_name!r} 命中 {len(ship_plans)} 条 plan "
                    f"{plan_nos}; 必须传入 transport_plan_no=回运计划号,禁止按船名猜"
                ),
            )
        target_plan = ship_plans[0]

    # 3. DB 拉车
    wagons = fetch_wagons(
        db_path=db_path, batch_id=batch_id,
        car_nos=car_nos, ydids=ydids, date_prefix=date_prefix,
    )
    if not wagons:
        return UploadResult(
            success=False,
            error=f"DB 里 batch={batch_id[:8]} "
                  f"car_nos={len(car_nos) if car_nos else None} "
                  f"date={date_prefix} 没车",
        )

    # 3.5 幂等闸(#issue-20260623):上传前先查门户已有车,只传门户上没有的;全有则跳过。
    # 防任何任务异常重跑导致重复上传(今早中联发因老代码反复抓 matched 候选被传了 3 次,
    # 缺的就是这道闸)。best-effort:查门户失败则照常上传(不因闸失败而挡正常发运)。
    try:
        _pre = _wagons_from_query_response(
            call_query_wmwm19(session=session, plan_raw=target_plan.raw))
        today_yyyymmdd = datetime.now().strftime("%Y%m%d")
        _pre_counts = _today_site_car_counts(_pre, today_yyyymmdd)
    except Exception:
        _pre_counts = Counter()
    if _pre_counts:
        _already = [
            w for w in wagons
            if str(w.car_no or "").strip() in _pre_counts
        ]
        wagons = [
            w for w in wagons
            if str(w.car_no or "").strip() not in _pre_counts
        ]
        if not wagons:
            return UploadResult(
                success=True,
                uploaded_count=0,
                verified_count=len(_already),
                upload_response_msg=f"幂等跳过:{len(_already)} 车门户已存在,无需重复上传",
                plan_summary=f"{ship_name} / WAYBILL={target_plan.waybill_no}",
            )

    # 4. 上传
    payload = build_upload_payload(plan_raw=target_plan.raw, wagons=wagons)
    upload_body = call_upload(session=session, payload=payload)
    upload_sys = upload_body.get("SysInfo") or {}
    upload_flag = int(upload_sys.get("Flag", -1))
    upload_msg = (upload_sys.get("Msg") or "").strip()
    if upload_flag != 0:
        return UploadResult(
            success=False,
            uploaded_count=len(wagons),
            upload_response_msg=upload_msg,
            error=f"upload flag={upload_flag} msg={upload_msg!r}",
            plan_summary=f"{ship_name} / {target_plan.waybill_no}",
        )
    # 服务端返回新 REC_ID list
    new_recs = 0
    for tbl in (upload_body.get("Tables") or []):
        if tbl.get("Name") == "TWMWM19":
            new_recs = len(tbl.get("Rows") or [])

    # 5. 反查(业务铁律)
    if verify_sleep_seconds > 0:
        time.sleep(verify_sleep_seconds)
    verify_body = call_query_wmwm19(session=session, plan_raw=target_plan.raw)
    verify_sys = verify_body.get("SysInfo") or {}
    verify_msg = (verify_sys.get("Msg") or "").strip()
    site_wagons = _wagons_from_query_response(verify_body)

    # 6. 对账(2026-06-29 口径:present + unique,按 car_no;不做整批对齐)
    today_yyyymmdd = datetime.now().strftime("%Y%m%d")
    verified, missing, duplicate, extra = reconcile_uploaded_cars(
        wagons, site_wagons, today_yyyymmdd)

    # 通过 = 本次车号全部出现(missing==0)且各自唯一(duplicate==0)。
    success = (len(missing) == 0 and len(duplicate) == 0
               and len(verified) == len(wagons))
    return UploadResult(
        success=success,
        uploaded_count=len(wagons),
        server_returned_count=new_recs,
        verified_count=len(verified),
        missing_car_nos=missing,
        duplicate_car_nos=duplicate,
        extra_car_nos=extra,
        upload_response_msg=upload_msg,
        verify_response_msg=verify_msg,
        plan_summary=f"{ship_name} / WAYBILL={target_plan.waybill_no} / "
                     f"REC_ID={(target_plan.raw.get('REC_ID') or '')[:8]}",
    )


# ── CLI ─────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="鞍钢门户 wagon list 上传 + 反查(一体化)",
    )
    p.add_argument("--db", default="data/sop_agent.db")
    p.add_argument("--batch-id", required=True, help="release_batches.id")
    p.add_argument("--date", default=None,
                   help="如 2026-06-02(按 ticketed_at 前缀筛选;跟 --car-no 二选一)")
    p.add_argument("--car-no", action="append", default=None,
                   help="精确车号(可多个 --car-no);跟 --date 二选一")
    p.add_argument("--ship", required=True, help="ship_name 用于 wmwm01 匹配 plan")
    p.add_argument("--verify-sleep", type=int, default=2)
    args = p.parse_args(argv)

    if not args.date and not args.car_no:
        print("❌ 至少给 --date 或 --car-no 之一", file=sys.stderr)
        return 2

    print(f"→ 上传 batch={args.batch_id[:8]} ship={args.ship} "
          f"{'cars=' + str(len(args.car_no)) if args.car_no else 'date=' + args.date}")
    result = upload_and_verify(
        db_path=args.db,
        batch_id=args.batch_id,
        car_nos=args.car_no,
        date_prefix=args.date,
        ship_name=args.ship,
        verify_sleep_seconds=args.verify_sleep,
    )

    print()
    if result.success:
        print(f"✅ 上传 + 反查全部成功")
    else:
        print(f"❌ {result.error or '反查不一致'}")
    print(f"  plan       : {result.plan_summary}")
    print(f"  uploaded   : {result.uploaded_count}")
    print(f"  server new : {result.server_returned_count}")
    print(f"  verified   : {result.verified_count}/{result.uploaded_count}")
    if result.missing_car_nos:
        print(f"  ⚠️ missing {len(result.missing_car_nos)}: {result.missing_car_nos[:5]}{'…' if len(result.missing_car_nos) > 5 else ''}")
    if result.duplicate_car_nos:
        print(f"  ⚠️ duplicate {len(result.duplicate_car_nos)}: {result.duplicate_car_nos[:5]}{'…' if len(result.duplicate_car_nos) > 5 else ''}")
    if result.extra_car_nos:
        print(f"  ⚠️ extra   {len(result.extra_car_nos)}: {result.extra_car_nos[:5]}{'…' if len(result.extra_car_nos) > 5 else ''}")
    if result.upload_response_msg:
        print(f"  upload msg: {result.upload_response_msg!r}")
    if result.verify_response_msg:
        print(f"  verify msg: {result.verify_response_msg!r}")
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
