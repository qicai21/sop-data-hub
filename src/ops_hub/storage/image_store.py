"""图片归档存储管理器

基于 wx-ops-agent 中 ImageStore 的接口设计，实现本地版本。
负责将业务图片按 群名/年月/分类/ 结构归档存储。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Union


def _sanitize_name(name: str) -> str:
    """清洗文件/目录名中的非法字符"""
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in " _-()（）").strip()
    return cleaned or "unknown"


def build_group_image_subdir(
    group_name: str,
    category: str,
    date_str: str,
) -> Path:
    """生成归档子目录路径: 群名/yy-mm/分类/

    Args:
        group_name: 群名称
        category: 分类名称（如 "检装车通知单"）
        date_str: 日期字符串，格式 "yy-mm"
    """
    return (
        Path(_sanitize_name(group_name))
        / _sanitize_name(date_str)
        / _sanitize_name(category)
    )


class ImageStore:
    """图片资产管理器 — 按群名/年月/分类归档存储"""

    def __init__(self, base_dir: Union[str, Path] = "data/images") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_group_image(
        self,
        base_dir: Union[str, Path],
        subdir: Union[str, Path],
        filename: str,
        image_data: bytes,
    ) -> str:
        """将图片数据保存到归档目录

        Args:
            base_dir: 基础存储目录
            subdir: 子目录路径（通常由 build_group_image_subdir 生成）
            filename: 文件名
            image_data: 图片二进制数据

        Returns:
            保存后的绝对路径字符串
        """
        target_dir = Path(base_dir) / subdir
        target_dir.mkdir(parents=True, exist_ok=True)

        # 清洗文件名
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", filename)
        target_path = target_dir / safe_name

        target_path.write_bytes(image_data)
        return str(target_path.resolve())
