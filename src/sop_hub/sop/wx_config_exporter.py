"""导出 wx-ops-agent 启动所需的配置 JSON — Phase 3 解耦的桥梁。

历史:
- Phase 1/2 之前:wx-ops-agent run-daemon 启动时通过 sys.path 黑魔法
  `from sop_hub.config import load_settings`,拿 monitored_groups +
  tracking_tasks + daemon_* 参数。
- Phase 3(2026-06-02):反转方向。sop-data-hub 把这些**预计算好的**结果
  序列化到 JSON 落地,wx-ops-agent 启动时只 open + json.load,**不再跨仓 import**。

JSON 落点(约定):
  ~/projects/repos/wx-ops-agent/data/runtime/wx_config.json
可被环境变量 SOP_HUB_WX_CONFIG_OUT 覆盖。

何时跑:
- 手动:`python3 -m sop_hub.sop.wx_config_exporter`
- 自动:scripts/run_live_service.py 启动时调一次(下个 commit 加 hook)
- SOP yaml 改动后 → 重跑一次

注意:wx 端读这个 JSON 后才能确定监听哪些群。文件不存在 → wx 端走兼容
回退(读本仓 tracking_rules.yaml,缺 sop_route_contexts)。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger("sop_hub.wx_config_exporter")


DEFAULT_OUT_PATH = Path(
    "/Users/qicai21/projects/repos/wx-ops-agent/data/runtime/wx_config.json"
)


def _route_to_dict(route: Any) -> dict[str, Any]:
    if is_dataclass(route):
        return asdict(route)
    if isinstance(route, dict):
        return dict(route)
    return {
        "message_type": getattr(route, "message_type", ""),
        "trigger_condition": getattr(route, "trigger_condition", ""),
        "target_node": getattr(route, "target_node", ""),
        "save_db": getattr(route, "save_db", False),
        "send_report_to": getattr(route, "send_report_to", None),
        "report_targets": getattr(route, "report_targets", None),
    }


def _task_to_context(task: Any) -> dict[str, Any]:
    return {
        "project_id": str(getattr(task, "project_id", "") or ""),
        "group_id": str(getattr(task, "group_id", "") or ""),
        "group_name": str(getattr(task, "group_name", "") or ""),
        "wxid": str(getattr(task, "wxid", "") or ""),
        "routing": [
            _route_to_dict(r) for r in (getattr(task, "routing", None) or [])
        ],
    }


def build_wx_config_payload() -> dict[str, Any]:
    """从 sop 当前 settings 构造 wx 启动用的 config dict。"""
    from sop_hub.config import load_settings

    s = load_settings()

    monitored_groups = list(getattr(s, "monitored_groups", None) or [])
    tracking_tasks = list(getattr(s, "tracking_tasks", None) or [])
    sop_route_contexts = [_task_to_context(t) for t in tracking_tasks]

    payload: dict[str, Any] = {
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "sop_hub.wx_config_exporter",
        "monitored_groups": monitored_groups,
        "sop_route_contexts": sop_route_contexts,
        "daemon_interval": getattr(s, "daemon_interval", None),
        "daemon_message_limit": getattr(s, "daemon_message_limit", None),
        "tracking_rules_path": str(
            getattr(s, "tracking_rules_path", None) or ""
        ),
    }
    return payload


def write_wx_config(
    out_path: Path | str | None = None,
    *,
    create_parents: bool = True,
) -> Path:
    """构造 + 写到 JSON 文件。返回最终路径。"""
    target = Path(
        out_path
        or os.environ.get("SOP_HUB_WX_CONFIG_OUT")
        or DEFAULT_OUT_PATH
    ).expanduser()
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    payload = build_wx_config_payload()
    # 原子写:先写 .tmp 再 rename
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    tmp.replace(target)
    logger.info(
        "wx_config exported groups=%d tasks=%d → %s",
        len(payload["monitored_groups"]),
        len(payload["sop_route_contexts"]),
        target,
    )
    return target


# ── CLI ─────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Export wx-ops-agent runtime config from sop-data-hub settings.",
    )
    p.add_argument("--out", type=Path, default=None,
                   help=f"output JSON path (default env SOP_HUB_WX_CONFIG_OUT "
                        f"or {DEFAULT_OUT_PATH})")
    p.add_argument("--print", action="store_true",
                   help="print the payload to stdout instead of writing")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.print:
        payload = build_wx_config_payload()
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    write_wx_config(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
