#!/usr/bin/env python3
"""
R63: Canonical document archiver.
Processes business_critical canonical documents from R62 manifest.
Hardlinks/copies to canonical project archive paths, updates sop_agent.db.
Does NOT delete original files.
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "sop_agent.db"
IMAGES_ROOT = Path("/Users/qicai21/Documents/bussiness-artifacts/wechat_images")
R62_MANIFEST = REPO_ROOT / "runtime" / "storage_cleanup_manifest" / "image_storage_manifest.json"
OUTPUT_DIR = REPO_ROOT / "runtime" / "storage_canonical_archive_r63"

CANONICAL_DOCS = {
    "出港计划通知单": "release",
    "检装车通知单": "inspection",
}


def load_r62_manifest() -> list[dict]:
    with open(R62_MANIFEST) as f:
        return json.load(f)


def build_db_lookup(conn: sqlite3.Connection) -> dict:
    """Build lookups from DB tables."""
    lookup = {
        "image_audit": {},       # id -> record
        "release_batches": {},   # id -> record
        "inspection_candidates": {},  # id -> record
        "message_inbox_by_path": {},  # raw_standard_image_path -> record
    }

    # image_ingestion_audit
    cur = conn.execute("SELECT * FROM image_ingestion_audit")
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        lookup["image_audit"][rec["id"]] = rec

    # release_batches
    cur = conn.execute("SELECT * FROM release_batches")
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        lookup["release_batches"][rec["id"]] = rec

    # inspection_ingestion_candidates
    cur = conn.execute("SELECT * FROM inspection_ingestion_candidates")
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        lookup["inspection_candidates"][rec["id"]] = rec

    # message_inbox
    cur = conn.execute("SELECT id, message_id, raw_standard_image_path, business_archive_image_path FROM message_inbox")
    for row in cur.fetchall():
        rec = {"id": row[0], "message_id": row[1], "raw_path": row[2], "archive_path": row[3]}
        if rec["raw_path"]:
            lookup["message_inbox_by_path"][rec["raw_path"]] = rec

    return lookup


def resolve_context(entry: dict, db_lookup: dict) -> dict:
    """
    Resolve canonical path context from DB.
    Returns dict with: project_id, destination, ship_name, lot, document_stage,
    yyyy_mm_dd, message_id, incomplete_context, fallback_message_id.
    """
    ctx = {
        "project_id": "",
        "destination": "unknown",
        "ship_name": "unknown",
        "lot": "lotunknown",
        "document_stage": "",
        "yyyy_mm_dd": "",
        "message_id": "",
        "incomplete_context": False,
        "fallback_message_id": False,
    }

    # Document stage
    ctx["document_stage"] = CANONICAL_DOCS.get(entry.get("document_type", ""), "unknown")

    # Get DB audit record
    audit_id = entry.get("linked_db_id", "")
    audit = db_lookup["image_audit"].get(audit_id, {}) if audit_id else {}

    ctx["project_id"] = audit.get("project_id", "") or ""

    # Resolve release_batch → ship_name, destination, lot
    db_ids_str = audit.get("db_record_ids", "")
    db_ids = []
    if db_ids_str:
        try:
            db_ids = json.loads(db_ids_str)
        except (json.JSONDecodeError, TypeError):
            pass

    # Try to find release_batch via inspection_candidate or directly
    rb_id = ""
    for did in db_ids:
        if did in db_lookup["release_batches"]:
            rb_id = did
            break
        if did in db_lookup["inspection_candidates"]:
            cand = db_lookup["inspection_candidates"][did]
            if cand.get("release_batch_id"):
                rb_id = cand["release_batch_id"]
                break

    if rb_id and rb_id in db_lookup["release_batches"]:
        rb = db_lookup["release_batches"][rb_id]
        ctx["destination"] = rb.get("destination_station", "unknown") or "unknown"
        ctx["ship_name"] = rb.get("ship_name", "unknown") or "unknown"
        ctx["lot"] = rb.get("batch_sequence", "lotunknown") or "lotunknown"
    else:
        ctx["incomplete_context"] = True

    # Date: from audit created_at or entry mtime
    created_at = audit.get("created_at", "")
    if created_at:
        try:
            dt = datetime.strptime(str(created_at)[:10], "%Y-%m-%d")
            ctx["yyyy_mm_dd"] = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass
    if not ctx["yyyy_mm_dd"]:
        mtime = entry.get("mtime", "")
        if mtime:
            ctx["yyyy_mm_dd"] = mtime[:10]

    # message_id
    ctx["message_id"] = audit.get("message_id") or entry.get("message_id") or ""
    if not ctx["message_id"]:
        # Fallback: file stem
        filepath = Path(entry.get("path", ""))
        ctx["message_id"] = filepath.stem
        ctx["fallback_message_id"] = True

    return ctx


def build_canonical_path(ctx: dict, file_type: str, file_ext: str) -> str:
    """
    Build canonical archive path following storage_policy.yaml pattern:
    business/projects/{project_id}/{destination}/{ship_name}/{lot}/{document_stage}/{yyyy_mm_dd}/{message_id}{.ext}
    """
    project_id = ctx["project_id"] or "unknown_project"
    destination = ctx["destination"]
    ship_name = ctx["ship_name"]
    lot = ctx["lot"]
    stage = ctx["document_stage"]
    date = ctx["yyyy_mm_dd"]
    msg_id = ctx["message_id"]

    rel = f"business/projects/{project_id}/{destination}/{ship_name}/{lot}/{stage}/{date}/{msg_id}{file_ext}"
    return rel


def compute_sha256(filepath: Path) -> str:
    try:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def archive_file(source_path: str, target_rel: str, images_root: Path, dry_run: bool) -> tuple[str, str]:
    """
    Hardlink or copy source to target under images_root.
    Returns (archive_method, actual_target_full_path).
    Prefers hardlink, falls back to copy.
    """
    target_full = images_root / target_rel
    target_full.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        return "hardlink (dry-run)", str(target_full)

    try:
        # Try hardlink first
        if target_full.exists():
            # Already exists — verify SHA matches
            source_sha = compute_sha256(Path(source_path))
            target_sha = compute_sha256(target_full)
            if source_sha == target_sha:
                return "hardlink (exists)", str(target_full)
            else:
                # SHA mismatch — need different name
                stem = target_full.stem
                suffix = target_full.suffix
                counter = 1
                while True:
                    alt = target_full.parent / f"{stem}_{counter}{suffix}"
                    if not alt.exists():
                        target_full = alt
                        break
                    counter += 1
                os.link(source_path, str(target_full))
                return "hardlink (alt)", str(target_full)

        os.link(source_path, str(target_full))
        return "hardlink", str(target_full)
    except OSError:
        # Hardlink failed, try copy
        shutil.copy2(source_path, str(target_full))
        return "copy", str(target_full)


def update_db_project_archive_paths(
    conn: sqlite3.Connection,
    audit_id: str,
    image_path: str | None,
    json_path: str | None,
    dry_run: bool
) -> bool:
    """Update image_ingestion_audit.project_archive_paths."""
    if dry_run:
        return True

    # Read current
    cur = conn.execute(
        "SELECT project_archive_paths FROM image_ingestion_audit WHERE id=?",
        (audit_id,)
    )
    row = cur.fetchone()
    if not row:
        return False

    current = {}
    if row[0]:
        try:
            current = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            current = {}

    if image_path:
        current["image"] = image_path
    if json_path:
        current["json"] = json_path

    conn.execute(
        "UPDATE image_ingestion_audit SET project_archive_paths=? WHERE id=?",
        (json.dumps(current, ensure_ascii=False), audit_id)
    )
    return True


def update_message_inbox(
    conn: sqlite3.Connection,
    entry: dict,
    image_path: str | None,
    json_path: str | None,
    dry_run: bool
) -> bool:
    """Update message_inbox business_archive_image_path / business_archive_json_path."""
    if dry_run:
        return False

    source_path = entry.get("path", "")
    if not source_path:
        return False

    # Try to find message_inbox entry by raw_standard_image_path or file match
    cur = conn.execute("SELECT id, raw_standard_image_path, extraction_json_path FROM message_inbox")
    updated = False

    for row in cur.fetchall():
        mi_id, raw_path, extr_path = row[0], row[1] or "", row[2] or ""

        # Match by image path
        if image_path and raw_path and source_path in (raw_path, os.path.realpath(source_path)):
            conn.execute(
                "UPDATE message_inbox SET business_archive_image_path=? WHERE id=?",
                (image_path, mi_id)
            )
            updated = True

        # Match by json path
        if json_path and extr_path and source_path in (extr_path, os.path.realpath(source_path)):
            conn.execute(
                "UPDATE message_inbox SET business_archive_json_path=? WHERE id=?",
                (json_path, mi_id)
            )
            updated = True

    return updated


def process_canonical_documents(dry_run: bool = True) -> dict:
    """Main processing function."""
    rows = load_r62_manifest()
    conn = sqlite3.connect(str(DB_PATH))
    db_lookup = build_db_lookup(conn)

    # Filter target: business_critical canonical, exclude internal, keep/move/review
    target = [
        r for r in rows
        if r["is_canonical_document"]
        and r["is_business_critical"]
        and r["source_area"] != "internal"
        and r["suggested_action"] in ("move", "keep", "review")
    ]

    results = []
    stats = Counter()

    for entry in target:
        source_path = entry.get("path", "")
        source_exists = os.path.exists(source_path)
        doc_type = entry.get("document_type", "")
        suggested_action = entry.get("suggested_action", "")
        file_type = entry.get("file_type", "")
        is_archived = entry.get("is_already_archived_to_project", False)

        ctx = resolve_context(entry, db_lookup)
        file_ext = Path(source_path).suffix

        result = {
            "source_path": source_path,
            "source_exists": source_exists,
            "document_type": doc_type,
            "document_stage": ctx["document_stage"],
            "project_id": ctx["project_id"],
            "destination": ctx["destination"],
            "ship_name": ctx["ship_name"],
            "lot": ctx["lot"],
            "message_id": ctx["message_id"],
            "linked_db_table": entry.get("linked_db_table", ""),
            "linked_db_id": entry.get("linked_db_id", ""),
            "archive_image_path": "",
            "archive_json_path": "",
            "archive_method": "skip",
            "db_updated": False,
            "message_inbox_updated": False,
            "status": "skipped",
            "reason": "",
            "incomplete_context": ctx["incomplete_context"],
            "fallback_message_id": ctx["fallback_message_id"],
        }

        if not source_exists:
            result["status"] = "failed"
            result["reason"] = "source file does not exist"
            results.append(result)
            stats["failed"] += 1
            continue

        # For "keep" entries: verify target exists, don't re-archive
        if suggested_action == "keep" and is_archived:
            # Verify the archive path exists
            audit = db_lookup["image_audit"].get(entry.get("linked_db_id", ""), {})
            proj_paths_str = audit.get("project_archive_paths", "")
            verified = False
            if proj_paths_str:
                try:
                    proj_paths = json.loads(proj_paths_str)
                    verify_key = "image" if file_type == "image" else "json"
                    verify_path = proj_paths.get(verify_key, "")
                    if verify_path and os.path.exists(verify_path):
                        result["archive_image_path"] = proj_paths.get("image", "")
                        result["archive_json_path"] = proj_paths.get("json", "")
                        result["status"] = "kept"
                        result["reason"] = "already archived and verified"
                        result["archive_method"] = "skip (verified)"
                        verified = True
                except Exception:
                    pass
            if not verified:
                result["status"] = "review"
                result["reason"] = "marked keep but archive path verification failed"
                stats["review"] += 1
                results.append(result)
                continue
            stats["kept"] += 1
            results.append(result)
            continue

        # For "move" entries: archive to canonical path
        canonical_rel = build_canonical_path(ctx, file_type, file_ext)
        method, target_full = archive_file(source_path, canonical_rel, IMAGES_ROOT, dry_run)
        result["archive_method"] = method

        # Set archive paths based on file type
        if file_type == "image":
            result["archive_image_path"] = str(target_full)
        elif file_type == "json":
            result["archive_json_path"] = str(target_full)

        # Update DB
        audit_id = entry.get("linked_db_id", "")
        img_path = str(target_full) if file_type == "image" else None
        json_path = str(target_full) if file_type == "json" else None

        db_updated = update_db_project_archive_paths(conn, audit_id, img_path, json_path, dry_run)
        mi_updated = update_message_inbox(conn, entry, img_path, json_path, dry_run)

        result["db_updated"] = db_updated
        result["message_inbox_updated"] = mi_updated
        result["status"] = "archived"
        result["reason"] = f"archived via {method} to canonical path"
        stats["archived"] += 1

        results.append(result)

    conn.commit()
    conn.close()

    # Build summary
    summary = {
        "r63_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_manifest": str(R62_MANIFEST),
        "dry_run": dry_run,
        "total_canonical_in_manifest": sum(1 for r in rows if r["is_canonical_document"]),
        "total_business_critical_in_manifest": sum(1 for r in rows if r["is_business_critical"]),
        "target_processed": len(target),
        "status_counts": dict(stats),
        "archived_count": stats.get("archived", 0),
        "kept_count": stats.get("kept", 0),
        "review_count": stats.get("review", 0),
        "skipped_count": stats.get("skipped", 0),
        "failed_count": stats.get("failed", 0),
        "archive_methods": dict(Counter(r["archive_method"] for r in results)),
        "db_update_count": sum(1 for r in results if r["db_updated"]),
        "message_inbox_update_count": sum(1 for r in results if r["message_inbox_updated"]),
        "incomplete_context_count": sum(1 for r in results if r.get("incomplete_context")),
        "fallback_message_id_count": sum(1 for r in results if r.get("fallback_message_id")),
    }

    return {"results": results, "summary": summary}


def main():
    parser = argparse.ArgumentParser(description="R63: Archive canonical business documents")
    parser.add_argument("--apply", action="store_true", help="Actually perform archiving (default: dry-run)")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Output directory")
    args = parser.parse_args()

    dry_run = not args.apply
    mode = "DRY-RUN" if dry_run else "APPLY"

    print(f"=== R63 Canonical Archive ({mode}) ===")
    print()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    result = process_canonical_documents(dry_run=dry_run)
    results = result["results"]
    summary = result["summary"]

    # Print summary
    print(f"  Target processed:        {summary['target_processed']}")
    print(f"  Archived:                {summary['archived_count']}")
    print(f"  Kept (verified):         {summary['kept_count']}")
    print(f"  Review needed:           {summary['review_count']}")
    print(f"  Failed:                  {summary['failed_count']}")
    print(f"  DB updates:              {summary['db_update_count']}")
    print(f"  message_inbox updates:   {summary['message_inbox_update_count']}")
    print(f"  Incomplete context:      {summary['incomplete_context_count']}")
    print(f"  Fallback message_id:     {summary['fallback_message_id_count']}")
    print(f"  Archive methods:")
    for method, count in sorted(summary["archive_methods"].items()):
        print(f"    {method}: {count}")

    # Write CSV
    csv_path = outdir / "r63_canonical_archive_result.csv"
    fieldnames = [
        "source_path", "source_exists", "document_type", "document_stage",
        "project_id", "destination", "ship_name", "lot", "message_id",
        "linked_db_table", "linked_db_id", "archive_image_path", "archive_json_path",
        "archive_method", "db_updated", "message_inbox_updated",
        "status", "reason", "incomplete_context", "fallback_message_id",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\nCSV written: {csv_path}")

    # Write JSON
    json_path = outdir / "r63_canonical_archive_result.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"JSON written: {json_path}")

    # Write summary
    summary_path = outdir / "r63_canonical_archive_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Summary written: {summary_path}")

    if dry_run:
        print("\n*** DRY-RUN complete. Run with --apply to execute. ***")


if __name__ == "__main__":
    main()
