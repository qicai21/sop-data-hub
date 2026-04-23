"""公共图片处理与模型调用工具

提取自各引擎中重复的 _prepare_preview / _extract_json_fragment / call_api 逻辑。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageOps


def prepare_preview(
    image_path: Path,
    max_edge: int = 1400,
    max_short_edge: int = 900,
    preview_dir: Path | None = None,
) -> Path:
    """统一的图片预处理：EXIF 修正 + 等比缩放

    Args:
        image_path: 原始图片路径
        max_edge: 最长边上限
        max_short_edge: 最短边上限
        preview_dir: 预览图存放目录，默认为原图同级 _previews/

    Returns:
        预览图路径
    """
    if preview_dir is None:
        preview_dir = image_path.parent / "_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = preview_dir / image_path.name

    if preview_path.exists():
        return preview_path

    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        longest = max(img.size)
        shortest = min(img.size)
        scale = min(1.0, max_edge / float(longest), max_short_edge / float(shortest))
        if scale < 1.0:
            new_size = (max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale)))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        img.save(preview_path, format="JPEG", quality=90, optimize=True)

    return preview_path


def extract_json_fragment(text: str) -> Any:
    """从模型输出中鲁棒提取 JSON 片段

    支持以下情况：
    - 纯 JSON 字符串
    - markdown 代码块包裹的 JSON
    - 混合文本中嵌入的 JSON 对象/数组
    """
    text = text.strip()

    # 去除 markdown 代码块
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 回退：查找第一个 [ 或 {
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch not in "[{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
            return obj
        except json.JSONDecodeError:
            continue

    raise ValueError(f"Could not parse JSON from model output: {text[:500]}")


def call_vlm_api(
    service_url: str,
    prompt: str,
    image_path: str,
    max_tokens: int = 256,
    temperature: float = 0.0,
    timeout: int = 180,
) -> tuple[Any, float]:
    """统一的 VLM API 调用

    Returns:
        (解析后的 JSON 对象, 耗时秒数)
    """
    started = time.perf_counter()
    resp = requests.post(
        service_url,
        json={
            "prompt": prompt,
            "image_path": str(image_path),
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    text = resp.json().get("text", "")
    elapsed = time.perf_counter() - started
    return extract_json_fragment(text), elapsed


def check_vlm_health(service_url: str = "http://127.0.0.1:8018") -> dict[str, Any]:
    """检查 VLM 服务健康状态"""
    results: dict[str, Any] = {}

    # Check Gemma4 (port 8018)
    try:
        resp = requests.get(f"{service_url}/health", timeout=5)
        data = resp.json()
        results["gemma4"] = {
            "status": "online",
            "model_loaded": data.get("model_loaded", False),
            "url": service_url,
        }
    except Exception as e:
        results["gemma4"] = {"status": "offline", "error": str(e), "url": service_url}

    # Check Qwen3-VL (port 8019)
    qwen_url = service_url.replace("8018", "8019")
    try:
        resp = requests.get(f"{qwen_url}/health", timeout=5)
        data = resp.json()
        results["qwen3vl"] = {
            "status": "online",
            "model_loaded": data.get("model_loaded", False),
            "url": qwen_url,
        }
    except Exception as e:
        results["qwen3vl"] = {"status": "offline", "error": str(e), "url": qwen_url}

    return results
