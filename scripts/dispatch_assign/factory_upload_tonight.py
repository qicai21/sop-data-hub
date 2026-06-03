"""Upload tonight's 92 new boxes to factory system.

Only ships wagon_shipments with source_message_id='229'. Reuses
factory_upload.login_to_factory + upload_one_wagon. Overrides cargo_name
to use release_batches.cargo_product_name (印粉) per yaml requirement.

Skips history (lot01: 92 boxes / lot02: 90 boxes — already at factory per
user-confirmed verify on 2026-06-02 night).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

# expose project src
sys.path.insert(0, str(Path("/Users/qicai21/projects/repos/sop-data-hub/src")))

from sop_hub.sop.factory_upload import (
    _load_factory_config,
    login_to_factory,
    upload_one_wagon,
    WagonUploadPayload,
)

SOP_DB = Path("/Users/qicai21/projects/repos/sop-data-hub/data/sop_agent.db")
SOURCE_MSG_ID = "229"  # 今晚煤六46节四平铁那条


def build_tonight_payloads():
    config = _load_factory_config()
    fields = config.fields  # 来自 yaml.factory_upload_config.payload_fields

    con = sqlite3.connect(str(SOP_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT ws.car_no, ws.container_no, ws.container_numbers_json,
               rb.ship_name, rb.contract_no, rb.order_identifier,
               rb.cargo_product_name AS rb_product, rb.cargo_name AS rb_cargo
        FROM wagon_shipments ws
        LEFT JOIN release_batches rb ON rb.id = ws.batch_id
        WHERE ws.source_message_id = ?
        ORDER BY rb.batch_sequence, ws.car_no
    """, (SOURCE_MSG_ID,)).fetchall()
    con.close()

    payloads: list[WagonUploadPayload] = []
    for r in rows:
        # 取本行实际箱号列表 — container_numbers_json 优先
        boxes: list[str] = []
        if r["container_numbers_json"]:
            try:
                boxes = [b for b in json.loads(r["container_numbers_json"]) if b]
            except Exception:
                pass
        if not boxes and r["container_no"]:
            boxes = [b.strip() for b in r["container_no"].split("/") if b.strip()]

        # 货名优先 cargo_product_name(印粉)
        cargo = (r["rb_product"] or r["rb_cargo"] or "").strip()

        for box in boxes:
            payload: dict = {}
            for fd in fields:
                if fd.source == "fixed":
                    payload[fd.key] = fd.value
                elif fd.source == "95306_confirm":
                    if fd.key == "wagonNumber":
                        payload[fd.key] = r["car_no"]
                    elif fd.key == "boxNumber":
                        payload[fd.key] = box
                elif fd.source == "release_batch.cargo_name":
                    payload[fd.key] = cargo
                elif fd.source == "release_batch.ship_name":
                    payload[fd.key] = r["ship_name"] or ""
                elif fd.source == "release_batch.factory_contract_no":
                    payload[fd.key] = r["contract_no"] or ""
                elif fd.source == "release_batch.order_identifier":
                    payload[fd.key] = r["order_identifier"] or ""
            payloads.append(WagonUploadPayload(
                wagon_no=r["car_no"], container_no=box, payload=payload))
    return payloads, config


def main(apply: bool = False):
    payloads, config = build_tonight_payloads()
    print(f"=== tonight payloads: {len(payloads)} box(es) ===")
    # 抽样 5
    for w in payloads[:3]:
        print(f"  sample: car={w.wagon_no} box={w.container_no} payload={json.dumps(w.payload, ensure_ascii=False)}")
    print(f"  ...")
    for w in payloads[-2:]:
        print(f"  sample: car={w.wagon_no} box={w.container_no} payload={json.dumps(w.payload, ensure_ascii=False)}")

    # group by orderId
    from collections import Counter
    order_count = Counter(w.payload.get("orderId", "") for w in payloads)
    print(f"\n=== by orderId ===")
    for oid, n in order_count.items():
        print(f"  {oid}  {n} box(es)")

    if not apply:
        print("\n(dry run — pass --apply to POST)")
        return

    print("\n=== login ===")
    token, err = login_to_factory(config)
    if err:
        print(f"LOGIN FAILED: {err}"); return
    print(f"  token = {token[:20]}...")

    print("\n=== uploading ===")
    success = 0; fail = 0
    fail_details = []
    for i, w in enumerate(payloads, 1):
        r = upload_one_wagon(w, token, config)
        if r.success:
            success += 1
        else:
            fail += 1
            fail_details.append((w.wagon_no, w.container_no, r.http_status, r.error or r.response_body[:200]))
        if i % 10 == 0 or i == len(payloads):
            print(f"  progress {i}/{len(payloads)} success={success} fail={fail}")
        time.sleep(0.3)

    print(f"\n=== result ===")
    print(f"  total={len(payloads)}  success={success}  fail={fail}")
    if fail_details:
        print(f"  failures:")
        for d in fail_details[:10]:
            print(f"    car={d[0]} box={d[1]} http={d[2]} err={d[3]!r}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    main(apply=a.apply)
