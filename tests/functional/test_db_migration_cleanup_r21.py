"""R21: Database migration and cleanup tests.

Scope:
- Zombie DB file deletion verification
- Legacy agent.db auto-migration to sop_agent.db
- No remaining references to zombie DB names in codebase
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def test_zombie_db_files_deleted():
    """R21: ops_data_hub.db, rail95306.db, message_store.db no longer exist."""
    zombies = [
        ("ops-data-hub", "ops_data_hub.db"),
        ("wx-ops-agent", "message_store.db"),
    ]
    repos_root = Path(__file__).resolve().parents[2].parent

    for repo_name, db_name in zombies:
        db_path = repos_root / repo_name / "data" / db_name
        assert not db_path.exists(), f"Zombie DB should be deleted: {db_path}"

    # rail95306.db was in ops-data-hub/data/rail95306.db
    rail_path = repos_root / "ops-data-hub" / "data" / "rail95306.db"
    assert not rail_path.exists(), f"Zombie DB should be deleted: {rail_path}"


def test_legacy_agent_db_auto_migrates_to_sop_agent_db(tmp_path):
    """R21: Legacy agent.db at data/agent.db auto-copies to sop_agent.db on open."""
    from ops_hub.data_agent.db import _migrate_legacy_db, get_db_path
    import importlib
    import ops_hub.data_agent.db as db_module

    legacy = tmp_path / "data" / "agent.db"
    legacy.parent.mkdir(parents=True, exist_ok=True)

    # Create a legacy DB with recognizable content
    conn = sqlite3.connect(str(legacy))
    conn.execute("CREATE TABLE test_migration (msg TEXT)")
    conn.execute("INSERT INTO test_migration VALUES ('legacy data')")
    conn.commit()
    conn.close()

    sop_db = tmp_path / "data" / "sop_agent.db"
    assert not sop_db.exists(), "sop_agent.db should not exist before migration"

    # Run migration
    _migrate_legacy_db(sop_db)

    assert sop_db.exists(), "sop_agent.db should exist after migration"
    assert sop_db.stat().st_size > 0, "sop_agent.db should have data"

    # Verify data was copied
    conn2 = sqlite3.connect(str(sop_db))
    row = conn2.execute("SELECT msg FROM test_migration").fetchone()
    assert row[0] == "legacy data", f"Expected 'legacy data', got {row}"
    conn2.close()

    # Re-running migration should NOT overwrite (DB already exists)
    _migrate_legacy_db(sop_db)
    conn3 = sqlite3.connect(str(sop_db))
    row2 = conn3.execute("SELECT msg FROM test_migration").fetchone()
    assert row2[0] == "legacy data", "Data should be unchanged after re-run"
    conn3.close()


def test_no_zombie_db_names_in_code(tmp_path):
    """R21: Grep for zombie DB names returns zero hits in code/docs (not reports)."""
    repo_root = Path(__file__).resolve().parents[2]
    zombie_names = ["ops_data_hub.db", "rail95306.db", "message_store.db"]

    skip_paths = {".venv", ".git", "__pycache__", ".pytest_cache", ".db", ".sqlite3"}
    skip_exts = {".db", ".sqlite3", ".pyc", ".pyo", ".DS_Store"}

    hits = []
    for f in repo_root.rglob("*"):
        if f.is_dir():
            if any(s in f.parts for s in skip_paths):
                continue
            continue
        if f.suffix in skip_exts:
            continue
        try:
            content = f.read_text(errors='ignore')
        except Exception:
            continue
        for name in zombie_names:
            if name in content:
                # Allow mentions in R21 report (this one) and updated audit reports
                rel = str(f.relative_to(repo_root))
                if "test_db_migration_cleanup_r21" in rel:
                    continue
                if "db_cleanup_and_migration_r21" in rel:
                    continue
                if rel.startswith("reports/data_persistence_topology_audit") and "已清理僵尸" in content:
                    continue
                if rel.startswith("reports/runtime_boundary_audit_r13_5"):
                    continue
                hits.append((rel, name))

    assert len(hits) == 0, (
        f"Found {len(hits)} zombie DB name references outside known reports:\n"
        + "\n".join(f"  {path}: {name}" for path, name in hits)
    )


def test_data_dir_exists_and_sop_agent_db_present():
    """R21: sop-data-hub/data/sop_agent.db exists after migration."""
    repo_root = Path(__file__).resolve().parents[2]
    db_path = repo_root / "data" / "sop_agent.db"
    assert db_path.exists(), f"Canonical sop_agent.db should exist: {db_path}"
    assert db_path.stat().st_size > 0, "sop_agent.db should not be empty"

    # Verify it has the expected tables
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"release_batches", "contracts", "release_dispatch_match_rules", "image_ingestion_audit", "inspection_ingestion_candidates"}
    missing = expected - tables
    assert not missing, f"Missing tables: {missing}"
    conn.close()


def test_default_db_path_uses_sop_agent_db():
    """R21: get_db_path() default resolves to sop_agent.db, not agent.db."""
    from ops_hub.data_agent.db import get_db_path

    # Clear any env var override
    old_env = os.environ.pop("BUSINESS_DATA_AGENT_DB_PATH", None)
    try:
        path = get_db_path()
        assert path.name == "sop_agent.db", f"Expected sop_agent.db, got {path.name}"
    finally:
        if old_env:
            os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = old_env
