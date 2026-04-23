from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ops_hub.pipeline.models import ClassificationResult, ProcessResult
from ops_hub.pipeline.doc_detail_mode import normalize_doc_detail_mode, should_skip_deep_detail
from ops_hub.storage.artifact_store import BusinessArtifactStore
from ops_hub.engines.materials_stats import MaterialsStatsEngine
from ops_hub.engines.departure_plan import DeparturePlanEngine
from ops_hub.engines.inspection_slip import InspectionSlipEngine
from ops_hub.storage.image_store import ImageStore, build_group_image_subdir


@dataclass
class ProcessorContext:
    group_name: str
    wxid: str
    date_str: str
    timestamp: int | None = None
    detail_mode: str = "shallow"


def build_lightweight_doc_payload(
    *,
    doc_type: str,
    classification: ClassificationResult,
    image_path: str,
    relative_image_path: str,
    context: ProcessorContext,
) -> dict[str, Any]:
    return {
        "doc_type": doc_type,
        "detail_mode": normalize_doc_detail_mode(context.detail_mode),
        "detail_skipped": True,
        "image_path": str(image_path),
        "relative_image_path": str(relative_image_path),
        "group_name": context.group_name,
        "group_wxid": context.wxid,
        "message_time": context.timestamp,
        "classification": {
            "label": classification.category,
            "score": classification.confidence,
            "detected_title": classification.detected_title,
            "evidence": classification.evidence,
        },
    }


class SaveOnlyProcessor:
    def __init__(self, image_store: ImageStore | None = None) -> None:
        self.image_store = image_store or ImageStore()

    def process(
        self,
        image_path: str | Path,
        classification: ClassificationResult,
        context: ProcessorContext,
    ) -> ProcessResult:
        img_path = Path(image_path)
        data = img_path.read_bytes()
        subdir = build_group_image_subdir(
            context.group_name,
            classification.category,
            context.date_str,
        )
        relative_image_path = str(subdir / img_path.name)
        saved_path = self.image_store.save_group_image(
            base_dir=self.image_store.base_dir,
            subdir=subdir,
            filename=img_path.name,
            image_data=data,
        )
        return ProcessResult(
            category=classification.category,
            bucket=self._bucket_for(classification.category),
            saved_image_path=saved_path,
            relative_image_path=relative_image_path,
            payload={"classification": classification.__dict__},
            classification=classification,
        )

    @staticmethod
    def _bucket_for(category: str) -> str:
        if category.startswith("照片-"):
            return "photo"
        if category.startswith("手写"):
            return "handwritten"
        return "table"


class DeparturePlanProcessor(SaveOnlyProcessor):
    def __init__(
        self,
        image_store: ImageStore | None = None,
        artifact_store: BusinessArtifactStore | None = None,
        engine: DeparturePlanEngine | None = None,
    ) -> None:
        super().__init__(image_store=image_store)
        if artifact_store is None:
            release_root = Path(__file__).resolve().parents[3] / "data" / "cargo_release"
            artifact_store = BusinessArtifactStore(base_dir=release_root)
        self.artifact_store = artifact_store
        self.engine = engine or DeparturePlanEngine()

    def process(
        self,
        image_path: str | Path,
        classification: ClassificationResult,
        context: ProcessorContext,
    ) -> ProcessResult:
        saved = super().process(image_path, classification, context)
        if should_skip_deep_detail(classification.category, context.detail_mode):
            extracted = build_lightweight_doc_payload(
                doc_type=classification.category,
                classification=classification,
                image_path=saved.saved_image_path,
                relative_image_path=saved.relative_image_path,
                context=context,
            )
            record = {
                "image_path": saved.saved_image_path,
                "relative_image_path": saved.relative_image_path,
                "category": classification.category,
                "extracted": extracted,
            }
            self.artifact_store.append_jsonl(context.group_name, classification.category, record)
            json_path = self.artifact_store.write_json(
                context.group_name,
                classification.category,
                record,
                timestamp=context.timestamp,
            )
            return ProcessResult(
                category=classification.category,
                bucket=saved.bucket,
                saved_image_path=saved.saved_image_path,
                relative_image_path=saved.relative_image_path,
                artifact_path=str(json_path),
                payload=extracted,
                message="",
                should_notify=False,
                send_file_path=None,
                summary="出港计划通知单已完成浅层记录。",
                classification=classification,
            )
        extracted = self.engine.process_image(saved.saved_image_path)
        extracted["image_path"] = saved.saved_image_path
        extracted["relative_image_path"] = saved.relative_image_path
        jsonl_path = self.artifact_store.append_jsonl(context.group_name, classification.category, extracted)
        json_path = self.artifact_store.write_json(
            context.group_name,
            classification.category,
            extracted,
            timestamp=context.timestamp,
        )
        return ProcessResult(
            category=classification.category,
            bucket=saved.bucket,
            saved_image_path=saved.saved_image_path,
            relative_image_path=saved.relative_image_path,
            artifact_path=str(json_path),
            payload=extracted,
            message="",
            should_notify=False,
            send_file_path=None,
            summary="出港计划通知单已抽取为 JSON。",
            classification=classification,
        )


class InspectionSlipProcessor(SaveOnlyProcessor):
    def __init__(
        self,
        image_store: ImageStore | None = None,
        artifact_store: BusinessArtifactStore | None = None,
        engine: InspectionSlipEngine | None = None,
    ) -> None:
        super().__init__(image_store=image_store)
        if artifact_store is None:
            loaded_root = Path(__file__).resolve().parents[3] / "data" / "loaded_cars"
            artifact_store = BusinessArtifactStore(base_dir=loaded_root)
        self.artifact_store = artifact_store
        self.engine = engine or InspectionSlipEngine()

    def process(
        self,
        image_path: str | Path,
        classification: ClassificationResult,
        context: ProcessorContext,
    ) -> ProcessResult:
        saved = super().process(image_path, classification, context)
        if should_skip_deep_detail(classification.category, context.detail_mode):
            extracted = build_lightweight_doc_payload(
                doc_type=classification.category,
                classification=classification,
                image_path=saved.saved_image_path,
                relative_image_path=saved.relative_image_path,
                context=context,
            )
            record = {
                "image_path": saved.saved_image_path,
                "relative_image_path": saved.relative_image_path,
                "category": classification.category,
                "extracted": extracted,
            }
            self.artifact_store.append_jsonl(context.group_name, classification.category, record)
            json_path = self.artifact_store.write_json(
                context.group_name,
                classification.category,
                record,
                timestamp=context.timestamp,
            )
            return ProcessResult(
                category=classification.category,
                bucket=saved.bucket,
                saved_image_path=saved.saved_image_path,
                relative_image_path=saved.relative_image_path,
                artifact_path=str(json_path),
                payload=extracted,
                message="",
                should_notify=False,
                send_file_path=None,
                summary="检装车通知单已完成浅层记录。",
                classification=classification,
            )
        extracted = self.engine.process_image(saved.saved_image_path)
        if not extracted.get("car_nos"):
            rows = extracted.get("rows")
            if isinstance(rows, list):
                car_nos: list[str] = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    car_no = str(row.get("car_no", "") or "").strip()
                    if car_no and car_no not in car_nos:
                        car_nos.append(car_no)
                extracted["car_nos"] = car_nos
        record = {
            "image_path": saved.saved_image_path,
            "relative_image_path": saved.relative_image_path,
            "category": classification.category,
            "extracted": extracted,
        }
        self.artifact_store.append_jsonl(context.group_name, classification.category, record)
        json_path = self.artifact_store.write_json(
            context.group_name,
            classification.category,
            record,
            timestamp=context.timestamp,
        )
        return ProcessResult(
            category=classification.category,
            bucket=saved.bucket,
            saved_image_path=saved.saved_image_path,
            relative_image_path=saved.relative_image_path,
            artifact_path=str(json_path),
            payload=extracted,
            message=str(extracted.get("message", "")),
            should_notify=bool(extracted.get("is_inspection", False)),
            send_file_path=None,
            summary="检装车通知单已完成结构化识别。",
            classification=classification,
        )


class MaterialsStatsProcessor(SaveOnlyProcessor):
    def __init__(
        self,
        image_store: ImageStore | None = None,
        artifact_store: BusinessArtifactStore | None = None,
        engine: MaterialsStatsEngine | None = None,
    ) -> None:
        super().__init__(image_store=image_store)
        if artifact_store is None:
            materials_root = Path(__file__).resolve().parents[3] / "data" / "materials"
            artifact_store = BusinessArtifactStore(base_dir=materials_root)
        self.artifact_store = artifact_store
        self.engine = engine or MaterialsStatsEngine()

    def process(
        self,
        image_path: str | Path,
        classification: ClassificationResult,
        context: ProcessorContext,
    ) -> ProcessResult:
        saved = super().process(image_path, classification, context)
        if should_skip_deep_detail(classification.category, context.detail_mode):
            extracted = build_lightweight_doc_payload(
                doc_type=classification.category,
                classification=classification,
                image_path=saved.saved_image_path,
                relative_image_path=saved.relative_image_path,
                context=context,
            )
            record = {
                "image_path": saved.saved_image_path,
                "relative_image_path": saved.relative_image_path,
                "category": classification.category,
                "extracted": extracted,
            }
            self.artifact_store.append_jsonl(context.group_name, classification.category, record)
            json_path = self.artifact_store.write_json(
                context.group_name,
                classification.category,
                record,
                timestamp=context.timestamp,
            )
            return ProcessResult(
                category=classification.category,
                bucket=saved.bucket,
                saved_image_path=saved.saved_image_path,
                relative_image_path=saved.relative_image_path,
                artifact_path=str(json_path),
                payload=extracted,
                message="",
                should_notify=False,
                send_file_path=None,
                summary="耗材统计表已完成浅层记录。",
                classification=classification,
            )
        extracted = self.engine.process_image(saved.saved_image_path)
        record = {
            "image_path": saved.saved_image_path,
            "relative_image_path": saved.relative_image_path,
            "category": classification.category,
            "extracted": extracted,
        }
        self.artifact_store.append_jsonl(context.group_name, classification.category, record)
        json_path = self.artifact_store.write_json(
            context.group_name,
            classification.category,
            record,
            timestamp=context.timestamp,
        )
        return ProcessResult(
            category=classification.category,
            bucket=saved.bucket,
            saved_image_path=saved.saved_image_path,
            relative_image_path=saved.relative_image_path,
            artifact_path=str(json_path),
            payload=extracted,
            message=str(extracted.get("message", "")),
            should_notify=bool(extracted.get("is_target", False)),
            send_file_path=None,
            summary="耗材统计表已完成结构化识别。",
            classification=classification,
        )
