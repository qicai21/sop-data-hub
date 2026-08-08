"""Load deterministic replay fixtures under tests/fixtures/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures"


def fixtures_root() -> Path:
    return FIXTURES_ROOT


def load_replay(relative_path: str | Path) -> dict[str, Any]:
    """Load a JSON replay case: ``meta`` / ``input`` / ``expected`` (+ optional variants)."""
    path = FIXTURES_ROOT / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"replay fixture not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"replay fixture must be object: {path}")
    return payload


def list_replay_relpaths(subdir: str) -> list[str]:
    base = FIXTURES_ROOT / subdir
    if not base.is_dir():
        return []
    return sorted(
        str(p.relative_to(FIXTURES_ROOT))
        for p in base.rglob("*.json")
    )
