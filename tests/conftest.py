"""Shared test fixtures."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


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
            mock_resp.json.return_value = {"text": json.dumps(json_payload, ensure_ascii=False)}
        return mock_resp
    return _make_response


@pytest.fixture
def tmp_db(tmp_path: Path):
    """Create a temporary database for testing."""
    import os
    db_path = tmp_path / "test_agent.db"
    os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = str(db_path)
    yield db_path
    os.environ.pop("BUSINESS_DATA_AGENT_DB_PATH", None)
