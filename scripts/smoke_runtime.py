#!/usr/bin/env python3
"""Runtime-machine smoke checks (release gate contract).

Portable logic is covered by ``make test``. This script fails when the *runtime*
host is missing necessities for shipping automation (config, readonly 95306 DB,
runtime dir). Dev laptops should not use this as a substitute for pytest.

See issue: docs/issues/2026-08-08-工单-测试体系重构-双端开发与可移植门禁.md §0.6
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    errors: list[str] = []

    # 1) project SOP yaml loadable
    try:
        import yaml

        sop_dir = REPO / "config" / "project_sops"
        yamls = list(sop_dir.glob("*.yaml"))
        if not yamls:
            errors.append(f"no project SOP yaml under {sop_dir}")
        for path in yamls:
            yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — smoke reports any load failure
        errors.append(f"project SOP yaml load failed: {exc}")

    # 2) runtime dir exists / writable
    runtime = REPO / "runtime"
    try:
        runtime.mkdir(parents=True, exist_ok=True)
        probe = runtime / ".smoke_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        errors.append(f"runtime/ not writable: {exc}")

    # 3) production SOP DB present + readonly open + key tables
    sop_db = REPO / "data" / "sop_agent.db"
    if not sop_db.is_file():
        errors.append(f"missing production SOP DB: {sop_db}")
    else:
        try:
            uri = f"file:{sop_db}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            try:
                names = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                for required in ("release_batches", "wagon_shipments"):
                    if required not in names:
                        errors.append(f"SOP DB missing table {required}")
            finally:
                conn.close()
        except sqlite3.Error as exc:
            errors.append(f"SOP DB readonly open failed: {exc}")

    # 4) 95306 collection DB readonly (runtime machine hard requirement — §11 H)
    rail_default = (
        Path.home()
        / "projects"
        / "repos"
        / "rail95306-sync"
        / "runtime"
        / "95306_collection.sqlite3"
    )
    rail = Path(os.environ.get("SOP_RAIL_DB", str(rail_default)))
    if not rail.is_file():
        errors.append(
            f"missing 95306 readonly DB: {rail} "
            "(set SOP_RAIL_DB to override; required on runtime machine)"
        )
    else:
        try:
            conn = sqlite3.connect(f"file:{rail}?mode=ro", uri=True)
            try:
                conn.execute("SELECT 1 FROM shipments LIMIT 1").fetchone()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            errors.append(f"95306 DB readonly check failed: {exc}")

    # 5) key launchd plists present in repo (install state is separate)
    launchd = REPO / "deploy" / "launchd"
    for label in (
        "com.qicai21.sop-data-hub.live-service.plist",
        "com.qicai21.sop-data-hub.text-watch.plist",
    ):
        if not (launchd / label).is_file():
            errors.append(f"missing launchd template: {label}")

    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        print(f"smoke-runtime: {len(errors)} failure(s)", file=sys.stderr)
        return 1

    print("smoke-runtime: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
