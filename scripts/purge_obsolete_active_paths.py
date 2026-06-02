#!/usr/bin/env python3
"""
R65.1: Purge ALL remaining files & empty dirs from obsolete active paths.
Moves everything to _quarantine/r65_20260531_review/.
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

IMAGES_ROOT = Path("/Users/qicai21/Documents/bussiness-artifacts/wechat_images")
QUARANTINE_ROOT = IMAGES_ROOT / "_quarantine" / "r65_20260531_review"
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "runtime" / "storage_cleanup_r65_1"

# ── Obsolete dir names (at root AND inside group dirs) ──────────────────
OBSOLETE_DIR_NAMES = [
    "_status", "_raw", "_previews", "other", "unknown", "unmatched",
    "extractions", "出港计划通知单", "检装车通知单", "请车表",
    "手写箱号车号表", "日现场工作记录表", "耗材统计表",
    "照片-装卸现场情况", "照片-集装箱内情况和作业",
    "照片-敞车内部情况和作业", "照片-火车涂写mark", "照片-检查工人",
]

# ── Protected prefixes ──────────────────────────────────────────────────
PROTECTED = ["business/projects/", "business/general/", "_quarantine/"]


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
    for p in PROTECTED:
        if rel_path.startswith(p):
            return True
    return False


def collect_all_obsolete_files(images_root: Path) -> list[dict]:
    """Find ALL files (not .DS_Store) inside obsolete directories."""
    files = []

    for root, dirs, filenames in os.walk(images_root):
        rel_root = str(Path(root).relative_to(images_root))

        # Skip protected trees
        if is_protected(rel_root):
            dirs[:] = []
            continue

        # At root level, only descend into directories (filter out files)
        if rel_root == ".":
            # Allow descending into all non-protected subdirs
            dirs[:] = [d for d in dirs
                       if not is_protected(d)
                       and not d.startswith("business")
                       and d != "projects"]
            continue

        # Only process files inside obsolete dirs
        parts = rel_root.split("/")
        is_obs = any(p in OBSOLETE_DIR_NAMES for p in parts)
        if not is_obs:
            # Keep only subdirs that could lead to obsolete content
            dirs[:] = [d for d in dirs if d in OBSOLETE_DIR_NAMES
                       or any(od in d for od in OBSOLETE_DIR_NAMES)]
            continue

        for f in sorted(filenames):
            if f == ".DS_Store":
                continue
            fp = os.path.join(root, f)
            files.append({
                "full_path": fp,
                "rel_path": str(Path(fp).relative_to(images_root)),
                "sha256": compute_sha256(Path(fp)),
            })

    return files


def move_to_quarantine(full_path: str, quarantine_root: Path, dry_run: bool) -> tuple[bool, str, str]:
    rel = str(Path(full_path).relative_to(IMAGES_ROOT))
    target = quarantine_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        return True, str(target), ""

    try:
        if target.exists():
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


def count_obsolete_active_paths(images_root: Path) -> int:
    """Count how many obsolete path patterns still exist."""
    count = 0
    for pat in OBSOLETE_DIR_NAMES:
        p = images_root / pat
        if p.exists():
            count += 1
    # Also check group-level
    for d in os.listdir(images_root):
        dp = images_root / d
        if not dp.is_dir() or d.startswith("_") or d.startswith("business"):
            continue
        for pat in OBSOLETE_DIR_NAMES:
            if (dp / pat).exists():
                count += 1
    return count


def cleanup_empty_obsolete_dirs(images_root: Path, dry_run: bool) -> int:
    """Recursively remove empty obsolete directories. Returns count."""
    count = 0
    for root, dirs, files in os.walk(images_root, topdown=False):
        rel_root = str(Path(root).relative_to(images_root))
        if is_protected(rel_root):
            continue
        if rel_root == ".":
            continue

        parts = rel_root.split("/")
        is_obs = any(p in OBSOLETE_DIR_NAMES for p in parts)
        if not is_obs:
            continue

        contents = [x for x in os.listdir(root) if x != ".DS_Store"]
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


def generate_active_path_check(images_root: Path) -> tuple[str, int]:
    """Generate report and return (text, remaining_obsolete_count)."""
    lines = []
    lines.append("=== R65.1 Active Path Check ===")
    lines.append(f"Generated: {now_iso_beijing()}")
    lines.append("")

    checks = [
        ("_status/", "_status"),
        ("_raw/", "_raw"),
        ("_previews/", "_previews"),
        ("other/", "other"),
        ("unknown/", "unknown"),
        ("unmatched/", "unmatched"),
        ("extractions/", "extractions"),
        ("出港计划通知单/", "出港计划通知单"),
        ("检装车通知单/", "检装车通知单"),
        ("请车表/", "请车表"),
        ("手写箱号车号表/", "手写箱号车号表"),
        ("日现场工作记录表/", "日现场工作记录表"),
        ("耗材统计表/", "耗材统计表"),
        ("照片-装卸现场情况/", "照片-装卸现场情况"),
        ("照片-集装箱内情况和作业/", "照片-集装箱内情况和作业"),
        ("照片-敞车内部情况和作业/", "照片-敞车内部情况和作业"),
        ("照片-火车涂写mark/", "照片-火车涂写mark"),
        ("照片-检查工人/", "照片-检查工人"),
    ]

    remaining = 0
    for label, d in checks:
        p = images_root / d
        if p.exists():
            contents = [f for f in p.iterdir() if f.name != ".DS_Store"]
            if contents:
                lines.append(f"  STILL EXISTS: {d}/ ({len(contents)} items)")
                remaining += 1
            else:
                lines.append(f"  EMPTY: {d}/")
                remaining += 1
        else:
            lines.append(f"  CLEAN: {d}/")

    # Group-level
    lines.append("")
    lines.append("--- Group-level obsolete dirs ---")
    for gd in sorted(os.listdir(images_root)):
        gp = images_root / gd
        if not gp.is_dir() or gd.startswith("_") or gd.startswith("business") or gd.startswith("projects"):
            continue
        for dname in OBSOLETE_DIR_NAMES:
            tp = gp / dname
            if tp.exists():
                contents = [f for f in tp.iterdir() if f.name != ".DS_Store"]
                if contents:
                    lines.append(f"  STILL EXISTS: {gd}/{dname}/ ({len(contents)} items)")
                    remaining += 1
                else:
                    lines.append(f"  EMPTY: {gd}/{dname}/")
                    # Empty dir not counted
                    pass

    lines.append("")
    for d in ["business/projects", "business/general", "_quarantine/r65_20260531", "_quarantine/r65_20260531_review"]:
        p = images_root / d
        if p.exists():
            import subprocess
            r = subprocess.run(["find", str(p), "-type", "f", "-not", "-name", ".DS_Store"], capture_output=True, text=True)
            count = len([l for l in r.stdout.strip().split("\n") if l])
            lines.append(f"  EXISTS (protected): {d}/ ({count} files)")
        else:
            lines.append(f"  MISSING: {d}/")

    return "\n".join(lines), remaining


def main():
    parser = argparse.ArgumentParser(description="R65.1: Purge ALL remaining files from obsolete active paths")
    parser.add_argument("--apply", action="store_true", help="Actually move files")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Output directory")
    args = parser.parse_args()

    dry_run = not args.apply
    mode = "DRY-RUN" if dry_run else "APPLY"

    print(f"=== R65.1 Purge Obsolete Active Paths ({mode}) ===")
    print(f"Quarantine: {QUARANTINE_ROOT}")
    print()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Count before
    before_count = count_obsolete_active_paths(IMAGES_ROOT)
    print(f"  Obsolete active paths BEFORE: {before_count}")

    # Collect all files
    all_files = collect_all_obsolete_files(IMAGES_ROOT)
    print(f"  Files to move: {len(all_files)}")

    # Stats
    stats = Counter()
    results = []

    # Move
    for item in all_files:
        fp = item["full_path"]
        sha_before = item["sha256"]

        success, qpath, error = move_to_quarantine(fp, QUARANTINE_ROOT, dry_run)

        result = {
            "source_path": fp,
            "rel_path": item["rel_path"],
            "quarantine_path": qpath,
            "sha256_before": sha_before,
            "moved": success,
            "error": error,
        }

        if success:
            if not dry_run and os.path.exists(qpath):
                sha_after = compute_sha256(Path(qpath))
                result["sha256_after"] = sha_after
                result["sha_mismatch"] = sha_before and sha_after and sha_before != sha_after
                result["source_exists_after"] = os.path.exists(fp)
            else:
                result["sha256_after"] = ""
                result["sha_mismatch"] = False
                result["source_exists_after"] = False
            stats["moved"] += 1
        else:
            stats["failed"] += 1

        results.append(result)

    # Cleanup empty dirs
    empty_count = cleanup_empty_obsolete_dirs(IMAGES_ROOT, dry_run)
    stats["empty_dirs_removed"] = empty_count
    print(f"  Empty dirs removed: {empty_count}")

    # Count after
    after_count = count_obsolete_active_paths(IMAGES_ROOT)

    # Protected area counts
    import subprocess
    biz_proj = len([l for l in subprocess.run(["find", str(IMAGES_ROOT / "business/projects"), "-type", "f", "-not", "-name", ".DS_Store"], capture_output=True, text=True).stdout.strip().split("\n") if l])
    biz_gen = len([l for l in subprocess.run(["find", str(IMAGES_ROOT / "business/general"), "-type", "f", "-not", "-name", ".DS_Store"], capture_output=True, text=True).stdout.strip().split("\n") if l])

    print(f"\n  Moved:       {stats['moved']}")
    print(f"  Failed:      {stats['failed']}")
    print(f"  Empty dirs:  {stats['empty_dirs_removed']}")
    print(f"  Obs BEFORE:  {before_count}")
    print(f"  Obs AFTER:   {after_count}")

    # Active path check
    check_text, _ = generate_active_path_check(IMAGES_ROOT)
    print(f"\n{check_text}")

    # Summary
    sha_mismatches = sum(1 for r in results if r.get("sha_mismatch"))
    summary = {
        "r65_1_version": 1,
        "generated_at": now_iso_beijing(),
        "dry_run": dry_run,
        "quarantine_review_root": str(QUARANTINE_ROOT),
        "review_moved_count": stats["moved"],
        "empty_dirs_removed_count": stats["empty_dirs_removed"],
        "failed_count": stats["failed"],
        "sha_mismatch_count": sha_mismatches,
        "obsolete_active_paths_before": before_count,
        "obsolete_active_paths_after": after_count,
        "business_projects_file_count": biz_proj,
        "business_general_file_count": biz_gen,
    }

    # Write files
    csv_path = outdir / "r65_1_purge_result.csv"
    fieldnames = [
        "source_path", "rel_path", "quarantine_path", "sha256_before",
        "sha256_after", "sha_mismatch", "moved", "source_exists_after", "error",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"\nCSV: {csv_path}")

    json_path = outdir / "r65_1_purge_result.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"JSON: {json_path}")

    summary_path = outdir / "r65_1_purge_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Summary: {summary_path}")

    check_path = outdir / "r65_1_active_path_check.txt"
    with open(check_path, "w") as f:
        f.write(check_text)
    print(f"Active check: {check_path}")

    # Quarantine paths
    if QUARANTINE_ROOT.exists():
        import subprocess
        r = subprocess.run(["find", str(QUARANTINE_ROOT), "-type", "f", "-not", "-name", ".DS_Store"], capture_output=True, text=True)
        qpaths = "\n".join(sorted(
            str(Path(l).relative_to(IMAGES_ROOT))
            for l in r.stdout.strip().split("\n") if l
        ))
        qp_path = outdir / "r65_1_quarantine_review_paths.txt"
        with open(qp_path, "w") as f:
            f.write(qpaths)
        print(f"Quarantine list: {qp_path}")

    if dry_run:
        print("\n*** DRY-RUN complete. Run with --apply to execute. ***")


if __name__ == "__main__":
    main()
