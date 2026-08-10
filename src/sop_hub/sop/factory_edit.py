"""吉林金钢收货人门户运输记录逐条修改。

门户 edit 接口要求回传 list 接口返回的完整记录。本模块只允许按
``门户 id + 箱号 + 车号`` 精确修改，默认 dry-run，并在 apply 后同时
反查原订单和目标订单，避免把删除、重复或错票误报为成功。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from typing import Any

import requests

from sop_hub.sop.factory_verify import (
    FACTORY_ENDPOINT,
    LIST_PATH,
    _login,
    build_box_wagon_key,
)

EDIT_PATH = "/sales/transportOrder/edit"
DEFAULT_FORM_ID = "MR07"
EDITABLE_FIELDS = frozenset({"orderId", "contractNumber", "goodName", "boatName"})


@dataclass
class FactoryEditResult:
    old_order_id: str
    wagon_number: str
    box_number: str
    updates: dict[str, Any]
    dry_run: bool = True
    login_ok: bool = False
    portal_id: int | None = None
    new_portal_id: int | None = None
    source_match_count: int = 0
    old_order_match_count_after: int | None = None
    new_order_match_count_after: int | None = None
    applied: bool = False
    verified: bool = False
    http_status: int | None = None
    error: str = ""
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Cookie": f"Admin-Token={token}",
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json",
    }


def fetch_full_order_rows(
    order_id: str,
    token: str,
    *,
    form_id: str = DEFAULT_FORM_ID,
    page_size: int = 100,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """Return complete portal rows without dropping fields required by edit."""
    rows: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        response = requests.get(
            f"{FACTORY_ENDPOINT}{LIST_PATH}",
            params={
                "pageNum": page,
                "pageSize": page_size,
                "orderId": order_id,
                "formId": form_id,
            },
            headers=_headers(token),
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        chunk = body.get("rows", [])
        rows.extend(dict(row) for row in chunk)
        total = int(body.get("total") or 0)
        if not chunk or len(rows) >= total:
            break
    return rows


def _matching_rows(
    rows: list[dict[str, Any]], wagon_number: str, box_number: str
) -> list[dict[str, Any]]:
    expected = build_box_wagon_key(box_number, wagon_number)
    return [
        row
        for row in rows
        if build_box_wagon_key(row.get("boxNumber", ""), row.get("wagonNumber", ""))
        == expected
    ]


def edit_factory_record(
    *,
    old_order_id: str,
    wagon_number: str,
    box_number: str,
    updates: dict[str, Any],
    expected_portal_id: int | None = None,
    form_id: str = DEFAULT_FORM_ID,
    dry_run: bool = True,
    allow_appointed: bool = False,
) -> FactoryEditResult:
    """Edit one exact portal record and verify its post-state.

    ``updates`` may contain only ``EDITABLE_FIELDS``. Records whose
    ``isAppointment`` is not 0 are blocked unless explicitly overridden.
    """
    result = FactoryEditResult(
        old_order_id=old_order_id,
        wagon_number=wagon_number,
        box_number=box_number,
        updates=dict(updates),
        dry_run=dry_run,
    )
    invalid = sorted(set(updates) - EDITABLE_FIELDS)
    if invalid:
        result.error = f"unsupported update fields: {invalid}"
        return result
    if not updates:
        result.error = "no updates"
        return result

    token, error = _login()
    if error or not token:
        result.error = f"login failed: {error}"
        return result
    result.login_ok = True

    try:
        source_rows = fetch_full_order_rows(old_order_id, token, form_id=form_id)
    except requests.RequestException as exc:
        result.error = f"source query failed: {exc}"
        return result
    matches = _matching_rows(source_rows, wagon_number, box_number)
    result.source_match_count = len(matches)
    if len(matches) != 1:
        result.error = f"source record is not unique: {len(matches)} match(es)"
        return result

    before = matches[0]
    result.before = dict(before)
    result.portal_id = before.get("id")
    if expected_portal_id is not None and result.portal_id != expected_portal_id:
        result.error = (
            f"portal id changed: expected {expected_portal_id}, got {result.portal_id}"
        )
        return result
    if not allow_appointed and before.get("isAppointment") not in (0, "0", None):
        result.error = f"record is appointed and cannot be edited: {before.get('isAppointment')}"
        return result

    payload = dict(before)
    payload.update(updates)
    if dry_run:
        result.after = payload
        return result

    try:
        response = requests.post(
            f"{FACTORY_ENDPOINT}{EDIT_PATH}",
            json=payload,
            headers=_headers(token),
            timeout=30,
        )
        result.http_status = response.status_code
        response.raise_for_status()
    except requests.RequestException as exc:
        result.error = f"edit request failed: {exc}"
        return result
    result.applied = True

    new_order_id = str(payload.get("orderId") or old_order_id)
    try:
        old_after = _matching_rows(
            fetch_full_order_rows(old_order_id, token, form_id=form_id),
            wagon_number,
            box_number,
        )
        if new_order_id == old_order_id:
            new_after = old_after
        else:
            new_after = _matching_rows(
                fetch_full_order_rows(new_order_id, token, form_id=form_id),
                wagon_number,
                box_number,
            )
    except requests.RequestException as exc:
        result.error = f"post-edit verification failed: {exc}"
        return result

    result.old_order_match_count_after = len(old_after)
    result.new_order_match_count_after = len(new_after)
    if len(new_after) == 1:
        result.after = dict(new_after[0])
        result.new_portal_id = new_after[0].get("id")
    fields_match = bool(new_after) and all(
        new_after[0].get(key) == value for key, value in updates.items()
    )
    source_is_correct = len(old_after) == (1 if new_order_id == old_order_id else 0)
    result.verified = source_is_correct and len(new_after) == 1 and fields_match
    if not result.verified:
        result.error = "post-edit verification mismatch"
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="吉林收货人系统逐条修改")
    parser.add_argument("--old-order-id", required=True)
    parser.add_argument("--wagon-number", required=True)
    parser.add_argument("--box-number", required=True)
    parser.add_argument("--new-order-id")
    parser.add_argument("--contract-number")
    parser.add_argument("--expected-portal-id", type=int)
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    updates = {}
    if args.new_order_id:
        updates["orderId"] = args.new_order_id
    if args.contract_number:
        updates["contractNumber"] = args.contract_number
    result = edit_factory_record(
        old_order_id=args.old_order_id,
        wagon_number=args.wagon_number,
        box_number=args.box_number,
        updates=updates,
        expected_portal_id=args.expected_portal_id,
        dry_run=not args.apply,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    return 0 if (result.verified if args.apply else not result.error) else 1


if __name__ == "__main__":
    raise SystemExit(main())
