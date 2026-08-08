"""Test DB helpers that prefer production schema entry points."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def production_sop_db_path(root: Path | None = None) -> Path:
    """Canonical production business DB path under the repo (never open for write in tests)."""
    base = root or project_root()
    return (base / "data" / "sop_agent.db").resolve()


def init_sop_db(db_path: Path) -> Path:
    """Create a temp SOP DB via production ``open_db`` schema/migration path.

    Sets ``BUSINESS_DATA_AGENT_DB_PATH`` for the process; callers using the
    ``tmp_db`` fixture already manage that env var — prefer the fixture when
    possible.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = str(db_path)
    try:
        from sop_hub.data_agent.db import open_db

        conn = open_db()
        conn.close()
    finally:
        if previous is None:
            os.environ.pop("BUSINESS_DATA_AGENT_DB_PATH", None)
        else:
            os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = previous
    return db_path


def init_min_rail_db(db_path: Path) -> Path:
    """Minimal 95306-like ``shipments`` table for window/query functional tests.

    Column set is the contract used by inspection/window code paths; expand only
    with a matching field-contract test when production SQL gains columns.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shipments (
              ydid TEXT PRIMARY KEY,
              wagon_no TEXT,
              origin_name TEXT,
              destination_name TEXT,
              transport_mode_name TEXT,
              ticketed_at TEXT,
              status_name TEXT,
              latest_stage_key TEXT,
              container_numbers_json TEXT,
              raw_core_json TEXT,
              hph TEXT,
              marked_weight REAL,
              cargo_name TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def sqlite_database_path(database: str | os.PathLike[str]) -> Path | None:
    """Resolve a sqlite3.connect database argument to a filesystem Path, if any."""
    text = str(database)
    if text == ":memory:":
        return None
    if text.startswith("file:"):
        from urllib.parse import unquote, urlparse

        parsed = urlparse(text)
        if not parsed.path:
            return None
        try:
            return Path(unquote(parsed.path)).expanduser().resolve()
        except OSError:
            return None
    try:
        return Path(text).expanduser().resolve()
    except OSError:
        return None


def is_readonly_sqlite_uri(database: str | os.PathLike[str]) -> bool:
    text = str(database)
    return text.startswith("file:") and "mode=ro" in text


def is_production_sop_db_path(database: str | os.PathLike[str], *, root: Path | None = None) -> bool:
    """Return True if *database* resolves to the repo production sop_agent.db."""
    path = sqlite_database_path(database)
    if path is None:
        return False
    return path == production_sop_db_path(root)
