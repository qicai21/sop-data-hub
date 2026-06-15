"""一次性:合并 wx_17 鞍子河 36 车 2 个 candidate → 1 个 + 修 OCR 错读 + 新建 task。

根因(明日修):qwen3-vl 把空 cargo_info_raw 误读"鞍子河" + 7 位车号丢成 6 位;
#130 拆分逻辑同船名多 anchor 错切。今天只跑数据。
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path("/Users/qicai21/projects/repos/sop-data-hub/data/sop_agent.db")
KEEP_CID = "c1cd5876d4e979b9cfd732f92dfaf03fcf6f2f05"  # 28 车,合并目标
DROP_CID = "31525879b819f217d4204629da9395e01a6ce188"  # 8 车头段,合并掉
BATCH_ID = "a2c7bbdfd7fd5f5ce9cfab8bf40a13ce6793f57a"  # 鞍子河 loading
INBOX_ID = 100000250  # wx_17 中唐特钢发运群 2026-06-08 20:34

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 1. 读两份 payload
keep = dict(conn.execute(
    "SELECT * FROM inspection_ingestion_candidates WHERE id=?", (KEEP_CID,)
).fetchone())
drop = dict(conn.execute(
    "SELECT * FROM inspection_ingestion_candidates WHERE id=?", (DROP_CID,)
).fetchone())
keep_payload = json.loads(keep["payload_json"])
drop_payload = json.loads(drop["payload_json"])

# 2. 合并 rows:31525879 (8 行表头段) 在前 + c1cd5876 (28 行) 在后
merged_rows = list(drop_payload["rows"]) + list(keep_payload["rows"])
assert len(merged_rows) == 36, f"expect 36 rows, got {len(merged_rows)}"

# 3. 修 OCR 错读:合并后 row[28] car_no = '493255' → '4933255'
ocr_fix_idx = 28
assert merged_rows[ocr_fix_idx]["car_no"] == "493255", \
    f"row[28] expected 493255, got {merged_rows[ocr_fix_idx]['car_no']}"
merged_rows[ocr_fix_idx]["car_no"] = "4933255"
print(f"OCR fix: row[{ocr_fix_idx}] 493255 -> 4933255")

# 4. 构合并 payload(继承 31525879 的表头 meta,因为它含汐子/鞍子河/36节)
merged_payload = dict(drop_payload)  # 拿表头 meta
merged_payload["rows"] = merged_rows
merged_payload["rows_count"] = 36
merged_payload["last_car_no"] = merged_rows[-1]["car_no"]
merged_payload["car_nos"] = [r["car_no"] for r in merged_rows if r.get("car_no")]
merged_payload["footer"] = {"zhuangche_jieshu": 36, "paiche_jieshu": 0}
# 标记本次合并来源
merged_payload["_manual_merge_2026_06_08"] = {
    "merged_from": [DROP_CID, KEEP_CID],
    "ocr_fix": {"row_index": ocr_fix_idx, "from": "493255", "to": "4933255"},
    "reason": "qwen3-vl 误把 row[8] 空字段读成'鞍子河'触发 #130 错切",
}

car_nos_36 = [r["car_no"] for r in merged_rows]
print(f"合并后 36 车: {car_nos_36}")
print(f"unique: {len(set(car_nos_36))}")
assert len(set(car_nos_36)) == 36, "duplicates after merge"

# 5. UPDATE c1cd5876 → 36 车 + candidate
now = datetime.now().astimezone().isoformat(timespec="seconds")
conn.execute(
    "UPDATE inspection_ingestion_candidates SET "
    "  wagon_count=?, car_numbers_json=?, payload_json=?, "
    "  candidate_status=?, release_batch_id=?, reason=?, updated_at=? "
    "WHERE id=?",
    (
        36,
        json.dumps(car_nos_36, ensure_ascii=False),
        json.dumps(merged_payload, ensure_ascii=False),
        "candidate",
        BATCH_ID,
        f"merged 36 cars from [{DROP_CID[:8]}+{KEEP_CID[:8]}]; "
        f"OCR fix 493255->4933255 at row[{ocr_fix_idx}]",
        now,
        KEEP_CID,
    ),
)

# 6. UPDATE 31525879 → superseded
conn.execute(
    "UPDATE inspection_ingestion_candidates SET "
    "  candidate_status=?, reason=?, updated_at=? WHERE id=?",
    ("superseded", f"merged into {KEEP_CID}", now, DROP_CID),
)

# 7. 新建 workflow_task,daemon 5s 内拾取
task_input = {
    "message_inbox_id": INBOX_ID,
    "message_id": "wx_17",
    "group_name": "中唐特钢发运群",
    "received_datetime": "2026-06-08 20:34:10",
    "sop_project_id": "zhongtang_special_steel",
    "sop_flow": "inspection_notice_flow",
    "sop_node": "create_inspection_candidate",
    "media_status": "ready",
    "manual_retry": True,
    "manual_retry_reason": "wx_17 鞍子河 36 车 candidate 合并 + OCR fix 后重跑",
}
cur = conn.execute(
    "INSERT INTO workflow_task_db "
    "  (message_inbox_id, message_id, project_id, flow_name, node_name, "
    "   task_type, task_status, input_json, retry_count, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (
        INBOX_ID, "wx_17", "zhongtang_special_steel",
        "inspection_notice_flow", "create_inspection_candidate",
        "zhongtang_inspection_chain", "pending",
        json.dumps(task_input, ensure_ascii=False), 0, now, now,
    ),
)
new_task_id = cur.lastrowid
conn.commit()
conn.close()

print(f"\n✓ 合并完成:c1cd5876 36 车 candidate;31525879 标 superseded")
print(f"✓ 新建 task id={new_task_id} pending,等 text_watch_daemon (5s) 拾取")
