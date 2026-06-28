"""Verify factory upload results by querying the list API — R55.

After uploading wagon data to the factory system, this module:
  1. Logs in to get a token
  2. Queries GET /prod-api/sales/transportOrder/list
     ?pageNum=1&pageSize=100&orderId=<ID>&formId=MR07
  3. Handles pagination (fetches all pages if total > pageSize)
  4. 判定口径(2026-06-29):门户按计划号查必返回整单全量累计 + 收货端偶发删数,
     故**不做整批对齐**。只校验本次上传的每个键(吉林=box_no):
       a) 都出现在返回里(present / missing==0)
       b) 各自唯一(unique / 无 duplicate)
     total / extra 仍计算但仅作观测,不参与成败判定。
  5. Reports mismatches (missing / duplicate / extra-for-log)

Usage:
  PYTHONPATH=src python -m sop_hub.sop.factory_verify \\
    --release-batch-id <ID> [--order-id <ID>]
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# ── Canonical paths ──────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[3]
SOP_DB = REPO_ROOT / "data" / "sop_agent.db"

FACTORY_ENDPOINT = "http://111.26.178.96:88/prod-api"
LOGIN_PATH = "/auth/login"
LIST_PATH = "/sales/transportOrder/list"

FACTORY_USERNAME = "saibin"
FACTORY_PASSWORD = "Xts@95306"


# ── Data models ──────────────────────────────────────────────────────────

@dataclass
class VerifySummary:
    """Result of verifying uploaded data against factory system."""

    order_id: str
    form_id: str = "MR07"
    login_ok: bool = False
    login_error: str = ""

    # From our upload
    expected_count: int = 0
    expected_box_numbers: set[str] = field(default_factory=set)

    # From factory list API
    api_total: int = 0
    api_rows_fetched: int = 0
    api_box_numbers: set[str] = field(default_factory=set)
    pages_fetched: int = 0

    # Comparison
    total_match: bool = False
    all_boxes_found: bool = False
    missing_boxes: list[str] = field(default_factory=list)
    extra_boxes: list[str] = field(default_factory=list)
    # 2026-06-29 口径:本次上传键在门户返回里出现多于一条(应唯一却重复)
    duplicate_boxes: list[str] = field(default_factory=list)

    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "form_id": self.form_id,
            "login_ok": self.login_ok,
            "expected_count": self.expected_count,
            "api_total": self.api_total,
            "api_rows_fetched": self.api_rows_fetched,
            "pages_fetched": self.pages_fetched,
            "total_match": self.total_match,
            "all_boxes_found": self.all_boxes_found,
            "missing_boxes": self.missing_boxes[:20],
            "missing_box_count": len(self.missing_boxes),
            "extra_boxes": self.extra_boxes[:20],
            "extra_box_count": len(self.extra_boxes),
            "duplicate_boxes": self.duplicate_boxes[:20],
            "duplicate_box_count": len(self.duplicate_boxes),
            "error": self.error,
            "login_error": self.login_error,
        }


# ── Auth ─────────────────────────────────────────────────────────────────

def _login() -> tuple[str | None, str]:
    try:
        resp = requests.post(
            f"{FACTORY_ENDPOINT}{LOGIN_PATH}",
            json={"username": FACTORY_USERNAME, "password": FACTORY_PASSWORD},
            timeout=15,
        )
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        data = resp.json()
        token = (
            data.get("token")
            or data.get("access_token")
            or (data.get("data") or {}).get("token")
            or (data.get("data") or {}).get("access_token")
        )
        if not token:
            return None, f"no token: {json.dumps(data)[:200]}"
        return token, ""
    except requests.RequestException as e:
        return None, str(e)


# ── Per-event 判定口径(2026-06-29)─────────────────────────────────────

def evaluate_presence_and_uniqueness(
    expected_keys: set[str],
    api_key_counts: "Counter[str] | dict[str, int]",
) -> tuple[list[str], list[str]]:
    """本次上传是否"全部到位且唯一"——per-event 反查判定。

    收货人门户按计划号(orderId/回运计划号)反查**必然返回整单全量累计**,
    且收货端偶发删已收货数据,所以**不做整批对齐**:不要求 total==expected,
    也不因门户存在历史 extra 记录而判失败。只校验本次上传的每个键:
      1) 出现在返回里(present)        → 否则计入 missing
      2) 在返回里各自仅一条(unique)   → 否则计入 duplicate
    键:吉林金钢=box_no(箱号),朝钢=car_no(车号)。

    返回 (missing, duplicate);二者皆空即通过。
    """
    missing = sorted(k for k in expected_keys if api_key_counts.get(k, 0) == 0)
    duplicate = sorted(k for k in expected_keys if api_key_counts.get(k, 0) > 1)
    return missing, duplicate


# ── Core ─────────────────────────────────────────────────────────────────

def verify_factory_upload(
    order_id: str,
    *,
    form_id: str = "MR07",
    expected_box_numbers: set[str] | None = None,
    expected_count: int | None = None,
    db_path: str | Path | None = None,
    release_batch_id: str | None = None,
    page_size: int = 100,
    dry_run: bool = False,
) -> VerifySummary:
    """Verify uploaded wagon data against factory list API.

    Args:
        order_id: The orderId parameter (订单标识号).
        form_id: The formId parameter (default "MR07").
        expected_box_numbers: Box numbers we uploaded (optional, inferred from DB).
        expected_count: Total records we uploaded (optional, inferred).
        db_path: Optional sop_agent.db path for auto-inference.
        release_batch_id: Optional release_batch_id for auto-inference.
        page_size: Page size for list API (default 100).
        dry_run: Skip actual API call.

    Returns:
        VerifySummary with comparison results.
    """
    summary = VerifySummary(order_id=order_id, form_id=form_id)

    # ── Auto-infer expected values from DB ──
    if release_batch_id and (expected_box_numbers is None or expected_count is None):
        sop_path = Path(db_path) if db_path else SOP_DB
        if sop_path.exists():
            conn = sqlite3.connect(str(sop_path))
            conn.row_factory = sqlite3.Row
            try:
                batch = conn.execute(
                    "SELECT order_identifier FROM release_batches WHERE id=?",
                    (release_batch_id,),
                ).fetchone()
                if batch and not order_id:
                    order_id = batch["order_identifier"] or order_id
                    summary.order_id = order_id

                # #123 Phase 2:优先 SELECT 新表 wagon_container_shipments;
                # fallback wagon_shipments(老业务/老数据)
                box_rows = conn.execute(
                    "SELECT box_no FROM wagon_container_shipments WHERE batch_id=?",
                    (release_batch_id,),
                ).fetchall()
                if box_rows:
                    boxes = {r["box_no"] for r in box_rows if r["box_no"]}
                else:
                    # fallback 老路径
                    wagons = conn.execute(
                        "SELECT container_no, container_numbers_json "
                        "FROM wagon_shipments WHERE batch_id=?",
                        (release_batch_id,),
                    ).fetchall()
                    def _row_boxes(row) -> list[str]:
                        cnj = row["container_numbers_json"]
                        if cnj:
                            try:
                                return [b for b in json.loads(cnj) if b]
                            except Exception:
                                pass
                        raw = row["container_no"] or ""
                        return [b.strip() for b in raw.split("/") if b.strip()]
                    boxes = set()
                    for w in wagons:
                        boxes.update(_row_boxes(w))
                if expected_box_numbers is None:
                    expected_box_numbers = boxes
                if expected_count is None:
                    expected_count = len(boxes)
            finally:
                conn.close()

    summary.expected_count = expected_count or 0
    summary.expected_box_numbers = expected_box_numbers or set()

    if dry_run:
        summary.total_match = True
        summary.all_boxes_found = True
        return summary

    # ── Login ──
    token, err = _login()
    if err:
        summary.login_error = err
        summary.error = f"login failed: {err}"
        return summary
    summary.login_ok = True

    headers = {
        "Authorization": f"Bearer {token}",
        "Cookie": f"Admin-Token={token}",
        "Accept": "application/json",
    }

    # ── Paginated fetch ──
    # 用 Counter 记每个 boxNumber 在门户返回里出现的次数(唯一性校验要用)
    api_box_counts: Counter[str] = Counter()
    page = 1
    total = 0

    while True:
        resp = requests.get(
            f"{FACTORY_ENDPOINT}{LIST_PATH}",
            params={
                "pageNum": page,
                "pageSize": page_size,
                "orderId": order_id,
                "formId": form_id,
            },
            headers=headers,
            timeout=30,
        )

        if resp.status_code != 200:
            summary.error = f"list API HTTP {resp.status_code}: {resp.text[:200]}"
            return summary

        data = resp.json()
        page_total = data.get("total", 0)
        if page == 1:
            total = page_total
        rows = data.get("rows", [])

        for row in rows:
            bn = (row.get("boxNumber") or "").strip()
            if bn:
                api_box_counts[bn] += 1

        summary.api_rows_fetched += len(rows)
        summary.pages_fetched = page

        if page * page_size >= total:
            break
        page += 1

    all_boxes: set[str] = set(api_box_counts)
    summary.api_total = total
    summary.api_box_numbers = all_boxes

    # ── Compare(2026-06-29 口径:present + unique,不做整批对齐)──
    # total_match / extra_boxes 仍计算并保留,**仅作观测/脏 log**,不参与判定。
    summary.total_match = (summary.expected_count == total)
    summary.extra_boxes = sorted(all_boxes - summary.expected_box_numbers)
    # 判定:本次上传的每个键都出现(present)且各自唯一(unique)即通过。
    missing, duplicate = evaluate_presence_and_uniqueness(
        summary.expected_box_numbers, api_box_counts
    )
    summary.missing_boxes = missing
    summary.duplicate_boxes = duplicate
    summary.all_boxes_found = (len(missing) == 0 and len(duplicate) == 0)

    return summary


# ── CLI ──────────────────────────────────────────────────────────────────

def _build_cli_parser():
    import argparse
    p = argparse.ArgumentParser(
        description="Verify factory upload by querying list API."
    )
    p.add_argument("--order-id", required=True, help="Order identifier")
    p.add_argument("--form-id", default="MR07")
    p.add_argument("--expected-count", type=int, default=None)
    p.add_argument("--release-batch-id", default=None)
    p.add_argument("--dry-run", action="store_true")
    return p


def main():
    args = _build_cli_parser().parse_args()

    result = verify_factory_upload(
        order_id=args.order_id,
        form_id=args.form_id,
        expected_count=args.expected_count,
        release_batch_id=args.release_batch_id,
        dry_run=args.dry_run,
    )

    d = result.to_dict()
    # Pretty-print key fields
    for k in ["login_ok", "expected_count", "api_total",
              "pages_fetched", "total_match", "all_boxes_found",
              "missing_box_count", "duplicate_box_count", "extra_box_count"]:
        val = d.get(k)
        print(f"{k}: {val}")
    if d.get("error"):
        print(f"error: {d['error']}")
    if d.get("login_error"):
        print(f"login_error: {d['login_error']}")

    return 0 if result.all_boxes_found else 1


if __name__ == "__main__":
    raise SystemExit(main())
