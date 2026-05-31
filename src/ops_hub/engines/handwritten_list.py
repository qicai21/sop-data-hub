from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageOps

from ops_hub.classifier.prompts import HANDWRITTEN_LIST_EXTRACTION_PROMPT


API_URL = "http://127.0.0.1:8018/generate"
MAX_IMAGE_EDGE = 1600
MAX_SHORT_EDGE = 1200


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


class HandwrittenListEngine:
    def __init__(self, service_url: str = API_URL) -> None:
        self.service_url = service_url

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

    def process_image(self, image_path: str | Path, max_tokens: int = 1500) -> dict[str, Any]:
        img_path = Path(image_path).resolve()
        preview_path = self._prepare_preview(img_path)
        started = time.perf_counter()
        response = requests.post(
            self.service_url,
            json={
                "prompt": HANDWRITTEN_LIST_EXTRACTION_PROMPT,
                "image_path": str(preview_path),
                "max_tokens": max_tokens,
                "temperature": 0.0,
            },
            timeout=300,
        )
        response.raise_for_status()
        payload = response.json()
        latency = time.perf_counter() - started
        raw_text = payload.get("text", "")
        
        try:
            result = _extract_json_fragment(raw_text)
        except Exception as e:
            result = {"is_target": False, "reason": f"unparseable output: {str(e)}"}
            
        if not isinstance(result, dict):
            result = {"is_target": False, "reason": "unparseable output"}
            
        result["image_path"] = str(img_path)
        result["preview_path"] = str(preview_path)
        result["latency"] = latency
        # Add a convenience count
        if result.get("is_target") and isinstance(result.get("rows"), list):
            result["rows_count"] = len(result["rows"])
        else:
            result["rows_count"] = 0
            
        return result
