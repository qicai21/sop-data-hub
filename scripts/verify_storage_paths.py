#!/usr/bin/env python3
"""
R65.2: Verify storage paths — check that forbidden directories do NOT exist
in wechat_images active area, and allowed directories DO exist.
"""

import os
import sys
from pathlib import Path

IMAGES_ROOT = Path("/Users/qicai21/Documents/bussiness-artifacts/wechat_images")

# ── Forbidden patterns (must NOT exist as directories or contain files) ──
FORBIDDEN_DIRS = [
    "_status", "_raw", "_previews",
    "other", "unknown", "unmatched",
    "extractions",
    "business/general",
    "出港计划通知单", "检装车通知单",
    "请车表", "手写箱号车号表", "日现场工作记录表", "耗材统计表",
    "照片-装卸现场情况", "照片-集装箱内情况和作业",
    "照片-敞车内部情况和作业", "照片-火车涂写mark", "照片-检查工人",
]

FORBIDDEN_SUBDIR_NAMES = [
    "_status", "_raw", "_previews",
    "extractions",
    "出港计划通知单", "检装车通知单",
    "请车表", "手写箱号车号表", "日现场工作记录表", "耗材统计表",
    "照片-装卸现场情况", "照片-集装箱内情况和作业",
    "照片-敞车内部情况和作业", "照片-火车涂写mark", "照片-检查工人",
    "other", "unknown", "unmatched",
]

# ── Allowed patterns ────────────────────────────────────────────────────
ALLOWED_DIRS = [
    "business/projects",
    "_quarantine",
]

# ── Allowed group-level patterns (raw image dirs: {group}/{YYYY-MM}/) ────
ALLOWED_GROUP_DIR_PATTERNS = [
    # {group}/{YYYY-MM}/images
]


def check_forbidden_root() -> list[str]:
    """Check for forbidden directories at wechat_images root."""
    violations = []
    for d in FORBIDDEN_DIRS:
        p = IMAGES_ROOT / d
        if p.exists():
            contents = [f for f in p.iterdir() if f.name != ".DS_Store"] if p.is_dir() else []
            violations.append(f"FORBIDDEN ROOT: {d}/ ({len(contents)} items)")
    return violations


def check_forbidden_inside_groups() -> list[str]:
    """Check for forbidden subdirs inside group directories."""
    violations = []
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
                violations.append(f"FORBIDDEN SUBDIR: {name}/{sub}/ ({len(contents)} items)")
    return violations


def check_allowed() -> list[str]:
    """Check that allowed directories exist."""
    results = []
    for d in ALLOWED_DIRS:
        p = IMAGES_ROOT / d
        if p.exists():
            results.append(f"OK: {d}/ exists")
        else:
            results.append(f"MISSING: {d}/ should exist")
    # Check group raw dirs have YYYY-MM subdirs
    for entry in sorted(IMAGES_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith("_") or name.startswith("business") or name == "projects":
            continue
        has_month = False
        for sub in entry.iterdir():
            if sub.is_dir() and len(sub.name) == 7 and sub.name[4] == "-":
                has_month = True
                break
        if has_month:
            results.append(f"OK: {name}/[YYYY-MM]/ raw images present")
        else:
            results.append(f"NOTE: {name}/ no YYYY-MM subdir found")
    return results


def main():
    print("=== R65.2 Storage Path Verification ===")
    print()

    forbidden_root = check_forbidden_root()
    forbidden_sub = check_forbidden_inside_groups()
    allowed = check_allowed()

    violations = forbidden_root + forbidden_sub

    print(f"  Forbidden root violations: {len(forbidden_root)}")
    for v in forbidden_root:
        print(f"    {v}")

    print(f"\n  Forbidden subdir violations: {len(forbidden_sub)}")
    for v in forbidden_sub:
        print(f"    {v}")

    print(f"\n  Allowed checks:")
    for a in allowed:
        print(f"    {a}")

    print(f"\n  TOTAL VIOLATIONS: {len(violations)}")

    # Self-test runner paths
    print()
    print("=== Runner Path Self-Test ===")
    test_runner_paths()

    if violations:
        print(f"\n*** FAIL: {len(violations)} forbidden paths detected ***")
        return 1
    else:
        print("\n*** PASS: No forbidden paths in active area ***")
        return 0


def test_runner_paths():
    """Simulate runner path generation without calling OCR/VLM."""
    repo_root = Path(__file__).resolve().parents[1]

    # Simulate: image from 铁晟业务工作群, category=检装车通知单
    group = "铁晟业务工作群"
    category = "检装车通知单"
    yyyy_mm = "2026-05"
    img_stem = "test_1234"

    # ── Check: NO {group}/{category}/ ──
    old_classified = IMAGES_ROOT / group / category
    exists_old = old_classified.exists()
    print(f"  {group}/{category}/ : {'EXISTS (BAD)' if exists_old else 'OK (不存在)'}")

    # ── Check: NO {group}/extractions/ ──
    old_ext = IMAGES_ROOT / group / "extractions"
    exists_ext = old_ext.exists()
    print(f"  {group}/extractions/ : {'EXISTS (BAD)' if exists_ext else 'OK (不存在)'}")

    # ── Check: NO _status/ ──
    old_status = IMAGES_ROOT / "_status"
    exists_status = old_status.exists()
    print(f"  _status/ : {'EXISTS (BAD)' if exists_status else 'OK (不存在)'}")

    # ── Check: NO business/general/ ──
    old_gen = IMAGES_ROOT / "business" / "general"
    exists_gen = old_gen.exists()
    print(f"  business/general/ : {'EXISTS (BAD)' if exists_gen else 'OK (不存在)'}")

    # ── Check: runtime/extractions/ path would be correct ──
    runtime_ext = repo_root / "runtime" / "extractions" / group / yyyy_mm / category
    print(f"  runtime/extractions/{group}/{yyyy_mm}/{category}/ : {runtime_ext}")
    print(f"    (would be created on first extraction)")

    # ── Check: runtime/image_status/ path would be correct ──
    runtime_status = repo_root / "runtime" / "image_status" / group / yyyy_mm
    print(f"  runtime/image_status/{group}/{yyyy_mm}/ : {runtime_status}")
    print(f"    (would be created on first status write)")

    # ── Check: business/projects/ exists ──
    biz_proj = IMAGES_ROOT / "business" / "projects"
    print(f"  business/projects/ : {'OK (exists)' if biz_proj.exists() else 'MISSING'}")


if __name__ == "__main__":
    sys.exit(main())
