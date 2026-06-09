"""蓝鳍 50 车(wx_367 / 2026-06-06 21:28 fleet)重发 excel + 重传工厂。

之前 chain task #257 跑通后用户在工厂系统手动删了上传记录,需要重新提交。
直接跑 3 步函数:
  1. generate_dispatch_event_excel(50 wagons)— 52 行(2 split 各拆 2 行)
  2. upload_dispatch_event_wagons(50 wagon_ids) → 100 box payload(split 也按 box 上传)
  3. send_to_wechat(target=GROUP013, file=excel)— yaml-driven report_delivery_flow

幂等说明:upload 走 factory_upload 的内部 (wagonNumber, boxNumber) 自查重,
工厂端真删过 → 这次重新 insert;send_to_wechat 走 wx-ui-bridge,每次都发新消息。
"""
from __future__ import annotations
import sqlite3, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

DB = REPO / "data" / "sop_agent.db"
PROJECT = "jilin_jingang_jinzhou"
SHIP = "蓝鳍"


def collect_wagon_ids() -> list[str]:
    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        "SELECT id FROM wagon_shipments "
        "WHERE source_message_id='wx_367' "
        "ORDER BY ticketed_at, car_no"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def main():
    wagon_ids = collect_wagon_ids()
    print(f"=== wagons: {len(wagon_ids)} ===")
    if len(wagon_ids) != 50:
        print(f"WARN: expected 50, got {len(wagon_ids)}")

    # Step 1: generate per-event excel
    from sop_hub.sop.departure_excel import generate_dispatch_event_excel
    excel_res = generate_dispatch_event_excel(
        wagon_ids=wagon_ids, db_path=str(DB),
    )
    print(f"Excel: {excel_res.output_path}")
    print(f"  rows: {excel_res.row_count}  wagons: {excel_res.wagon_count}")
    if excel_res.error:
        print(f"  ERR: {excel_res.error}")
        return 1

    # Step 2: factory upload
    from sop_hub.sop.factory_upload import upload_dispatch_event_wagons
    up_res = upload_dispatch_event_wagons(
        wagon_ids=wagon_ids, project_id=PROJECT, db_path=str(DB),
    )
    print(f"\nFactory upload:")
    print(f"  login_success: {up_res.login_success}")
    if up_res.login_error:
        print(f"  login_error: {up_res.login_error}")
    print(f"  total_wagons (payloads): {up_res.total_wagons}")
    print(f"  success: {up_res.success_count} / failure: {up_res.failure_count}")

    # Step 2b: verify upload (yaml acceptance + 同 [[chaoyang-ansteel-upload-verify-rule]] 铁律)
    from sop_hub.sop.factory_verify import verify_factory_upload
    print(f"\nVerify (per order_identifier):")
    verify_all_ok = True
    if hasattr(up_res, "order_identifier_groups") and up_res.order_identifier_groups:
        for oid, bids in up_res.order_identifier_groups.items():
            for bid in bids:
                v = verify_factory_upload(order_id=oid, release_batch_id=bid)
                tag = "OK" if (v.total_match and v.all_boxes_found) else "MISS"
                print(f"  [{tag}] order={oid} batch={bid[:12]} api_total={v.api_total} "
                      f"missing={len(v.missing_boxes)} extra={len(v.extra_boxes)}")
                if not v.all_boxes_found:
                    verify_all_ok = False
    print(f"  all_ok: {verify_all_ok}")

    # Step 3: send excel to GROUP013 via wx-ui-bridge
    from sop_hub.sop.send_excel import send_to_wechat
    from sop_hub.sop.workflow_task_executor import _resolve_send_target
    target = _resolve_send_target(PROJECT) or "[GROUP013]"
    msg = f"吉林金钢发运数据 {SHIP} 6/6 21:28 共 {excel_res.wagon_count}车(跨 lot03/05/06,2 个 split 车按 box 拆行)"
    send_res = send_to_wechat(
        target=target, message=msg, file_path=excel_res.output_path,
    )
    print(f"\nSend to {target}:")
    print(f"  success: {send_res.success}")
    if send_res.error:
        print(f"  error: {send_res.error}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
