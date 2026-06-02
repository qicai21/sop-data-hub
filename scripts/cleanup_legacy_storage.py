#!/usr/bin/env python3
"""
R65: Legacy storage cleanup — moves obsolete directories/files to quarantine.
NO deletions. Uses shutil.move (rename) to _quarantine/r65_20260531/.
Protects business/projects, business/general, _quarantine.
"""

from sop_hub.utils.time import now_iso_beijing
import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IMAGES_ROOT = Path("/Users/qicai21/Documents/bussiness-artifacts/wechat_images")
QUARANTINE_ROOT = IMAGES_ROOT / "_quarantine" / "r65_20260531"
R62_MANIFEST = REPO_ROOT / "runtime" / "storage_cleanup_manifest" / "image_storage_manifest.json"
R63_RESULTS = REPO_ROOT / "runtime" / "storage_canonical_archive_r63" / "r63_canonical_archive_result.json"
R64_RESULTS = REPO_ROOT / "runtime" / "general_archive_r64" / "r64_general_archive_result.json"
OUTPUT_DIR = REPO_ROOT / "runtime" / "storage_cleanup_r65"

# ── Protected paths (won't be touched) ───────────────────────────────────
PROTECTED_PREFIXES = [
    "business/projects/",
    "business/general/",
    "_quarantine/",
    "projects/",
]

# ── Obsolete directory patterns (relative to wechat_images root) ─────────
# These entire directory trees are candidates for quarantine.
OBSOLETE_DIRS = [
    # Internal tracking
    "_status", "_raw", "_previews",
    # Legacy
    "other", "unknown",
    # Extractions
    "extractions",
    # Unmatched
    "unmatched",
]

# Old classified dirs at root level and inside groups
OBSOLETE_CLASSIFIED_NAMES = [
    "出港计划通知单", "检装车通知单",
    "照片-装卸现场情况", "照片-集装箱内情况和作业",
    "照片-敞车内部情况和作业", "照片-火车涂写mark",
    "照片-检查工人",
    "请车表", "手写箱号车号表", "日现场工作记录表", "耗材统计表",
    "手写记录",
]


def load_r62_manifest() -> list[dict]:
    with open(R62_MANIFEST) as f:
        return json.load(f)


def load_archived_sources() -> set:
    """Get all source paths archived in R63 or R64."""
    sources = set()
    for path in [R63_RESULTS, R64_RESULTS]:
        if path.exists():
            with open(path) as f:
                for r in json.load(f):
                    if r.get("status") in ("archived", "kept"):
                        sources.add(r["source_path"])
    return sources


def compute_sha256(filepath: Path) -> str:
    try:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def is_protected(rel_path: str) -> bool:
    """Check if a relative path is in protected area."""
    for prefix in PROTECTED_PREFIXES:
        if rel_path.startswith(prefix):
            return True
    return False


def has_archive_hardlink(source_path: str, images_root: Path) -> bool:
    """
    Check if source file has a hardlink in business/ (i.e., link count > 1
    AND the other link is under business/).
    """
    try:
        st = os.stat(source_path)
        if st.st_nlink <= 1:
            return False
        # Quick check: same inode exists under business/
        ino = st.st_ino
        for biz_dir in ["business/projects", "business/general"]:
            biz_path = images_root / biz_dir
            if not biz_path.exists():
                continue
            for root, dirs, files in os.walk(biz_path):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        if os.stat(fp).st_ino == ino and fp != source_path:
                            return True
                    except OSError:
                        continue
                # Limit search depth
                break  # Only check first level for speed; we rely on same-inode logic
        return False
    except OSError:
        return False


def should_skip_review(full_path: str, manifest_rows: list[dict]) -> bool:
    """Check if file is marked 'review' in R62 manifest and not archived."""
    for r in manifest_rows:
        if r.get("path") == full_path or r.get("rel_path", "") and str(IMAGES_ROOT / r["rel_path"]) == full_path:
            if r["suggested_action"] == "review":
                return True
            return False
    return False


def is_in_obsolete_dir(rel_path: str) -> tuple[bool, str]:
    """
    Check if path is inside an obsolete directory.
    Returns (is_obsolete, reason).
    """
    parts = Path(rel_path).parts

    for od in OBSOLETE_DIRS:
        if od in parts:
            return True, f"inside obsolete dir: {od}"

    for cn in OBSOLETE_CLASSIFIED_NAMES:
        if cn in parts:
            return True, f"inside old classified dir: {cn}"

    return False, ""


def move_to_quarantine(full_path: str, quarantine_root: Path, dry_run: bool) -> tuple[bool, str, str]:
    """
    Move file to quarantine preserving relative structure.
    Returns (success, quarantine_path, error).
    """
    rel = str(Path(full_path).relative_to(IMAGES_ROOT))
    target = quarantine_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        return True, str(target), ""

    try:
        if target.exists():
            # Avoid overwriting
            stem = target.stem
            suffix = target.suffix
            counter = 1
            while True:
                alt = target.parent / f"{stem}_{counter}{suffix}"
                if not alt.exists():
                    target = alt
                    break
                counter += 1
        shutil.move(full_path, str(target))
        return True, str(target), ""
    except Exception as e:
        return False, str(target), str(e)


def collect_obsolete_files(manifest_rows: list[dict], archived_sources: set) -> list[dict]:
    """
    Collect all files that should be moved to quarantine.
    Returns list of dicts with: full_path, rel_path, reason, source_area.
    """
    candidates = []

    for r in manifest_rows:
        full_path = r.get("path", "")
        if not full_path or not os.path.exists(full_path):
            continue

        rel_path = r.get("rel_path", "")
        source_area = r.get("source_area", "")
        suggested_action = r.get("suggested_action", "")

        # Skip protected
        if is_protected(rel_path):
            continue

        # Determine if obsolete
        is_obs, obs_reason = is_in_obsolete_dir(rel_path)

        # If archived AND in obsolete dir: still move to quarantine
        # (data is preserved via hardlink in business/)
        if full_path in archived_sources and is_obs:
            candidates.append({
                "full_path": full_path,
                "rel_path": rel_path,
                "reason": f"{obs_reason} (archived, data preserved via hardlink in business/)",
                "source_area": source_area,
                "suggested_action": suggested_action,
                "sha256": r.get("sha256", ""),
            })
            continue

        # Skip already archived (but NOT in obsolete dir)
        if full_path in archived_sources:
            continue

        # trash entries are always candidates
        if suggested_action == "trash":
            reason_text = obs_reason or "marked trash in R62"
            candidates.append({
                "full_path": full_path,
                "rel_path": rel_path,
                "reason": reason_text,
                "source_area": source_area,
                "suggested_action": suggested_action,
                "sha256": r.get("sha256", ""),
            })
            continue

        # review entries: skip
        if suggested_action == "review":
            continue

        # In obsolete dir
        if is_obs:
            # Check if has archive hardlink
            if has_archive_hardlink(full_path, IMAGES_ROOT):
                candidates.append({
                    "full_path": full_path,
                    "rel_path": rel_path,
                    "reason": f"{obs_reason} (data preserved via hardlink in business/)",
                    "source_area": source_area,
                    "suggested_action": suggested_action,
                    "sha256": r.get("sha256", ""),
                })
            elif suggested_action in ("move", "keep"):
                # move/keep but no archive hardlink — check if business has copy
                candidates.append({
                    "full_path": full_path,
                    "rel_path": rel_path,
                    "reason": f"{obs_reason} (no hardlink detected, but action={suggested_action})",
                    "source_area": source_area,
                    "suggested_action": suggested_action,
                    "sha256": r.get("sha256", ""),
                })

    return candidates


def collect_extra_files_not_in_manifest(quarantine_root: Path) -> list[dict]:
    """
    Find files on disk that exist in obsolete directories but weren't in R62 manifest.
    E.g., .json status files, legacy files not tracked.
    """
    extras = []
    for root, dirs, files in os.walk(IMAGES_ROOT):
        # Skip protected and quarantine dirs
        rel_root = str(Path(root).relative_to(IMAGES_ROOT))
        if is_protected(rel_root):
            dirs[:] = [d for d in dirs if is_protected(os.path.join(rel_root, d))]
            continue
        if rel_root.startswith("_quarantine"):
            dirs[:] = []
            continue

        is_obs, _ = is_in_obsolete_dir(rel_root)
        if not is_obs:
            # Only descend into obsolete dirs for extras
            dirs[:] = []
            continue

        for f in files:
            if f == ".DS_Store":
                continue
            fp = os.path.join(root, f)
            rel = str(Path(fp).relative_to(IMAGES_ROOT))
            extras.append({
                "full_path": fp,
                "rel_path": rel,
                "reason": "extra file in obsolete dir (not in R62 manifest)",
                "source_area": "legacy",
                "suggested_action": "trash",
                "sha256": compute_sha256(Path(fp)),
            })

    return extras


def cleanup_empty_dirs(quarantine_root: Path, dry_run: bool) -> int:
    """Remove empty directories in obsolete areas. Returns count."""
    count = 0
    # Walk bottom-up to remove empty dirs
    for root, dirs, files in os.walk(IMAGES_ROOT, topdown=False):
        rel_root = str(Path(root).relative_to(IMAGES_ROOT))

        # Skip protected
        if is_protected(rel_root) or rel_root.startswith("_quarantine"):
            continue

        # Skip root itself
        if rel_root == ".":
            continue

        # Check if empty (excluding .DS_Store)
        contents = [f for f in os.listdir(root) if f != ".DS_Store"]
        if not contents:
            if not dry_run:
                try:
                    os.rmdir(root)
                    count += 1
                except OSError:
                    pass
            else:
                count += 1

    return count


def generate_active_path_check() -> str:
    """Generate active path check report."""
    lines = []
    lines.append("=== R65 Active Path Check ===")
    lines.append(f"Generated: {now_iso_beijing()}")
    lines.append("")

    # Check obsolete patterns
    checks = [
        ("_status directories", "_status"),
        ("_raw directories", "_raw"),
        ("_previews directories", "_previews"),
        ("other/", "other"),
        ("unknown/", "unknown"),
        ("extractions/", "extractions"),
        ("unmatched/", "unmatched"),
        ("出港计划通知单/ (old classified)", "出港计划通知单"),
        ("检装车通知单/ (old classified)", "检装车通知单"),
        ("照片-装卸现场情况/", "照片-装卸现场情况"),
        ("照片-集装箱内情况和作业/", "照片-集装箱内情况和作业"),
        ("照片-敞车内部情况和作业/", "照片-敞车内部情况和作业"),
        ("照片-火车涂写mark/", "照片-火车涂写mark"),
        ("照片-检查工人/", "照片-检查工人"),
        ("请车表/", "请车表"),
        ("手写箱号车号表/", "手写箱号车号表"),
        ("日现场工作记录表/", "日现场工作记录表"),
        ("耗材统计表/", "耗材统计表"),
    ]

    for label, check_dir in checks:
        p = IMAGES_ROOT / check_dir
        if p.exists():
            # Check if it's empty
            contents = [f for f in os.listdir(p) if f != ".DS_Store"]
            if contents:
                lines.append(f"  STILL EXISTS: {check_dir}/ ({len(contents)} items)")
            else:
                lines.append(f"  EMPTY: {check_dir}/")
        else:
            lines.append(f"  CLEAN: {check_dir}/")

    # Check protected areas
    lines.append("")
    for check_dir in ["business/projects", "business/general", "_quarantine/r65_20260531"]:
        p = IMAGES_ROOT / check_dir
        if p.exists():
            lines.append(f"  EXISTS (protected): {check_dir}/")
        else:
            lines.append(f"  MISSING: {check_dir}/")

    return "\n".join(lines)


def generate_quarantine_paths(quarantine_root: Path) -> str:
    """List all files in quarantine."""
    if not quarantine_root.exists():
        return "Quarantine directory does not exist."
    lines = []
    for root, dirs, files in os.walk(quarantine_root):
        for f in sorted(files):
            fp = os.path.join(root, f)
            rel = str(Path(fp).relative_to(IMAGES_ROOT))
            lines.append(rel)
    return "\n".join(sorted(lines))


def generate_obsolete_paths(manifest_rows: list[dict]) -> str:
    """List obsolete paths that should no longer exist."""
    lines = []
    seen_dirs = set()
    for r in manifest_rows:
        rel = r.get("rel_path", "")
        if is_protected(rel):
            continue
        is_obs, reason = is_in_obsolete_dir(rel)
        if is_obs:
            dir_path = str(Path(rel).parent)
            if dir_path not in seen_dirs:
                seen_dirs.add(dir_path)
                lines.append(f"{dir_path}/  ({reason})")
    return "\n".join(sorted(lines))


def main():
    parser = argparse.ArgumentParser(description="R65: Cleanup legacy storage to quarantine")
    parser.add_argument("--apply", action="store_true", help="Actually move files (default: dry-run)")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Output directory")
    args = parser.parse_args()

    dry_run = not args.apply
    mode = "DRY-RUN" if dry_run else "APPLY"

    print(f"=== R65 Legacy Storage Cleanup ({mode}) ===")
    print(f"Quarantine: {QUARANTINE_ROOT}")
    print()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    manifest_rows = load_r62_manifest()
    archived_sources = load_archived_sources()

    # Collect candidates
    candidates = collect_obsolete_files(manifest_rows, archived_sources)
    print(f"  Candidates from manifest: {len(candidates)}")
    extras = collect_extra_files_not_in_manifest(QUARANTINE_ROOT)
    print(f"  Extra files (not in manifest): {len(extras)}")

    all_files = candidates + extras

    # Deduplicate by full_path
    seen = set()
    unique = []
    for f in all_files:
        if f["full_path"] not in seen:
            seen.add(f["full_path"])
            unique.append(f)

    print(f"  Total unique files to move: {len(unique)}")

    # Execute moves
    results = []
    stats = Counter()
    stats["dry_run_target_count"] = len(unique)

    for item in unique:
        fp = item["full_path"]
        sha_before = item.get("sha256", "") or compute_sha256(Path(fp))

        success, qpath, error = move_to_quarantine(fp, QUARANTINE_ROOT, dry_run)

        result = {
            "source_path": fp,
            "rel_path": item["rel_path"],
            "quarantine_path": qpath,
            "source_area": item["source_area"],
            "suggested_action": item["suggested_action"],
            "sha256_before": sha_before,
            "moved": success,
            "reason": item["reason"],
            "error": error,
        }

        if success:
            # Verify quarantine (only in apply mode)
            if not dry_run and os.path.exists(qpath):
                sha_after = compute_sha256(Path(qpath))
                if sha_before and sha_after and sha_before != sha_after:
                    result["sha256_after"] = sha_after
                    result["sha_mismatch"] = True
                else:
                    result["sha256_after"] = sha_after
                    result["sha_mismatch"] = False

            # Verify source removed
            if not dry_run:
                result["source_exists_after"] = os.path.exists(fp)
            stats["moved_count"] += 1
        else:
            stats["failed_count"] += 1

        results.append(result)

    # Cleanup empty dirs
    empty_count = cleanup_empty_dirs(QUARANTINE_ROOT, dry_run)
    stats["empty_dirs_removed_count"] = empty_count
    print(f"  Empty dirs removed: {empty_count}")

    # Generate reports
    # By source area
    by_area = Counter(r["source_area"] for r in results if r["moved"])
    by_reason = Counter(r["reason"] for r in results if r["moved"])

    # Check remaining obsolete paths
    active_path_check = generate_active_path_check()

    # Count remaining obsolete paths
    remaining = 0
    for line in active_path_check.split("\n"):
        if "STILL EXISTS" in line or "EMPTY" in line:
            remaining += 1

    summary = {
        "r65_version": 1,
        "generated_at": now_iso_beijing(),
        "dry_run": dry_run,
        "quarantine_root": str(QUARANTINE_ROOT),
        "dry_run_target_count": stats["dry_run_target_count"],
        "moved_count": stats.get("moved_count", 0),
        "failed_count": stats.get("failed_count", 0),
        "skipped_review_count": 0,  # We don't track this separately
        "skipped_protected_count": 0,
        "empty_dirs_removed_count": stats["empty_dirs_removed_count"],
        "by_source_area": dict(by_area),
        "by_reason": dict(by_reason),
        "active_obsolete_path_remaining_count": remaining,
    }

    print(f"\n  Moved:     {summary['moved_count']}")
    print(f"  Failed:    {summary['failed_count']}")
    print(f"  By area:   {dict(by_area)}")

    # Write CSV
    csv_path = outdir / "r65_cleanup_result.csv"
    fieldnames = [
        "source_path", "rel_path", "quarantine_path", "source_area",
        "suggested_action", "sha256_before", "sha256_after", "sha_mismatch",
        "moved", "source_exists_after", "reason", "error",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"\nCSV: {csv_path}")

    json_path = outdir / "r65_cleanup_result.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"JSON: {json_path}")

    summary_path = outdir / "r65_cleanup_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Summary: {summary_path}")

    # Active path check
    check_path = outdir / "r65_active_path_check.txt"
    with open(check_path, "w") as f:
        f.write(active_path_check)
    print(f"Active path check: {check_path}")

    # Quarantine paths
    qpaths = generate_quarantine_paths(QUARANTINE_ROOT)
    qp_path = outdir / "r65_quarantine_paths.txt"
    with open(qp_path, "w") as f:
        f.write(qpaths)
    print(f"Quarantine paths: {qp_path}")

    # Obsolete paths
    obs = generate_obsolete_paths(manifest_rows)
    obs_path = outdir / "r65_obsolete_paths.txt"
    with open(obs_path, "w") as f:
        f.write(obs)
    print(f"Obsolete paths: {obs_path}")

    print(f"\n{active_path_check}")

    if dry_run:
        print("\n*** DRY-RUN complete. Run with --apply to execute. ***")


if __name__ == "__main__":
    main()
