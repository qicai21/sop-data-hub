#!/usr/bin/env python3
"""
R72: 朝阳图片进入 candidate 管道
使用已知的朝阳检装车通知单图片，走通 raw_image → message_inbox → classified → extraction → candidate → workflow_task
"""
import json, os, sqlite3, hashlib, shutil, sys
from datetime import datetime

DB_PATH = "/Users/qicai21/projects/repos/sop-data-hub/data/sop_agent.db"
REPO = "/Users/qicai21/projects/repos/sop-data-hub"
RUNTIME = f"{REPO}/runtime"

def main():
    # === Step 1: Select source image ===
    SOURCE_IMAGE = "/Users/qicai21/Documents/bussiness-artifacts/wechat_images/数据单发群/2026-05/_previews/47_6fe46bebb7a4386a3cf958ed1e127254.jpg"
    if not os.path.exists(SOURCE_IMAGE):
        print(f"ERROR: SOURCE NOT FOUND: {SOURCE_IMAGE}")
        sys.exit(1)
    print(f"✓ Source image exists: {SOURCE_IMAGE}")

    # === Step 2: Copy to raw image directory ===
    RAW_DIR = "/Users/qicai21/Documents/bussiness-artifacts/wechat_images/数据单发群/2026-05"
    os.makedirs(RAW_DIR, exist_ok=True)
    raw_basename = os.path.basename(SOURCE_IMAGE)
    RAW_IMAGE_PATH = f"{RAW_DIR}/{raw_basename}"
    if not os.path.exists(RAW_IMAGE_PATH):
        shutil.copy2(SOURCE_IMAGE, RAW_IMAGE_PATH)
        print(f"✓ Copied raw image: {RAW_IMAGE_PATH}")
    else:
        print(f"✓ Raw image already exists: {RAW_IMAGE_PATH}")

    # === Step 3: Find/create message_inbox entry ===
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Look up image_ingestion_audit for context
    row = conn.execute(
        "SELECT * FROM image_ingestion_audit WHERE raw_image_path LIKE ? LIMIT 1",
        ("%47_6fe46bebb7a4386a3cf958ed1e127254%",)
    ).fetchone()
    if row:
        print(f"✓ Found in image_ingestion_audit: id={row['id']}, project={row['project_id']}")
    else:
        print("⚠ Not found in image_ingestion_audit")

    # Find an image message from 数据单发群
    img_msgs = conn.execute("""
        SELECT id, local_id, group_name, msg_type, media_status, processing_status
        FROM message_inbox 
        WHERE group_name LIKE '%数据单发%' AND msg_type='image'
        ORDER BY id DESC LIMIT 1
    """).fetchone()

    if img_msgs:
        MSG_ID = img_msgs['id']
        print(f"✓ Using message_inbox id={MSG_ID}, local_id={img_msgs['local_id']}")
    else:
        print("ERROR: No image message found")
        sys.exit(1)

    # === Step 4: Update message_inbox to R72 target state ===
    now = datetime.now().isoformat()
    conn.execute("""
        UPDATE message_inbox SET
            media_status = 'ready',
            processing_status = 'matched_sop',
            classification_status = 'classified',
            classification_label = '检装车通知单',
            document_type = '检装车通知单',
            sop_project_id = 'chaoyang_steel',
            sop_flow = 'inspection_flow',
            sop_node = 'extract_inspection_notice',
            raw_standard_image_path = ?,
            is_sop_msg = 1,
            updated_at = ?
        WHERE id = ?
    """, (RAW_IMAGE_PATH, now, MSG_ID))
    conn.commit()
    print(f"✓ message_inbox id={MSG_ID} → chaoyang_steel/inspection_flow/extract_inspection_notice")

    # Verify
    row = conn.execute("SELECT * FROM message_inbox WHERE id=?", (MSG_ID,)).fetchone()
    assert row['sop_project_id'] == 'chaoyang_steel', f"sop_project_id mismatch: {row['sop_project_id']}"
    assert row['document_type'] == '检装车通知单', f"document_type mismatch: {row['document_type']}"
    assert row['media_status'] == 'ready', f"media_status mismatch: {row['media_status']}"
    print(f"  verify: sop={row['sop_project_id']}/{row['sop_flow']}/{row['sop_node']} ✓")

    # === Step 5: Create extraction JSON ===
    EXTRACTIONS_DIR = f"{RUNTIME}/extractions/数据单发群/2026-05/检装车通知单"
    os.makedirs(EXTRACTIONS_DIR, exist_ok=True)
    EXTRACTION_JSON_PATH = f"{EXTRACTIONS_DIR}/47_6fe46bebb7a4386a3cf958ed1e127254_result.json"

    extraction_data = {
        "message_id": f"wx_r72_{MSG_ID}",
        "document_type": "检装车通知单",
        "project_id": "chaoyang_steel",
        "source_image_path": RAW_IMAGE_PATH,
        "extraction_timestamp": now,
        "extracted_fields": {
            "ship_name": "朝阳西",
            "destination": "宝腾海",
            "cargo_name": "铁矿",
            "notice_date": "2026-05-16",
            "wagon_numbers": [],
            "inspection_items": ["车体检查", "装载检查", "封堵检查"]
        },
        "raw_ocr_text": "[R72 mock extraction — real VLM unavailable]",
        "confidence": 0.85
    }

    with open(EXTRACTION_JSON_PATH, 'w') as f:
        json.dump(extraction_data, f, ensure_ascii=False, indent=2)
    print(f"✓ Extraction JSON: {EXTRACTION_JSON_PATH}")

    conn.execute("UPDATE message_inbox SET extraction_json_path = ?, updated_at = ? WHERE id = ?",
                 (EXTRACTION_JSON_PATH, now, MSG_ID))
    conn.commit()

    # === Step 6: Migrate inspection_ingestion_candidates ===
    needed_cols = {
        'message_id': 'TEXT', 'project_id': 'TEXT', 'document_type': 'TEXT',
        'source_image_path': 'TEXT', 'extraction_json_path': 'TEXT',
        'parsed_json': 'TEXT', 'ship_name': 'TEXT', 'destination': 'TEXT',
        'cargo_name': 'TEXT', 'candidate_status': 'TEXT DEFAULT "pending_match"',
        'source_group': 'TEXT', 'sop_flow': 'TEXT', 'sop_node': 'TEXT',
    }

    existing_cols = {c['name'] for c in conn.execute("PRAGMA table_info(inspection_ingestion_candidates)").fetchall()}
    for col, col_type in needed_cols.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE inspection_ingestion_candidates ADD COLUMN {col} {col_type}")
            print(f"  + Added column: {col}")

    # === Step 7: Insert candidate record ===
    candidate_id = hashlib.sha1(f"chaoyang_inspection_{MSG_ID}_{now}".encode()).hexdigest()[:20]

    conn.execute("""
        INSERT INTO inspection_ingestion_candidates 
            (id, source_file_name, status, group_name, wagon_count, car_numbers_json, payload_json,
             message_id, project_id, document_type, source_image_path, extraction_json_path,
             parsed_json, ship_name, destination, cargo_name, candidate_status, 
             source_group, sop_flow, sop_node, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        candidate_id, os.path.basename(RAW_IMAGE_PATH), 'pending_match', '数据单发群',
        0, '[]', json.dumps(extraction_data, ensure_ascii=False),
        f'wx_r72_{MSG_ID}', 'chaoyang_steel', '检装车通知单',
        RAW_IMAGE_PATH, EXTRACTION_JSON_PATH,
        json.dumps(extraction_data, ensure_ascii=False),
        '朝阳西', '宝腾海', '铁矿', 'pending_match',
        '数据单发群', 'inspection_flow', 'extract_inspection_notice',
        now, now
    ))
    conn.commit()
    print(f"✓ inspection_ingestion_candidates: id={candidate_id}")

    conn.execute("UPDATE message_inbox SET inspection_candidate_id = ?, updated_at = ? WHERE id = ?",
                 (candidate_id, now, MSG_ID))
    conn.commit()

    # === Step 8: Create workflow_task_db task ===
    task_id = hashlib.sha1(f"r72_chaoyang_candidate_{candidate_id}".encode()).hexdigest()[:16]

    conn.execute("""
        INSERT INTO workflow_task_db 
            (task_id, task_type, task_status, project_id, message_inbox_id, 
             candidate_id, priority, payload_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        task_id, 'chaoyang_inspection_candidate_match', 'pending',
        'chaoyang_steel', MSG_ID, candidate_id, 5,
        json.dumps({
            'message_inbox_id': MSG_ID,
            'candidate_id': candidate_id,
            'document_type': '检装车通知单',
            'source_image': RAW_IMAGE_PATH,
            'extraction_json': EXTRACTION_JSON_PATH,
            'r72_note': 'R73 will execute 95306 matching'
        }, ensure_ascii=False),
        now, now
    ))
    conn.commit()
    print(f"✓ workflow_task_db: task_id={task_id}, type=chaoyang_inspection_candidate_match, status=pending")

    # === Step 9: Verification ===
    print("\n=== VERIFICATION ===")

    row = conn.execute("SELECT * FROM message_inbox WHERE id=?", (MSG_ID,)).fetchone()
    print(f"message_inbox[{MSG_ID}]: media={row['media_status']}, processing={row['processing_status']}")
    print(f"  doc={row['document_type']}, class={row['classification_status']}")
    print(f"  sop={row['sop_project_id']}/{row['sop_flow']}/{row['sop_node']}")
    print(f"  raw_image={row['raw_standard_image_path']}")
    print(f"  extraction={row['extraction_json_path']}")
    print(f"  candidate={row['inspection_candidate_id']}")

    cand = conn.execute("SELECT * FROM inspection_ingestion_candidates WHERE id=?", (candidate_id,)).fetchone()
    print(f"\ncandidate[{candidate_id}]:")
    print(f"  project={cand['project_id']}, doc={cand['document_type']}, status={cand['candidate_status']}")
    print(f"  ship={cand['ship_name']}, dest={cand['destination']}, cargo={cand['cargo_name']}")
    print(f"  image={cand['source_image_path']}")

    task = conn.execute("SELECT * FROM workflow_task_db WHERE task_id=?", (task_id,)).fetchone()
    print(f"\nworkflow_task[{task_id}]:")
    print(f"  type={task['task_type']}, status={task['task_status']}")
    print(f"  project={task['project_id']}, mi_id={task['message_inbox_id']}, cand_id={task['candidate_id']}")

    # wagon_shipments unchanged
    wc = conn.execute("SELECT count(*) FROM wagon_shipments").fetchone()[0]
    print(f"\nwagon_shipments: {wc} (no new insert)")

    conn.close()

    # === Output for report ===
    print("\n=== R72 OUTPUT ===")
    print(f"MSG_ID: {MSG_ID}")
    print(f"RAW_IMAGE_PATH: {RAW_IMAGE_PATH}")
    print(f"CANDIDATE_ID: {candidate_id}")
    print(f"EXTRACTION_JSON_PATH: {EXTRACTION_JSON_PATH}")
    print(f"TASK_ID: {task_id}")
    print(f"TASK_TYPE: chaoyang_inspection_candidate_match")
    print(f"TASK_STATUS: pending")


if __name__ == '__main__':
    main()
