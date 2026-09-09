from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sop_hub.sop.workflow_task_executor import (
    _execute_create_release_batch,
    _existing_batches_cover_release_notice,
)


def _write_notice(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "project": "jilin_jingang_jinzhou",
                "business_info": {"船名": "林加尼"},
                "remarks": [
                    {"sequence": "第一次下达计划", "raw_line": "8月27日第一次下达计划"},
                    {"sequence": "第二次下达计划", "raw_line": "9月2日第二次下达计划"},
                    {"sequence": "第三次下达计划", "raw_line": "9月9日第三次下达计划"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _make_db(path: Path, sequences: tuple[str, ...]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE release_batches (
            id TEXT PRIMARY KEY, project TEXT, ship_name TEXT, batch_sequence TEXT
        )"""
    )
    conn.executemany(
        "INSERT INTO release_batches VALUES (?, ?, ?, ?)",
        [
            (f"batch-{seq}", "jilin_jingang_jinzhou", "林加尼", seq)
            for seq in sequences
        ],
    )
    conn.commit()
    conn.close()


def test_cumulative_notice_with_all_lots_existing_is_idempotent(tmp_path: Path) -> None:
    notice = tmp_path / "notice.json"
    db = tmp_path / "sop.db"
    _write_notice(notice)
    _make_db(db, ("lot01", "lot02", "lot03"))

    assert _existing_batches_cover_release_notice(notice, db) == [
        "batch-lot01",
        "batch-lot02",
        "batch-lot03",
    ]


def test_cumulative_notice_with_missing_latest_lot_still_requires_alert(tmp_path: Path) -> None:
    notice = tmp_path / "notice.json"
    db = tmp_path / "sop.db"
    _write_notice(notice)
    _make_db(db, ("lot01", "lot02"))

    assert _existing_batches_cover_release_notice(notice, db) == []


def test_executor_does_not_warn_when_zero_insert_is_fully_covered(
    tmp_path: Path, monkeypatch,
) -> None:
    notice = tmp_path / "notice.json"
    db = tmp_path / "sop.db"
    _write_notice(notice)
    _make_db(db, ("lot01", "lot02", "lot03"))

    class FakeAgent:
        def ingest_release_batch_file(self, _path: str) -> list:
            return []

    sent: list[str] = []
    monkeypatch.setattr("sop_hub.data_agent.agent.BusinessDataAgent", FakeAgent)
    monkeypatch.setattr(
        "sop_hub.sop.send_excel.send_to_wechat",
        lambda **kwargs: sent.append(kwargs["message"]),
    )

    result = _execute_create_release_batch(
        {"extraction_json_path": str(notice)}, "wx-test", db_path=db
    )

    assert result["status"] == "succeeded"
    assert result["output_json"]["idempotent_existing_batch_ids"] == [
        "batch-lot01",
        "batch-lot02",
        "batch-lot03",
    ]
    assert sent == []


def test_executor_warns_when_zero_insert_still_has_missing_lot(
    tmp_path: Path, monkeypatch,
) -> None:
    notice = tmp_path / "notice.json"
    db = tmp_path / "sop.db"
    _write_notice(notice)
    _make_db(db, ("lot01", "lot02"))

    class FakeAgent:
        def ingest_release_batch_file(self, _path: str) -> list:
            return []

    sent: list[str] = []
    monkeypatch.setattr("sop_hub.data_agent.agent.BusinessDataAgent", FakeAgent)
    monkeypatch.setattr(
        "sop_hub.sop.send_excel.send_to_wechat",
        lambda **kwargs: sent.append(kwargs["message"]),
    )

    _execute_create_release_batch(
        {"extraction_json_path": str(notice)}, "wx-test", db_path=db
    )

    assert len(sent) == 1
    assert "疑似漏建" in sent[0]
