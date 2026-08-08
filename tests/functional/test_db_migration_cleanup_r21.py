"""R21: Database migration and cleanup tests (portable).

- Legacy agent.db auto-migration to sop_agent.db (tmp only)
- No zombie DB *filenames* hard-wired as live paths in src/scripts
- Does NOT scan sibling repos or require production data/sop_agent.db
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def test_legacy_agent_db_auto_migrates_to_sop_agent_db(tmp_path):
    """R21: Legacy agent.db at data/agent.db auto-copies to sop_agent.db on open."""
    from sop_hub.data_agent.db import _migrate_legacy_db

    legacy = tmp_path / "data" / "agent.db"
    legacy.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(legacy))
    conn.execute("CREATE TABLE test_migration (msg TEXT)")
    conn.execute("INSERT INTO test_migration VALUES ('legacy data')")
    conn.commit()
    conn.close()

    sop_db = tmp_path / "data" / "sop_agent.db"
    assert not sop_db.exists(), "sop_agent.db should not exist before migration"

    _migrate_legacy_db(sop_db)

    assert sop_db.exists(), "sop_agent.db should exist after migration"
    assert sop_db.stat().st_size > 0, "sop_agent.db should have data"

    conn2 = sqlite3.connect(str(sop_db))
    row = conn2.execute("SELECT msg FROM test_migration").fetchone()
    assert row[0] == "legacy data", f"Expected 'legacy data', got {row}"
    conn2.close()

    _migrate_legacy_db(sop_db)
    conn3 = sqlite3.connect(str(sop_db))
    row2 = conn3.execute("SELECT msg FROM test_migration").fetchone()
    assert row2[0] == "legacy data", "Data should be unchanged after re-run"
    conn3.close()


def test_no_zombie_db_names_as_active_paths_in_src():
    """R21: src/ + scripts/ must not still open legacy zombie DB filenames.

    Historical mentions in docs/tests/reports are out of scope for portable gate.
    """
    repo_root = Path(__file__).resolve().parents[2]
    zombie_names = ("ops_data_hub.db", "rail95306.db", "message_store.db")
    scan_roots = [repo_root / "src", repo_root / "scripts"]
    hits: list[tuple[str, str]] = []

    for root in scan_roots:
        if not root.is_dir():
            continue
        for f in root.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for name in zombie_names:
                if name in content:
                    hits.append((str(f.relative_to(repo_root)), name))

    assert hits == [], (
        "Found zombie DB name references in src/scripts:\n"
        + "\n".join(f"  {path}: {name}" for path, name in hits)
    )


def test_open_db_schema_on_tmp_has_core_tables(tmp_path, monkeypatch):
    """Schema init works without opening production data/sop_agent.db."""
    from sop_hub.data_agent.db import open_db

    tmp = tmp_path / "sop_agent.db"
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(tmp))
    conn = open_db()
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()

    expected = {
        "release_batches",
        "contracts",
        "release_dispatch_match_rules",
        "image_ingestion_audit",
        "inspection_ingestion_candidates",
    }
    missing = expected - tables
    assert not missing, f"Missing tables on schema init: {missing}"


def test_default_db_path_uses_sop_agent_db():
    """R21: get_db_path() default resolves to sop_agent.db, not agent.db."""
    from sop_hub.data_agent.db import get_db_path

    old_env = os.environ.pop("BUSINESS_DATA_AGENT_DB_PATH", None)
    try:
        path = get_db_path()
        assert path.name == "sop_agent.db", f"Expected sop_agent.db, got {path.name}"
    finally:
        if old_env:
            os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = old_env
