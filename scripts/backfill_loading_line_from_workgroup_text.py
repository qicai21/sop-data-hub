#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from sop_hub.sop.loading_line_backfill import apply_backfill, decide_backfill


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill wagon/container shipment loading_line from authorized group history"
    )
    parser.add_argument(
        "--db",
        default=str(Path(__file__).resolve().parents[1] / "data" / "sop_agent.db"),
        help="Path to sop_agent.db",
    )
    parser.add_argument(
        "--projects",
        default="jiusan,chaoyang_steel,jilin_jingang_jinzhou,zhongtang_special_steel",
        help="Comma-separated project ids",
    )
    parser.add_argument(
        "--group-name",
        default="",
        help="Override source group (default: project-authorized group)",
    )
    parser.add_argument("--since", default="", help="Only inspect tickets on/after YYYY-MM-DD")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply matched loading lines back to wagon_shipments",
    )
    args = parser.parse_args()

    project_ids = [p.strip() for p in args.projects.split(",") if p.strip()]
    conn = sqlite3.connect(args.db)
    try:
        decisions = decide_backfill(
            conn, project_ids=project_ids, group_name=args.group_name, since=args.since,
        )
        matched = [d for d in decisions if d.status == "matched"]
        ambiguous = [d for d in decisions if d.status == "ambiguous"]
        unmatched = [d for d in decisions if d.status == "unmatched"]

        print(f"groups={len(decisions)} matched={len(matched)} ambiguous={len(ambiguous)} unmatched={len(unmatched)}")
        for decision in decisions:
            g = decision.group
            print(
                f"{decision.status}\t{g.source_table}\t{g.project_id}\t{g.dispatch_train_code}\t"
                f"{g.ticketed_at}\t{g.ship_name or '-'}\t{g.car_count}\t"
                f"{decision.lane or '-'}\t{decision.inbox_id or '-'}\t"
                f"{decision.text_preview or decision.note}"
            )

        if args.apply:
            applied = apply_backfill(conn, matched)
            conn.commit()
            print(f"applied_rows={applied}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
