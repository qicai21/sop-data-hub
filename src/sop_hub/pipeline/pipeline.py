from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sop_hub.classifier.classifier import BusinessGroupImageClassifier
from sop_hub.pipeline.models import CategoryRoute, ClassificationResult, ProcessResult, GroupImageStrategy
from sop_hub.pipeline.doc_detail_mode import normalize_doc_detail_mode
from sop_hub.pipeline.processors import (
    DeparturePlanProcessor,
    InspectionSlipProcessor,
    MaterialsStatsProcessor,
    ProcessorContext,
    SaveOnlyProcessor,
)
from sop_hub.storage.artifact_store import BusinessArtifactStore
from sop_hub.storage.image_store import ImageStore


class BusinessGroupImagePipeline:
    def __init__(
        self,
        strategy: GroupImageStrategy,
        classifier: BusinessGroupImageClassifier | None = None,
        image_store: ImageStore | None = None,
        artifact_store: BusinessArtifactStore | None = None,
        ) -> None:
        self.strategy = strategy
        self.classifier = classifier or BusinessGroupImageClassifier()
        self.image_store = image_store or ImageStore()
        self.artifact_store = artifact_store
        self._processors = {
            "save_only": SaveOnlyProcessor(image_store=self.image_store),
            "departure_plan_extract": DeparturePlanProcessor(
                image_store=self.image_store,
                artifact_store=artifact_store,
            ),
            "inspection_slip_extract": InspectionSlipProcessor(
                image_store=self.image_store,
                artifact_store=artifact_store,
            ),
            "materials_extract": MaterialsStatsProcessor(
                image_store=self.image_store,
                artifact_store=artifact_store,
            ),
        }

    @staticmethod
    def _date_str(msg_time: int | None = None) -> str:
        if msg_time:
            return datetime.fromtimestamp(msg_time).strftime("%y-%m")
        return datetime.now().strftime("%y-%m")

    def process_image(
        self,
        image_path: str | Path,
        *,
        msg_time: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> ProcessResult:
        img_path = Path(image_path)
        hint = ""
        if context:
            hint = str(
                context.get("story_hint")
                or context.get("cluster_hint")
                or context.get("summary_hint")
                or ""
            ).strip()
        classification = self.classifier.classify(img_path, hint=hint)
        route = self.strategy.route_for(classification.category)
        processor = self._processors.get(route.action) or self._processors["save_only"]

        date_str = self._date_str(msg_time)
        proc_context = ProcessorContext(
            group_name=context.get("group_name", self.strategy.name) if context else self.strategy.name,
            wxid=context.get("wxid", self.strategy.wxid) if context else self.strategy.wxid,
            date_str=date_str,
            timestamp=msg_time,
            detail_mode=normalize_doc_detail_mode(context.get("doc_detail_mode")) if context else "shallow",
        )
        result = processor.process(img_path, classification, proc_context)
        result.classification = classification
        result.route = route
        return result
