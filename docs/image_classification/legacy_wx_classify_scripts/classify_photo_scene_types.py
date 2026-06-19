#!/usr/bin/env python3
"""
Classify the photo-based scene folders under 业务群图片_分析结果.

These folders are already manually grouped by the suffix after `照片-`.
The goal here is to validate that the scene type is recognized correctly,
regardless of whether the picture is taken in daytime or at night.
"""

from __future__ import annotations

import argparse
import json
import re
from PIL import ImageDraw
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageOps


API_URL = "http://127.0.0.1:8018/generate"

DEFAULT_ROOT = Path("/Users/qicai21/Desktop/业务群图片_分析结果")
PREVIEW_DIR = DEFAULT_ROOT / "_photo_scene_previews"
SHEET_DIR = DEFAULT_ROOT / "_photo_scene_sheets"
RESULTS_JSONL = DEFAULT_ROOT / "photo_scene_classification.jsonl"
REPORT_MD = DEFAULT_ROOT / "photo_scene_classification_report.md"

TARGET_FOLDERS = [
    "照片-敞车内部情况和作业",
    "照片-集装箱内情况和作业",
    "照片-装卸现场情况",
    "照片-货垛",
    "照片-火车涂写mark",
    "照片-检查工人",
    "照片-杂物垃圾-塑料布",
]

CATEGORY_ORDER = TARGET_FOLDERS + ["other"]

MODEL_RETRIES = 4
MAX_IMAGE_EDGE = 1200
MAX_SHORT_EDGE = 800
BATCH_SIZE = 6


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


def ensure_preview(image_path: Path, preview_dir: Path) -> Path:
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


def call_model(prompt: str, image_path: Path, max_tokens: int) -> tuple[Any, dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(1, MODEL_RETRIES + 1):
        try:
            started = time.perf_counter()
            response = requests.post(
                API_URL,
                json={
                    "prompt": prompt,
                    "image_path": str(image_path),
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                },
                timeout=240,
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


CATEGORY_HINTS = {
    "照片-敞车内部情况和作业": "敞车/敞顶车/货车内部，重点看车厢里正在装卸、清理、铺垫、检查的情况。",
    "照片-集装箱内情况和作业": "集装箱内部或箱门附近的作业，重点看箱内、箱壁、箱门、箱内装卸。",
    "照片-装卸现场情况": "整体装卸现场、港口/货场/堆场/线路作业，机械、人员、车辆、集装箱、吊装、搬运都可能出现。",
    "照片-货垛": "货物堆垛、料垛、货堆、成堆堆放的货物，重点看垛本身，不是车辆或箱体内部。",
    "照片-火车涂写mark": "火车车体或车皮上的涂写、mark、编号、标记、白字、黑字、粉笔字等近景。",
    "照片-检查工人": "重点主体是工人/作业人员，画面里人是主角，常见 2-5 个戴安全帽或反光衣的工人检查、查看、站立、巡视、沟通、合影；只要人物成组出现，就优先看成检查工人。",
    "照片-杂物垃圾-塑料布": "杂物、垃圾、废弃塑料布、包装残料、散乱物、地面清理后的废弃物。",
}


PROMPT_TEMPLATE = """你是现场照片分类助手。请只判断图片属于哪个场景类别，不要做内容长描述，不要读取单据内容。

候选类别只有：
- "照片-敞车内部情况和作业"
- "照片-集装箱内情况和作业"
- "照片-装卸现场情况"
- "照片-货垛"
- "照片-火车涂写mark"
- "照片-检查工人"
- "照片-杂物垃圾-塑料布"
- "other"

判断原则：
1. 只做场景分类，优先看画面主体是什么。
2. 白天和夜晚都一样按内容分类，光线暗、夜景、逆光、闪光灯、曝光不足都不能作为 other 的理由。
3. 如果图片不属于以上任何一种场景，或只有局部无法判断，输出 other。
4. 不要把同一个场景因为拍摄角度不同、远近不同、清晰度不同而分到别类。
5. 只输出严格 JSON，不要 markdown，不要解释。

类别说明：
{cards}

输出格式：
{{"category":"","confidence":0.0,"detected_scene":"","evidence":""}}

字段要求：
- category 必须是上面 8 个类别之一。
- confidence 取 0 到 1 的小数。
- detected_scene 只写你看见的最显著场景短语，没有就空字符串。
- evidence 只写最短依据，尽量 8 到 18 个汉字。

特别判定规则：
1. 如果主体是车厢内部、敞车内部、车内作业、车厢里铺垫/清理/装卸，判 "照片-敞车内部情况和作业"。即使有工人，也不要抢成检查工人。
2. 如果主体是集装箱内部、箱门、箱内作业，判 "照片-集装箱内情况和作业"。即使有工人，也不要抢成检查工人。
3. 如果是整体港口/货场/线路/机械/车辆/人员的装卸现场，判 "照片-装卸现场情况"。
4. 如果主体是堆起来的货堆/料垛，判 "照片-货垛"。
5. 如果主体是火车车皮或车体上的涂写、标记、编号近景，判 "照片-火车涂写mark"。
6. 如果主体是工人/作业人员被检查、巡视、站立沟通，尤其是 2-5 个戴安全帽或反光衣的工人成组出现，判 "照片-检查工人"。
7. 如果主体是杂物、垃圾、塑料布、废弃物，判 "照片-杂物垃圾-塑料布"。
8. 如果主体里人物最突出、照片明显围绕工人展开，就优先判 "照片-检查工人"，但只要车厢/箱体/货物/货堆明显是主体，就不要抢成检查工人。
9. 如果你不确定，宁可输出 other，也不要乱猜。

{hint}
"""

BATCH_PROMPT_TEMPLATE = """你是现场照片分类助手。你将看到一张 6 宫格拼图，格子按从上到下、从左到右编号 1-6。请对每个格子分别判断图片属于哪个场景类别。

候选类别只有：
- "照片-敞车内部情况和作业"
- "照片-集装箱内情况和作业"
- "照片-装卸现场情况"
- "照片-货垛"
- "照片-火车涂写mark"
- "照片-检查工人"
- "照片-杂物垃圾-塑料布"
- "other"

判断原则：
1. 只做场景分类，优先看画面主体是什么。
2. 白天和夜晚都一样按内容分类，光线暗、夜景、逆光、闪光灯、曝光不足都不能作为 other 的理由。
3. 如果图片不属于以上任何一种场景，或只有局部无法判断，输出 other。
4. 不要把同一个场景因为拍摄角度不同、远近不同、清晰度不同而分到别类。
5. 只输出严格 JSON 数组，不要 markdown，不要解释。

类别说明：
{cards}

输出格式：
[
  {{
    "slot": 1,
    "category": "",
    "confidence": 0.0,
    "detected_scene": "",
    "evidence": ""
  }}
]

字段要求：
- slot 必须是 1 到 6，顺序固定。
- category 必须是上面 8 个类别之一。
- confidence 取 0 到 1 的小数。
- detected_scene 只写你看见的最显著场景短语，没有就空字符串。
- evidence 只写最短依据，尽量 8 到 18 个汉字。

特别判定规则：
1. 如果主体是车厢内部、敞车内部、车内作业、车厢里铺垫/清理/装卸，判 "照片-敞车内部情况和作业"。即使有工人，也不要抢成检查工人。
2. 如果主体是集装箱内部、箱门、箱内作业，判 "照片-集装箱内情况和作业"。即使有工人，也不要抢成检查工人。
3. 如果是整体港口/货场/线路/机械/车辆/人员的装卸现场，判 "照片-装卸现场情况"。
4. 如果主体是堆起来的货堆/料垛，判 "照片-货垛"。
5. 如果主体是火车车皮或车体上的涂写、标记、编号近景，判 "照片-火车涂写mark"。
6. 如果主体是 2-5 个戴安全帽或反光衣的工人被检查、巡视、站立沟通，且人物是画面主角，判 "照片-检查工人"。
7. 如果主体是杂物、垃圾、塑料布、废弃物，判 "照片-杂物垃圾-塑料布"。
8. 如果主体里人物最突出、照片明显围绕工人展开，就优先判 "照片-检查工人"，但只要车厢/箱体/货物/货堆明显是主体，就不要抢成检查工人。
9. 如果你不确定，宁可输出 other，也不要乱猜。

{hint}
"""


@dataclass
class Prediction:
    image: str
    true_label: str
    pred_label: str
    confidence: float
    detected_scene: str
    evidence: str
    took_ms: int
    elapsed_s: float
    raw_text: str
    preview_path: str | None = None


def collect_images(root: Path) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for label in TARGET_FOLDERS:
        folder = root / label
        if not folder.exists():
            continue
        for ext in ("*.jpg", "*.jpeg", "*.png"):
            for image_path in sorted(folder.glob(ext)):
                items.append((image_path, label))
    return items


def make_sheet(batch: list[Path], batch_index: int, preview_dir: Path) -> Path:
    sheet_dir = preview_dir / "sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    sheet_path = sheet_dir / f"batch_{batch_index:03d}.jpg"
    if sheet_path.exists():
        return sheet_path

    cols = 3
    rows = 2
    cell_w = 560
    cell_h = 420
    label_h = 30
    margin = 12
    canvas = Image.new("RGB", (cols * cell_w + (cols + 1) * margin, rows * cell_h + (rows + 1) * margin), "white")
    font_color = "#111111"
    for idx, image_path in enumerate(batch):
        col = idx % cols
        row = idx // cols
        x0 = margin + col * (cell_w + margin)
        y0 = margin + row * (cell_h + margin)
        cell = Image.new("RGB", (cell_w, cell_h), "#f5f5f5")
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            thumb = ImageOps.contain(img, (cell_w - 20, cell_h - label_h - 20), Image.Resampling.LANCZOS)
            paste_x = (cell_w - thumb.width) // 2
            paste_y = label_h + (cell_h - label_h - thumb.height) // 2
            cell.paste(thumb, (paste_x, paste_y))
        draw = ImageDraw.Draw(cell)
        draw.text((8, 6), f"{idx + 1}. {image_path.name[:36]}", fill=font_color)
        canvas.paste(cell, (x0, y0))

    canvas.save(sheet_path, quality=92)
    return sheet_path


def is_readable_image(image_path: Path) -> bool:
    try:
        with Image.open(image_path) as img:
            img.verify()
        return True
    except Exception:
        return False


def classify_image(image_path: Path, true_label: str, preview_dir: Path) -> Prediction:
    preview_path = ensure_preview(image_path, preview_dir)
    prompt = PROMPT_TEMPLATE.format(
        cards="\n".join(f"### {name}\n{desc}" for name, desc in CATEGORY_HINTS.items()),
        hint="如果画面在白天/夜晚之间变化，只按场景本身判断，不要把夜景单独当成类别。",
    )
    result, timing = call_model(prompt, preview_path, max_tokens=96)
    if not isinstance(result, dict):
        raise ValueError(f"Unexpected result for {image_path}: {result!r}")

    category = str(result.get("category") or "other")
    if category not in CATEGORY_ORDER:
        category = "other"

    confidence = result.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.0

    return Prediction(
        image=image_path.name,
        true_label=true_label,
        pred_label=category,
        confidence=confidence,
        detected_scene=str(result.get("detected_scene") or ""),
        evidence=str(result.get("evidence") or ""),
        took_ms=int(timing.get("took_ms", 0)),
        elapsed_s=float(timing.get("elapsed_s", 0.0)),
        raw_text=str(timing.get("raw_text", "")),
        preview_path=str(preview_path),
    )


def classify_batch(batch: list[Path], batch_index: int, preview_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sheet_path = make_sheet(batch, batch_index, preview_dir)
    prompt = BATCH_PROMPT_TEMPLATE.format(
        cards="\n".join(f"### {name}\n{desc}" for name, desc in CATEGORY_HINTS.items()),
        hint="如果是夜景也按场景本身判断；如果不确定，才输出 other。",
    )
    result, timing = call_model(prompt, sheet_path, max_tokens=360)
    if not isinstance(result, list):
        raise ValueError(f"Unexpected batch result: {result!r}")
    if len(result) != len(batch):
        raise ValueError(f"Expected {len(batch)} slots, got {len(result)}")
    return result, timing


def write_jsonl(predictions: list[Prediction], out_path: Path) -> None:
    with out_path.open("w", encoding="utf-8") as f:
        for pred in predictions:
            f.write(json.dumps(pred.__dict__, ensure_ascii=False) + "\n")


def render_report(predictions: list[Prediction], report_path: Path, root: Path) -> None:
    total = len(predictions)
    correct = sum(1 for p in predictions if p.true_label == p.pred_label)
    accuracy = correct / total if total else 0.0

    per_label = defaultdict(lambda: {"total": 0, "correct": 0})
    confusion = defaultdict(Counter)
    for p in predictions:
        per_label[p.true_label]["total"] += 1
        per_label[p.true_label]["correct"] += int(p.true_label == p.pred_label)
        confusion[p.true_label][p.pred_label] += 1

    lines: list[str] = [
        "# 现场照片分类报告",
        "",
        f"- 评估范围：{root}",
        f"- 总图片数：{total}",
        f"- 总体准确率：{correct}/{total} = {accuracy:.1%}" if total else "- 总体准确率：0/0 = 0.0%",
        "",
        "## 各类准确率",
        "",
    ]
    for label in TARGET_FOLDERS:
        stats = per_label[label]
        acc = stats["correct"] / stats["total"] if stats["total"] else 0.0
        lines.append(f"- {label}：{stats['correct']}/{stats['total']} = {acc:.1%}")

    lines.extend([
        "",
        "## 主要误判",
        "",
    ])
    mistakes = [p for p in predictions if p.true_label != p.pred_label]
    if mistakes:
        for item in mistakes[:80]:
            lines.append(
                f"- `{item.image}`：真实 `{item.true_label}` -> 预测 `{item.pred_label}` "
                f"(conf={item.confidence:.2f})"
            )
    else:
        lines.append("- 无误判样本。")

    lines.extend([
        "",
        "## 混淆矩阵",
        "",
        "|真实\\预测|" + "|".join(CATEGORY_ORDER) + "|",
        "|---|" + "|".join(["---"] * len(CATEGORY_ORDER)) + "|",
    ])
    for label in TARGET_FOLDERS:
        row = [label]
        for pred in CATEGORY_ORDER:
            row.append(str(confusion[label][pred]))
        lines.append("|" + "|".join(row) + "|")

    lines.extend([
        "",
        "## 低置信度样本",
        "",
    ])
    for item in sorted(predictions, key=lambda p: p.confidence)[:40]:
        lines.append(
            f"- `{item.image}`：真实 `{item.true_label}` -> 预测 `{item.pred_label}` "
            f"(conf={item.confidence:.2f})"
        )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify scene photos into the photo-* folders.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="analysis result root directory")
    parser.add_argument("--limit", type=int, default=0, help="optional limit for quick testing")
    args = parser.parse_args()

    root = args.root
    preview_dir = PREVIEW_DIR
    preview_dir.mkdir(parents=True, exist_ok=True)
    SHEET_DIR.mkdir(parents=True, exist_ok=True)

    items = collect_images(root)
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    print(f"Found {len(items)} images under the photo folders.")

    predictions: list[Prediction] = []
    for batch_start in range(0, len(items), BATCH_SIZE):
        batch = items[batch_start : batch_start + BATCH_SIZE]
        if len(batch) < BATCH_SIZE:
            batch = batch + [batch[-1]] * (BATCH_SIZE - len(batch))

        batch_image_paths = [image_path for image_path, _ in batch]
        batch_labels = [true_label for _, true_label in batch]
        batch_index = batch_start // BATCH_SIZE + 1

        # Broken images are handled individually so the batch sheet remains valid.
        broken_slots = [i for i, image_path in enumerate(batch_image_paths) if not is_readable_image(image_path)]
        batch_result: list[dict[str, Any]] | None = None
        batch_timing: dict[str, Any] = {"took_ms": 0, "elapsed_s": 0.0, "raw_text": ""}

        if not broken_slots:
            try:
                batch_result, batch_timing = classify_batch(batch_image_paths, batch_index, preview_dir)
            except Exception:
                batch_result = None

        if batch_result is None:
            for slot, (image_path, true_label) in enumerate(batch, start=1):
                if not is_readable_image(image_path):
                    pred = Prediction(
                        image=image_path.name,
                        true_label=true_label,
                        pred_label="other",
                        confidence=0.0,
                        detected_scene="",
                        evidence="图片损坏，无法打开。",
                        took_ms=0,
                        elapsed_s=0.0,
                        raw_text="",
                        preview_path=None,
                    )
                else:
                    try:
                        pred = classify_image(image_path, true_label, preview_dir)
                    except Exception as exc:
                        pred = Prediction(
                            image=image_path.name,
                            true_label=true_label,
                            pred_label="other",
                            confidence=0.0,
                            detected_scene="",
                            evidence=f"模型调用失败：{exc}",
                            took_ms=0,
                            elapsed_s=0.0,
                            raw_text="",
                            preview_path=None,
                        )
                predictions.append(pred)
                status = "✅" if pred.true_label == pred.pred_label else "❌"
                print(
                    f"[{batch_start + slot:03d}/{len(items):03d}] {status} {image_path.name} "
                    f"{pred.true_label} -> {pred.pred_label} (conf={pred.confidence:.2f}, {pred.elapsed_s:.1f}s)"
                )
            continue

        for slot, slot_item in enumerate(batch_result, start=1):
            image_path, true_label = batch[slot - 1]
            if not is_readable_image(image_path):
                pred = Prediction(
                    image=image_path.name,
                    true_label=true_label,
                    pred_label="other",
                    confidence=0.0,
                    detected_scene="",
                    evidence="图片损坏，无法打开。",
                    took_ms=0,
                    elapsed_s=0.0,
                    raw_text="",
                    preview_path=None,
                )
                predictions.append(pred)
                print(f"[{batch_start + slot:03d}/{len(items):03d}] {image_path.name} -> other (broken)")
                continue

            category = str(slot_item.get("category") or "other")
            if category not in CATEGORY_ORDER:
                category = "other"
            confidence = slot_item.get("confidence", 0.0)
            try:
                confidence = float(confidence)
            except Exception:
                confidence = 0.0

            pred = Prediction(
                image=image_path.name,
                true_label=true_label,
                pred_label=category,
                confidence=confidence,
                detected_scene=str(slot_item.get("detected_scene") or ""),
                evidence=str(slot_item.get("evidence") or ""),
                took_ms=int(batch_timing.get("took_ms", 0)),
                elapsed_s=float(batch_timing.get("elapsed_s", 0.0)),
                raw_text=str(batch_timing.get("raw_text", "")),
                preview_path=str(preview_dir / image_path.name),
            )

            # Recheck uncertain predictions one-by-one.
            if pred.pred_label == "other" or pred.confidence < 0.72:
                try:
                    single_pred = classify_image(image_path, true_label, preview_dir)
                    if single_pred.pred_label != "other" or pred.pred_label == "other":
                        pred = single_pred
                except Exception:
                    pass

            predictions.append(pred)
            status = "✅" if pred.true_label == pred.pred_label else "❌"
            print(
                f"[{batch_start + slot:03d}/{len(items):03d}] {status} {image_path.name} "
                f"{pred.true_label} -> {pred.pred_label} (conf={pred.confidence:.2f}, {pred.elapsed_s:.1f}s)"
            )

    write_jsonl(predictions, RESULTS_JSONL)
    render_report(predictions, REPORT_MD, root)

    total = len(predictions)
    correct = sum(1 for p in predictions if p.true_label == p.pred_label)
    final_acc = (correct / total) if total else 0.0
    print(f"\nDone. Accuracy {correct}/{total} = {final_acc:.1%}")
    print(f"JSONL: {RESULTS_JSONL}")
    print(f"Report: {REPORT_MD}")


if __name__ == "__main__":
    main()
