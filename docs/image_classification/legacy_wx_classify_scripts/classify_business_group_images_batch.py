#!/usr/bin/env python3
"""
Batch-classify the decoded business-group images from Desktop/业务群图片.

This variant uses 6-up contact sheets to reduce the number of Qwen calls.
It is intended to be fast enough for a full 358-image pass.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageOps, ImageDraw, UnidentifiedImageError


API_URL = "http://127.0.0.1:8018/generate"
INPUT_DIR = Path("/Users/qicai21/Desktop/业务群图片")
OUTPUT_ROOT = Path("/Users/qicai21/Desktop/业务群图片_分析结果")
RESULTS_JSONL = OUTPUT_ROOT / "analysis_results.jsonl"
SUMMARY_JSON = OUTPUT_ROOT / "analysis_summary.json"
SUMMARY_MD = OUTPUT_ROOT / "analysis_summary.md"
TARGET_DIR = OUTPUT_ROOT / "锦州港货物出港计划通知单"
SHEET_DIR = OUTPUT_ROOT / "_sheets"
PREVIEW_DIR = OUTPUT_ROOT / "_previews"

BATCH_SIZE = 6
MAX_IMAGE_EDGE = 1600
MODEL_RETRIES = 4

CATEGORY_DIRS = {
    "install_notice": OUTPUT_ROOT / "检装车通知单",
    "site_photo": OUTPUT_ROOT / "现场作业照片",
    "handwritten": OUTPUT_ROOT / "手写单据",
    "jinzhou_plan": TARGET_DIR,
    "other": OUTPUT_ROOT / "其他",
}

CLASSIFY_PROMPT = """你将看到一张 6 宫格业务图片拼图，格子按从上到下、从左到右编号 1-6。
请对每个格子中的图片分类，输出严格 JSON 数组，不要输出解释，不要使用 markdown。

category 只能是：
- "install_notice"：图片是《检、装车通知单》或其变体/同类版式。
- "site_photo"：明显的现场作业实拍照片，如堆场、装卸、车辆、集装箱、机械、道路、港口现场等。
- "handwritten"：明显手写单据、手写记录、手写表格、便签。
- "jinzhou_plan"：图片明确是《锦州港货物出港计划通知单》或高度疑似。
- "other"：其他。

输出格式：
[
  {
    "slot": 1,
    "category": "",
    "confidence": 0.0,
    "detected_title": "",
    "evidence": ""
  }
]

规则：
1. 必须返回 6 个对象，对应 slot 1-6，顺序固定。
2. slot 必须是整数 1 到 6。
3. detected_title 仅写能看清的标题，没有就空字符串。
4. evidence 用一句短语说明判断依据。
5. 如果看到“锦州港货物出港计划通知单”或其明显同类标题/版式，请直接分类为 jinzhou_plan。
6. 如果图片明显是《检、装车通知单》，请分类为 install_notice。
7. 只输出 JSON 数组，不要额外文字。"""

SINGLE_CLASSIFY_PROMPT = """请判断这张图片的类型，输出严格 JSON，不要解释，不要 markdown。
category 只能是：
- "install_notice"：图片是《检、装车通知单》或其变体/同类版式。
- "site_photo"：明显的现场作业实拍照片。
- "handwritten"：明显手写单据、手写记录、手写表格、便签。
- "jinzhou_plan"：图片明确是《锦州港货物出港计划通知单》或高度疑似。
- "other"：其他。

输出格式：
{
  "category": "",
  "confidence": 0.0,
  "detected_title": "",
  "evidence": ""
}

规则：
1. 只输出 JSON。
2. 如果看到“锦州港货物出港计划通知单”或其明显同类标题/版式，请直接分类为 jinzhou_plan。
3. 如果图片明显是《检、装车通知单》，请分类为 install_notice。
4. 不要输出任何额外文本。"""

MINIMAL_SINGLE_CLASSIFY_PROMPT = """请只输出严格 JSON：
{"category":"","confidence":0.0,"detected_title":"","evidence":""}

category 只能是 install_notice、site_photo、handwritten、jinzhou_plan、other。
如果看到“锦州港货物出港计划通知单”或同类版式，请用 jinzhou_plan。
如果看到《检、装车通知单》，请用 install_notice。"""

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


def prepare_preview(image_path: Path, out_dir: Path = PREVIEW_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    preview_path = out_dir / image_path.name
    if preview_path.exists():
        return preview_path

    with Image.open(image_path) as img:
        img = img.convert("RGB")
        longest = max(img.size)
        if longest > MAX_IMAGE_EDGE:
            scale = MAX_IMAGE_EDGE / float(longest)
            new_size = (max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale)))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        img.save(preview_path, format="JPEG", quality=90, optimize=True)
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
    qwen_took_ms: int
    qwen_elapsed_s: float
    qwen_raw_text: str
    needs_ocr: bool = False
    target_extraction: dict[str, Any] | None = None


def ensure_dirs() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    SHEET_DIR.mkdir(parents=True, exist_ok=True)
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


def is_readable_image(image_path: Path) -> bool:
    try:
        with Image.open(image_path) as img:
            img.verify()
        return True
    except Exception:
        return False


def make_sheet(batch: list[Path], batch_index: int) -> tuple[Path, list[str]]:
    cols = 3
    rows = 2
    cell_w = 560
    cell_h = 420
    label_h = 28
    margin = 12
    sheet_w = cols * cell_w + (cols + 1) * margin
    sheet_h = rows * cell_h + (rows + 1) * margin
    canvas = Image.new("RGB", (sheet_w, sheet_h), "white")
    font_color = "#111111"
    labels: list[str] = []

    for idx, image_path in enumerate(batch):
        col = idx % cols
        row = idx // cols
        x0 = margin + col * (cell_w + margin)
        y0 = margin + row * (cell_h + margin)
        cell = Image.new("RGB", (cell_w, cell_h), "#f3f3f3")
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            thumb = ImageOps.contain(img, (cell_w - 20, cell_h - label_h - 20), Image.Resampling.LANCZOS)
            paste_x = (cell_w - thumb.width) // 2
            paste_y = label_h + (cell_h - label_h - thumb.height) // 2
            cell.paste(thumb, (paste_x, paste_y))
        draw = ImageDraw.Draw(cell)
        label = f"{idx + 1}. {image_path.name[:34]}"
        labels.append(label)
        draw.text((8, 4), label, fill=font_color)
        canvas.paste(cell, (x0, y0))

    sheet_path = SHEET_DIR / f"batch_{batch_index:03d}.jpg"
    canvas.save(sheet_path, quality=92)
    return sheet_path, labels


def classify_batch(sheet_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result, timing = call_model(CLASSIFY_PROMPT, sheet_path, max_tokens=450)
    if not isinstance(result, list):
        raise ValueError(f"Unexpected batch classification result: {result!r}")
    if len(result) != BATCH_SIZE:
        raise ValueError(f"Expected {BATCH_SIZE} slots, got {len(result)}")
    return result, timing


def classify_single(image_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        result, timing = call_model(SINGLE_CLASSIFY_PROMPT, image_path, max_tokens=256)
        if isinstance(result, dict):
            return result, timing
    except Exception:
        pass

    result, timing = call_model(MINIMAL_SINGLE_CLASSIFY_PROMPT, image_path, max_tokens=96)
    if not isinstance(result, dict):
        raise ValueError(f"Unexpected single classification result for {image_path}: {result!r}")
    return result, timing


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
    uncertain = [r for r in records if r.confidence < 0.68]
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
    pending = [img for img in images if img.name not in seen]

    broken = [img for img in pending if not is_readable_image(img)]
    for image_path in broken:
        record = ImageRecord(
            image=image_path.name,
            category="other",
            confidence=0.0,
            detected_title="",
            evidence="图片文件损坏，无法打开。",
            qwen_took_ms=0,
            qwen_elapsed_s=0.0,
            qwen_raw_text="",
            needs_ocr=False,
            target_extraction=None,
        )
        append_result(record)
        records.append(record)
        seen.add(image_path.name)
        copy_into_category(image_path, "other")
        print(f"[{len(records)}/{len(images)}] {image_path.name} -> other (broken image)")

    pending = [img for img in pending if img.name not in seen]
    for batch_index in range(0, len(pending), BATCH_SIZE):
        batch = pending[batch_index: batch_index + BATCH_SIZE]
        if len(batch) < BATCH_SIZE:
            # Pad the last batch with repeated images only for model input; we ignore the extras.
            batch = batch + [batch[-1]] * (BATCH_SIZE - len(batch))

        sheet_path, _ = make_sheet(batch, batch_index // BATCH_SIZE + 1)
        try:
            batch_result, timing = classify_batch(sheet_path)
            slot_items = batch_result
            use_fallback = False
        except Exception:
            slot_items = []
            timing = {"took_ms": 0, "elapsed_s": 0.0, "raw_text": ""}
            use_fallback = True

        if use_fallback:
            for image_path in batch:
                if image_path.name in seen:
                    continue
                single_result, single_timing = classify_single(image_path)
                slot_items.append(
                    {
                        "slot": len(slot_items) + 1,
                        "category": single_result.get("category", "other"),
                        "confidence": single_result.get("confidence", 0.0),
                        "detected_title": single_result.get("detected_title", ""),
                        "evidence": single_result.get("evidence", ""),
                        "_timing": single_timing,
                    }
                )
            if len(slot_items) != BATCH_SIZE:
                raise RuntimeError(f"Fallback classification did not yield {BATCH_SIZE} items for {sheet_path}")

        for slot_item in slot_items:
            slot = int(slot_item.get("slot", 0))
            if slot < 1 or slot > BATCH_SIZE:
                continue
            image_path = batch[slot - 1]
            if image_path.name in seen:
                continue

            category = str(slot_item.get("category") or "other")
            if category not in CATEGORY_DIRS:
                category = "other"
            confidence = slot_item.get("confidence", 0.0)
            try:
                confidence = float(confidence)
            except Exception:
                confidence = 0.0

            target_payload = None
            if category == "jinzhou_plan":
                target_payload, _ = extract_target(image_path)

            record = ImageRecord(
                image=image_path.name,
                category=category,
                confidence=confidence,
                detected_title=str(slot_item.get("detected_title") or ""),
                evidence=str(slot_item.get("evidence") or ""),
                needs_ocr=False,
                qwen_took_ms=int(slot_item.get("_timing", timing).get("took_ms", timing["took_ms"])),
                qwen_elapsed_s=float(slot_item.get("_timing", timing).get("elapsed_s", timing["elapsed_s"])),
                qwen_raw_text=str(slot_item.get("_timing", timing).get("raw_text", timing["raw_text"])),
                target_extraction=target_payload,
            )
            append_result(record)
            records.append(record)
            seen.add(image_path.name)
            copy_into_category(image_path, category)

            print(
                f"[{len(records)}/{len(images)}] {image_path.name} -> {category} "
                f"(confidence={record.confidence:.2f}, took={record.qwen_elapsed_s:.1f}s)"
            )

    write_summary(records)
    print(f"Done. Summary written to {SUMMARY_MD}")


if __name__ == "__main__":
    main()
