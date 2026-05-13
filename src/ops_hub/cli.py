"""ops-data-hub 命令行工具

用法:
    python -m ops_hub process <image>           处理单张图片 (微信新图钩子)
    python -m ops_hub batch <dir>               批量处理目录下所有图片
    python -m ops_hub classify <image>          分类单张图片
    python -m ops_hub inspect <image>           识别检装车通知单
    python -m ops_hub departure <image>         识别出港计划通知单
    python -m ops_hub handwritten <image>       识别手写箱号车号表
    python -m ops_hub fix-container <7digits>   校验/补全箱号
    python -m ops_hub health                    检查 VLM 服务状态
    python -m ops_hub ingest <json_file>        导入放货批次数据
    python -m ops_hub list-batches              列出放货批次
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


from pathlib import Path


def cmd_process(args: argparse.Namespace) -> None:
    from ops_hub.config import load_settings
    from ops_hub.runner import process_new_image

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
    from ops_hub.config import load_settings
    from ops_hub.runner import batch_process

    settings = load_settings(args.config)
    if args.service_url:
        settings.vlm_service_url = args.service_url

    batch_process(args.dir, settings, force_extract=args.force_extract)


def cmd_classify(args: argparse.Namespace) -> None:
    from ops_hub.classifier.classifier import BusinessGroupImageClassifier

    service_url = args.service_url or "http://127.0.0.1:8018/generate"
    classifier = BusinessGroupImageClassifier(service_url=service_url)
    result = classifier.classify(args.image)
    output = {
        "category": result.category,
        "confidence": result.confidence,
        "detected_title": result.detected_title,
        "evidence": result.evidence,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


def cmd_inspect(args: argparse.Namespace) -> None:
    from ops_hub.engines.inspection_slip import InspectionSlipEngine

    service_url = args.service_url or "http://127.0.0.1:8018/generate"
    engine = InspectionSlipEngine(service_url=service_url)
    result = engine.process_image(args.image)
    _output_result(result, args.output)


def cmd_departure(args: argparse.Namespace) -> None:
    from ops_hub.engines.departure_plan import DeparturePlanEngine

    service_url = args.service_url or "http://127.0.0.1:8018/generate"
    engine = DeparturePlanEngine(service_url=service_url)
    result = engine.process_image(args.image)
    _output_result(result, args.output)


def cmd_handwritten(args: argparse.Namespace) -> None:
    from ops_hub.engines.handwritten_list import HandwrittenListEngine

    service_url = args.service_url or "http://127.0.0.1:8018/generate"
    engine = HandwrittenListEngine(service_url=service_url)
    result = engine.process_image(args.image)
    _output_result(result, args.output)


def cmd_fix_container(args: argparse.Namespace) -> None:
    from ops_hub.utils.container_fixer import complete_container_number

    result, msg = complete_container_number(args.digits)
    if result:
        print(f"✅ {result}  ({msg})")
    else:
        print(f"❌ {msg}")
        sys.exit(1)


def cmd_health(args: argparse.Namespace) -> None:
    from ops_hub.utils.image_utils import check_vlm_health

    service_url = args.service_url or "http://127.0.0.1:8018"
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
    from ops_hub.data_agent.agent import BusinessDataAgent

    agent = BusinessDataAgent()
    records = agent.ingest_release_batch_file(args.json_file)
    print(f"导入完成: {len(records)} 条批次记录")
    for r in records:
        print(f"  - {r.ship_name} / {r.cargo_name} / {r.batch_date} / {r.batch_quantity}吨")


def cmd_list_batches(args: argparse.Namespace) -> None:
    from ops_hub.data_agent.agent import BusinessDataAgent

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
    from ops_hub.data_agent.agent import BusinessDataAgent

    agent = BusinessDataAgent()
    count = agent.refresh_release_dispatch_match_rules()
    print(f"已刷新 release_dispatch_match_rules: {count} 条 release_batches 已维护")


def cmd_complete_match_rule(args: argparse.Namespace) -> None:
    from ops_hub.data_agent.agent import BusinessDataAgent

    agent = BusinessDataAgent()
    ok = agent.complete_release_dispatch_match_rule(args.release_batch_id, manual_note=args.note)
    if ok:
        print(f"已下表 release_batch_id={args.release_batch_id}")
    else:
        print(f"未找到匹配规则 release_batch_id={args.release_batch_id}")
        sys.exit(1)


def cmd_reopen_match_rule(args: argparse.Namespace) -> None:
    from ops_hub.data_agent.agent import BusinessDataAgent

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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ops-hub",
        description="运营数据枢纽 — 图像识别、数据处理与Agent自动化",
    )
    parser.add_argument("-c", "--config", help="配置文件路径 (默认加载 config/settings.yaml)")
    parser.add_argument("--service-url", help="VLM 服务地址 (默认 http://127.0.0.1:8018/generate)")
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

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
