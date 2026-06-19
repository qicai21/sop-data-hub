#!/usr/bin/env python3
"""
Classify the decoded business-group images from Desktop/业务群图片.

Pipeline:
1. Use Qwen3-VL to classify each image into:
   - install_notice
   - site_photo
   - handwritten
   - jinzhou_plan
   - other
2. Copy each image into a category folder under the output root.
3. For `jinzhou_plan`, run a dedicated extraction prompt and write per-image JSON.

The script is resumable via a JSONL results file.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests
from PIL import Image


API_URL = "http://127.0.0.1:8018/generate"
INPUT_DIR = Path("/Users/qicai21/Desktop/业务群图片")
OUTPUT_ROOT = Path("/Users/qicai21/Desktop/业务群图片_分析结果")
RESULTS_JSONL = OUTPUT_ROOT / "analysis_results.jsonl"
SUMMARY_JSON = OUTPUT_ROOT / "analysis_summary.json"
SUMMARY_MD = OUTPUT_ROOT / "analysis_summary.md"
TARGET_DIR = OUTPUT_ROOT / "锦州港货物出港计划通知单"
PREVIEW_DIR = OUTPUT_ROOT / "_previews"
MAX_IMAGE_EDGE = 1600
MODEL_RETRIES = 5

CATEGORY_DIRS = {
    "install_notice": OUTPUT_ROOT / "检装车通知单",
    "site_photo": OUTPUT_ROOT / "现场作业照片",
    "handwritten": OUTPUT_ROOT / "手写单据",
    "jinzhou_plan": TARGET_DIR,
    "other": OUTPUT_ROOT / "其他",
}

CLASSIFY_PROMPT = """你是微信群业务图片分拣助手。请只判断图片类型，输出严格JSON，不要输出解释，不要使用markdown。
可选 category 只能是：
- "install_notice"：图片是《检、装车通知单》或其变体/同类版式；只要标题或版式明确指向检、装车通知单就归此类。
- "site_photo"：明显的现场作业实拍照片，如堆场、装卸、车辆、集装箱、机械、道路、港口现场等，不是单据。
- "handwritten"：明显手写单据、手写记录、手写表格、便签。
- "jinzhou_plan"：图片明确是《锦州港货物出港计划通知单》或高度疑似。
- "other"：其他。
输出 JSON：
{
  "category": "",
  "confidence": 0.0,
  "detected_title": "",
  "evidence": "",
  "needs_ocr": false
}
规则：
1. 只输出 JSON。
2. category 必须是上述之一。
3. detected_title 仅写你能看清的标题，没有就空字符串。
4. evidence 用一句短语说明判断依据。
5. needs_ocr: 仅当图片像正式单据但分类仍不确定时 true；其他情况 false。
6. 如果看到“锦州港货物出港计划通知单”或其明显同类标题/版式，请直接分类为 jinzhou_plan。
7. 如果图片明显是《检、装车通知单》，请分类为 install_notice，不要继续细分。
8. 不要输出任何额外文本。"""

TARGET_EXTRACTION_PROMPT = """你是业务单据结构化抽取助手。请识别图片是否为《锦州港货物出港计划通知单》。

如果不是，请只输出：
{
  "is_target": false,
  "reason": ""
}

如果是，请严格按以下 JSON 输出，不要输出解释，不要输出 markdown：
{
  "is_target": true,
  "title": "锦州港货物出港计划通知单",
  "header_info": {
    "通知日期": "",
    "内、外贸": ""
  },
  "business_info": {
    "发货单位": "",
    "联系人": "",
    "电话": "",
    "收货单位": "",
    "到达港": "",
    "船名": "",
    "接货库场": "",
    "承运单位": "",
    "船期": ""
  },
  "cargo_info": {
    "货物名称": "",
    "包装": "",
    "单件重": "",
    "总件数": "",
    "总重里": "",
    "日进货量": "",
    "运输方式": "",
    "进货时间": "",
    "发货站(地)": ""
  },
  "special_matter": "",
  "remarks": [
    {
      "date": "",
      "sequence": "",
      "plan": "",
      "raw_line": ""
    }
  ],
  "footer_ignored": true
}

规则：
1. 只提取图片中明确可见的信息；看不清就留空字符串。
2. 备注部分按从上到下顺序写成数组，每条下达计划单独一项。
3. 若备注内部是多条记录，尽量拆成多项。
4. `special_matter` 只写特约事项主体描述，不要把备注混进去。
5. 页尾“货主签字、负责人、经办人”等一律忽略。
6. 不要发散解释结构，只输出 JSON。"""


def extract_json_fragment(text: str) -> Any:
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


def prepare_preview(image_path: Path) -> Path:
    """
    Resize large images before sending them to Qwen3-VL.
    This helps stabilize the MLX service on large screenshots/photos.
    """
    preview_path = PREVIEW_DIR / image_path.name
    if preview_path.exists():
        return preview_path

    with Image.open(image_path) as img:
        img = img.convert("RGB")
        width, height = img.size
        longest = max(width, height)
        if longest > MAX_IMAGE_EDGE:
            scale = MAX_IMAGE_EDGE / float(longest)
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        img.save(preview_path, format="JPEG", quality=92, optimize=True)
    return preview_path


def call_model(prompt: str, image_path: Path, max_tokens: int) -> tuple[Any, dict[str, Any]]:
    preview_path = prepare_preview(image_path)
    last_error: Exception | None = None
    for attempt in range(1, MODEL_RETRIES + 1):
        try:
            started = time.perf_counter()
            response = requests.post(
                API_URL,
                json={
                    "prompt": prompt,
                    "image_path": str(preview_path),
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                },
                timeout=300,
            )
            response.raise_for_status()
            payload = response.json()
            elapsed_s = time.perf_counter() - started
            return extract_json_fragment(payload["text"]), {
                "took_ms": int(payload.get("took_ms", int(elapsed_s * 1000))),
                "elapsed_s": round(elapsed_s, 3),
                "raw_text": payload.get("text", ""),
            }
        except Exception as exc:
            last_error = exc
            if attempt < MODEL_RETRIES:
                time.sleep(2 * attempt)
            else:
                break

    assert last_error is not None
    raise last_error


@dataclass
class ImageRecord:
    image: str
    category: str
    confidence: float
    detected_title: str
    evidence: str
    needs_ocr: bool
    qwen_took_ms: int
    qwen_elapsed_s: float
    qwen_raw_text: str
    target_extraction: dict[str, Any] | None = None


def ensure_dirs() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    for path in CATEGORY_DIRS.values():
        path.mkdir(parents=True, exist_ok=True)


def load_existing_results() -> dict[str, ImageRecord]:
    if not RESULTS_JSONL.exists():
        return {}
    existing: dict[str, ImageRecord] = {}
    for line in RESULTS_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        existing[obj["image"]] = ImageRecord(**obj)
    return existing


def classify_image(image_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    result, timing = call_model(CLASSIFY_PROMPT, image_path, max_tokens=128)
    if not isinstance(result, dict):
        raise ValueError(f"Unexpected classification result for {image_path}: {result!r}")

    category = str(result.get("category") or "other")
    if category not in CATEGORY_DIRS:
        category = "other"

    confidence = result.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.0

    return {
        "category": category,
        "confidence": confidence,
        "detected_title": str(result.get("detected_title") or ""),
        "evidence": str(result.get("evidence") or ""),
        "needs_ocr": bool(result.get("needs_ocr", False)),
    }, timing


def extract_target(image_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    result, timing = call_model(TARGET_EXTRACTION_PROMPT, image_path, max_tokens=1500)
    if not isinstance(result, dict):
        raise ValueError(f"Unexpected target extraction result for {image_path}: {result!r}")
    return result, timing


def append_result(record: ImageRecord) -> None:
    with RESULTS_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def copy_into_category(image_path: Path, category: str) -> Path:
    dest_dir = CATEGORY_DIRS[category]
    dest = dest_dir / image_path.name
    if not dest.exists():
        shutil.copy2(image_path, dest)
    return dest


def build_summary(records: list[ImageRecord]) -> dict[str, Any]:
    counts: dict[str, int] = {key: 0 for key in CATEGORY_DIRS}
    for record in records:
        counts[record.category] = counts.get(record.category, 0) + 1

    target_hits = [r for r in records if r.category == "jinzhou_plan"]
    uncertain = [r for r in records if r.needs_ocr or r.confidence < 0.68]
    return {
        "total": len(records),
        "counts": counts,
        "target_hits": [r.image for r in target_hits],
        "uncertain": [r.image for r in uncertain],
    }


def write_summary(records: list[ImageRecord]) -> None:
    summary = build_summary(records)
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 业务群图片分析结果",
        "",
        f"- 总数：{summary['total']}",
        f"- 检装车通知单：{summary['counts'].get('install_notice', 0)}",
        f"- 现场作业照片：{summary['counts'].get('site_photo', 0)}",
        f"- 手写单据：{summary['counts'].get('handwritten', 0)}",
        f"- 锦州港货物出港计划通知单：{summary['counts'].get('jinzhou_plan', 0)}",
        f"- 其他：{summary['counts'].get('other', 0)}",
        "",
        "## 重点目标",
        "",
    ]
    if summary["target_hits"]:
        lines.extend(f"- `{item}`" for item in summary["target_hits"])
    else:
        lines.append("- 未发现明确的《锦州港货物出港计划通知单》图片。")

    lines.extend(["", "## 需要复核", ""])
    if summary["uncertain"]:
        lines.extend(f"- `{item}`" for item in summary["uncertain"][:50])
    else:
        lines.append("- 无明显低置信度样本。")

    SUMMARY_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ensure_dirs()
    existing = load_existing_results()

    images = sorted(INPUT_DIR.glob("*.jpg"))
    if not images:
        raise SystemExit(f"No jpg images found in {INPUT_DIR}")

    records: list[ImageRecord] = list(existing.values())
    seen = {r.image for r in records}

    print(f"Found {len(images)} images, {len(records)} already processed.")
    for idx, image_path in enumerate(images, 1):
        if image_path.name in seen:
            continue

        classification, qwen_timing = classify_image(image_path)
        category = classification["category"]

        target_payload = None
        if category == "jinzhou_plan":
            target_payload, _ = extract_target(image_path)

        record = ImageRecord(
            image=image_path.name,
            category=category,
            confidence=classification["confidence"],
            detected_title=classification["detected_title"],
            evidence=classification["evidence"],
            needs_ocr=classification["needs_ocr"],
            qwen_took_ms=qwen_timing["took_ms"],
            qwen_elapsed_s=qwen_timing["elapsed_s"],
            qwen_raw_text=qwen_timing["raw_text"],
            target_extraction=target_payload,
        )

        append_result(record)
        records.append(record)
        seen.add(image_path.name)
        copy_into_category(image_path, category)

        if idx % 10 == 0 or category == "jinzhou_plan":
            print(
                f"[{idx}/{len(images)}] {image_path.name} -> {category} "
                f"(confidence={record.confidence:.2f}, took={record.qwen_elapsed_s:.1f}s)"
            )

    write_summary(records)
    print(f"Done. Summary written to {SUMMARY_MD}")


if __name__ == "__main__":
    main()
