#!/usr/bin/env python3
"""
R74 观察采样脚本 — 每 10 分钟采集系统状态并追加到观测日志
"""
import json, sqlite3, time, os
from datetime import datetime
from urllib.request import urlopen

DB = "/Users/qicai21/projects/repos/sop-data-hub/data/sop_agent.db"
LOG = "/Users/qicai21/projects/repos/sop-data-hub/runtime/r74_observation_log.jsonl"
INTERVAL = 600  # 10 min
DURATION = 3600  # 60 min total

def sample():
    ts = datetime.now().isoformat()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # live_service state
    with open("/Users/qicai21/projects/repos/sop-data-hub/runtime/live_service_state.json") as f:
        lv = json.load(f)

    # cursor
    with open("/Users/qicai21/projects/repos/sop-data-hub/runtime/cursors/live_service_cursor.json") as f:
        cs = json.load(f)

    # DB counts
    mi_total = conn.execute("SELECT count(*) FROM message_inbox").fetchone()[0]
    mi_waiting = conn.execute("SELECT count(*) FROM message_inbox WHERE media_status='waiting_media'").fetchone()[0]
    mi_ignored = conn.execute("SELECT count(*) FROM message_inbox WHERE processing_status='ignored'").fetchone()[0]
    mi_succeeded = conn.execute("SELECT count(*) FROM message_inbox WHERE processing_status='task_succeeded'").fetchone()[0]

    # dashboard health
    try:
        resp = urlopen("http://localhost:8787/api/status", timeout=5)
        dash_status = json.loads(resp.read())
    except:
        dash_status = {"error": "unreachable"}

    # storage
    import subprocess
    sp = subprocess.run(
        ["/opt/homebrew/bin/python3.14", "scripts/verify_storage_paths.py"],
        cwd="/Users/qicai21/projects/repos/sop-data-hub",
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONPATH": "src"}
    )
    violations = "FAIL" not in sp.stdout.split("\n")[-1] if sp.stdout else "unknown"

    sample = {
        "ts": ts,
        "live_service_alive": lv.get("alive"),
        "last_processed_msg": lv.get("last_processed_message_id"),
        "cursor_sources": len(cs.get("sources", {})),
        "cursor_updated": cs.get("updated_at"),
        "mi_total": mi_total,
        "mi_waiting": mi_waiting,
        "mi_ignored": mi_ignored,
        "mi_succeeded": mi_succeeded,
        "dashboard_alive": dash_status.get("alive", False),
        "storage_violations": 0 if violations else -1,
    }

    conn.close()
    return sample

def main():
    print(f"R74 observation starting — {DURATION//60} min, sampling every {INTERVAL//60} min")
    print(f"Log: {LOG}")
    os.makedirs(os.path.dirname(LOG), exist_ok=True)

    start = time.time()
    samples = []
    while time.time() - start < DURATION:
        s = sample()
        samples.append(s)
        with open(LOG, "a") as f:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
        elapsed = int(time.time() - start)
        print(f"[{s['ts']}] +{elapsed//60}m | alive={s['live_service_alive']} | mi={s['mi_total']} | dash={s['dashboard_alive']} | storage_ok={s['storage_violations']==0}")
        time.sleep(INTERVAL)

    print(f"\nObservation complete. {len(samples)} samples.")
    # Final summary
    alive_ok = all(s['live_service_alive'] for s in samples)
    dash_ok = all(s['dashboard_alive'] for s in samples)
    storage_ok = all(s['storage_violations'] == 0 for s in samples)
    mi_grew = samples[-1]['mi_total'] >= samples[0]['mi_total']
    print(f"All alive: {alive_ok}, dashboard stable: {dash_ok}, storage clean: {storage_ok}, mi stable: {mi_grew}")

if __name__ == "__main__":
    main()
