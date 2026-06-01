from __future__ import annotations

import json
import re
from datetime import datetime
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def _sanitize_component(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch in " _-()").strip()
    return cleaned or "unknown"


class BusinessArtifactStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _group_dir(self, group_name: str, category: str) -> Path:
        group_dir = self.base_dir / _sanitize_component(group_name) / _sanitize_component(category)
        group_dir.mkdir(parents=True, exist_ok=True)
        return group_dir

    def append_jsonl(self, group_name: str, category: str, payload: Any) -> Path:
        group_dir = self._group_dir(group_name, category)
        out = group_dir / "records.jsonl"
        record = asdict(payload) if is_dataclass(payload) else payload
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return out

    @staticmethod
    def _day_prefix(timestamp: int | float | None = None) -> str:
        if timestamp is None:
            return datetime.now().strftime("%y%m%d")
        try:
            return datetime.fromtimestamp(float(timestamp)).strftime("%y%m%d")
        except Exception:
            return datetime.now().strftime("%y%m%d")

    @staticmethod
    def _next_sequence(group_dir: Path, day_prefix: str) -> int:
        max_seq = 0
        pattern = re.compile(rf"^{re.escape(day_prefix)}-(\d+)-data\.json$")
        for path in group_dir.glob(f"{day_prefix}-*-data.json"):
            match = pattern.match(path.name)
            if not match:
                continue
            try:
                max_seq = max(max_seq, int(match.group(1)))
            except ValueError:
                continue
        return max_seq + 1

    def write_json(
        self,
        group_name: str,
        category: str,
        record: Any,
        *,
        timestamp: int | float | None = None,
    ) -> Path:
        group_dir = self._group_dir(group_name, category)
        day_prefix = self._day_prefix(timestamp)
        seq = self._next_sequence(group_dir, day_prefix)
        out = group_dir / f"{day_prefix}-{seq}-data.json"
        payload = asdict(record) if is_dataclass(record) else record
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
