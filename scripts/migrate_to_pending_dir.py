#!/usr/bin/env python3
"""R77: 把现有"待落实"图片归到统一 _pending/ 区。

把两个分散的"系统不能完全确定的图"目录合并到一个 wechat_images/_pending/
<YYYY-MM>/{images,json}/,并生成 index.json 作为人侧浏览的入口。

合并的来源:
  A) runtime/unmatched/<YYYY-MM>/{images,json}/*
     (SOP 完全没识别项目;runner.py 的旧 else 分支)
  B) {artifact_root}/business/projects/<project>/unknown/unknown/lotunknown/
     {images,json}/<date>/*
     (SOP 识别了项目但 dest/ship/lot 缺;runner.py 的旧 if 分支落进 unknown)

同步:
  - 把 image_ingestion_audit 表里指向旧路径的 raw/classified/extraction
    路径字段改写到新位置。
  - 清掉迁完后空的旧目录。

幂等:重复跑不会重复迁(目标文件存在则跳过)。
"""
from __future__ import annotations

from sop_hub.utils.time import now_iso_beijing as _now_iso_beijing

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = Path("/Users/qicai21/Documents/bussiness-artifacts/wechat_images")
RUNTIME_UNMATCHED = REPO_ROOT / "runtime" / "unmatched"
BUSINESS_PROJECTS = ARTIFACT_ROOT / "business" / "projects"
PENDING_ROOT = ARTIFACT_ROOT / "_pending"
DEFAULT_DB = REPO_ROOT / "data" / "sop_agent.db"


def _month_from_date(date_text: str) -> str:
    """ '2026-06-01' / '20260601' / '2026-06' → '2026-06'."""
    s = (date_text or "").strip().replace("/", "-")
    if len(s) >= 7 and s[4] == "-":
        return s[:7]
    if len(s) >= 6 and s[:6].isdigit():
        return f"{s[:4]}-{s[4:6]}"
    return datetime.now().strftime("%Y-%m")


def _read_extraction_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ensure_dirs(month: str):
    (PENDING_ROOT / month / "images").mkdir(parents=True, exist_ok=True)
    (PENDING_ROOT / month / "json").mkdir(parents=True, exist_ok=True)


def _move_pair(image_src: Path, json_src: Path | None, month: str,
               apply: bool) -> tuple[Path, Path | None]:
    """Move image and (optional) json to _pending/<month>/.
    Returns (image_dest, json_dest). Idempotent: skip if dest already exists.
    """
    _ensure_dirs(month)
    image_dest = PENDING_ROOT / month / "images" / image_src.name
    json_dest = None
    if json_src and json_src.exists():
        json_dest = PENDING_ROOT / month / "json" / json_src.name

    if not apply:
        return image_dest, json_dest

    if image_src.exists() and not image_dest.exists():
        shutil.move(str(image_src), str(image_dest))
    elif image_src.exists() and image_dest.exists():
        # Same content; remove source
        try:
            image_src.unlink()
        except OSError:
            pass

    if json_src and json_src.exists():
        if not json_dest.exists():
            shutil.move(str(json_src), str(json_dest))
        else:
            try:
                json_src.unlink()
            except OSError:
                pass

    return image_dest, json_dest


def _build_index_entry(image_name: str, json_name: str | None,
                       payload: dict, reason: str,
                       project_hint: str = "") -> dict:
    biz = payload.get("business_info") if isinstance(payload.get("business_info"), dict) else {}
    cargo = payload.get("cargo_info") if isinstance(payload.get("cargo_info"), dict) else {}
    proj = (payload.get("project") or project_hint or "").strip()
    ship = (biz.get("船名") or biz.get("进口船名") or "").strip()
    classification = (payload.get("title") or "").strip()

    def _empty(v): return not str(v).strip() or str(v).strip().lower() in {"unknown", "lotunknown"}

    missing = []
    if _empty(proj):    missing.append("project")
    if _empty(ship):    missing.append("ship")
    missing.append("destination")  # 来源历史数据,destination 普遍缺
    missing.append("lot")

    return {
        "image": image_name,
        "json": json_name or "",
        "classification": classification,
        "extracted_project": "" if _empty(proj) else proj,
        "extracted_destination": "",
        "extracted_ship": "" if _empty(ship) else ship,
        "extracted_lot": "",
        "extracted_cargo": cargo.get("货物名称", ""),
        "missing": missing,
        "reason": reason,
        "recorded_at": _now_iso_beijing(),
    }


def _write_index(month: str, entries: list[dict], apply: bool):
    if not apply:
        return
    idx_path = PENDING_ROOT / month / "index.json"
    existing: list[dict] = []
    if idx_path.exists():
        try:
            existing = json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    # Dedup by image name
    seen = {e.get("image") for e in entries}
    merged = entries + [e for e in existing if e.get("image") not in seen]
    tmp = idx_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(idx_path)


# ── Source A: runtime/unmatched/<YYYY-MM>/ ─────────────────────────────

def migrate_unmatched(apply: bool) -> list[dict]:
    moved: list[dict] = []
    if not RUNTIME_UNMATCHED.exists():
        return moved
    for month_dir in sorted(RUNTIME_UNMATCHED.iterdir()):
        if not month_dir.is_dir():
            continue
        month = month_dir.name  # e.g. 2026-06
        images_dir = month_dir / "images"
        json_dir = month_dir / "json"
        if not images_dir.exists():
            continue
        entries_to_write: list[dict] = []
        for img in sorted(images_dir.iterdir()):
            if not img.is_file() or img.name.startswith("."):
                continue
            stem = img.stem
            jsn = json_dir / f"{stem}_result.json"
            payload = _read_extraction_json(jsn) if jsn.exists() else {}
            image_dest, json_dest = _move_pair(img, jsn if jsn.exists() else None,
                                                month, apply)
            entries_to_write.append(_build_index_entry(
                image_dest.name,
                json_dest.name if json_dest else None,
                payload,
                reason="no_sop_project_match (migrated from runtime/unmatched)",
            ))
            moved.append({"src": "runtime/unmatched", "image": img.name, "month": month})
        if entries_to_write:
            _write_index(month, entries_to_write, apply)
    return moved


# ── Source B: business/projects/*/unknown/unknown/lotunknown/{images,json}/<date>/ ──

def migrate_unknown_unknown_lotunknown(apply: bool) -> list[dict]:
    moved: list[dict] = []
    if not BUSINESS_PROJECTS.exists():
        return moved
    for project_dir in sorted(BUSINESS_PROJECTS.iterdir()):
        if not project_dir.is_dir():
            continue
        unknown_branch = project_dir / "unknown" / "unknown" / "lotunknown"
        if not unknown_branch.exists():
            continue
        images_root = unknown_branch / "images"
        json_root = unknown_branch / "json"
        if not images_root.exists():
            continue
        for date_dir in sorted(images_root.iterdir()):
            if not date_dir.is_dir():
                continue
            month = _month_from_date(date_dir.name)
            entries_to_write: list[dict] = []
            for img in sorted(date_dir.iterdir()):
                if not img.is_file() or img.name.startswith("."):
                    continue
                stem = img.stem
                json_date_dir = json_root / date_dir.name
                jsn = json_date_dir / f"{stem}_result.json"
                payload = _read_extraction_json(jsn) if jsn.exists() else {}
                image_dest, json_dest = _move_pair(img, jsn if jsn.exists() else None,
                                                    month, apply)
                entries_to_write.append(_build_index_entry(
                    image_dest.name,
                    json_dest.name if json_dest else None,
                    payload,
                    reason="sop_authorized_but_fields_missing "
                           "(migrated from <proj>/unknown/unknown/lotunknown)",
                    project_hint=project_dir.name,
                ))
                moved.append({"src": "business_projects_unknown",
                              "image": img.name, "project": project_dir.name,
                              "month": month})
            if entries_to_write:
                _write_index(month, entries_to_write, apply)
    return moved


# ── Source C: wechat_images/<群>/<category>/{*.jpg + read_data/*.json} ─────
# wx-ops-agent 的旧 BusinessImageRouterHandler fallback 路径
# (group/category/),这些图实际是待落实的(没真正进项目归档),应该一起
# 收口到 _pending/。

_KNOWN_CATEGORIES = {
    "出港计划通知单", "检装车通知单", "现场照片", "其他图片", "表格", "文档",
}
_ARTIFACT_ROOT_GROUPS = ARTIFACT_ROOT  # wechat_images/


def migrate_router_misplaced(apply: bool) -> list[dict]:
    """Pick up router-misplaced images at wechat_images/<群>/<分类>/."""
    moved: list[dict] = []
    if not _ARTIFACT_ROOT_GROUPS.exists():
        return moved
    for group_dir in sorted(_ARTIFACT_ROOT_GROUPS.iterdir()):
        if not group_dir.is_dir() or group_dir.name in {"_pending", "business",
                                                         "extractions", "_quarantine"}:
            continue
        for cat_dir in sorted(group_dir.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name not in _KNOWN_CATEGORIES:
                continue
            # 整个目录就是 router 错位归档
            json_dir = cat_dir / "read_data"
            entries_by_month: dict[str, list[dict]] = {}
            for img in sorted(cat_dir.iterdir()):
                if not img.is_file() or img.name.startswith(".") or not img.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                    continue
                stem = img.stem
                jsn = json_dir / f"{stem}_result.json" if json_dir.exists() else None
                payload = _read_extraction_json(jsn) if (jsn and jsn.exists()) else {}
                # 从 mtime 推月份(没更好的信息源)
                import os, time
                ts = os.path.getmtime(img)
                month = time.strftime("%Y-%m", time.localtime(ts))
                image_dest, json_dest = _move_pair(
                    img, jsn if (jsn and jsn.exists()) else None, month, apply
                )
                entries_by_month.setdefault(month, []).append(_build_index_entry(
                    image_dest.name,
                    json_dest.name if json_dest else None,
                    payload,
                    reason=f"router_misplaced (from {group_dir.name}/{cat_dir.name})",
                ))
                moved.append({"src": "router_misplaced", "group": group_dir.name,
                              "category": cat_dir.name,
                              "image": img.name, "month": month})
            for m, ents in entries_by_month.items():
                _write_index(m, ents, apply)
            # 清空 read_data/ 残留 + cat_dir 残留
            if apply:
                if json_dir.exists():
                    for f in json_dir.iterdir():
                        if f.is_file():
                            try: f.unlink()
                            except OSError: pass
                    try: json_dir.rmdir()
                    except OSError: pass
                try: cat_dir.rmdir()
                except OSError: pass
    return moved


def update_audit_paths(apply: bool, db_path: Path) -> int:
    """Rewrite image_ingestion_audit rows whose paths point to the old locations."""
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    n = 0
    rows = conn.execute(
        "SELECT id, raw_image_path, classified_image_path, extraction_json_path, "
        "project_archive_paths FROM image_ingestion_audit "
        "WHERE COALESCE(classified_image_path,'') LIKE '%runtime/unmatched%' "
        "   OR COALESCE(extraction_json_path,'') LIKE '%runtime/unmatched%' "
        "   OR COALESCE(classified_image_path,'') LIKE '%/unknown/unknown/lotunknown/%' "
        "   OR COALESCE(extraction_json_path,'') LIKE '%/unknown/unknown/lotunknown/%'"
    ).fetchall()
    for rid, raw_p, cls_p, ext_p, pap in rows:
        new_cls = _rewrite(cls_p)
        new_ext = _rewrite(ext_p)
        new_pap = pap
        if pap and ("runtime/unmatched" in pap or "/unknown/unknown/lotunknown/" in pap):
            try:
                d = json.loads(pap)
                if isinstance(d, dict):
                    if "image" in d:
                        d["image"] = _rewrite(d["image"])
                    if "json" in d:
                        d["json"] = _rewrite(d["json"])
                    new_pap = json.dumps(d, ensure_ascii=False)
            except json.JSONDecodeError:
                pass
        if apply:
            conn.execute(
                "UPDATE image_ingestion_audit SET classified_image_path=?, "
                "extraction_json_path=?, project_archive_paths=? WHERE id=?",
                (new_cls, new_ext, new_pap, rid),
            )
        n += 1
    if apply:
        conn.commit()
    conn.close()
    return n


def _rewrite(p: str | None) -> str | None:
    """Rewrite an old path to its new _pending equivalent (by filename + month)."""
    if not p:
        return p
    name = Path(p).name
    # Try to infer month from path
    parts = Path(p).parts
    month = ""
    for i, seg in enumerate(parts):
        if len(seg) == 7 and seg[4] == "-":
            month = seg
            break
        if len(seg) == 10 and seg[4] == "-" and seg[7] == "-":
            month = seg[:7]
            break
    if not month:
        month = datetime.now().strftime("%Y-%m")
    subdir = "json" if p.endswith(".json") else "images"
    return str(PENDING_ROOT / month / subdir / name)


def cleanup_empty_dirs(apply: bool) -> int:
    """Remove now-empty old directories."""
    n = 0
    candidates = []
    if RUNTIME_UNMATCHED.exists():
        for path in sorted(RUNTIME_UNMATCHED.rglob("*"), reverse=True):
            candidates.append(path)
        candidates.append(RUNTIME_UNMATCHED)
    if BUSINESS_PROJECTS.exists():
        for project_dir in BUSINESS_PROJECTS.iterdir():
            uu = project_dir / "unknown" / "unknown" / "lotunknown"
            if uu.exists():
                for path in sorted(uu.rglob("*"), reverse=True):
                    candidates.append(path)
                candidates.append(uu)
                candidates.append(project_dir / "unknown" / "unknown")
                candidates.append(project_dir / "unknown")
    for p in candidates:
        if p.exists() and p.is_dir():
            try:
                if not any(p.iterdir()):
                    if apply:
                        p.rmdir()
                    n += 1
            except OSError:
                continue
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    print(f"[r77] mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"[r77] _pending root: {PENDING_ROOT}")
    print()

    print("=== Source A: runtime/unmatched/ ===")
    a_moved = migrate_unmatched(args.apply)
    print(f"  moved: {len(a_moved)}")
    if a_moved[:3]:
        for m in a_moved[:3]:
            print(f"    {m}")

    print()
    print("=== Source B: business/projects/*/unknown/unknown/lotunknown/ ===")
    b_moved = migrate_unknown_unknown_lotunknown(args.apply)
    print(f"  moved: {len(b_moved)}")
    if b_moved[:3]:
        for m in b_moved[:3]:
            print(f"    {m}")

    print()
    print("=== Source C: wechat_images/<群>/<分类>/ (router misplaced) ===")
    c_moved = migrate_router_misplaced(args.apply)
    print(f"  moved: {len(c_moved)}")
    for m in c_moved[:5]:
        print(f"    {m}")

    print()
    print("=== image_ingestion_audit path rewrite ===")
    audit_n = update_audit_paths(args.apply, Path(args.db))
    print(f"  rows touched: {audit_n}")

    print()
    print("=== cleanup empty dirs ===")
    empty_n = cleanup_empty_dirs(args.apply)
    print(f"  removed empty dirs: {empty_n}")

    if not args.apply:
        print("\n[r77] DRY-RUN only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
