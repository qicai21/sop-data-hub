"""sop-data-hub 命令行工具

用法:
    python -m sop_hub process <image>           处理单张图片 (微信新图钩子)
    python -m sop_hub batch <dir>               批量处理目录下所有图片
    python -m sop_hub classify <image>          分类单张图片
    python -m sop_hub inspect <image>           识别检装车通知单
    python -m sop_hub departure <image>         识别出港计划通知单
    python -m sop_hub handwritten <image>       识别手写箱号车号表
    python -m sop_hub fix-container <7digits>   校验/补全箱号
    python -m sop_hub health                    检查 VLM 服务状态
    python -m sop_hub ingest <json_file>        导入放货批次数据
    python -m sop_hub list-batches              列出放货批次
    python -m sop_hub pending drop <audit_id>   把 dashboard 待落实项标为 dropped

注:看板自身改 CLI(scripts/cli_dashboard.py),不再产 dispatch_board.html
   + dispatch_board_data.json。相关子命令于 2026-06-02 dead-code 清理时一并删除。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


from pathlib import Path


def cmd_process(args: argparse.Namespace) -> None:
    from sop_hub.config import load_settings
    from sop_hub.runner import process_new_image

    settings = load_settings(args.config)
    if args.service_url:
        settings.vlm_service_url = args.service_url

    print(f"🔄 开始处理图片: {args.image}")
    result = process_new_image(args.image, settings, force_extract=args.force_extract)
    
    if not result.success:
        print(f"❌ 处理失败: {result.error}")
        sys.exit(1)
        
    print(f"✅ 分类: {result.category} (置信度: {result.confidence:.2f})")
    print(f"   归档至: {result.saved_path}")
    if result.was_extracted:
        print(f"   已深度识别，结果保存至: {result.extraction_saved_path}")
    elif result.category in settings.auto_extract_categories:
        print("   ⚠️ 触发深度识别但未成功提取数据。")
    else:
        print("   📌 该类别无需深度识别。")


def cmd_batch(args: argparse.Namespace) -> None:
    from sop_hub.config import load_settings
    from sop_hub.runner import batch_process

    settings = load_settings(args.config)
    if args.service_url:
        settings.vlm_service_url = args.service_url

    batch_process(args.dir, settings, force_extract=args.force_extract)


def cmd_classify(args: argparse.Namespace) -> None:
    from sop_hub.classifier.classifier import BusinessGroupImageClassifier

    service_url = args.service_url or "http://127.0.0.1:8021/v1/chat/completions"
    classifier = BusinessGroupImageClassifier(
        service_url=service_url,
        openai_model="/Users/qicai21/models/Qwen3.6-35B-A3B-4bit" if "/v1" in service_url else None,
    )
    result = classifier.classify(args.image)
    output = {
        "category": result.category,
        "confidence": result.confidence,
        "detected_title": result.detected_title,
        "evidence": result.evidence,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


def cmd_inspect(args: argparse.Namespace) -> None:
    from sop_hub.engines.inspection_slip import InspectionSlipEngine

    service_url = args.service_url or "http://127.0.0.1:8021/v1/chat/completions"
    engine = InspectionSlipEngine(service_url=service_url)
    result = engine.process_image(args.image)
    _output_result(result, args.output)


def cmd_departure(args: argparse.Namespace) -> None:
    from sop_hub.engines.departure_plan import DeparturePlanEngine

    service_url = args.service_url or "http://127.0.0.1:8021/v1/chat/completions"
    engine = DeparturePlanEngine(service_url=service_url)
    result = engine.process_image(args.image)
    _output_result(result, args.output)


def cmd_handwritten(args: argparse.Namespace) -> None:
    from sop_hub.engines.handwritten_list import HandwrittenListEngine

    service_url = args.service_url or "http://127.0.0.1:8021/v1/chat/completions"
    engine = HandwrittenListEngine(service_url=service_url)
    result = engine.process_image(args.image)
    _output_result(result, args.output)


def cmd_fix_container(args: argparse.Namespace) -> None:
    from sop_hub.utils.container_fixer import complete_container_number

    result, msg = complete_container_number(args.digits)
    if result:
        print(f"✅ {result}  ({msg})")
    else:
        print(f"❌ {msg}")
        sys.exit(1)


def cmd_health(args: argparse.Namespace) -> None:
    from sop_hub.utils.image_utils import check_vlm_health

    service_url = args.service_url or "http://127.0.0.1:8021"
    results = check_vlm_health(service_url)
    for name, info in results.items():
        status = info["status"]
        icon = "🟢" if status == "online" else "🔴"
        loaded = info.get("model_loaded", "N/A")
        url = info.get("url", "")
        print(f"{icon} {name}: {status} (model_loaded={loaded}) @ {url}")
        if status == "offline":
            print(f"   Error: {info.get('error', 'unknown')}")


def cmd_ingest(args: argparse.Namespace) -> None:
    from sop_hub.data_agent.agent import BusinessDataAgent

    agent = BusinessDataAgent()
    records = agent.ingest_release_batch_file(args.json_file)
    print(f"导入完成: {len(records)} 条批次记录")
    for r in records:
        print(f"  - {r.ship_name} / {r.cargo_name} / {r.batch_date} / {r.batch_quantity}吨")


def cmd_list_batches(args: argparse.Namespace) -> None:
    from sop_hub.data_agent.agent import BusinessDataAgent

    agent = BusinessDataAgent()
    records = agent.list_release_batches()
    if not records:
        print("暂无放货批次记录。")
        return
    print(f"共 {len(records)} 条批次记录:")
    for r in records:
        weighed = "✅" if r.is_weighed else "❌"
        print(f"  [{r.id[:8]}] {r.ship_name} / {r.cargo_name} → {r.destination_station} "
              f"| {r.batch_quantity}吨 | 发运状态:{r.dispatch_status} | 过磅{weighed} | {r.notice_date}")


def cmd_refresh_match_rules(args: argparse.Namespace) -> None:
    from sop_hub.data_agent.agent import BusinessDataAgent

    agent = BusinessDataAgent()
    count = agent.refresh_release_dispatch_match_rules()
    print(f"已刷新 release_dispatch_match_rules: {count} 条 release_batches 已维护")


def cmd_complete_match_rule(args: argparse.Namespace) -> None:
    from sop_hub.data_agent.agent import BusinessDataAgent

    agent = BusinessDataAgent()
    ok = agent.complete_release_dispatch_match_rule(args.release_batch_id, manual_note=args.note)
    if ok:
        print(f"已下表 release_batch_id={args.release_batch_id}")
    else:
        print(f"未找到匹配规则 release_batch_id={args.release_batch_id}")
        sys.exit(1)


def cmd_reopen_match_rule(args: argparse.Namespace) -> None:
    from sop_hub.data_agent.agent import BusinessDataAgent

    agent = BusinessDataAgent()
    ok = agent.force_reopen_release_dispatch_match_rule(args.release_batch_id, manual_note=args.note)
    if ok:
        print(f"已重新打开 release_batch_id={args.release_batch_id}，可按用户指令补充匹配")
    else:
        print(f"未找到放货记录 release_batch_id={args.release_batch_id}")
        sys.exit(1)


def _output_result(result: dict, output_path: str | None) -> None:
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if output_path:
        Path(output_path).write_text(text, encoding="utf-8")
        print(f"结果已保存到: {output_path}")
    else:
        print(text)


def cmd_pending_drop(args: argparse.Namespace) -> None:
    """把 image_ingestion_audit 行标记为 discarded_by_operator(看板待落实项移除)。"""
    import sqlite3
    from sop_hub.data_agent.db import get_db_path

    audit_id = args.audit_id
    reason = args.reason or "user_dropped_from_dispatch_board"

    db_path = get_db_path()
    with sqlite3.connect(str(db_path)) as db:
        db.row_factory = sqlite3.Row
        existing = db.execute(
            "SELECT id, status, reason, db_action FROM image_ingestion_audit WHERE id = ?",
            (audit_id,),
        ).fetchone()
        if not existing:
            print(f"❌ image_ingestion_audit record not found: {audit_id}", file=sys.stderr)
            sys.exit(1)
        prev_status = existing["status"]
        prev_reason = existing["reason"]
        db.execute(
            "UPDATE image_ingestion_audit "
            "SET db_action = 'discarded_by_operator', reason = ?, status = 'ingested' "
            "WHERE id = ?",
            (reason, audit_id),
        )
        db.commit()
    print(f"✅ pending item dropped: audit_id={audit_id}")
    print(f"   prev status={prev_status} reason={prev_reason}")
    print(f"   new reason={reason}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ops-hub",
        description="运营数据枢纽 — 图像识别、数据处理与Agent自动化",
    )
    parser.add_argument("-c", "--config", help="配置文件路径 (默认加载 config/settings.yaml)")
    parser.add_argument("--service-url", help="VLM 服务地址 (默认 http://127.0.0.1:8021/v1/chat/completions)")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # process
    p_proc = subparsers.add_parser("process", help="处理单张图片 (分类+归档+按需识别)")
    p_proc.add_argument("image", help="图片路径")
    p_proc.add_argument("--force-extract", action="store_true", help="强制触发深度识别")
    p_proc.set_defaults(func=cmd_process)

    # batch
    p_batch = subparsers.add_parser("batch", help="批量处理目录下的所有图片")
    p_batch.add_argument("dir", help="图片源目录")
    p_batch.add_argument("--force-extract", action="store_true", help="强制对所有图片触发深度识别")
    p_batch.set_defaults(func=cmd_batch)

    # classify
    p_cls = subparsers.add_parser("classify", help="分类单张图片")
    p_cls.add_argument("image", help="图片路径")
    p_cls.set_defaults(func=cmd_classify)

    # inspect
    p_insp = subparsers.add_parser("inspect", help="识别检装车通知单")
    p_insp.add_argument("image", help="图片路径")
    p_insp.add_argument("-o", "--output", help="输出 JSON 文件路径")
    p_insp.set_defaults(func=cmd_inspect)

    # departure
    p_dep = subparsers.add_parser("departure", help="识别出港计划通知单")
    p_dep.add_argument("image", help="图片路径")
    p_dep.add_argument("-o", "--output", help="输出 JSON 文件路径")
    p_dep.set_defaults(func=cmd_departure)

    # handwritten
    p_hw = subparsers.add_parser("handwritten", help="识别手写箱号车号表")
    p_hw.add_argument("image", help="图片路径")
    p_hw.add_argument("-o", "--output", help="输出 JSON 文件路径")
    p_hw.set_defaults(func=cmd_handwritten)

    # fix-container
    p_fix = subparsers.add_parser("fix-container", help="校验/补全箱号")
    p_fix.add_argument("digits", help="7位数字 (6位序列号 + 1位校验位)")
    p_fix.set_defaults(func=cmd_fix_container)

    # health
    p_health = subparsers.add_parser("health", help="检查 VLM 服务状态")
    p_health.set_defaults(func=cmd_health)

    # ingest
    p_ingest = subparsers.add_parser("ingest", help="导入放货批次 JSON")
    p_ingest.add_argument("json_file", help="识别结果 JSON 文件路径")
    p_ingest.set_defaults(func=cmd_ingest)

    # list-batches
    p_list = subparsers.add_parser("list-batches", help="列出放货批次")
    p_list.set_defaults(func=cmd_list_batches)

    # refresh-match-rules
    p_refresh_rules = subparsers.add_parser("refresh-match-rules", help="从 release_batches 刷新 active 放货发运匹配规则")
    p_refresh_rules.set_defaults(func=cmd_refresh_match_rules)

    # complete-match-rule
    p_complete_rule = subparsers.add_parser("complete-match-rule", help="将指定 release_batch_id 的发运状态标记为 completed/完结")
    p_complete_rule.add_argument("release_batch_id", help="release_batches.id")
    p_complete_rule.add_argument("--note", default=None, help="手动备注")
    p_complete_rule.set_defaults(func=cmd_complete_match_rule)

    # reopen-match-rule
    p_reopen_rule = subparsers.add_parser("reopen-match-rule", help="按用户指令重新打开已完结批次，允许补充发车匹配")
    p_reopen_rule.add_argument("release_batch_id", help="release_batches.id")
    p_reopen_rule.add_argument("--note", default=None, help="手动备注")
    p_reopen_rule.set_defaults(func=cmd_reopen_match_rule)

    # pending
    p_pending = subparsers.add_parser("pending", help="处理 dashboard 待落实事项")
    p_pending_sub = p_pending.add_subparsers(dest="pending_action", help="pending 操作")
    p_pending_drop = p_pending_sub.add_parser("drop", help="删除 dashboard 待落实项 (标记为 discarded_by_operator)")
    p_pending_drop.add_argument("audit_id", help="image_ingestion_audit.id")
    p_pending_drop.add_argument("--reason", default="user_dropped_from_dispatch_board", help="drop 原因")
    p_pending_drop.set_defaults(func=cmd_pending_drop)

    # dispatch-board 子命令在 2026-06-02 移除(HTML 看板退役,替代为
    # scripts/cli_dashboard.py)。如要"看板",直接 `python3 scripts/cli_dashboard.py`。

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
