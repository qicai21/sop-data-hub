"""核心处理逻辑 — 单图处理 / 批量处理 / wx-ops-agent 钩子

这是 sop-data-hub 的执行引擎，连接 分类器 → 引擎 → 存储 的完整链路。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
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
    """Return canonical artifact base.

    `month_str` is accepted for compatibility with wx-ops-agent callers, but the
    month must not create an extra top-level YYYYMM directory. Month separation
    belongs either in the raw chat-record path or metadata/status, not in the
    classified/extraction artifact hierarchy.
    """
    base = Path(root)
    group = _sanitize_component(group_name) if group_name else ""
    if group:
        return base / group
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
    if result.project_archive_paths:
        payload["project_archive_paths"] = result.project_archive_paths
    if isinstance(result.extracted, dict):
        # Surface DB/business landing signals without duplicating the full OCR payload.
        for key in ("_agent_ingested", "_agent_ingest_error", "_agent_updated_ids", "_agent_update_error"):
            if key in result.extracted:
                payload[key] = result.extracted[key]
        for key in ("rows_count", "last_car_no"):
            if key in result.extracted:
                payload[key] = result.extracted[key]
        plan = result.extracted.get("_agent_reconcile_plan")
        if isinstance(plan, dict):
            payload["reconcile_plan"] = plan.get("reconcile_plan") or plan
            payload["safe_to_commit"] = bool(plan.get("safe_to_commit"))
            payload["requires_manual_review"] = bool(plan.get("requires_manual_review"))
            payload["review_reasons"] = plan.get("review_reasons") or []
            payload["planned_write_count"] = int(plan.get("planned_write_count") or 0)
            payload["excluded_count"] = len(plan.get("excluded") or [])
        footer = result.extracted.get("footer")
        if isinstance(footer, dict):
            payload["footer"] = footer
    status_path = status_dir / f"{img.stem}.json"
    db_summary = _db_landing_summary(result)
    payload.update(db_summary)
    status_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    result.status_path = str(status_path)


def _db_landing_summary(result: "ProcessingResult") -> dict[str, Any]:
    extracted = result.extracted if isinstance(result.extracted, dict) else {}
    if extracted.get("_agent_ingested"):
        return {
            "db_action": "release_batch_upsert",
            "db_tables": ["release_batches"],
            "db_record_ids": extracted.get("_agent_updated_ids") or [],
            "status": "ingested",
            "reason": "departure_plan_ingested",
        }
    if extracted.get("_agent_sop_skip_reason"):
        return {
            "db_action": "none",
            "db_tables": [],
            "db_record_ids": [],
            "status": "extracted",
            "reason": extracted.get("_agent_sop_skip_reason"),
        }
    if extracted.get("_agent_candidate_ids"):
        return {
            "db_action": "candidate_pending",
            "db_tables": ["inspection_ingestion_candidates"],
            "db_record_ids": extracted.get("_agent_candidate_ids") or [],
            "status": "pending",
            "reason": extracted.get("_agent_pending_reason") or "candidate_pending",
        }
    if extracted.get("_agent_ingest_error"):
        return {
            "db_action": "ingest_error",
            "db_tables": [],
            "db_record_ids": [],
            "status": "failed",
            "reason": extracted.get("_agent_ingest_error"),
        }
    return {
        "db_action": "none",
        "db_tables": [],
        "db_record_ids": [],
        "status": "failed" if result.error else ("extracted" if result.extraction_saved_path else "classified"),
        "reason": result.error or "no_db_landing_required",
    }


def _write_audit_record(
    settings: Settings,
    img: Path,
    result: "ProcessingResult",
    *,
    group_name: str = "",
) -> None:
    try:
        from ops_hub.data_agent.agent import BusinessDataAgent
        summary = _db_landing_summary(result)
        extracted = result.extracted if isinstance(result.extracted, dict) else {}
        agent = BusinessDataAgent()
        agent.write_image_ingestion_audit(
            {
                "group_name": group_name,
                "raw_image_path": str(img),
                "classified_category": result.category,
                "classification_confidence": result.confidence,
                "classified_image_path": result.saved_path,
                "extraction_json_path": result.extraction_saved_path,
                "project_id": extracted.get("project") or extracted.get("项目"),
                "adopted_fields": extracted.get("_adopted_fields") or [],
                "ignored_fields": extracted.get("_ignored_fields") or [],
                "reconcile_plan": extracted.get("_agent_reconcile_plan"),
                "project_archive_paths": result.project_archive_paths,
                **summary,
            }
        )
    except Exception:
        pass


# ── 项目归档与自动发运入库计划 ─────────────────────────
def _archive_date_from_payload(payload: dict[str, Any], *, month_str: str = "") -> str:
    # Project archives should line up with the business batch date when a
    # departure plan contains explicit per-lot remarks.  The document notice
    # date is only a fallback.
    candidates = []
    for remark in payload.get("remarks") or []:
        if isinstance(remark, dict):
            candidates.append(remark.get("date"))
    candidates.extend([
        payload.get("notice_date"),
        (payload.get("meta") or {}).get("date") if isinstance(payload.get("meta"), dict) else None,
        (payload.get("header_info") or {}).get("通知日期") if isinstance(payload.get("header_info"), dict) else None,
    ])
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        text = text.replace("年", "-").replace("月", "-").replace("日", "")
        parts = [part for part in text.split("-") if part]
        if len(parts) >= 3 and all(part.isdigit() for part in parts[:3]):
            y, m, d = parts[:3]
            if len(y) == 4:
                return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    if month_str:
        month = _normalize_month_compact(month_str)
        if len(month) == 6 and month.isdigit():
            return f"{month[:4]}-{month[4:6]}-01"
    return datetime.now().strftime("%Y-%m-%d")


def _archive_month_from_date(date_text: str, *, month_str: str = "") -> str:
    if date_text and len(date_text) >= 7:
        return date_text[:7]
    month = _normalize_month_compact(month_str)
    if len(month) == 6 and month.isdigit():
        return f"{month[:4]}-{month[4:6]}"
    return datetime.now().strftime("%Y-%m")


def _lot_component(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "lotunknown"
    lowered = text.lower()
    if lowered.startswith("lot"):
        suffix = lowered[3:]
        return f"lot{int(suffix):02d}" if suffix.isdigit() else _sanitize_component(lowered)
    match = re.search(r"(\d{1,2})", lowered)
    if match:
        return f"lot{int(match.group(1)):02d}"
    chinese = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    for token, number in chinese.items():
        if token in text:
            return f"lot{number:02d}"
    return _sanitize_component(text)


def _release_row_for_archive(settings: Settings, release_batch_id: str | None) -> dict[str, Any]:
    if not release_batch_id:
        return {}
    try:
        import sqlite3
        conn = sqlite3.connect(str(settings.agent_db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM release_batches WHERE id=?", (release_batch_id,)).fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()
    except Exception:
        return {}


def _archive_components(payload: dict[str, Any], settings: Settings) -> dict[str, str]:
    agent_status = str(payload.get("_agent_status") or "")
    release_ids = payload.get("_agent_updated_ids") or []
    candidate_release_ids = payload.get("_agent_candidate_release_batch_ids") or payload.get("_candidate_release_batch_ids") or []
    archive_release_id = release_ids[0] if release_ids else (candidate_release_ids[0] if agent_status == "ambiguous" and candidate_release_ids else None)
    release_row = _release_row_for_archive(settings, str(archive_release_id) if archive_release_id else None)
    business_info = payload.get("business_info") if isinstance(payload.get("business_info"), dict) else {}
    cargo_info = payload.get("cargo_info") if isinstance(payload.get("cargo_info"), dict) else {}
    remarks = [item for item in (payload.get("remarks") or []) if isinstance(item, dict)]
    first_remark = remarks[0] if remarks else {}
    lot_value = "pending_lot" if agent_status == "ambiguous" else _lot_component(release_row.get("batch_sequence") or first_remark.get("sequence") or payload.get("batch_sequence"))
    # If an archive release row was selected, keep all path components from that
    # same business row.  Mixed-project inspection slips may carry a document-level
    # payload.project from one segment while the candidate release row belongs to
    # another project; combining those sources creates invalid paths such as
    # 朝阳钢铁/.../汐子/马兰探险.  The release row is the authoritative archive
    # anchor whenever present.
    return {
        "project": str(release_row.get("project") or payload.get("project") or "unknown"),
        "destination": str(release_row.get("destination_station") or first_remark.get("destination") or cargo_info.get("到站") or payload.get("destination_station") or "unknown"),
        "ship": str(release_row.get("ship_name") or business_info.get("进口船名") or business_info.get("船名") or payload.get("ship_name") or "unknown"),
        "lot": lot_value,
    }


def _move_processed_artifacts(settings: Settings, img: Path, result: "ProcessingResult", *, month_str: str = "") -> None:
    if not isinstance(result.extracted, dict) or not result.extraction_saved_path:
        return
    payload = result.extracted
    date_text = _archive_date_from_payload(payload, month_str=month_str)
    root = Path(settings.classified_output_dir)
    if payload.get("_agent_sop_authorized") is True:
        parts = _archive_components(payload, settings)
        base = root / "projects" / _sanitize_component(parts["project"]) / _sanitize_component(parts["destination"]) / _sanitize_component(parts["ship"]) / parts["lot"]
        image_dir = base / "images" / date_text
        json_dir = base / "json" / date_text
    else:
        month = _archive_month_from_date(date_text, month_str=month_str)
        image_dir = root / "unmatched" / month / "images"
        json_dir = root / "unmatched" / month / "json"
    image_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)
    image_dest = image_dir / img.name
    json_dest = json_dir / f"{img.stem}_result.json"
    if Path(result.saved_path) != image_dest:
        shutil.copy2(img, image_dest)
    Path(result.extraction_saved_path).replace(json_dest)
    result.saved_path = str(image_dest)
    result.extraction_saved_path = str(json_dest)
    result.project_archive_paths = {"image": result.saved_path, "json": result.extraction_saved_path}


def _attach_reconcile_plan_if_possible(settings: Settings, payload: dict[str, Any]) -> None:
    candidate_ids = [str(item) for item in (payload.get("_agent_candidate_ids") or []) if str(item).strip()]
    release_ids = [str(item) for item in (payload.get("_agent_updated_ids") or []) if str(item).strip()]
    if not candidate_ids or not release_ids or not settings.db_95306_path:
        return
    rail_path = Path(settings.db_95306_path)
    if not rail_path.exists():
        return
    try:
        from ops_hub.matching.inspection_95306_reconciler import reconcile_inspection_shipments

        payload["_agent_reconcile_plan"] = reconcile_inspection_shipments(
            business_db_path=settings.agent_db_path,
            rail_db_path=rail_path,
            project_id=str(payload.get("project") or ""),
            release_batch_id=release_ids[0],
            run_mode="plan",
            operator_note="auto plan from runner after inspection candidate ingestion",
            candidate_ids=candidate_ids,
        ).to_report_dict()
    except Exception as exc:
        payload["_agent_reconcile_plan_error"] = str(exc)


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
    project_archive_paths: dict[str, str] = field(default_factory=dict)
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
    if getattr(settings, "agent_db_path", ""):
        os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = str(settings.agent_db_path)
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
            extracted = _run_extraction(result.category, str(img), settings, group_name=group_name)
            result.elapsed_extract = time.perf_counter() - t0
            result.extracted = extracted

            # 保存识别结果
            if extracted:
                _attach_reconcile_plan_if_possible(settings, extracted)
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
                _move_processed_artifacts(settings, img, result, month_str=month_str)
        except Exception as e:
            result.error = f"识别失败: {e}"

    try:
        _write_status_file(settings, img, result, month_str=month_str, group_name=group_name)
        _write_audit_record(settings, img, result, group_name=group_name)
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


@lru_cache(maxsize=1)
def _active_project_sop_tokens() -> set[str]:
    """Return active ProjectSOP ids/names used as the DB-ingestion gate.

    Classification/OCR is always allowed; database writes are allowed only when
    the extracted payload can be attributed to one of these active SOP projects.
    """
    tokens: set[str] = set()
    try:
        from ops_hub.models.project_sop import load_project_sop

        sops_dir = Path(__file__).resolve().parents[3] / "business-system-docs" / "test-plan" / "fixtures" / "project_sops"
        for sop_file in sorted(sops_dir.glob("*.yaml")):
            sop = load_project_sop(sop_file)
            if sop.status == "active":
                for value in (sop.project_id, sop.project_name):
                    text = str(value or "").strip()
                    if text:
                        tokens.add(text)
    except Exception:
        pass
    return tokens


def _payload_project_token(payload: dict[str, Any]) -> str:
    return str(
        payload.get("project")
        or payload.get("项目")
        or payload.get("project_name")
        or payload.get("项目名称")
        or ""
    ).strip()


def _payload_search_text(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return str(payload)


def _infer_sop_project_token(payload: dict[str, Any], *, category: str) -> str:
    """Infer an active SOP project from business content when OCR omitted project.

    龙虾测试群 is a sandbox transport, so database authorization must be based on
    the document's business content rather than the physical WeChat group.  Keep
    the rules narrow: departure plans need a ship/customer/project anchor;
    inspection slips may use destination anchors because they often have no
    header fields at all.
    """
    text = _payload_search_text(payload)
    if category == "出港计划通知单":
        if any(token in text for token in ("合远9", "朝阳钢铁", "朝钢", "朝阳西", "朝阳铁")):
            return "朝阳钢铁铁矿发运项目"
        if any(token in text for token in ("汐子", "鞍子河", "丰收散运", "丰收", "沱子", "中唐", "赤峰中唐", "ZLZT")):
            return "中唐特钢铁矿发运项目"
        return ""
    if category == "检装车通知单":
        if any(token in text for token in ("合远9", "朝阳西", "朝阳铁", "朝阳钢铁", "朝钢")):
            return "朝阳钢铁铁矿发运项目"
        if any(token in text for token in ("汐子", "鞍子河", "中唐", "赤峰中唐")):
            return "中唐特钢铁矿发运项目"
        return ""
    return ""


def _ensure_sop_project(payload: dict[str, Any], *, category: str) -> bool:
    project = _payload_project_token(payload)
    tokens = _active_project_sop_tokens()
    if project and project in tokens:
        return True
    inferred = _infer_sop_project_token(payload, category=category)
    if inferred and inferred in tokens:
        payload["project"] = inferred
        payload["_agent_project_inferred_from"] = category
        return True
    return False


def _mark_sop_skip(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    payload["_agent_sop_authorized"] = False
    payload["_agent_sop_skip_reason"] = reason
    return payload


def _run_extraction(category: str, image_path: str, settings: Settings, *, group_name: str = "") -> dict[str, Any]:
    """根据分类结果调用对应识别引擎"""
    service_url = settings.vlm_service_url

    if category == "检装车通知单":
        from ops_hub.engines.inspection_slip import InspectionSlipEngine
        engine = InspectionSlipEngine(service_url=service_url)
        result = engine.process_image(image_path)
        if result and result.get("is_inspection"):
            if not _ensure_sop_project(result, category=category):
                return _mark_sop_skip(result, "non_sop_project_json_only")
            result["_agent_sop_authorized"] = True
            try:
                from ops_hub.data_agent.agent import BusinessDataAgent
                agent = BusinessDataAgent()
                landing = agent.ingest_inspection_payload(
                    result,
                    source_file_name=Path(image_path).name,
                    group_name=group_name or None,
                )
                result["_agent_candidate_ids"] = landing.get("candidate_ids", [])
                result["_agent_candidate_release_batch_ids"] = landing.get("release_batch_ids", [])
                result["_candidate_release_batch_ids"] = landing.get("release_batch_ids", [])
                if landing.get("status") == "candidate":
                    result["_agent_updated_ids"] = landing.get("release_batch_ids", [])
                else:
                    result["_agent_updated_ids"] = []
                result["_agent_pending_reason"] = landing.get("reason")
                result["_agent_status"] = landing.get("status")
            except Exception as e:
                result["_agent_ingest_error"] = str(e)
        return result

    elif category == "出港计划通知单":
        from ops_hub.engines.departure_plan import DeparturePlanEngine
        engine = DeparturePlanEngine(service_url=service_url)
        result = engine.process_image(image_path)

        # 自动导入放货批次数据：仅 ProjectSOP 已登记项目允许写库。
        if result and result.get("is_target"):
            if not _ensure_sop_project(result, category=category):
                return _mark_sop_skip(result, "non_sop_project_json_only")
            result["_agent_sop_authorized"] = True
            try:
                from ops_hub.data_agent.agent import BusinessDataAgent
                agent = BusinessDataAgent()
                records = agent.ingest_release_batch(result, source_file_name=Path(image_path).name)
                result["_agent_ingested"] = len(records)
                result["_agent_updated_ids"] = [record.id for record in records]
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
