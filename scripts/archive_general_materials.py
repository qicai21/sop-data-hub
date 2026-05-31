#!/usr/bin/env python3
"""
R64: General materials archiver.
Archives non-business-critical images (photos, unknown) to business/general/.
Hardlinks preferred, copy fallback. No deletions.
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "sop_agent.db"
IMAGES_ROOT = Path("/Users/qicai21/Documents/bussiness-artifacts/wechat_images")
R62_MANIFEST = REPO_ROOT / "runtime" / "storage_cleanup_manifest" / "image_storage_manifest.json"
R63_RESULTS = REPO_ROOT / "runtime" / "storage_canonical_archive_r63" / "r63_canonical_archive_result.json"
OUTPUT_DIR = REPO_ROOT / "runtime" / "general_archive_r64"


def load_r62_manifest() -> list[dict]:
    with open(R62_MANIFEST) as f:
        return json.load(f)


def get_r63_archived_sources() -> set:
    """Get source paths already handled by R63."""
    if not R63_RESULTS.exists():
        return set()
    with open(R63_RESULTS) as f:
        results = json.load(f)
    return {r["source_path"] for r in results if r["status"] == "archived"}


def extract_yyyy_mm(entry: dict) -> str:
    """Extract YYYY-MM from mtime or path."""
    mtime = entry.get("mtime", "")
    if mtime and len(mtime) >= 7:
        return mtime[:7]

    # Try from path (e.g. 铁晟业务工作群/2026-05/...)
    rel = entry.get("rel_path", "")
    for part in Path(rel).parts:
        if len(part) == 7 and part[4] == "-" and part[:4].isdigit() and part[5:].isdigit():
            return part

    # Fallback
    return datetime.now(timezone.utc).strftime("%Y-%m")


def sanitize_doc_type(doc_type: str) -> str:
    """Sanitize document type for path use."""
    # Replace / and other problematic chars
    return doc_type.replace("/", "-").replace(" ", "_").strip()


def build_target_rel_path(entry: dict) -> str:
    """Build business/general/<YYYY-MM>/<doc_type>/<filename>"""
    yyyy_mm = extract_yyyy_mm(entry)
    doc_type = sanitize_doc_type(entry.get("document_type", "unknown"))
    filename = Path(entry["path"]).name
    return f"business/general/{yyyy_mm}/{doc_type}/{filename}"


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
    """Hardlink or copy source to target. Returns (method, target_full_path)."""
    target_full = images_root / target_rel
    target_full.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        return "hardlink (dry-run)", str(target_full)

    try:
        if target_full.exists():
            source_sha = compute_sha256(Path(source_path))
            target_sha = compute_sha256(target_full)
            if source_sha == target_sha:
                return "hardlink (exists)", str(target_full)
            else:
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
        shutil.copy2(source_path, str(target_full))
        return "copy", str(target_full)


def update_image_ingestion_audit(conn: sqlite3.Connection, entry: dict, target_full: str, dry_run: bool) -> bool:
    """Update project_archive_paths in image_ingestion_audit."""
    db_id = entry.get("linked_db_id", "")
    if not db_id:
        return False

    if dry_run:
        return True

    cur = conn.execute(
        "SELECT project_archive_paths FROM image_ingestion_audit WHERE id=?",
        (db_id,)
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

    current["image"] = target_full
    conn.execute(
        "UPDATE image_ingestion_audit SET project_archive_paths=? WHERE id=?",
        (json.dumps(current, ensure_ascii=False), db_id)
    )
    return True


def process_general_materials(dry_run: bool = True) -> dict:
    rows = load_r62_manifest()
    r63_sources = get_r63_archived_sources()

    # Filter: suggested_action=move, NOT business_critical canonical, NOT already R63
    target = [
        r for r in rows
        if r["suggested_action"] == "move"
        and not (r["is_canonical_document"] and r["is_business_critical"])
        and r.get("path", "") not in r63_sources
        and r["file_type"] == "image"
        and r.get("exists", False)
    ]

    conn = sqlite3.connect(str(DB_PATH))
    results = []
    stats = Counter()

    for entry in target:
        source_path = entry["path"]
        target_rel = build_target_rel_path(entry)
        method, target_full = archive_file(source_path, target_rel, IMAGES_ROOT, dry_run)
        db_updated = update_image_ingestion_audit(conn, entry, target_full, dry_run)

        result = {
            "source_path": source_path,
            "rel_path": entry.get("rel_path", ""),
            "source_exists": True,
            "document_type": entry.get("document_type", ""),
            "file_type": "image",
            "target_path": str(target_full),
            "target_rel": target_rel,
            "archive_method": method,
            "db_updated": db_updated,
            "has_db_link": bool(entry.get("linked_db_id")),
            "yyyy_mm": extract_yyyy_mm(entry),
            "status": "archived" if "hardlink" in method or "copy" in method else "error",
            "reason": f"archived to business/general via {method}",
        }
        results.append(result)
        stats["archived"] += 1

    conn.commit()
    conn.close()

    summary = {
        "r64_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "total_target": len(target),
        "archived_count": stats.get("archived", 0),
        "failed_count": stats.get("failed", 0),
        "db_updated_count": sum(1 for r in results if r["db_updated"]),
        "archive_methods": dict(Counter(r["archive_method"] for r in results)),
        "by_document_type": dict(Counter(r["document_type"] for r in results)),
        "by_yyyy_mm": dict(Counter(r["yyyy_mm"] for r in results)),
    }

    return {"results": results, "summary": summary}


def main():
    parser = argparse.ArgumentParser(description="R64: Archive general materials to business/general/")
    parser.add_argument("--apply", action="store_true", help="Actually perform archiving")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Output directory")
    args = parser.parse_args()

    dry_run = not args.apply
    mode = "DRY-RUN" if dry_run else "APPLY"

    print(f"=== R64 General Materials Archive ({mode}) ===")
    print()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    result = process_general_materials(dry_run=dry_run)
    results = result["results"]
    summary = result["summary"]

    print(f"  Target:                  {summary['total_target']}")
    print(f"  Archived:                {summary['archived_count']}")
    print(f"  DB updated:              {summary['db_updated_count']}")
    print(f"  Archive methods:")
    for method, count in sorted(summary["archive_methods"].items()):
        print(f"    {method}: {count}")
    print(f"  By document type:")
    for dt, count in sorted(summary["by_document_type"].items()):
        print(f"    {dt}: {count}")
    print(f"  By YYYY-MM:")
    for ym, count in sorted(summary["by_yyyy_mm"].items()):
        print(f"    {ym}: {count}")

    # CSV
    csv_path = outdir / "r64_general_archive_result.csv"
    fieldnames = [
        "source_path", "rel_path", "source_exists", "document_type",
        "file_type", "target_path", "target_rel", "archive_method",
        "db_updated", "has_db_link", "yyyy_mm", "status", "reason",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\nCSV: {csv_path}")

    json_path = outdir / "r64_general_archive_result.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"JSON: {json_path}")

    summary_path = outdir / "r64_general_archive_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Summary: {summary_path}")

    if dry_run:
        print("\n*** DRY-RUN complete. Run with --apply to execute. ***")


if __name__ == "__main__":
    main()
