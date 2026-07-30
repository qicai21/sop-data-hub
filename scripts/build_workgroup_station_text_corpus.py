#!/usr/bin/env python3
"""Build a versioned, de-duplicated station-text corpus from message_inbox.

The output contains only representative text plus source IDs/times.  It is a
test/design baseline, not a second operational message store.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3

from sop_hub.sop.text_station_segments import STATION_ALIASES, extract_station_segments


def _pattern(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    normalized = re.sub(r"\d+\s*(?:节|车)", "{N}节", normalized)
    return normalized


def build_corpus(db_path: Path, output_path: Path, month: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, message_id, received_datetime, text_content
           FROM message_inbox
           WHERE group_name='铁晟业务工作群'
             AND received_datetime >= ? AND received_datetime < date(?, '+1 month')
             AND text_content <> ''
           ORDER BY received_datetime, id""",
        (f"{month}-01", f"{month}-01"),
    ).fetchall()
    conn.close()
    grouped: dict[str, dict] = {}
    for row in rows:
        text = str(row["text_content"])
        if not re.search(r"\d+\s*(?:节|车)", text) or not extract_station_segments(text):
            continue
        key = _pattern(text)
        entry = grouped.setdefault(key, {
            "pattern": key,
            "occurrences": 0,
            "example": {
                "inbox_id": row["id"], "message_id": row["message_id"],
                "received_datetime": row["received_datetime"], "text": text,
            },
            "stations": sorted({segment.destination for segment in extract_station_segments(text)}),
        })
        entry["occurrences"] += 1
    payload = {
        "source": "message_inbox/铁晟业务工作群",
        "month": month,
        "station_aliases": {alias: item.canonical for alias, item in STATION_ALIASES.items()},
        "matched_message_count": sum(item["occurrences"] for item in grouped.values()),
        "unique_pattern_count": len(grouped),
        "patterns": sorted(grouped.values(), key=lambda item: (-item["occurrences"], item["pattern"])),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=repo / "data" / "sop_agent.db")
    parser.add_argument("--month", default="2026-07")
    parser.add_argument("--output", type=Path, default=repo / "tests" / "fixtures" / "workgroup_station_text_corpus_2026-07.json")
    args = parser.parse_args()
    payload = build_corpus(args.db, args.output, args.month)
    print(f"{payload['matched_message_count']} messages -> {payload['unique_pattern_count']} unique patterns: {args.output}")


if __name__ == "__main__":
    main()
