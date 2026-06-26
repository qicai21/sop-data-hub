from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageOps

from sop_hub.classifier.prompts import DEPARTURE_PLAN_EXTRACTION_PROMPT


API_URL = "http://127.0.0.1:8018/generate"
MAX_IMAGE_EDGE = 1400
MAX_SHORT_EDGE = 900


def _extract_json_fragment(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
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


class DeparturePlanEngine:
    def __init__(self, service_url: str = API_URL, openai_model: str | None = None) -> None:
        self.service_url = service_url
        self._openai_model = openai_model

    def _prepare_preview(self, image_path: Path) -> Path:
        preview_path = image_path.parent / f"{image_path.stem}_vlm.jpg"
        if preview_path.exists():
            return preview_path
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            longest = max(img.size)
            shortest = min(img.size)
            scale = min(1.0, MAX_IMAGE_EDGE / float(longest), MAX_SHORT_EDGE / float(shortest))
            if scale < 1.0:
                new_size = (max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale)))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            img.save(preview_path, format="JPEG", quality=90, optimize=True)
        return preview_path

    def process_image(self, image_path: str | Path, max_tokens: int = 1200) -> dict[str, Any]:
        img_path = Path(image_path)
        preview_path = self._prepare_preview(img_path)
        started = time.perf_counter()
        from sop_hub.vlm_client import call_vlm
        raw_text = call_vlm(
            self.service_url, DEPARTURE_PLAN_EXTRACTION_PROMPT, preview_path, max_tokens,
            openai_model=self._openai_model, timeout=300,
        )
        _ = time.perf_counter() - started
        result = _extract_json_fragment(raw_text)
        if not isinstance(result, dict):
            result = {"is_target": False, "reason": "unparseable output"}
        result["image_path"] = str(img_path)
        result["preview_path"] = str(preview_path)
        return result
