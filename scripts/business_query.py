#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ops_hub.config import load_settings  # noqa: E402
from ops_hub.matching.inspection_finalizer import finalize_inspection_candidates  # noqa: E402


def cmd_finalize_inspection(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    run_mode = "dry_run" if args.dry_run else "commit"
    result = finalize_inspection_candidates(
        business_db_path=args.business_db or settings.agent_db_path,
        rail_db_path=args.rail_db or settings.db_95306_path,
        project_id=args.project_id,
        release_batch_id=args.release_batch_id,
        candidate_ids=args.candidate_id,
        inspection_json_paths=args.inspection_json,
        run_mode=run_mode,
        operator_note=args.operator_note or "",
        window_minutes=args.window_minutes,
    )
    report = result.to_report_dict()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if run_mode == "commit" and not result.satisfied:
        raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpsDataHub business query/controlled execution CLI")
    parser.add_argument("--config", default=None, help="ops-data-hub config/settings.yaml path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("finalize-inspection", help="候选检装车单 -> 95306 校验 -> 正式 shipment_release_batch_matches 入库")
    p.add_argument("--release-batch-id", required=True)
    p.add_argument("--project-id", default="中唐特钢铁矿发运项目")
    p.add_argument("--candidate-id", action="append", default=[], help="inspection_ingestion_candidates.id，可重复")
    p.add_argument("--inspection-json", action="append", default=[], help="检装车 JSON 路径，可重复")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--commit", action="store_true")
    p.add_argument("--operator-note", default="")
    p.add_argument("--business-db", default=None)
    p.add_argument("--rail-db", default=None)
    p.add_argument("--window-minutes", type=int, default=30)
    p.set_defaults(func=cmd_finalize_inspection)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
