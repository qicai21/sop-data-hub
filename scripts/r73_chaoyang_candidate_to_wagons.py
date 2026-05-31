#!/usr/bin/env python3
"""
R73: 朝阳候选到入库
将 R72 创建的 candidate 关联到 release_batch，查询 95306 匹配数据，
写入 wagon_shipments，更新 candidate 和 workflow_task 状态。
"""
import json, os, sqlite3, sys
from datetime import datetime
from pathlib import Path

DB_PATH = "/Users/qicai21/projects/repos/sop-data-hub/data/sop_agent.db"
RAIL_DB = "/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3"
CANDIDATE_ID = "c7e365fcef68f3dea7df"
WORKFLOW_TASK_ID = 13
MSG_INBOX_ID = 59

def main():
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # === Step 1: Verify candidate exists ===
    cand = conn.execute("SELECT * FROM inspection_ingestion_candidates WHERE id=?", (CANDIDATE_ID,)).fetchone()
    if not cand:
        print(f"ERROR: candidate {CANDIDATE_ID} not found")
        sys.exit(1)
    print(f"✓ Candidate: id={cand['id']}, ship={cand['ship_name']}, dest={cand['destination']}, cargo={cand['cargo_name']}")

    # === Step 2: Find matching release_batch ===
    # Candidate: ship_name=朝阳西, destination=宝腾海, cargo=铁矿
    # (Note: mock extraction swapped ship/destination; correct is ship=宝腾海, destination=朝阳西)
    batch = conn.execute("""
        SELECT id, project, ship_name, destination_station, cargo_name, 
               batch_count, actual_wagon_count, dispatch_status, batch_key
        FROM release_batches 
        WHERE ship_name='宝腾海' AND destination_station='朝阳西' AND cargo_name='铁矿'
        AND dispatch_status='completed'
    """).fetchone()

    if not batch:
        # Try broader search
        batch = conn.execute("""
            SELECT id, project, ship_name, destination_station, cargo_name,
                   batch_count, actual_wagon_count, dispatch_status, batch_key
            FROM release_batches 
            WHERE destination_station='朝阳西' AND cargo_name='铁矿'
            ORDER BY actual_wagon_count DESC LIMIT 1
        """).fetchone()

    if not batch:
        print("ERROR: no matching release_batch found for Chaoyang")
        sys.exit(1)

    RELEASE_BATCH_ID = batch['id']
    print(f"✓ Release batch: id={RELEASE_BATCH_ID}")
    print(f"  project={batch['project']}, ship={batch['ship_name']}, dest={batch['destination_station']}")
    print(f"  cargo={batch['cargo_name']}, wagons={batch['actual_wagon_count']}, status={batch['dispatch_status']}")

    # === Step 3: Update candidate with release_batch link ===
    conn.execute("""
        UPDATE inspection_ingestion_candidates SET
            release_batch_id = ?,
            candidate_status = 'matched',
            updated_at = ?
        WHERE id = ?
    """, (RELEASE_BATCH_ID, now, CANDIDATE_ID))
    print(f"✓ Candidate linked to release_batch, status → matched")

    # === Step 4: Query rail DB for wagon data ===
    if os.path.exists(RAIL_DB):
        rail = sqlite3.connect(RAIL_DB)
        rail.row_factory = sqlite3.Row

        # Find shipments for Chaoyang route
        # The reconciler matches by destination station and time window
        # Query wagons for Chaoyang: destination 朝阳西, cargo 铁矿, around batch notice date
        # Release batch notice_date is ~2026-05-11, use ±60 day window
        wagons = rail.execute("""
            SELECT DISTINCT car_no, ydid, ticketed_at, origin_name, destination_name,
                   cargo_name, marked_weight
            FROM shipments 
            WHERE destination_name = '朝阳西' 
              AND cargo_name LIKE '%铁矿%'
              AND ticketed_at >= '2026-03-01'
              AND ticketed_at <= '2026-06-01'
            ORDER BY ticketed_at 
            LIMIT 200
        """).fetchall()

        print(f"  Rail DB: {len(wagons)} wagons found (朝阳西/铁矿, 2026-03~06)")

        if wagons:
            car_numbers = [w['car_no'] for w in wagons]
            wagon_count = len(car_numbers)

            # Update candidate with wagon data
            conn.execute("""
                UPDATE inspection_ingestion_candidates SET
                    wagon_count = ?,
                    car_numbers_json = ?,
                    updated_at = ?
                WHERE id = ?
            """, (wagon_count, json.dumps(car_numbers, ensure_ascii=False), now, CANDIDATE_ID))
            print(f"✓ Candidate updated: wagon_count={wagon_count}")

            # === Step 5: Insert into wagon_shipments ===
            inserted = 0
            skipped = 0
            for w in wagons:
                car_no = w['car_no']
                # Check if already exists (by car_no — unique per wagon)
                existing = conn.execute(
                    "SELECT id FROM wagon_shipments WHERE car_no=?",
                    (car_no,)
                ).fetchone()
                if existing:
                    skipped += 1
                    continue

                ws_id = f"ws_r73_{car_no}"
                conn.execute("""
                    INSERT INTO wagon_shipments (id, car_no, batch_id, cargo_name, 
                        origin_name, destination_name, ticketed_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (ws_id, car_no, RELEASE_BATCH_ID, 
                      w['cargo_name'] or '铁矿',
                      w['origin_name'] or '', 
                      w['destination_name'] or '朝阳西',
                      w['ticketed_at'] or now,
                      now))
                inserted += 1

            conn.commit()
            print(f"✓ wagon_shipments: {inserted} inserted, {skipped} skipped")

            # === Step 6: Insert shipment_release_batch_matches ===
            batch_match_inserted = 0
            match_skipped = 0
            for w in wagons:
                car_no = w['car_no']
                # Find the wagon_shipment id
                ws = conn.execute(
                    "SELECT id FROM wagon_shipments WHERE car_no=? ORDER BY created_at DESC LIMIT 1",
                    (car_no,)
                ).fetchone()
                if not ws:
                    continue

                # Check if match already exists
                exist_match = conn.execute(
                    "SELECT id FROM shipment_release_batch_matches WHERE release_batch_id=? AND wagon_no=?",
                    (RELEASE_BATCH_ID, car_no)
                ).fetchone()
                if exist_match:
                    match_skipped += 1
                    continue

                match_id = f"m_r73_{car_no}"
                conn.execute("""
                    INSERT INTO shipment_release_batch_matches 
                        (id, release_batch_id, wagon_shipment_id, ydid, waybill_no, 
                         wagon_no, match_source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (match_id, RELEASE_BATCH_ID, ws['id'],
                      w['ydid'] or '', w['ydid'] or '',
                      car_no, 'chaoyang_inspection_candidate_r73', now))
                batch_match_inserted += 1

            conn.commit()
            print(f"✓ shipment_release_batch_matches: {batch_match_inserted} inserted, {match_skipped} skipped")

        rail.close()
    else:
        print(f"⚠ Rail DB not found at {RAIL_DB}, using release_batch.actual_wagon_count")
        conn.execute("""
            UPDATE inspection_ingestion_candidates SET
                wagon_count = (SELECT actual_wagon_count FROM release_batches WHERE id=?),
                updated_at = ?
            WHERE id = ?
        """, (RELEASE_BATCH_ID, now, CANDIDATE_ID))

    # === Step 7: Update workflow_task_db ===
    total_wagons = conn.execute("SELECT count(*) FROM wagon_shipments").fetchone()[0]
    cand_updated = conn.execute("SELECT * FROM inspection_ingestion_candidates WHERE id=?", (CANDIDATE_ID,)).fetchone()

    conn.execute("""
        UPDATE workflow_task_db SET
            task_status = 'succeeded',
            output_json = ?,
            last_run_at = ?,
            updated_at = ?
        WHERE id = ?
    """, (
        json.dumps({
            'release_batch_id': RELEASE_BATCH_ID,
            'candidate_id': CANDIDATE_ID,
            'wagon_count': cand_updated['wagon_count'],
            'candidate_status': cand_updated['candidate_status'],
            'total_wagon_shipments': total_wagons,
        }, ensure_ascii=False),
        now, now, WORKFLOW_TASK_ID
    ))
    conn.commit()
    print(f"✓ workflow_task id={WORKFLOW_TASK_ID} → succeeded")

    # === Step 8: Update message_inbox ===
    conn.execute("""
        UPDATE message_inbox SET
            processing_status = 'task_succeeded',
            release_batch_id = ?,
            updated_at = ?
        WHERE id = ?
    """, (RELEASE_BATCH_ID, now, MSG_INBOX_ID))
    conn.commit()
    print(f"✓ message_inbox id={MSG_INBOX_ID} → task_succeeded")

    # === Verification ===
    print("\n=== VERIFICATION ===")
    
    mi = conn.execute("SELECT * FROM message_inbox WHERE id=?", (MSG_INBOX_ID,)).fetchone()
    print(f"message_inbox[{MSG_INBOX_ID}]: processing={mi['processing_status']}, release_batch={mi['release_batch_id']}")

    cand = conn.execute("SELECT * FROM inspection_ingestion_candidates WHERE id=?", (CANDIDATE_ID,)).fetchone()
    print(f"candidate[{CANDIDATE_ID}]: status={cand['candidate_status']}, wagons={cand['wagon_count']}, batch={cand['release_batch_id']}")

    task = conn.execute("SELECT * FROM workflow_task_db WHERE id=?", (WORKFLOW_TASK_ID,)).fetchone()
    print(f"workflow_task[{WORKFLOW_TASK_ID}]: status={task['task_status']}")

    wc = conn.execute("SELECT count(*) FROM wagon_shipments").fetchone()[0]
    mc = conn.execute("SELECT count(*) FROM shipment_release_batch_matches").fetchone()[0]
    print(f"wagon_shipments: {wc}, shipment_release_batch_matches: {mc}")

    conn.close()

    # === Output for report ===
    print("\n=== R73 OUTPUT ===")
    print(f"RELEASE_BATCH_ID: {RELEASE_BATCH_ID}")
    print(f"CANDIDATE_ID: {CANDIDATE_ID}")
    print(f"CANDIDATE_STATUS: {cand['candidate_status']}")
    print(f"WAGON_COUNT: {cand['wagon_count']}")
    print(f"WORKFLOW_TASK_STATUS: {task['task_status']}")


if __name__ == '__main__':
    main()
