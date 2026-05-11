"""核心处理逻辑 — 单图处理 / 批量处理 / wx-ops-agent 钩子

这是 ops-data-hub 的执行引擎，连接 分类器 → 引擎 → 存储 的完整链路。
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ops_hub.config import Settings


def _sanitize_component(value: str) -> str:
    cleaned = "".join(ch for ch in str(value) if ch.isalnum() or ch in " _-()（）").strip()
    return cleaned or "unknown"


def _normalize_month_compact(value: str) -> str:
    text = str(value or "").strip().replace("/", "-")
    if not text:
        return ""
    if len(text) >= 7 and text[4] == "-":
        return text[:7].replace("-", "")
    if len(text) >= 6 and text[:6].isdigit():
        return text[:6]
    return _sanitize_component(text)


def _artifact_base_dir(root: str | Path, *, month_str: str = "", group_name: str = "") -> Path:
    base = Path(root)
    month = _normalize_month_compact(month_str)
    group = _sanitize_component(group_name) if group_name else ""
    if month and group:
        return base / month / group
    if group:
        return base / group
    if month:
        return base / month
    return base


def _write_status_file(
    settings: Settings,
    img: Path,
    result: "ProcessingResult",
    *,
    month_str: str = "",
    group_name: str = "",
) -> None:
    """Write a deterministic per-image state file so _raw is only an inbox, not the source of truth."""
    status_base = _artifact_base_dir(
        settings.classified_output_dir,
        month_str=month_str,
        group_name=group_name,
    )
    status_dir = status_base / "_status"
    status_dir.mkdir(parents=True, exist_ok=True)
    state = "failed" if result.error else ("extracted" if result.extraction_saved_path else "classified")
    payload = {
        "source_image_path": str(img),
        "source_file_name": img.name,
        "state": state,
        "category": result.category,
        "confidence": result.confidence,
        "classified_image_path": result.saved_path,
        "extraction_json_path": result.extraction_saved_path,
        "error": result.error,
        "elapsed_classify": result.elapsed_classify,
        "elapsed_extract": result.elapsed_extract,
    }
    if isinstance(result.extracted, dict):
        # Surface DB/business landing signals without duplicating the full OCR payload.
        for key in ("_agent_ingested", "_agent_ingest_error", "_agent_updated_ids", "_agent_update_error"):
            if key in result.extracted:
                payload[key] = result.extracted[key]
        for key in ("rows_count", "last_car_no"):
            if key in result.extracted:
                payload[key] = result.extracted[key]
        footer = result.extracted.get("footer")
        if isinstance(footer, dict):
            payload["footer"] = footer
    status_path = status_dir / f"{img.stem}.json"
    status_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    result.status_path = str(status_path)


@dataclass
class ProcessingResult:
    """单张图片的处理结果"""
    image_path: str
    category: str
    confidence: float
    saved_path: str = ""
    extracted: dict[str, Any] = field(default_factory=dict)
    extraction_saved_path: str = ""
    status_path: str = ""
    error: str = ""
    elapsed_classify: float = 0.0
    elapsed_extract: float = 0.0

    @property
    def success(self) -> bool:
        return not self.error

    @property
    def was_extracted(self) -> bool:
        return bool(self.extracted)


def process_new_image(
    image_path: str | Path,
    settings: Settings,
    *,
    force_extract: bool = False,
    month_str: str = "",
    group_name: str = "",
) -> ProcessingResult:
    """处理单张新图片：分类 → 归档 → 按需识别

    这是 wx-ops-agent 的钩子入口。

    Args:
        image_path: 图片绝对路径
        settings: 运行时配置
        force_extract: 强制触发深度识别（忽略 auto_extract_categories 限制）

    Returns:
        ProcessingResult
    """
    from ops_hub.classifier.classifier import BusinessGroupImageClassifier
    from ops_hub.utils.image_utils import extract_json_fragment

    img = Path(image_path)
    if not img.exists():
        return ProcessingResult(
            image_path=str(img),
            category="error",
            confidence=0.0,
            error=f"图片不存在: {img}",
        )

    result = ProcessingResult(image_path=str(img), category="", confidence=0.0)

    # ── Step 1: 分类 ────────────────────────────────
    try:
        t0 = time.perf_counter()
        classifier = _get_classifier(settings.vlm_service_url)
        cls_result = classifier.classify(str(img))
        result.elapsed_classify = time.perf_counter() - t0
        result.category = cls_result.category
        result.confidence = cls_result.confidence
    except Exception as e:
        result.error = f"分类失败: {e}"
        result.category = "error"
        try:
            _write_status_file(settings, img, result, month_str=month_str, group_name=group_name)
        except Exception:
            pass
        return result

    # ── Step 2: 归档图片到分类目录 ──────────────────
    try:
        output_dir = _artifact_base_dir(
            settings.classified_output_dir,
            month_str=month_str,
            group_name=group_name,
        ) / result.category
        output_dir.mkdir(parents=True, exist_ok=True)
        dest = output_dir / img.name
        if not dest.exists():
            shutil.copy2(img, dest)
        result.saved_path = str(dest)
    except Exception as e:
        result.error = f"归档失败: {e}"
        try:
            _write_status_file(settings, img, result, month_str=month_str, group_name=group_name)
        except Exception:
            pass
        return result

    # ── Step 3: 按需触发深度识别 ────────────────────
    should_extract = force_extract or (result.category in settings.auto_extract_categories)
    if should_extract:
        try:
            t0 = time.perf_counter()
            extracted = _run_extraction(result.category, str(img), settings)
            result.elapsed_extract = time.perf_counter() - t0
            result.extracted = extracted

            # 保存识别结果
            if extracted:
                if month_str or group_name:
                    ext_dir = _artifact_base_dir(
                        settings.classified_output_dir,
                        month_str=month_str,
                        group_name=group_name,
                    ) / "extractions" / result.category
                else:
                    ext_dir = Path(settings.extraction_output_dir) / result.category
                ext_dir.mkdir(parents=True, exist_ok=True)
                json_name = f"{img.stem}_result.json"
                json_path = ext_dir / json_name
                json_path.write_text(
                    json.dumps(extracted, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                result.extraction_saved_path = str(json_path)
        except Exception as e:
            result.error = f"识别失败: {e}"

    try:
        _write_status_file(settings, img, result, month_str=month_str, group_name=group_name)
    except Exception:
        pass

    return result


def batch_process(
    source_dir: str | Path,
    settings: Settings,
    *,
    force_extract: bool = False,
    extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp"),
    callback: Any = None,
) -> list[ProcessingResult]:
    """批量处理目录下所有图片

    用于首次落地时处理历史积累数据。

    Args:
        source_dir: 图片源目录（递归扫描）
        settings: 运行时配置
        force_extract: 强制对所有图片触发深度识别
        extensions: 支持的图片扩展名
        callback: 每处理完一张图片的回调 fn(index, total, result)

    Returns:
        所有处理结果列表
    """
    src = Path(source_dir)
    if not src.exists():
        raise FileNotFoundError(f"源目录不存在: {src}")

    # 扫描所有图片
    images = sorted(
        f for f in src.rglob("*")
        if f.suffix.lower() in extensions and f.is_file()
    )

    if not images:
        print(f"⚠️  目录中没有图片: {src}")
        return []

    total = len(images)
    print(f"📦 发现 {total} 张图片，开始批量处理...")

    settings.ensure_dirs()
    results: list[ProcessingResult] = []
    success_count = 0
    extract_count = 0
    start_time = time.perf_counter()

    for idx, img_path in enumerate(images, 1):
        result = process_new_image(img_path, settings, force_extract=force_extract)
        results.append(result)

        if result.success:
            success_count += 1
        if result.was_extracted:
            extract_count += 1

        # 进度输出
        if idx % settings.batch_log_interval == 0 or idx == total:
            elapsed = time.perf_counter() - start_time
            rate = idx / elapsed if elapsed > 0 else 0
            print(
                f"  [{idx}/{total}] "
                f"{result.category} | "
                f"成功 {success_count} | 识别 {extract_count} | "
                f"{rate:.1f} 张/秒"
            )

        if callback:
            callback(idx, total, result)

    elapsed_total = time.perf_counter() - start_time
    print(f"\n✅ 批量处理完成: {success_count}/{total} 成功, {extract_count} 张触发识别, 耗时 {elapsed_total:.1f}s")

    # 输出分类统计
    category_counts: dict[str, int] = {}
    for r in results:
        category_counts[r.category] = category_counts.get(r.category, 0) + 1
    print("\n📊 分类统计:")
    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
        marker = " ⚡" if cat in settings.auto_extract_categories else ""
        print(f"  {cat}: {count}{marker}")

    return results


# ── 内部工具 ─────────────────────────────────────────

# 单例缓存，避免批处理时重复实例化
_classifier_cache: dict[str, Any] = {}


def _get_classifier(service_url: str):
    """获取分类器单例"""
    from ops_hub.classifier.classifier import BusinessGroupImageClassifier

    if service_url not in _classifier_cache:
        _classifier_cache[service_url] = BusinessGroupImageClassifier(service_url=service_url)
    return _classifier_cache[service_url]


def _run_extraction(category: str, image_path: str, settings: Settings) -> dict[str, Any]:
    """根据分类结果调用对应识别引擎"""
    service_url = settings.vlm_service_url

    if category == "检装车通知单":
        from ops_hub.engines.inspection_slip import InspectionSlipEngine
        engine = InspectionSlipEngine(service_url=service_url)
        return engine.process_image(image_path)

    elif category == "出港计划通知单":
        from ops_hub.engines.departure_plan import DeparturePlanEngine
        engine = DeparturePlanEngine(service_url=service_url)
        result = engine.process_image(image_path)

        # 自动导入放货批次数据
        if result and result.get("is_target"):
            try:
                from ops_hub.data_agent.agent import BusinessDataAgent
                agent = BusinessDataAgent()
                records = agent.ingest_release_batch(result, source_file_name=Path(image_path).name)
                result["_agent_ingested"] = len(records)
            except Exception as e:
                result["_agent_ingest_error"] = str(e)

        return result

    elif category == "手写箱号车号表":
        from ops_hub.engines.handwritten_list import HandwrittenListEngine
        engine = HandwrittenListEngine(service_url=service_url)
        return engine.process_image(image_path)

    elif category == "耗材统计表":
        from ops_hub.engines.materials_stats import MaterialsStatsEngine
        engine = MaterialsStatsEngine(service_url=service_url)
        return engine.process_image(image_path)

    return {}
