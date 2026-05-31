"""项目配置管理

支持从 config/settings.yaml 或环境变量加载配置。
环境变量优先级高于 YAML 文件。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"


@dataclass
class Settings:
    """运行时配置"""

    # ── 路径 ──────────────────────────────────────────
    # 微信图片来源目录（wx-ops-agent 落盘的原图目录）
    wechat_images_dir: str = ""

    # 分类处理后的图片保存目录
    classified_output_dir: str = "data/classified"

    # 95306 铁路发运数据库路径
    db_95306_path: str = ""

    # 结构化识别结果保存目录
    extraction_output_dir: str = "data/extractions"

    # 放货批次数据库路径
    agent_db_path: str = "data/sop_agent.db"
    # 测试专用数据库路径
    test_agent_db_path: str = "data/test_sop_agent.db"

    # ── VLM 服务 ─────────────────────────────────────
    vlm_service_url: str = "http://127.0.0.1:8018/generate"

    # ── 自动识别策略 ─────────────────────────────────
    # 分类命中这些类别后自动触发深度识别
    auto_extract_categories: list[str] = field(default_factory=lambda: [
        "出港计划通知单",
        "检装车通知单",
    ])

    # ── 处理策略 ─────────────────────────────────────
    # 批处理时的并发控制（VLM 是串行的，这里主要控制日志输出）
    batch_log_interval: int = 10

    # ── wx-ops-agent 控制 ────────────────────────────
    # 轮询时间间隔（秒）
    daemon_interval: int = 60
    # [兼容保留] 跟踪规则文件路径 (tracking_rules.yaml)
    # 用途：在全量替换为 ProjectSOP 前，作为 agent 本地失联或测试时的备用兜底。
    # 移除条件：当所有旧系统完全弃用 tracking_rules.yaml 且 agent 彻底完成基于 sop-data-hub 通讯的改造后移除。
    tracking_rules_path: str = ""
    
    # [权威内存状态] 唯一权威的业务追踪任务集合
    # 用途：由 project_sops (*.yaml) 【权威输入】解析而来，持有所有的业务目标 (target_node) 与路由逻辑。
    tracking_tasks: list[Any] = field(default_factory=list)
    
    # [派生兼容输出] 监听群组列表
    # 用途：由于现网的 wx-ops-agent (TrackerEngine) 强依赖 listener_objects 格式，此列表专门用于将 tracking_tasks 降维、提纯为物理指令（只包含群ID和拉图设置）。
    # 移除条件：当 wx-ops-agent 重构监听机制，能够直接接受通用指令树，不再依赖硬编码的 group_map 对象时可废弃。
    monitored_groups: list[dict[str, Any]] = field(default_factory=list)

    def ensure_dirs(self) -> None:
        """确保所有输出目录存在"""
        for attr in ("classified_output_dir", "extraction_output_dir"):
            path = Path(getattr(self, attr))
            if path:
                path.mkdir(parents=True, exist_ok=True)
        # agent_db 的父目录
        db_parent = Path(self.agent_db_path).parent
        if db_parent:
            db_parent.mkdir(parents=True, exist_ok=True)


def build_monitored_groups(tasks: list[Any]) -> list[dict[str, Any]]:
    """
    [派生函数] 将全量业务任务降维为纯物理指令。
    绝对不允许在此结构中泄露 target_node、routing 或 project_id。
    """
    group_map = {}
    for t in tasks:
        pull_image_target = getattr(t, "group_lookup_id", None) or f"[{t.group_id}]"
        if t.group_id not in group_map:
            group_map[t.group_id] = {
                "id": t.group_id,
                "name": t.group_name,
                "wxid": t.wxid,
                "pull_image_target": pull_image_target,
                "auto_pull_image": t.pull_image,
                "listen_options": {
                    "image": t.listen_options.get("image", False),
                    "text": t.listen_options.get("text", False),
                    "file": t.listen_options.get("file", False),
                    "voice": t.listen_options.get("voice", False)
                }
            }
        else:
            entry = group_map[t.group_id]
            # 如果已有条目 wxid 为空，后续任务带了有效 wxid，则覆盖
            if not entry.get("wxid") and t.wxid:
                entry["wxid"] = t.wxid
            existing_opts = entry["listen_options"]
            for k in ["image", "text", "file", "voice"]:
                existing_opts[k] = existing_opts[k] or t.listen_options.get(k, False)
            entry["auto_pull_image"] = entry["auto_pull_image"] or t.pull_image
            
    return list(group_map.values())


def load_settings(config_path: str | Path | None = None) -> Settings:
    """加载配置

    优先级: 环境变量 > YAML 文件 > 默认值
    """
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    file_data: dict[str, Any] = {}

    if path.exists():
        raw = path.read_text(encoding="utf-8")
        if yaml is not None:
            file_data = yaml.safe_load(raw) or {}
        else:
            # 无 pyyaml 时尝试简单 key: value 解析
            import json
            try:
                file_data = json.loads(raw)
            except Exception:
                pass

    settings = Settings()

    # 从 YAML 文件填充
    for key in (
        "wechat_images_dir",
        "classified_output_dir",
        "db_95306_path",
        "extraction_output_dir",
        "agent_db_path",
        "test_agent_db_path",
        "vlm_service_url",
        "batch_log_interval",
        "daemon_interval",
        "daemon_message_limit",
        "tracking_rules_path",
        "monitored_groups",
    ):
        if key in file_data:
            setattr(settings, key, file_data[key])

    if "auto_extract_categories" in file_data:
        val = file_data["auto_extract_categories"]
        if isinstance(val, list):
            settings.auto_extract_categories = val

    # 环境变量覆盖（OPS_HUB_ 前缀）
    env_map = {
        "OPS_HUB_WECHAT_IMAGES_DIR": "wechat_images_dir",
        "OPS_HUB_CLASSIFIED_OUTPUT_DIR": "classified_output_dir",
        "OPS_HUB_DB_95306_PATH": "db_95306_path",
        "OPS_HUB_EXTRACTION_OUTPUT_DIR": "extraction_output_dir",
        "OPS_HUB_AGENT_DB_PATH": "agent_db_path",
        "OPS_HUB_TEST_AGENT_DB_PATH": "test_agent_db_path",
        "OPS_HUB_VLM_SERVICE_URL": "vlm_service_url",
        "OPS_HUB_DAEMON_INTERVAL": "daemon_interval",
        "OPS_HUB_DAEMON_MESSAGE_LIMIT": "daemon_message_limit",
        "OPS_HUB_TRACKING_RULES_PATH": "tracking_rules_path",
    }
    for env_key, attr in env_map.items():
        val = os.environ.get(env_key)
        if val:
            setattr(settings, attr, val)

    # 核心：将 project_sops 配置作为系统唯一权威解析入口
    try:
        from ops_hub.models.project_sop import load_all_tracking_tasks
        # Default fixtures dir
        sops_dir = Path(__file__).resolve().parents[2] / "config" / "project_sops"
        tasks = load_all_tracking_tasks(sops_dir)
        settings.tracking_tasks = tasks
        
        # [兼容保留] 派生生成 generic monitored_groups list
        settings.monitored_groups = build_monitored_groups(tasks)
            
    except Exception as e:
        import logging
        logging.warning(f"Failed to load project SOPs: {e}")

    # 同步 agent_db_path 到环境变量（data_agent.db 通过环境变量读取）
    os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = str(settings.agent_db_path)

    return settings
