from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ProcessorAction = Literal["save_only", "departure_plan_extract", "inspection_slip_extract", "materials_extract"]


@dataclass
class ClassificationResult:
    category: str
    confidence: float = 0.0
    detected_title: str = ""
    evidence: str = ""
    raw_text: str = ""


@dataclass
class CategoryRoute:
    category: str
    bucket: str
    action: ProcessorAction = "save_only"
    output_subdir: str = ""
    enabled: bool = True
    notes: list[str] = field(default_factory=list)


@dataclass
class GroupImageStrategy:
    name: str
    wxid: str
    enabled: bool = True
    classifier_prompt_version: str = "v1"
    fallback_category: str = "other"
    routes: dict[str, CategoryRoute] = field(default_factory=dict)

    def route_for(self, category: str) -> CategoryRoute:
        route = self.routes.get(category)
        if route and route.enabled:
            return route
        return self.routes.get(self.fallback_category) or CategoryRoute(
            category=self.fallback_category,
            bucket="other",
            action="save_only",
            output_subdir=self.fallback_category,
        )


@dataclass
class ProcessResult:
    category: str
    bucket: str
    saved_image_path: str
    relative_image_path: str = ""
    artifact_path: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    should_notify: bool = False
    send_file_path: str | None = None
    summary: str = ""
    classification: ClassificationResult | None = None
    route: CategoryRoute | None = None
