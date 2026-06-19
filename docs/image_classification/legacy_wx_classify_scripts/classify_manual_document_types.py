#!/usr/bin/env python3
"""
Classify the manually re-sorted business-group images into 6 document types
or `other`, using qwen3-vl for type recognition only.

The six target folders are the ones already curated under the analysis result
root. This script reads each folder's `info.md`, turns the human-written
features into a single strict classification prompt, and evaluates the model
against the folder labels.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageOps, UnidentifiedImageError


API_URL = "http://127.0.0.1:8018/generate"

DEFAULT_ROOT = Path("/Users/qicai21/Desktop/业务群图片_分析结果")
PREVIEW_DIR = DEFAULT_ROOT / "_manual_doc_previews"
RESULTS_JSONL = DEFAULT_ROOT / "manual_doc_classification.jsonl"
REPORT_MD = DEFAULT_ROOT / "manual_doc_classification_report.md"

TARGET_FOLDERS = [
    "出港计划通知单",
    "耗材统计表",
    "检装车通知单",
    "请车表",
    "日现场工作记录表",
    "手写箱号车号表",
]

CATEGORY_ORDER = TARGET_FOLDERS + ["other"]

MODEL_RETRIES = 4
MAX_IMAGE_EDGE = 1200
MAX_SHORT_EDGE = 780


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


def safe_read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def build_category_cards(root: Path) -> dict[str, str]:
    # Keep the prompt short and stable. The folder-level info.md is still
    # used as the source of truth, but we condense it into a compact card.
    return {
        "出港计划通知单": "标题通常是“锦州港疏港货物放行计划通知单”；单张打印表；红色或黑色边框；有表首、表体、页脚，版式固定。",
        "耗材统计表": "无标题、无页脚；左右两栏结构；每栏首行常见“品名 / 尺寸 / 余量”；常是库存或余量统计，左栏有红黄标注。",
        "检装车通知单": "标题明确含“检、装车通知单”；打印表格；常见公司名+日期+道线；是检车/装车业务通知单。",
        "请车表": "无标题；通常 11 列大表；表头常见“日期、到站、品名、车型、需求、托运人、受理、装车地占、去向归、收货人、备注”；左侧可能有整车/集装箱/小计。",
        "日现场工作记录表": "无标题；通常 8 列左右；表头常见“日期、到站、船名、品名、道线、车数、班组、备注”；当前版本不作为重点目标，默认归 other。",
        "手写箱号车号表": "只在照片头部能看到“锦州港接车入库理货单”或“检车清单”时才算；纸面偏灰、拍摄有畸变、内容主要是密集数字、箱号、车号。",
    }


CLASSIFY_PROMPT_TEMPLATE = """你是业务图片类型识别助手。请只判断图片属于哪一个大类，不要读取或抽取表格内部内容。

候选类别只有：
- "出港计划通知单"
- "耗材统计表"
- "检装车通知单"
- "请车表"
- "日现场工作记录表"
- "手写箱号车号表"
- "other"

判断原则：
1. 只做类型识别，优先看标题、版式、是否手写、列数、是否现场实拍。
2. 如果不像下面任何一类，直接输出 other。
3. 如果图片很模糊、只拍到局部、标题看不清，也输出 other，不要猜。
4. 不要把现场照片类误判成任何表单类或手写类。照片类包括：敞车内部情况和作业、火车涂写mark、货垛、集装箱内情况和作业、检查工人、装卸现场情况，以及其他现场照片。
5. 不要把手写单据误判成打印表格。
6. `日现场工作记录表` 不作为重点类型处理，看到它也优先输出 other，不要把它硬分成其他重要单据。
7. 只输出严格 JSON，不要 markdown，不要解释。

类别定义：
{cards}

输出格式：
{{"category":"","confidence":0.0,"detected_title":"","evidence":""}}

字段要求：
- category 必须是上面 7 个类别之一。
- confidence 取 0 到 1 的小数。
- detected_title 只写图片里能看清的标题原文，没有就空字符串。
- evidence 只写最短依据，尽量 8 到 15 个汉字。

特别判定规则：
1. 看到“锦州港疏港货物放行计划通知单”或其明显同版式，判为 "出港计划通知单"。
2. 看到“检、装车通知单”字样，判为 "检装车通知单"。
3. "请车表" 通常是 11 列大表，常见表头含：日期、到站、品名、车型、需求、托运人、受理、装车地占、去向归、收货人、备注。
4. "耗材统计表" 通常没有标题和页页脚，左右两栏结构，首行常见“品名 / 尺寸 / 余量”；如果看到左右双栏和库存余量，就优先判这个。
5. "手写箱号车号表" 只在照片头部能看到“锦州港接车入库理货单”或“检车清单”时才判；如果是其他手写内容，一律归 other（作为其他手写报告单）。
6. 如果图片属于现场照片类，不要把它判成出港计划通知单、耗材统计表、检装车通知单、请车表、手写箱号车号表或其他手写记录。
7. "日现场工作记录表" 不是重点，除非图片上明确写着标题，否则输出 other。

{hint}
"""


@dataclass
class Prediction:
    image: str
    true_label: str
    pred_label: str
    confidence: float
    detected_title: str
    evidence: str
    took_ms: int
    elapsed_s: float
    raw_text: str
    broken: bool = False
    preview_path: str | None = None


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


def classify_image(image_path: Path, true_label: str, cards: dict[str, str], preview_dir: Path) -> Prediction:
    preview_path = ensure_preview(image_path, preview_dir)
    prompt = CLASSIFY_PROMPT_TEMPLATE.format(
        cards="\n".join(f"### {name}\n{cards[name]}" for name in TARGET_FOLDERS),
        hint="请特别注意：如果不确定，就输出 other，而不是勉强归类。",
    )
    result, timing = call_model(prompt, preview_path, max_tokens=128)
    if not isinstance(result, dict):
        raise ValueError(f"Unexpected classification result for {image_path}: {result!r}")

    category = str(result.get("category") or "other")
    if category not in CATEGORY_ORDER:
        category = "other"
    if category == "日现场工作记录表":
        category = "other"
    if category == "手写记录":
        category = "other"
    if category == "手写箱号车号表":
        detected_title = str(result.get("detected_title") or "")
        if ("锦州港接车入库理货单" not in detected_title) and ("检车清单" not in detected_title):
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
        detected_title=str(result.get("detected_title") or ""),
        evidence=str(result.get("evidence") or ""),
        took_ms=int(timing.get("took_ms", 0)),
        elapsed_s=float(timing.get("elapsed_s", 0.0)),
        raw_text=str(timing.get("raw_text", "")),
        broken=False,
        preview_path=str(preview_path),
    )


def is_readable_image(image_path: Path) -> bool:
    try:
        with Image.open(image_path) as img:
            img.verify()
        return True
    except Exception:
        return False


def collect_images(root: Path) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for label in TARGET_FOLDERS:
        folder = root / label
        if not folder.exists():
            continue
        for image_path in sorted(folder.glob("*.jpg")):
            items.append((image_path, label))
        for image_path in sorted(folder.glob("*.jpeg")):
            items.append((image_path, label))
        for image_path in sorted(folder.glob("*.png")):
            items.append((image_path, label))
    return items


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
        "# 六类单据识别报告",
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
        "- 策略说明：`日现场工作记录表` 在当前版本里会主动压到 `other`，它不作为重点识别类型。",
    ])

    lines.extend([
        "",
        "## 主要误判",
        "",
    ])

    mistakes = [p for p in predictions if p.true_label != p.pred_label]
    if mistakes:
        for item in mistakes[:60]:
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
    low_conf = sorted(predictions, key=lambda p: p.confidence)[:40]
    for item in low_conf:
        lines.append(
            f"- `{item.image}`：真实 `{item.true_label}` -> 预测 `{item.pred_label}` "
            f"(conf={item.confidence:.2f})"
        )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify the 6 manually sorted document folders.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="analysis result root directory")
    parser.add_argument("--limit", type=int, default=0, help="optional limit for quick testing")
    args = parser.parse_args()

    root = args.root
    cards = build_category_cards(root)
    preview_dir = PREVIEW_DIR
    preview_dir.mkdir(parents=True, exist_ok=True)

    items = collect_images(root)
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    predictions: list[Prediction] = []
    broken_count = 0

    print(f"Found {len(items)} images under the 6 manual folders.")
    for idx, (image_path, true_label) in enumerate(items, start=1):
        if not is_readable_image(image_path):
            broken_count += 1
            pred = Prediction(
                image=image_path.name,
                true_label=true_label,
                pred_label="other",
                confidence=0.0,
                detected_title="",
                evidence="图片文件损坏，无法打开。",
                took_ms=0,
                elapsed_s=0.0,
                raw_text="",
                broken=True,
                preview_path=None,
            )
            predictions.append(pred)
            print(f"[{idx:03d}/{len(items):03d}] {image_path.name} -> other (broken)")
            continue

        try:
            pred = classify_image(image_path, true_label, cards, preview_dir)
            predictions.append(pred)
            status = "✅" if pred.true_label == pred.pred_label else "❌"
            print(
                f"[{idx:03d}/{len(items):03d}] {status} {image_path.name} "
                f"{pred.true_label} -> {pred.pred_label} (conf={pred.confidence:.2f}, {pred.elapsed_s:.1f}s)"
            )
        except Exception as exc:
            pred = Prediction(
                image=image_path.name,
                true_label=true_label,
                pred_label="other",
                confidence=0.0,
                detected_title="",
                evidence=f"模型调用失败：{exc}",
                took_ms=0,
                elapsed_s=0.0,
                raw_text="",
                broken=False,
                preview_path=None,
            )
            predictions.append(pred)
            print(f"[{idx:03d}/{len(items):03d}] {image_path.name} -> other (error: {exc})")

    write_jsonl(predictions, RESULTS_JSONL)
    render_report(predictions, REPORT_MD, root)

    total = len(predictions)
    correct = sum(1 for p in predictions if p.true_label == p.pred_label)
    final_acc = (correct / total) if total else 0.0
    print(f"\nDone. Accuracy {correct}/{total} = {final_acc:.1%}")
    print(f"JSONL: {RESULTS_JSONL}")
    print(f"Report: {REPORT_MD}")
    if broken_count:
        print(f"Broken images: {broken_count}")


if __name__ == "__main__":
    main()
