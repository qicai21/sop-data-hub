#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ops_hub.config import load_settings  # noqa: E402
from ops_hub.data_agent.agent import BusinessDataAgent  # noqa: E402
from ops_hub.data_agent.dispatch_board import render_dispatch_board  # noqa: E402
from ops_hub.matching.inspection_95306_reconciler import reconcile_inspection_shipments  # noqa: E402


def _agent_for_business_db(db_path: str | Path | None) -> BusinessDataAgent:
    if db_path:
        os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = str(db_path)
    return BusinessDataAgent()


def _business_db_from_args(args: argparse.Namespace) -> str | Path | None:
    settings = load_settings(args.config)
    return args.business_db or settings.agent_db_path


def _run_reconcile(args: argparse.Namespace, *, deprecated_finalize_cli: bool = False) -> None:
    settings = load_settings(args.config)
    run_mode = "plan" if args.plan else "commit"
    result = reconcile_inspection_shipments(
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
    if deprecated_finalize_cli:
        report["deprecated_cli"] = "finalize-inspection is deprecated; use reconcile-inspection --plan/--commit"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if run_mode == "commit" and not result.safe_to_commit:
        raise SystemExit(2)


def cmd_reconcile_inspection(args: argparse.Namespace) -> None:
    _run_reconcile(args)


def cmd_finalize_inspection(args: argparse.Namespace) -> None:
    _run_reconcile(args, deprecated_finalize_cli=True)


def cmd_list_dispatch(args: argparse.Namespace) -> None:
    agent = _agent_for_business_db(_business_db_from_args(args))
    status = args.status
    if status == "all":
        status = None
    rows = agent.list_dispatch_rules(status=status)
    print(json.dumps({"count": len(rows), "rows": rows}, ensure_ascii=False, indent=2))


def cmd_set_dispatch_status(args: argparse.Namespace) -> None:
    agent = _agent_for_business_db(_business_db_from_args(args))
    changed = agent.update_release_dispatch_status(
        args.release_batch_id,
        args.status,
        manual_note=args.note or args.operator_note or "",
    )
    if not changed:
        raise SystemExit(f"release_batch_id not found: {args.release_batch_id}")
    row = agent.get(args.release_batch_id)
    print(json.dumps({"updated": True, "release_batch": row.to_dict() if row else None}, ensure_ascii=False, indent=2))


def cmd_assign_inspection_candidate(args: argparse.Namespace) -> None:
    agent = _agent_for_business_db(_business_db_from_args(args))
    assigned = agent.assign_inspection_candidate(
        args.candidate_id,
        args.release_batch_id,
        operator_note=args.operator_note or args.note or "",
    )
    print(json.dumps({"assigned": assigned, "candidate_id": args.candidate_id, "release_batch_id": args.release_batch_id}, ensure_ascii=False, indent=2))


def cmd_render_dispatch_board(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    output_path = args.output or "/Users/qicai21/projects/repos/business-system-docs/dashboard/dispatch_board.html"
    result = render_dispatch_board(
        business_db_path=args.business_db or settings.agent_db_path,
        rail_db_path=args.rail_db or settings.db_95306_path,
        output_path=output_path,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _add_reconcile_args(p: argparse.ArgumentParser, *, legacy_dry_run: bool = False) -> None:
    p.add_argument("--release-batch-id", required=True)
    p.add_argument("--project-id", default="中唐特钢铁矿发运项目")
    p.add_argument("--candidate-id", action="append", default=[], help="inspection_ingestion_candidates.id，可重复")
    p.add_argument("--inspection-json", action="append", default=[], help="检装车 JSON 路径，可重复")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="生成发运入库计划，不写 shipment_release_batch_matches")
    if legacy_dry_run:
        mode.add_argument("--dry-run", action="store_true", dest="plan", help="DEPRECATED: use --plan")
    mode.add_argument("--commit", action="store_true", help="safe_to_commit=true 时提交正式发运入库")
    p.add_argument("--operator-note", default="")
    p.add_argument("--business-db", default=None)
    p.add_argument("--rail-db", default=None)
    p.add_argument("--window-minutes", type=int, default=30)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpsDataHub business query/controlled execution CLI")
    parser.add_argument("--config", default=None, help="sop-data-hub config/settings.yaml path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("reconcile-inspection", help="检装车-95306 发运比对：生成发运入库计划或提交正式入库")
    _add_reconcile_args(p)
    p.set_defaults(func=cmd_reconcile_inspection)

    p = sub.add_parser("list-dispatch", help="查看当前发运中/发运索引批次")
    p.add_argument("--status", default="active", choices=["active", "in_progress", "completed", "suspended", "cancelled", "all"])
    p.add_argument("--business-db", default=None)
    p.set_defaults(func=cmd_list_dispatch)

    p = sub.add_parser("set-dispatch-status", help="设置 release_batch 的发运状态并同步发运索引")
    p.add_argument("--release-batch-id", required=True)
    p.add_argument("--status", required=True, choices=["active", "in_progress", "completed", "suspended", "cancelled"])
    p.add_argument("--note", default="")
    p.add_argument("--operator-note", default="")
    p.add_argument("--business-db", default=None)
    p.set_defaults(func=cmd_set_dispatch_status)

    p = sub.add_parser("assign-inspection-candidate", help="人工指认 inspection candidate 到 release_batch_id，不写正式 95306 表")
    p.add_argument("--candidate-id", required=True)
    p.add_argument("--release-batch-id", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--operator-note", default="")
    p.add_argument("--business-db", default=None)
    p.set_defaults(func=cmd_assign_inspection_candidate)

    p = sub.add_parser("render-dispatch-board", help="生成本地静态放货/发运/识别/匹配入库看板 HTML")
    p.add_argument("--business-db", default=None)
    p.add_argument("--rail-db", default=None, help="95306_collection.sqlite3；只读查询 shipment_release_batch_matches")
    p.add_argument("--output", default=None, help="输出 HTML 路径，默认写入 business-system-docs/dashboard/dispatch_board.html")
    p.set_defaults(func=cmd_render_dispatch_board)

    legacy = sub.add_parser("finalize-inspection", help="DEPRECATED: use reconcile-inspection --plan/--commit")
    _add_reconcile_args(legacy, legacy_dry_run=True)
    legacy.set_defaults(func=cmd_finalize_inspection)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
