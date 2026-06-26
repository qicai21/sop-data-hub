from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from sop_hub.utils.layout import TableLayoutAnalyzer
from sop_hub.vlm_client import call_vlm


API_URL = "http://127.0.0.1:8021/v1/chat/completions"
MAX_IMAGE_EDGE = 1400
MAX_SHORT_EDGE = 900

MATERIAL_ROWS_PROMPT = """你是耗材统计表OCR助手。请识别这张图片中的{side_label}内容，只输出严格JSON，不要输出解释。
JSON结构：
{
  "is_target": true,
  "title": "耗材统计表",
  "rows": [
    {
      "seq": 0,
      "item_name": "",
      "size": "",
      "balance": "",
      "remark": ""
    }
  ],
  "summary": ""
}
规则：
1. 只返回该栏可见且有序号/行顺序的数据，按从上到下顺序；
2. seq 如果图中有明确行号就填行号，没有就按从上到下从1开始；
3. item_name 只放品名/耗材名称；
4. size 只放尺寸/规格；
5. balance 只放余量/数量/库存；
6. remark 只放备注或其他可见补充信息；
7. 空白输出空字符串；
8. 不要继承前文，不要合并左右栏，不要自行补全。
"""

MATERIAL_ROWS_RETRY_PROMPT = """你是耗材统计表OCR助手。下面图片只包含{side_label}，请把该栏中所有可见行逐行完整输出。
只输出严格JSON，不要解释，不要示例，不要省略中间行。
字段固定为：
{
  "is_target": true,
  "title": "耗材统计表",
  "rows": [
    {
      "seq": 0,
      "item_name": "",
      "size": "",
      "balance": "",
      "remark": ""
    }
  ],
  "summary": ""
}
补充规则：
1. seq 如果图中有明确行号就填行号，没有就按从上到下从1开始；
2. item_name 只放品名/耗材名称；
3. size 只放尺寸/规格；
4. balance 只放余量/数量/库存；
5. remark 只放备注或其他可见补充信息；
6. 空白输出空字符串；
7. 不要继承前文，不要合并左右栏，不要自行补全。
"""


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


class MaterialsStatsEngine:
    def __init__(self, service_url: str = API_URL, output_base: str | None = None, openai_model: str | None = None) -> None:
        self.service_url = service_url
        self.openai_model = openai_model
        self.layout_analyzer = TableLayoutAnalyzer(service_url=service_url, openai_model=openai_model)
        self.output_base = Path(output_base) if output_base else None

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

    def _split_page_into_sides(self, page_path: Path, output_dir: Path) -> dict[str, Path]:
        image = Image.open(page_path)
        width, height = image.size
        top = int(height * 0.04)
        bottom = int(height * 0.90)
        split_x = int(width * 0.50)

        left_crop = image.crop((0, top, split_x, bottom))
        right_crop = image.crop((max(0, split_x - 8), top, width, bottom))

        left_path = output_dir / f"{page_path.stem}_left.png"
        right_path = output_dir / f"{page_path.stem}_right.png"
        left_crop.save(left_path)
        right_crop.save(right_path)
        return {"left": left_path, "right": right_path}

    def _build_rows_prompt(self, side_label: str, retry: bool = False) -> str:
        template = MATERIAL_ROWS_RETRY_PROMPT if retry else MATERIAL_ROWS_PROMPT
        return template.replace("{side_label}", side_label)

    def _call_api(self, prompt: str, image_path: str, max_tokens: int) -> tuple[Any, float]:
        started = time.perf_counter()
        text = call_vlm(
            self.service_url,
            prompt,
            image_path,
            max_tokens,
            openai_model=self.openai_model,
            timeout=180,
        )
        return _extract_json_fragment(text), time.perf_counter() - started

    def _normalize_rows(self, raw_rows: Any, page_index: int, side_name: str) -> list[dict[str, Any]]:
        if isinstance(raw_rows, dict):
            raw_rows = raw_rows.get("rows", [raw_rows])
        if not isinstance(raw_rows, list):
            return []

        rows: list[dict[str, Any]] = []
        for idx, row in enumerate(raw_rows, start=1):
            if not isinstance(row, dict):
                continue
            seq_text = str(row.get("seq", "")).strip()
            seq = int(seq_text) if seq_text.isdigit() else idx
            rows.append(
                {
                    "page_index": page_index,
                    "page_side": side_name,
                    "row_index_in_side": idx,
                    "seq": seq,
                    "item_name": str(row.get("item_name", "")).strip(),
                    "size": str(row.get("size", "")).strip(),
                    "balance": str(row.get("balance", "")).strip(),
                    "remark": str(row.get("remark", "")).strip(),
                }
            )
        return rows

    def process_image(self, image_path: str) -> dict[str, Any]:
        import tempfile
        img_path = Path(image_path).resolve()
        preview_path = self._prepare_preview(img_path)
        layout = self.layout_analyzer.analyze(str(preview_path))
        
        temp_parent = str(self.output_base) if self.output_base is not None else None
        with tempfile.TemporaryDirectory(prefix="materials_stats_", dir=temp_parent) as temp_dir:
            workspace = Path(temp_dir)
            if layout.body_column_mode == "multi":
                side_paths = self._split_page_into_sides(preview_path, workspace)
            else:
                side_paths = {"single": preview_path}

            merged_rows: list[dict[str, Any]] = []
            side_details: dict[str, Any] = {}
            page_index = 1
            for side_name, side_path in side_paths.items():
                side_label = f"第{page_index}页{side_name}栏" if side_name != "single" else "整页"
                prompt = self._build_rows_prompt(side_label, retry=False)
                raw_rows, _ = self._call_api(prompt, str(side_path.absolute()), 1200)
                normalized = self._normalize_rows(raw_rows, page_index, side_name)
                if len(normalized) <= 1:
                    retry_prompt = self._build_rows_prompt(side_label, retry=True)
                    retry_rows, _ = self._call_api(retry_prompt, str(side_path.absolute()), 1400)
                    retry_normalized = self._normalize_rows(retry_rows, page_index, side_name)
                    if len(retry_normalized) > len(normalized):
                        normalized = retry_normalized
                merged_rows.extend(normalized)
                side_details[side_name] = {
                    "image_path": str(side_path),
                    "rows": normalized,
                }

        merged_rows = [
            row
            for row in merged_rows
            if row.get("seq") is not None
            and (
                row.get("item_name")
                or row.get("size")
                or row.get("balance")
                or row.get("remark")
            )
        ]
        merged_rows.sort(key=lambda item: (item.get("seq") or 9999, item.get("page_index"), item.get("row_index_in_side")))
        for idx, row in enumerate(merged_rows, start=1):
            row["global_index"] = idx

        is_target = bool(merged_rows) or bool(layout.has_title) or "耗材统计表" in (layout.title_text or "")
        return {
            "is_target": is_target,
            "title": layout.title_text or "耗材统计表",
            "layout": layout.to_dict(),
            "rows": merged_rows,
            "rows_count": len(merged_rows),
            "sides": side_details,
            "preview_path": str(preview_path),
            "image_path": str(img_path),
            "message": "" if is_target else "未识别到耗材统计表结构",
        }
