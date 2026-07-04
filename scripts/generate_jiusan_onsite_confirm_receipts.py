#!/usr/bin/env python3
"""Generate 九三散粮辅助作业现场确认单 PDF receipts.

Each bulk train group in `bulk_loading_notice_wagon` becomes one 8cm-wide PDF
receipt page. The output is direct image-backed PDF, not Word conversion.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sop_hub.fees.jiusan_onsite_receipt import (  # noqa: E402
    default_output_path,
    fetch_bulk_train_groups,
    render_groups_to_pdf,
)


DB = REPO / "data" / "sop_agent.db"
DEFAULT_OUTPUT_DIR = REPO / "reports" / "fee_docs" / "jiusan" / "onsite_confirm_receipts"


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--ship", default="", help="船名,如 和谐1/诚信/美国")
    parser.add_argument(
        "--date",
        action="append",
        default=[],
        help="发生日期 YYYY-MM-DD; 可重复传入生成多列",
    )
    parser.add_argument("--track", default="", help="道线,如 七道")
    parser.add_argument("--output", type=Path, help="输出 PDF 路径;不填则自动命名")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--list", action="store_true", help="只列出匹配列,不生成 PDF")
    args = parser.parse_args()

    with open_db(args.db) as conn:
        if args.date:
            groups = []
            for date in args.date:
                groups.extend(
                    fetch_bulk_train_groups(
                        conn,
                        ship_name=args.ship,
                        notice_date=date,
                        track=args.track,
                    )
                )
        else:
            groups = fetch_bulk_train_groups(
                conn,
                ship_name=args.ship,
                notice_date="",
                track=args.track,
            )

    if not groups:
        raise SystemExit("未找到匹配的九三散粮列")

    if args.list:
        for g in groups:
            print(f"{g.notice_date}\t{g.ship_name}\t{g.track}\t{g.car_count}车")
        return

    output = args.output or default_output_path(groups, args.output_dir)
    render_groups_to_pdf(groups, output)
    print(output)
    for g in groups:
        print(f"- {g.notice_date} {g.ship_name} {g.track} {g.car_count}车")


if __name__ == "__main__":
    main()
