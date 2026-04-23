from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

import requests
from PIL import Image, ImageOps

from ops_hub.pipeline.models import ClassificationResult
from ops_hub.classifier.prompts import build_classify_prompt


API_URL = "http://127.0.0.1:8018/generate"
MAX_IMAGE_EDGE = 1200
MAX_SHORT_EDGE = 780


DEFAULT_CATEGORY_CARDS = {
    "出港计划通知单": "标题明确为“锦州港货物出港计划通知单”；典型的物流报表版式，包含发货单位、收货单位、船名等表格信息；通常是完整的A4纸打印扫描件。",
    "耗材统计表": "无明显大标题；版式特征是“双栏结构”，左右各有序号、品名、尺寸、余量等列；颜色上常有红、黄背景高亮指示低库存。",
    "检装车通知单": "关键标题：“锦州港杂码公司火运输港检、装车通知单”或“锦州港杂码公司火运货物疏港检、装车通知单”；包含品名、道线、日期、检车员等元信息；版式规整的打印表格。",
    "请车表": "通常为宽表。**核心识别特征**：最左侧一列必然出现“整车”（通常纵向排列）、“小计”和“集装箱”字样；表头包含“日期、到站、品名、需求、托运人”。",
    "日现场工作记录表": "手写或打印的现场日志；关键点是“作业班组、船名、品名、道线、车数”；相比检装车单，其内容更偏向现场流程记录；（如果确定性不高，可归入 other）。",
    "手写箱号车号表": "关键判定：顶部通常有打印的“检车清单”标题；内容是以手写数字为主的表格，包含：序号、车号（7位）、箱号1（7位）、箱号2（7位）；可能是一栏或多栏并排布局。",
    "手写记录": "广义的手写文档；特征是字迹凌乱或非表格排列；包含手写汇报、便签、签名确认单等，只要是手写且非特定单据，均在此类。",
    "照片-敞车内部情况和作业": "视角从高处俯拍进入车厢内部；可见车厢底板、侧壁、正在装载的货物或清理后的余料；重点是“车厢内壁和内部空间”。",
    "照片-火车涂写mark": "近距离拍摄火车身（车皮）；重点是识别车体表面的粉笔字、编号、划线标记、以及各种运营涂写。",
    "照片-集装箱内情况和作业": "视角在集装箱门口或内部；可见集装箱标志性的瓦楞内壁、箱门锁轴；重点是箱内装载情况。",
    "照片-检查工人": "画面中有穿反光背心或戴安全帽的人员正在进行指点、观测、合影或沟通；人员通常是画面的焦点中心。",
    "照片-装卸现场情况": "广角或远景照片；可见门机、正面吊、吊装作业、重卡等。**包含**：成堆存放的货物（货垛）、地面散落的塑料布、清扫出的垃圾、或者正在作业的繁忙场景。",
    "other": "图片内容模糊、信息量太少、或者不符合上述任何一类视觉特征的场景。",
}


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


class BusinessGroupImageClassifier:
    def __init__(
        self,
        service_url: str = API_URL,
        category_cards: Mapping[str, str] | None = None,
    ) -> None:
        self.service_url = service_url
        self.category_cards = dict(category_cards or DEFAULT_CATEGORY_CARDS)

    def _prepare_preview(self, image_path: Path) -> Path:
        preview_dir = image_path.parent / "_previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_path = preview_dir / image_path.name
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

    def classify(
        self,
        image_path: str | Path,
        *,
        max_tokens: int = 128,
        hint: str = "",
    ) -> ClassificationResult:
        img_path = Path(image_path)
        preview_path = self._prepare_preview(img_path)
        prompt = build_classify_prompt(
            self.category_cards,
            hint=(
                "判定引导：优先根据视觉特征进行分类，只有在内容极度模糊或完全无法识别时才使用 other。"
                + (f"\n本组聊天上下文：{hint}" if hint else "")
            ),
        )
        started = time.perf_counter()
        response = requests.post(
            self.service_url,
            json={
                "prompt": prompt,
                "image_path": str(preview_path),
                "max_tokens": max_tokens,
                "temperature": 0.0,
            },
            timeout=240,
        )
        response.raise_for_status()
        payload = response.json()
        elapsed_s = time.perf_counter() - started
        raw_text = payload.get("text", "")
        result = _extract_json_fragment(raw_text)
        if not isinstance(result, dict):
            result = {}

        category = str(result.get("category") or "other")
        detected_title = str(result.get("detected_title") or "")
        evidence = str(result.get("evidence") or "")
        
        # Relaxed classification logic
        if category not in self.category_cards:
            category = "other"
        
        # Specific category mapping adjustments
        if category == "日现场工作记录表":
            # Keep it if confidence is high, otherwise other as per requirement
            if float(result.get("confidence", 0.0)) < 0.8:
                category = "other"
                
        if category == "手写箱号车号表":
            # Validate handwritten num table with title check as safeguard
            if not detected_title and float(result.get("confidence", 0.0)) < 0.7:
                category = "手写记录"
        try:
            confidence = float(result.get("confidence", 0.0))
        except Exception:
            confidence = 0.0

        return ClassificationResult(
            category=category,
            confidence=confidence,
            detected_title=detected_title,
            evidence=evidence,
            raw_text=raw_text,
        )
