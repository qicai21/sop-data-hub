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
    agent_db_path: str = "data/agent.db"

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
        "vlm_service_url",
        "batch_log_interval",
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
        "OPS_HUB_VLM_SERVICE_URL": "vlm_service_url",
    }
    for env_key, attr in env_map.items():
        val = os.environ.get(env_key)
        if val:
            setattr(settings, attr, val)

    # 同步 agent_db_path 到环境变量（data_agent.db 通过环境变量读取）
    os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = str(settings.agent_db_path)

    return settings
