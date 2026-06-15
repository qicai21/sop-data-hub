"""吉林金钢工厂门户 —— 批量删除运输单记录(remove API 集成)。

纠正错误上传(整批误传 / 重复 / 脏行)时用:按订单标识号(或 release_batch)查出
门户内部 id,批量删除。与 factory_verify(查)/ factory_upload(传)配套三件套。

接口:POST {FACTORY_ENDPOINT}/sales/transportOrder/remove,body=[id, ...]
要点:
  - 删除用的是门户**内部 id(序号)**,不是箱号 —— 必须先查 list 拿到。
  - remove 返回 HTTP 200(常空 body),**不以 body 判成功,以"再查条数下降"为准**。
  - 默认 dry_run=True 只预览不删;真删的是**收货人第三方系统**,必须显式 apply。
  - 可按 record_date_prefix 过滤(只删某天误传,如 '2026-06-15'),不误伤历史。

用法:
  # 预览某订单将删多少(不删)
  python -m sop_hub.sop.factory_remove --order-id CGR20260612094028
  # 只删某天的(真删)
  python -m sop_hub.sop.factory_remove --order-id CGR... --record-date 2026-06-15 --apply
  # 按 release_batch 解析订单号后全删
  python -m sop_hub.sop.factory_remove --release-batch-id <ID> --apply
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from sop_hub.sop.factory_verify import (
    FACTORY_ENDPOINT,
    LIST_PATH,
    SOP_DB,
    _login,
)

REMOVE_PATH = "/sales/transportOrder/remove"
DEFAULT_FORM_ID = "MR07"


@dataclass
class RemoveSummary:
    order_id: str = ""
    form_id: str = DEFAULT_FORM_ID
    login_ok: bool = False
    matched: int = 0          # 命中(过滤后)待删条数
    removed: int = 0          # 实删条数(dry_run 时为 0)
    remaining_matched: int = 0  # 删后仍命中条数(应为 0)
    dry_run: bool = True
    error: str = ""
    sample: list = field(default_factory=list)  # 前几条 (id, 车号, 箱号, 录入)

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id, "form_id": self.form_id,
            "login_ok": self.login_ok, "matched": self.matched,
            "removed": self.removed, "remaining_matched": self.remaining_matched,
            "dry_run": self.dry_run, "error": self.error, "sample": self.sample,
        }


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Cookie": f"Admin-Token={token}",
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json",
    }


def fetch_order_rows(order_id: str, token: str, *, form_id: str = DEFAULT_FORM_ID,
                     page_size: int = 100, max_pages: int = 20) -> list[dict]:
    """拉某订单门户全部行(分页)。返回 [{id, wagonNumber, boxNumber, recordDate}]。"""
    headers = {"Authorization": f"Bearer {token}",
               "Cookie": f"Admin-Token={token}", "Accept": "application/json"}
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        resp = requests.get(
            f"{FACTORY_ENDPOINT}{LIST_PATH}",
            params={"pageNum": page, "pageSize": page_size,
                    "orderId": order_id, "formId": form_id},
            headers=headers, timeout=30,
        )
        if resp.status_code != 200:
            break
        rows = resp.json().get("rows", [])
        for r in rows:
            out.append({"id": r.get("id"), "wagonNumber": r.get("wagonNumber"),
                        "boxNumber": r.get("boxNumber"), "recordDate": r.get("recordDate")})
        if len(rows) < page_size:
            break
    return out


def _filter(rows: list[dict], *, record_date_prefix: str | None,
            car_nos: set[str] | None) -> list[dict]:
    out = rows
    if record_date_prefix:
        out = [r for r in out if (r.get("recordDate") or "").startswith(record_date_prefix)]
    if car_nos:
        out = [r for r in out if str(r.get("wagonNumber")) in car_nos]
    return out


def remove_ids(ids: list[int], token: str, *, batch_size: int = 40) -> int:
    """分批 POST remove。返回发出的删除条数(HTTP 200 计入,真实生效由调用方复查)。"""
    sent = 0
    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]
        resp = requests.post(f"{FACTORY_ENDPOINT}{REMOVE_PATH}", json=chunk,
                             headers=_headers(token), timeout=30)
        if resp.status_code == 200:
            sent += len(chunk)
        time.sleep(1.0)
    return sent


def _resolve_order_id(release_batch_id: str, db_path: Path | str | None) -> str:
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT order_identifier FROM release_batches WHERE id=?",
            (release_batch_id,)).fetchone()
        return (row[0] if row else "") or ""
    finally:
        conn.close()


def remove_order(
    order_id: str | None = None,
    *,
    release_batch_id: str | None = None,
    form_id: str = DEFAULT_FORM_ID,
    record_date_prefix: str | None = None,
    car_nos: set[str] | None = None,
    db_path: Path | str | None = None,
    dry_run: bool = True,
    batch_size: int = 40,
) -> RemoveSummary:
    """高层口:登录 → 查订单行 →(过滤)→ 删除 → 复查。

    order_id 与 release_batch_id 二选一(后者从 release_batches.order_identifier 解析)。
    dry_run=True(默认)只预览命中条数,不发删除请求。
    """
    s = RemoveSummary(form_id=form_id, dry_run=dry_run)
    if not order_id and release_batch_id:
        order_id = _resolve_order_id(release_batch_id, db_path)
    if not order_id:
        s.error = "no order_id (传 --order-id 或可解析的 --release-batch-id)"
        return s
    s.order_id = order_id

    token, err = _login()
    if err or not token:
        s.error = f"login failed: {err}"
        return s
    s.login_ok = True

    rows = fetch_order_rows(order_id, token, form_id=form_id)
    matched = _filter(rows, record_date_prefix=record_date_prefix, car_nos=car_nos)
    s.matched = len(matched)
    s.sample = [(r["id"], r["wagonNumber"], r["boxNumber"], r["recordDate"])
                for r in matched[:5]]
    if dry_run or not matched:
        return s

    s.removed = remove_ids([r["id"] for r in matched], token, batch_size=batch_size)
    # 复查:仍命中多少(应 0)
    after = _filter(fetch_order_rows(order_id, token, form_id=form_id),
                    record_date_prefix=record_date_prefix, car_nos=car_nos)
    s.remaining_matched = len(after)
    return s


def _build_cli():
    import argparse
    p = argparse.ArgumentParser(description="吉林金钢工厂门户批量删除")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--order-id", help="订单标识号(orderId)")
    g.add_argument("--release-batch-id", help="release_batch id(解析订单号)")
    p.add_argument("--form-id", default=DEFAULT_FORM_ID)
    p.add_argument("--record-date", help="只删录入日期前缀匹配的(如 2026-06-15)")
    p.add_argument("--apply", action="store_true", help="真删(默认只预览)")
    return p


def main():
    import json
    args = _build_cli().parse_args()
    s = remove_order(order_id=args.order_id, release_batch_id=args.release_batch_id,
                     form_id=args.form_id, record_date_prefix=args.record_date,
                     dry_run=not args.apply)
    print(json.dumps(s.to_dict(), ensure_ascii=False, indent=2))
    if s.dry_run and s.matched:
        print(f"\n[预览] 命中 {s.matched} 条,加 --apply 真删")
    elif not s.dry_run:
        print(f"\n[已删] 发出 {s.removed} 条,复查仍命中 {s.remaining_matched} 条"
              + ("(✓ 已清)" if s.remaining_matched == 0 else "(⚠ 没删干净)"))


if __name__ == "__main__":
    main()
