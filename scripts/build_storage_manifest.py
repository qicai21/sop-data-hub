#!/usr/bin/env python3
"""
R62: Read-only storage manifest builder.
Scans wechat_images, cross-references DB, generates manifest CSV/JSON/summary.
NO file moves, NO deletes, NO DB writes, NO external uploads, NO WeChat sends.
"""

from sop_hub.utils.time import now_iso_beijing
import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "sop_agent.db"
IMAGES_ROOT = Path("/Users/qicai21/Documents/bussiness-artifacts/wechat_images")
OUTPUT_DIR = REPO_ROOT / "runtime" / "storage_cleanup_manifest"
STORAGE_POLICY_PATH = REPO_ROOT / "config" / "storage_policy.yaml"

# Exclusion patterns (directories/files to skip)
EXCLUDE_DIRS = {
    "_migration_reports", "reports", "_previews", ".DS_Store",
}
EXCLUDE_FILES = {".DS_Store"}
# Extensions we care about
SCAN_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".json", ".xlsx", ".xls", ".pdf", ".docx", ".csv"}

# ── Canonical document types (from storage_policy.yaml) ──────────────────
CANONICAL_DOCS = {
    "出港计划通知单": {"document_stage": "release", "priority": "business_critical"},
    "检装车通知单": {"document_stage": "inspection", "priority": "business_critical"},
}

GENERAL_DOCS = {
    "现场照片", "其他照片", "unknown",
}


def compute_sha256(filepath: Path) -> str:
    """Compute SHA256 of a file. Returns empty string on error."""
    try:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def classify_source_area(rel_path: str) -> str:
    """
    Classify file into source_area based on directory structure.
    Returns one of: raw, group_classified, group_extractions, projects,
    unmatched, legacy, internal.
    """
    parts = Path(rel_path).parts

    # Internal tracking directories (not business assets)
    if "_status" in parts:
        return "internal"
    if "_raw" in parts:
        return "internal"
    if "_previews" in parts:
        return "internal"

    if len(parts) >= 2 and parts[0] == "projects":
        return "projects"
    if len(parts) >= 2 and parts[0] == "extractions":
        return "group_extractions"
    if "extractions" in parts:
        return "group_extractions"
    if len(parts) >= 2 and parts[0] == "unmatched":
        return "unmatched"
    if len(parts) >= 2 and parts[0] == "other":
        return "legacy"
    if len(parts) >= 2 and parts[0] == "unknown":
        return "legacy"

    # Group-level classified: <group_name>/<category>/<file>
    # or <group_name>/<YYYY-MM>/<file>
    known_categories = {
        "出港计划通知单", "检装车通知单", "请车表", "手写箱号车号表",
        "日现场工作记录表", "耗材统计表",
        "照片-敞车内部情况和作业", "照片-火车涂写mark",
        "照片-装卸现场情况", "照片-集装箱内情况和作业",
        "照片-检查工人",
    }
    if len(parts) >= 2:
        if parts[1] in known_categories or (len(parts[1]) == 7 and parts[1][:4].isdigit() and parts[1][4] == '-'):
            return "raw"
        if parts[0] in known_categories:
            return "raw"
    return "raw"


def extract_group_name(rel_path: str) -> str:
    """Extract group name from relative path."""
    parts = Path(rel_path).parts
    if len(parts) >= 2 and parts[0] == "projects":
        return ""
    if parts[0] in ("extractions", "unmatched", "other", "unknown"):
        return ""
    # <group_name>/...
    return parts[0]


def extract_category(rel_path: str) -> str:
    """Extract category from relative path."""
    parts = Path(rel_path).parts
    if len(parts) >= 2:
        if parts[0] == "projects":
            return ""
        if parts[0] == "extractions":
            return parts[1] if len(parts) >= 2 else ""
        if parts[0] in ("unmatched", "other", "unknown"):
            return parts[0]
        if parts[0] == "_migration_reports":
            return ""
        return parts[1] if len(parts) >= 2 else parts[0]
    return ""


def extract_document_type(rel_path: str, db_record: dict | None) -> str:
    """Determine document_type from path + DB."""
    if db_record and db_record.get("classified_category"):
        return db_record["classified_category"]

    parts = Path(rel_path).parts
    for part in parts:
        if part in CANONICAL_DOCS:
            return part
        if part in GENERAL_DOCS:
            return part

    # Heuristic from directory names
    category_map = {
        "出港计划通知单": "出港计划通知单",
        "检装车通知单": "检装车通知单",
        "请车表": "请车表",
        "手写箱号车号表": "手写箱号车号表",
        "日现场工作记录表": "日现场工作记录表",
        "耗材统计表": "耗材统计表",
    }
    for part in parts:
        for k, v in category_map.items():
            if k in part:
                return v

    # Check for photo categories
    photo_patterns = {
        "敞车内部": "照片-敞车内部情况和作业",
        "火车涂写": "照片-火车涂写mark",
        "装卸现场": "照片-装卸现场情况",
        "集装箱": "照片-集装箱内情况和作业",
        "检查工人": "照片-检查工人",
    }
    for part in parts:
        for k, v in photo_patterns.items():
            if k in part:
                return v

    return "unknown"


def build_db_index(conn: sqlite3.Connection) -> dict:
    """Build lookup index from all DB tables."""
    index = {
        "by_raw_path": {},       # raw_image_path -> image_ingestion_audit record
        "by_json_path": {},      # extraction_json_path -> image_ingestion_audit record
        "by_project_path": {},   # paths from project_archive_paths -> record
        "release_batches": {},   # id -> release_batch record
        "inspection_candidates": {},  # id -> candidate record
    }

    # image_ingestion_audit
    cur = conn.execute("SELECT * FROM image_ingestion_audit")
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        raw = rec.get("raw_image_path", "")
        if raw:
            index["by_raw_path"][raw] = rec
        json_p = rec.get("extraction_json_path", "")
        if json_p:
            index["by_json_path"][json_p] = rec
        proj = rec.get("project_archive_paths", "")
        if proj:
            try:
                proj_data = json.loads(proj)
                for k in ("image", "json"):
                    if k in proj_data and proj_data[k]:
                        index["by_project_path"][proj_data[k]] = rec
            except (json.JSONDecodeError, TypeError):
                pass

    # release_batches
    cur = conn.execute("SELECT * FROM release_batches")
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        index["release_batches"][rec["id"]] = rec

    # inspection_ingestion_candidates
    cur = conn.execute("SELECT * FROM inspection_ingestion_candidates")
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        index["inspection_candidates"][rec["id"]] = rec

    # message_inbox lookup by message_id
    index["message_inbox_by_msg_id"] = {}
    cur = conn.execute("SELECT * FROM message_inbox")
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        mid = rec.get("message_id", "")
        if mid:
            index["message_inbox_by_msg_id"][mid] = rec

    # shipment_release_batch_matches for cross-referencing
    index["matches_by_release_id"] = defaultdict(list)
    cur = conn.execute("SELECT * FROM shipment_release_batch_matches")
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        rid = rec.get("release_batch_id", "")
        if rid:
            index["matches_by_release_id"][rid].append(rec)

    return index


def lookup_db(filepath: Path, rel_path: str, full_path: str, db_index: dict) -> dict | None:
    """Find the DB record most associated with this file."""
    # Exact match by full path
    for lookup in [db_index["by_raw_path"], db_index["by_json_path"], db_index["by_project_path"]]:
        if full_path in lookup:
            return lookup[full_path]

    # Try resolving with realpath
    try:
        rp = str(Path(full_path).resolve())
        for lookup in [db_index["by_raw_path"], db_index["by_json_path"], db_index["by_project_path"]]:
            for k, v in lookup.items():
                try:
                    if str(Path(k).resolve()) == rp:
                        return v
                except Exception:
                    pass
    except Exception:
        pass

    # Stem-based matching for files in group directories
    stem = filepath.stem  # e.g., "1100_2b72c729af9c810ad4e9329bc6e852a8"
    parts = stem.split("_", 1)
    if len(parts) == 2:
        local_id, md5_part = parts
        for lookup_map in [db_index["by_raw_path"], db_index["by_json_path"]]:
            for k, v in lookup_map.items():
                if md5_part in k and (local_id in k or Path(k).stem == stem):
                    return v

    return None


def determine_suggested_action(
    rel_path: str, file_type: str, doc_type: str, db_rec: dict | None,
    is_canonical: bool, is_business_critical: bool, is_archived: bool,
    source_area: str, full_path: str, db_index: dict
) -> tuple[str, str, str]:
    """
    Determine suggested_action, suggested_canonical_path, and reason.
    Returns (action, suggested_canonical_path, reason).
    """
    actions = {
        "keep": "keep",
        "hardlink": "hardlink",
        "move": "move",
        "review": "review",
        "trash": "trash",
    }

    # .xlsx and non-image/non-json files
    if file_type not in ("image", "json"):
        return "review", "", f"non-media file type ({file_type}), manual review needed"

    # Already in projects/ with proper structure
    if source_area == "projects" and is_archived:
        return "keep", full_path, "already archived to project with correct structure"

    # JSON in extractions
    if source_area == "group_extractions":
        if is_business_critical and db_rec:
            proj = db_rec.get("project_id", "")
            if proj:
                # Check if canonical exists in projects
                project_base = IMAGES_ROOT / "projects"
                parts = Path(rel_path).parts
                json_filename = Path(full_path).name
                # Generate suggested canonical path
                suggested = str(project_base / proj / ".../json" / json_filename)
                return "move", suggested, f"business critical JSON still in extractions, needs move to projects/{proj}"
            return "review", "", "business critical JSON in extractions but no project_id"
        return "trash", "", "non-critical JSON in legacy extractions directory"

    # Images in raw group directories
    if source_area == "raw":
        if is_business_critical and db_rec:
            proj = db_rec.get("project_id", "")
            if proj and is_archived:
                # Check project_archive_paths
                proj_paths = db_rec.get("project_archive_paths", "")
                if proj_paths:
                    try:
                        proj_data = json.loads(proj_paths)
                        img_path = proj_data.get("image", "")
                    except Exception:
                        img_path = ""
                    if img_path and Path(img_path).exists():
                        return "keep", img_path, "business critical image already archived to project"
            if proj and not is_archived:
                return "move", f"projects/{proj}/.../images/<file>", "business critical image not yet archived, needs move to project"
            return "review", "", "business critical image without clear project path"
        # General photos
        if doc_type in GENERAL_DOCS or doc_type.startswith("照片-"):
            return "move", f"business/general/<YYYY-MM>/<file>", f"general photo ({doc_type}), move to business/general"
        return "review", "", "image in raw directory, needs classification"

    # Unmatched
    if source_area == "unmatched":
        if db_rec:
            status = db_rec.get("db_action", "")
            if status == "discarded_by_operator":
                return "trash", "", "explicitly discarded by operator"
            return "review", "", "unmatched with DB record but unclear disposition"
        return "review", "", "unmatched file with no DB record"

    # Internal tracking files (_status, _raw) — not business assets
    if source_area == "internal":
        return "trash", "", "internal tracking file, not a business asset"

    # Legacy (other, unknown)
    if source_area == "legacy":
        return "trash", "", "legacy directory, candidate for cleanup"

    return "review", "", "unknown status, manual review needed"


def build_manifest() -> dict:
    """Main function: scan files, cross-reference DB, generate manifest."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    db_index = build_db_index(conn)

    rows = []
    sha_counter = defaultdict(list)

    # Walk the wechat_images directory
    for dirpath, dirnames, filenames in os.walk(IMAGES_ROOT):
        # Filter excluded directories
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        for filename in filenames:
            if filename in EXCLUDE_FILES:
                continue

            filepath = Path(dirpath) / filename
            ext = filepath.suffix.lower()
            if ext not in SCAN_EXTENSIONS:
                continue

            full_path = str(filepath)
            rel_path = str(filepath.relative_to(IMAGES_ROOT))

            # Compute basic attributes
            file_type = "image" if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"} else (
                "json" if ext == ".json" else "xlsx" if ext in {".xlsx", ".xls"} else "other"
            )
            exists = filepath.exists()

            sha = compute_sha256(filepath) if exists else ""
            size = filepath.stat().st_size if exists else 0
            mtime_ts = filepath.stat().st_mtime if exists else 0
            mtime = datetime.fromtimestamp(mtime_ts, tz=timezone.utc).isoformat() if mtime_ts else ""

            source_area = classify_source_area(rel_path)
            group_name = extract_group_name(rel_path)
            category = extract_category(rel_path)
            db_rec = lookup_db(filepath, rel_path, full_path, db_index)

            # Determine document type
            doc_type = extract_document_type(rel_path, db_rec)
            is_canonical = doc_type in CANONICAL_DOCS
            is_general = doc_type in GENERAL_DOCS or doc_type.startswith("照片-")

            # Business critical: canonical + has DB record with db_action
            is_business_critical = False
            if is_canonical and db_rec:
                db_action = db_rec.get("db_action", "")
                if db_action and db_action not in ("none", "discarded_by_operator", ""):
                    is_business_critical = True

            # Archive status
            is_archived = False
            proj_paths = db_rec.get("project_archive_paths", "") if db_rec else ""
            if proj_paths:
                try:
                    proj_data = json.loads(proj_paths)
                    if proj_data.get("image") or proj_data.get("json"):
                        is_archived = True
                except Exception:
                    pass

            # Linked DB info
            linked_db_table = ""
            linked_db_id = ""
            release_batch_id = ""
            inspection_candidate_id = ""
            message_id = ""
            local_id = ""

            if db_rec:
                linked_db_table = "image_ingestion_audit"
                linked_db_id = db_rec.get("id", "")

                # Extract release_batch_id from db_record_ids or project_archive_paths
                db_ids_str = db_rec.get("db_record_ids", "")
                if db_ids_str:
                    try:
                        db_ids = json.loads(db_ids_str)
                    except Exception:
                        db_ids = []
                    if db_ids:
                        # First ID is often the release_batch_id
                        first_id = db_ids[0]
                        if first_id in db_index["release_batches"]:
                            release_batch_id = first_id

                # Check inspection candidates
                for cid, crec in db_index["inspection_candidates"].items():
                    if crec.get("release_batch_id") == release_batch_id:
                        inspection_candidate_id = cid
                        break

                # Get message_id from various sources
                mid = db_rec.get("message_id", "")
                if mid:
                    message_id = mid
                local_id = db_rec.get("local_id", "")

            # If release_batch_id not found directly, try matching by release_batch project
            if not release_batch_id and db_rec:
                proj_id = db_rec.get("project_id", "")
                if proj_id:
                    for rid, rrec in db_index["release_batches"].items():
                        if rrec.get("project") == proj_id and rrec.get("source_message_id") == message_id:
                            release_batch_id = rid
                            break

            # Determine suggested action
            suggested_action, suggested_canonical_path, reason = determine_suggested_action(
                rel_path, file_type, doc_type, db_rec,
                is_canonical, is_business_critical, is_archived,
                source_area, full_path, db_index
            )

            # Track SHA for duplicate detection
            if sha:
                sha_counter[sha].append(full_path)

            row = {
                "path": full_path,
                "rel_path": rel_path,
                "exists": exists,
                "file_type": file_type,
                "sha256": sha,
                "size": size,
                "mtime": mtime,
                "category": category,
                "source_area": source_area,
                "message_id": message_id,
                "local_id": local_id,
                "group_name": group_name,
                "document_type": doc_type,
                "is_canonical_document": is_canonical,
                "is_general_document": is_general,
                "is_business_critical": is_business_critical,
                "linked_db_table": linked_db_table,
                "linked_db_id": linked_db_id,
                "release_batch_id": release_batch_id,
                "inspection_candidate_id": inspection_candidate_id,
                "is_already_archived_to_project": is_archived,
                "suggested_canonical_path": suggested_canonical_path,
                "suggested_action": suggested_action,
                "reason": reason,
            }
            rows.append(row)

    conn.close()

    # Compute duplicates
    duplicates = {sha: paths for sha, paths in sha_counter.items() if len(paths) > 1}

    # Compute summary
    action_counts = defaultdict(int)
    for r in rows:
        action_counts[r["suggested_action"]] += 1

    summary = {
        "manifest_version": 1,
        "generated_at": now_iso_beijing(),
        "storage_policy_version": 1,
        "total_files": len(rows),
        "image_count": sum(1 for r in rows if r["file_type"] == "image"),
        "json_count": sum(1 for r in rows if r["file_type"] == "json"),
        "other_count": sum(1 for r in rows if r["file_type"] not in ("image", "json")),
        "canonical_document_count": sum(1 for r in rows if r["is_canonical_document"]),
        "business_critical_count": sum(1 for r in rows if r["is_business_critical"]),
        "missing_db_path_count": sum(1 for r in rows if not r["linked_db_id"]),
        "duplicate_sha_count": len(duplicates),
        "duplicate_file_count": sum(len(paths) - 1 for paths in duplicates.values()),
        "suggested_action_counts": dict(action_counts),
        "source_area_counts": {
            area: sum(1 for r in rows if r["source_area"] == area)
            for area in sorted(set(r["source_area"] for r in rows))
        },
        "document_type_counts": {
            dt: sum(1 for r in rows if r["document_type"] == dt)
            for dt in sorted(set(r["document_type"] for r in rows), key=lambda x: -sum(1 for r in rows if r["document_type"] == x))
        },
    }

    return {"rows": rows, "summary": summary, "duplicates": duplicates}


def main():
    parser = argparse.ArgumentParser(description="R62: Build read-only storage manifest")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Output directory")
    args = parser.parse_args()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    print("Scanning files and building manifest...")
    result = build_manifest()

    rows = result["rows"]
    summary = result["summary"]

    # Write CSV
    csv_path = outdir / "image_storage_manifest.csv"
    fieldnames = [
        "path", "rel_path", "exists", "file_type", "sha256", "size", "mtime",
        "category", "source_area", "message_id", "local_id", "group_name",
        "document_type", "is_canonical_document", "is_general_document",
        "is_business_critical", "linked_db_table", "linked_db_id",
        "release_batch_id", "inspection_candidate_id",
        "is_already_archived_to_project", "suggested_canonical_path",
        "suggested_action", "reason",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV written: {csv_path} ({len(rows)} rows)")

    # Write JSON
    json_path = outdir / "image_storage_manifest.json"
    with open(json_path, "w") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"JSON written: {json_path}")

    # Write summary
    summary_path = outdir / "image_storage_manifest_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Summary written: {summary_path}")

    # Print summary
    print()
    print("=== Manifest Summary ===")
    print(f"  total_files:           {summary['total_files']}")
    print(f"  image_count:           {summary['image_count']}")
    print(f"  json_count:            {summary['json_count']}")
    print(f"  canonical_documents:   {summary['canonical_document_count']}")
    print(f"  business_critical:     {summary['business_critical_count']}")
    print(f"  missing_db_path:       {summary['missing_db_path_count']}")
    print(f"  duplicate_sha:         {summary['duplicate_sha_count']}")
    print(f"  duplicate_files:       {summary['duplicate_file_count']}")
    print(f"  suggested_actions:")
    for action, count in sorted(summary["suggested_action_counts"].items(), key=lambda x: -x[1]):
        print(f"    {action}: {count}")
    print(f"  source_areas:")
    for area, count in sorted(summary["source_area_counts"].items()):
        print(f"    {area}: {count}")
    print(f"  document_types (top 10):")
    for i, (dt, cnt) in enumerate(summary["document_type_counts"].items()):
        if i >= 10:
            break
        print(f"    {dt}: {cnt}")


if __name__ == "__main__":
    main()
