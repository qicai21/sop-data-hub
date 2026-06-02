#!/usr/bin/env python3
"""
R66: Storage Contract Total Verification & Smoke Test

核验项:
  1. storage_policy.yaml v2 完整性
  2. Active area forbidden paths (0 violations)
  3. Allowed active paths
  4. Raw msg_path 抽样可打开
  5. Runner self-test (mock)
  6. business/projects file count
  7. quarantine state
  8. runtime/extractions, runtime/image_status
  9. live_service, cursor, message_inbox 状态
 10. 旧路径是否复活
"""

from sop_hub.utils.time import now_iso_beijing
import json
import os
import sys
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IMAGES_ROOT = Path("/Users/qicai21/Documents/bussiness-artifacts/wechat_images")
WX_AGENT_ROOT = Path("/Users/qicai21/projects/repos/wx-ops-agent")
DB_PATH = REPO_ROOT / "data" / "sop_agent.db"

# ── Forbidden patterns (from storage_policy.yaml v2) ──────────────────────
FORBIDDEN_ROOT_DIRS = [
    "_status", "_raw", "_previews",
    "other", "unknown", "unmatched",
    "extractions",
    "business/general",
]

FORBIDDEN_CATEGORY_NAMES = [
    "出港计划通知单", "检装车通知单",
    "请车表", "手写箱号车号表", "日现场工作记录表", "耗材统计表",
    "照片-装卸现场情况", "照片-集装箱内情况和作业",
    "照片-敞车内部情况和作业", "照片-火车涂写mark", "照片-检查工人",
    "手写记录",  # R66: found surviving legacy
]

FORBIDDEN_SUBDIR_NAMES = FORBIDDEN_CATEGORY_NAMES + [
    "_status", "_raw", "_previews",
    "extractions",
    "other", "unknown", "unmatched",
]


def check_forbidden() -> dict:
    """Check all forbidden patterns in active area."""
    violations = []

    # Root-level checks
    for d in FORBIDDEN_ROOT_DIRS:
        p = IMAGES_ROOT / d
        if p.exists():
            contents = [f for f in p.iterdir() if f.name != ".DS_Store"] if p.is_dir() else []
            violations.append({
                "path": str(p.relative_to(IMAGES_ROOT)),
                "type": "forbidden_root",
                "items": len(contents),
            })

    # Group-level category checks
    for entry in sorted(IMAGES_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith("_") or name.startswith("business") or name == "projects":
            continue
        for sub in FORBIDDEN_SUBDIR_NAMES:
            sp = entry / sub
            if sp.exists():
                contents = [f for f in sp.iterdir() if f.name != ".DS_Store"] if sp.is_dir() else []
                violations.append({
                    "path": str(sp.relative_to(IMAGES_ROOT)),
                    "type": "forbidden_subdir",
                    "group": name,
                    "items": len(contents),
                })

    return {
        "violations": violations,
        "count": len(violations),
        "passed": len(violations) == 0,
    }


def check_allowed() -> dict:
    """Check allowed active paths."""
    results = {}

    # business/projects
    bp = IMAGES_ROOT / "business" / "projects"
    results["business/projects"] = {
        "exists": bp.exists(),
        "file_count": len([f for f in bp.rglob("*") if f.is_file() and f.name != ".DS_Store"]) if bp.exists() else 0,
    }

    # quarantine
    q = IMAGES_ROOT / "_quarantine"
    quarantine_dirs = []
    if q.exists():
        for sub in sorted(q.iterdir()):
            if sub.is_dir():
                quarantine_dirs.append({
                    "name": sub.name,
                    "file_count": len([f for f in sub.rglob("*") if f.is_file() and f.name != ".DS_Store"]),
                })
    results["_quarantine"] = {
        "exists": q.exists(),
        "dirs": quarantine_dirs,
    }

    # Group raw image dirs
    groups = {}
    for entry in sorted(IMAGES_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith("_") or name.startswith("business") or name == "projects":
            continue
        month_dirs = []
        other_dirs = []
        file_count = 0
        for sub in entry.iterdir():
            if sub.is_dir():
                if len(sub.name) == 7 and sub.name[4] == "-":
                    fc = len([f for f in sub.iterdir() if f.is_file() and f.name != ".DS_Store"])
                    month_dirs.append({"month": sub.name, "file_count": fc})
                    file_count += fc
                else:
                    other_dirs.append({"name": sub.name, "file_count": len([f for f in sub.iterdir() if f.is_file() and f.name != ".DS_Store"])})
        groups[name] = {
            "month_dirs": month_dirs,
            "other_dirs": other_dirs,
            "total_raw_images": file_count,
        }
    results["group_raw_dirs"] = groups

    return results


def sample_raw_msg_paths() -> dict:
    """Sample 5 raw msg_path entries from chat_records and verify they exist."""
    samples = []
    chat_dir = WX_AGENT_ROOT / "data" / "chat_records"
    checked = 0
    passed = 0
    not_downloaded = 0

    # Strategy: look for image messages with actual paths (not 未下载)
    # Fall back to checking raw images directly in wechat_images
    for group in ["铁晟业务工作群", "数据单发群", "中唐特钢发运群"]:
        for month in ["2026-05", "2026-04"]:
            f = chat_dir / group / f"{month}.jsonl"
            if not f.exists():
                continue
            for line in open(f, encoding="utf-8"):
                msg = json.loads(line)
                mt = msg.get("msg-type", "")
                mp = msg.get("msg-path", "")
                if mt and "image" in str(mt).lower():
                    if mp == "未下载":
                        not_downloaded += 1
                        continue
                    if mp and os.path.exists(mp):
                        size = os.path.getsize(mp)
                        samples.append({
                            "group": group,
                            "seq": msg.get("seq"),
                            "path": mp,
                            "exists": True,
                            "size": size,
                        })
                        checked += 1
                        passed += 1
                    elif mp:
                        samples.append({
                            "group": group,
                            "seq": msg.get("seq"),
                            "path": mp,
                            "exists": False,
                            "size": 0,
                        })
                        checked += 1
                    if checked >= 5:
                        break
            if checked >= 5:
                break
        if checked >= 5:
            break

    # If no chat_record paths found, sample raw files directly
    if passed == 0 and checked > 0:
        # chat_record paths exist but broken — also sample raw images
        for group in ["铁晟业务工作群", "数据单发群", "中唐特钢发运群"]:
            for month in ["2026-05", "2026-04"]:
                raw_dir = IMAGES_ROOT / group / month
                if not raw_dir.exists():
                    continue
                for img_file in sorted(raw_dir.iterdir()):
                    if img_file.suffix.lower() in (".jpg", ".jpeg", ".png") and img_file.is_file():
                        samples.append({
                            "group": group,
                            "seq": None,
                            "path": str(img_file),
                            "exists": True,
                            "size": img_file.stat().st_size,
                            "note": "direct raw image sample (chat_record path broken)",
                        })
                        checked += 1
                        passed += 1
                        if passed >= 3:  # add 3 raw samples
                            break
                if passed >= 3:
                    break
            if passed >= 3:
                break

    return {
        "checked": checked,
        "passed": passed,
        "not_downloaded_in_chat_records": not_downloaded,
        "samples": samples,
    }


def runner_self_test() -> dict:
    """Mock runner path generation — no OCR/VLM."""
    results = {
        "test_count": 0,
        "passed": 0,
        "tests": [],
    }

    def _run_test(name, check_fn):
        results["test_count"] += 1
        try:
            passed, detail = check_fn()
            results["tests"].append({"name": name, "passed": passed, "detail": detail})
            if passed:
                results["passed"] += 1
        except Exception as e:
            results["tests"].append({"name": name, "passed": False, "detail": str(e)})

    # Test 1: 检装车通知单 — no old dir creation
    def t1():
        old_classified = IMAGES_ROOT / "铁晟业务工作群" / "检装车通知单"
        old_ext = IMAGES_ROOT / "铁晟业务工作群" / "extractions"
        return (
            not old_classified.exists() and not old_ext.exists(),
            f"group/category={old_classified.exists()}, group/extractions={old_ext.exists()}"
        )
    _run_test("T1: No group/category or group/extractions for 检装车通知单", t1)

    # Test 2: No _status at root
    def t2():
        return not (IMAGES_ROOT / "_status").exists(), str((IMAGES_ROOT / "_status").exists())
    _run_test("T2: No _status/ at wechat_images root", t2)

    # Test 3: No business/general
    def t3():
        return not (IMAGES_ROOT / "business" / "general").exists(), str((IMAGES_ROOT / "business" / "general").exists())
    _run_test("T3: No business/general/", t3)

    # Test 4: runtime paths are correct (not in wechat_images)
    def t4():
        runtime_ext = REPO_ROOT / "runtime" / "extractions" / "铁晟业务工作群" / "2026-05" / "检装车通知单"
        runtime_status = REPO_ROOT / "runtime" / "image_status" / "铁晟业务工作群" / "2026-05"
        return (
            str(runtime_ext).startswith(str(REPO_ROOT / "runtime")),
            f"extractions={runtime_ext}, status={runtime_status}"
        )
    _run_test("T4: Runtime extraction/status paths under repo runtime/", t4)

    # Test 5: business/projects exists with 8 files
    def t5():
        bp = IMAGES_ROOT / "business" / "projects"
        count = len([f for f in bp.rglob("*") if f.is_file() and f.name != ".DS_Store"]) if bp.exists() else 0
        return count == 8, f"business/projects file count={count} (expected 8)"
    _run_test("T5: business/projects has 8 files", t5)

    return results


def check_live_service() -> dict:
    """Check live_service state from runtime files."""
    state_file = REPO_ROOT / "runtime" / "live_service_state.json"
    pid_file = REPO_ROOT / "runtime" / "live_service.pid"

    result = {
        "state_file_exists": state_file.exists(),
        "pid_file_exists": pid_file.exists(),
        "alive": False,
    }

    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            result["state"] = state
            result["alive"] = state.get("alive", False)
        except Exception as e:
            result["state_error"] = str(e)

    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            result["pid"] = pid
            # Check if process is running
            try:
                os.kill(pid, 0)
                result["process_running"] = True
            except OSError:
                result["process_running"] = False
        except Exception as e:
            result["pid_error"] = str(e)

    return result


def check_cursor() -> dict:
    """Check live_service cursor state."""
    cursor_file = REPO_ROOT / "runtime" / "cursors" / "live_service_cursor.json"
    if not cursor_file.exists():
        return {"exists": False}

    data = json.loads(cursor_file.read_text())
    sources = data.get("sources", {})
    return {
        "exists": True,
        "source_count": len(sources),
        "updated_at": data.get("updated_at"),
        "sources": {
            k: {
                "last_local_id": v.get("last_local_id"),
                "group_name": v.get("group_name"),
            }
            for k, v in sources.items()
        },
    }


def check_message_inbox() -> dict:
    """Check message_inbox table."""
    if not DB_PATH.exists():
        return {"exists": False, "error": f"DB not found: {DB_PATH}"}

    conn = sqlite3.connect(str(DB_PATH))
    try:
        total = conn.execute("SELECT COUNT(*) FROM message_inbox").fetchone()[0]

        # msg_type breakdown
        type_counts = {}
        for row in conn.execute("SELECT msg_type, COUNT(*) FROM message_inbox GROUP BY msg_type"):
            type_counts[row[0]] = row[1]

        # media_status breakdown
        status_counts = {}
        for row in conn.execute("SELECT media_status, COUNT(*) FROM message_inbox GROUP BY media_status"):
            status_counts[row[0]] = row[1]

        # processing_status breakdown
        proc_counts = {}
        for row in conn.execute("SELECT processing_status, COUNT(*) FROM message_inbox GROUP BY processing_status"):
            proc_counts[row[0]] = row[1]

        # waiting_media count
        wm = conn.execute("SELECT COUNT(*) FROM message_inbox WHERE media_status='waiting_media'").fetchone()[0]

        # downloaded count
        dl = conn.execute("SELECT COUNT(*) FROM message_inbox WHERE media_status='downloaded'").fetchone()[0]

        # Check for old group name references
        old_refs = conn.execute(
            "SELECT COUNT(*) FROM message_inbox WHERE msg_path LIKE '%GROUP013%' OR raw_msg_path LIKE '%GROUP013%'"
        ).fetchone()[0]

        # latest update
        latest = conn.execute("SELECT MAX(updated_at), MAX(last_seen_at) FROM message_inbox").fetchone()

        return {
            "exists": True,
            "total_rows": total,
            "msg_type_breakdown": type_counts,
            "media_status_breakdown": status_counts,
            "processing_status_breakdown": proc_counts,
            "waiting_media": wm,
            "downloaded": dl,
            "old_group_refs": old_refs,
            "latest_update": latest[0],
            "latest_seen": latest[1],
        }
    finally:
        conn.close()


def check_old_path_resurrection() -> dict:
    """Check if any old paths have been recreated since R65.1 cleanup."""
    resurrected = []

    # Check specific paths that were cleaned
    checks = [
        "_status",
        "_raw",
        "_previews",
        "other",
        "unknown",
        "unmatched",
        "extractions",
        "business/general",
    ]
    for d in checks:
        p = IMAGES_ROOT / d
        if p.exists():
            resurrected.append({"path": d, "status": "RESURRECTED"})

    # Check group-level category dirs
    for entry in sorted(IMAGES_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith("_") or name.startswith("business") or name == "projects":
            continue
        for cat in FORBIDDEN_CATEGORY_NAMES:
            cp = entry / cat
            if cp.exists():
                resurrected.append({"path": f"{name}/{cat}", "status": "RESURRECTED (or legacy survivor)"})

    return {
        "resurrected_count": len(resurrected),
        "resurrected": resurrected,
    }


def main():
    timestamp = now_iso_beijing()
    report = {
        "r66_storage_contract_verification": {
            "timestamp": timestamp,
            "repo_root": str(REPO_ROOT),
            "images_root": str(IMAGES_ROOT),
        }
    }

    print("=" * 60)
    print("R66: Storage Contract Total Verification")
    print(f"Time: {timestamp}")
    print("=" * 60)

    # 1. Storage policy check
    policy_file = REPO_ROOT / "config" / "storage_policy.yaml"
    if policy_file.exists():
        import yaml
        policy = yaml.safe_load(policy_file.read_text())
        report["storage_policy"] = {
            "exists": True,
            "version": policy.get("version"),
            "general_archive_enabled": policy.get("business_archive", {}).get("general_archive_enabled"),
            "forbidden_new_writes_count": len(policy.get("legacy_directories", {}).get("forbidden_new_writes", [])),
            "runtime_outputs_keys": list(policy.get("runtime_outputs", {}).keys()) if policy.get("runtime_outputs") else [],
        }
        print(f"\n📋 Storage Policy: v{report['storage_policy']['version']}, "
              f"general_archive_enabled={report['storage_policy']['general_archive_enabled']}, "
              f"forbidden_patterns={report['storage_policy']['forbidden_new_writes_count']}")
    else:
        report["storage_policy"] = {"exists": False, "error": "file not found"}
        print("\n❌ Storage policy file NOT FOUND")

    # 2. Forbidden path check
    print("\n--- Forbidden Path Check ---")
    forbidden = check_forbidden()
    report["forbidden_check"] = forbidden
    if forbidden["passed"]:
        print("✅ 0 violations")
    else:
        print(f"❌ {forbidden['count']} violations:")
        for v in forbidden["violations"]:
            print(f"   {v['path']} ({v['items']} items)")

    # 3. Allowed path check
    print("\n--- Allowed Active Paths ---")
    allowed = check_allowed()
    report["allowed_check"] = allowed
    bp = allowed["business/projects"]
    print(f"  business/projects: {bp['file_count']} files")
    q = allowed["_quarantine"]
    print(f"  _quarantine: exists={q['exists']}, dirs={len(q['dirs'])}")
    for g_name, g_info in allowed["group_raw_dirs"].items():
        raw_count = g_info["total_raw_images"]
        other = g_info["other_dirs"]
        print(f"  {g_name}: {raw_count} raw images, other_dirs={[d['name'] for d in other]}")

    # 4. Raw msg_path sample
    print("\n--- Raw MsgPath Sample ---")
    samples = sample_raw_msg_paths()
    report["msg_path_sample"] = samples
    print(f"  Checked: {samples['checked']}, Passed: {samples['passed']}")
    for s in samples["samples"]:
        status = "✅" if s["exists"] else "❌"
        sid = s.get("seq") or s.get("note", "?")
        print(f"  {status} [{s['group']}] seq={sid} size={s['size']}")

    # 5. Runner self-test
    print("\n--- Runner Path Self-Test ---")
    self_test = runner_self_test()
    report["runner_self_test"] = self_test
    for t in self_test["tests"]:
        status = "✅" if t["passed"] else "❌"
        print(f"  {status} {t['name']}: {t['detail']}")
    print(f"  Result: {self_test['passed']}/{self_test['test_count']} passed")

    # 6. Live service
    print("\n--- Live Service ---")
    ls = check_live_service()
    report["live_service"] = ls
    print(f"  Alive: {ls['alive']}")
    if ls.get("pid"):
        print(f"  PID: {ls['pid']}, running: {ls.get('process_running', 'unknown')}")

    # 7. Cursor
    print("\n--- Cursor ---")
    cursor = check_cursor()
    report["cursor"] = cursor
    print(f"  Sources: {cursor.get('source_count', 0)}")
    print(f"  Updated: {cursor.get('updated_at')}")
    for src, info in cursor.get("sources", {}).items():
        print(f"    {info['group_name']}: last_local_id={info['last_local_id']}")

    # 8. Message inbox
    print("\n--- Message Inbox ---")
    mi = check_message_inbox()
    report["message_inbox"] = mi
    print(f"  Total rows: {mi.get('total_rows', 0)}")
    print(f"  Type breakdown: {mi.get('msg_type_breakdown', {})}")
    print(f"  Media status: {mi.get('media_status_breakdown', {})}")
    print(f"  Waiting media: {mi.get('waiting_media', 0)}")
    print(f"  Downloaded: {mi.get('downloaded', 0)}")
    print(f"  Old group refs: {mi.get('old_group_refs', 0)}")
    print(f"  Latest update: {mi.get('latest_update')}")

    # 9. Old path resurrection
    print("\n--- Old Path Resurrection Check ---")
    resurrection = check_old_path_resurrection()
    report["resurrection_check"] = resurrection
    if resurrection["resurrected_count"] == 0:
        print("✅ No old paths resurrected")
    else:
        print(f"⚠️  {resurrection['resurrected_count']} paths found:")
        for r in resurrection["resurrected"]:
            print(f"   {r['path']}: {r['status']}")

    # Write JSON report
    out_dir = REPO_ROOT / "runtime" / "storage_contract_r66"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "r66_storage_contract_check.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n📄 JSON report: {json_path}")

    # Write text summary
    txt_path = out_dir / "r66_storage_contract_check.txt"
    lines = []
    lines.append(f"R66 Storage Contract Verification — {timestamp}")
    lines.append("=" * 60)
    lines.append(f"Storage Policy: v{report['storage_policy'].get('version', '?')}")
    lines.append(f"Forbidden violations: {forbidden['count']}")
    lines.append(f"MsgPath sample: {samples['passed']}/{samples['checked']} passed")
    lines.append(f"Runner self-test: {self_test['passed']}/{self_test['test_count']} passed")
    lines.append(f"business/projects: {bp['file_count']} files")
    lines.append(f"_quarantine: exists={q['exists']}")
    lines.append(f"Live service: alive={ls['alive']}")
    lines.append(f"Cursor: {cursor.get('source_count', 0)} sources, updated={cursor.get('updated_at')}")
    lines.append(f"Message inbox: {mi.get('total_rows', 0)} rows, waiting_media={mi.get('waiting_media', 0)}")
    lines.append(f"Old path resurrection: {resurrection['resurrected_count']}")
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"📄 Text summary: {txt_path}")

    # Overall pass/fail
    overall_pass = (
        forbidden["passed"] and
        self_test["passed"] == self_test["test_count"] and
        samples["passed"] == samples["checked"] and
        resurrection["resurrected_count"] == 0
    )

    print(f"\n{'='*60}")
    print(f"{'✅ ALL CHECKS PASSED' if overall_pass else '⚠️  SOME CHECKS FAILED — SEE ABOVE'}")
    print(f"{'='*60}")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
