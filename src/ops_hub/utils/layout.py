import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

API_URL = "http://127.0.0.1:8018/generate"

LAYOUT_PROFILE_PROMPT = """你是表格版式分析助手。请只根据图片版式输出严格JSON，不要解释。
JSON结构：
{
  "page_mode": "single",
  "has_title": true,
  "has_header_band": true,
  "body_column_mode": "single",
  "body_has_headers": true,
  "body_has_footer": false,
  "title_text": "",
  "notes": []
}
字段要求：
1. page_mode 只能是 "single" 或 "multi"；
2. has_title 表示页面顶部是否存在明显标题；
3. has_header_band 表示标题下方是否存在固定表首/表眉区域；
4. body_column_mode 只能是 "single" 或 "multi"；
5. body_has_headers 表示表体内是否存在列头；
6. body_has_footer 表示表体内部或底部是否存在可单独识别的表尾/汇总区；
7. title_text 仅填写图片中明显可见的主标题，没有就留空；
8. notes 用简短中文列出版式补充，例如“左右双栏”“右下角有小表格”“无标题但有表头”。"""


@dataclass
class TableLayoutProfile:
    page_mode: str = "single"
    has_title: bool = False
    has_header_band: bool = False
    body_column_mode: str = "single"
    body_has_headers: bool = False
    body_has_footer: bool = False
    title_text: str = ""
    notes: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TableLayoutAnalyzer:
    """
    通用版式分类器。
    用于在进入具体单据 OCR 引擎前先产出稳定的版式元信息，方便后续复用到检车单、
    出港计划通知单、耗材统计表等不同表格。
    """

    def __init__(self, service_url: str = API_URL) -> None:
        self.service_url = service_url

    def _extract_json_fragment(self, text: str) -> Any:
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
        raise ValueError(f"Could not parse layout JSON from model output: {text[:500]}")

    def analyze(self, image_path: str, max_tokens: int = 260) -> TableLayoutProfile:
        started = time.perf_counter()
        resp = requests.post(
            self.service_url,
            json={
                "prompt": LAYOUT_PROFILE_PROMPT,
                "image_path": image_path,
                "max_tokens": max_tokens,
                "temperature": 0.0,
            },
            timeout=180,
        )
        resp.raise_for_status()
        text = resp.json().get("text", "")
        logger.debug("[TableLayoutAnalyzer] Raw model output: %s", text)
        raw = self._extract_json_fragment(text)
        if not isinstance(raw, dict):
            raw = {}

        notes = raw.get("notes")
        if not isinstance(notes, list):
            notes = []

        profile = TableLayoutProfile(
            page_mode=str(raw.get("page_mode", "single")).strip().lower() or "single",
            has_title=bool(raw.get("has_title", False)),
            has_header_band=bool(raw.get("has_header_band", False)),
            body_column_mode=str(raw.get("body_column_mode", "single")).strip().lower() or "single",
            body_has_headers=bool(raw.get("body_has_headers", False)),
            body_has_footer=bool(raw.get("body_has_footer", False)),
            title_text=str(raw.get("title_text", "")).strip(),
            notes=[str(item).strip() for item in notes if str(item).strip()],
        )
        logger.info(
            "[TableLayoutAnalyzer] Completed in %.2fs: page_mode=%s, columns=%s, title=%s",
            time.perf_counter() - started,
            profile.page_mode,
            profile.body_column_mode,
            "yes" if profile.has_title else "no",
        )
        return profile
