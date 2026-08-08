"""Shared test fixtures and portable safety rails.

See docs/issues/archived/2026-08-08-工单-测试体系重构-双端开发与可移植门禁.md §5.
unit/functional must never write production DBs or open network egress.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.support.db import is_production_sop_db_path, production_sop_db_path


@pytest.fixture
def project_root() -> Path:
    """Return the project root directory."""
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def samples_dir(project_root: Path) -> Path:
    """Return the samples directory."""
    return project_root / "samples"


@pytest.fixture
def mock_vlm_response():
    """Factory fixture to create mock VLM API responses."""

    def _make_response(json_payload: dict | list | str, status_code: int = 200):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.raise_for_status = MagicMock()
        if isinstance(json_payload, str):
            mock_resp.json.return_value = {"text": json_payload}
        else:
            mock_resp.json.return_value = {
                "text": json.dumps(json_payload, ensure_ascii=False)
            }
        return mock_resp

    return _make_response


@pytest.fixture
def tmp_db(tmp_path: Path):
    """Create a temporary database path and point BUSINESS_DATA_AGENT_DB_PATH at it."""
    db_path = tmp_path / "test_sop_agent.db"
    os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = str(db_path)
    yield db_path
    os.environ.pop("BUSINESS_DATA_AGENT_DB_PATH", None)


@pytest.fixture
def sop_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Temp SOP DB initialized via production open_db schema path."""
    from tests.support.db import init_sop_db

    db_path = tmp_path / "sop_agent.db"
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(db_path))
    return init_sop_db(db_path)


@pytest.fixture
def rail_db(tmp_path: Path) -> Path:
    """Temp minimal 95306-like DB for portable functional tests."""
    from tests.support.db import init_min_rail_db

    return init_min_rail_db(tmp_path / "95306_collection.sqlite3")


@pytest.fixture(autouse=True)
def _block_real_wechat_send(monkeypatch: pytest.MonkeyPatch):
    """Default safety rail: tests must not send real WeChat messages."""

    class _FakeSendResult:
        success = True
        error = ""

    monkeypatch.setattr(
        "sop_hub.sop.send_excel.send_to_wechat",
        lambda *a, **kw: _FakeSendResult(),
    )


@pytest.fixture(autouse=True)
def _block_all_http_egress(monkeypatch: pytest.MonkeyPatch):
    """Portable gate: no real HTTP egress (VLM, 95306 APIs, Ansteel, etc.).

    unit/functional have no network allow-list. Mock at the call site or patch
    ``requests`` on the module under test. Live tests also inherit this rail —
    they must not depend on outbound HTTP either.
    """
    import requests

    def _blocked(method: str, url: object) -> None:
        raise RuntimeError(
            "portable tests block all live HTTP egress "
            f"({method} {url!r}). Mock the client under test instead."
        )

    def guarded_request(self, method, url, *args, **kwargs):
        _blocked(str(method), url)

    def guarded_top(method, url, **kwargs):
        _blocked(str(method), url)

    monkeypatch.setattr(requests.sessions.Session, "request", guarded_request)
    monkeypatch.setattr(requests, "request", guarded_top)


@pytest.fixture(autouse=True)
def _forbid_production_sop_db_writes(monkeypatch: pytest.MonkeyPatch, project_root: Path):
    """Fail if any portable test opens the production sop_agent.db.

    Live tests (SOP_TEST_LIVE=1) may open it only via SQLite URI ``mode=ro``.
    There is no SOP_TEST_ALLOW_PROD_WRITE switch — writes never allowed.
    """
    from tests.support.db import is_readonly_sqlite_uri

    prod = production_sop_db_path(project_root)
    real_connect = sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        if is_production_sop_db_path(database, root=project_root):
            live_ro = (
                os.environ.get("SOP_TEST_LIVE") == "1"
                and is_readonly_sqlite_uri(database)
            )
            if not live_ro:
                raise RuntimeError(
                    f"tests must not open production SOP DB ({prod}) for write "
                    "or from portable tests; use tmp_path / sop_db fixture "
                    "(live readonly: file:...?mode=ro with SOP_TEST_LIVE=1)"
                )
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
