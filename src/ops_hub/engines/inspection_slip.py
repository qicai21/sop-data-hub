import json
import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

API_URL = "http://127.0.0.1:8018/generate"
MAX_IMAGE_EDGE = 1600
MAX_SHORT_EDGE = 1000

STATIONS = [
    "汐子", "马林", "凌源东", "凌东", "朝阳西", "朝阳", "四平", "新台子",
    "八角台", "瓢儿屯", "赤峰南", "赤峰", "林西", "霍林河", "草市", "长春东",
    "虎石台", "珲春南", "海拉尔东", "富拉尔基", "榆树屯", "沙岭", "乌兰浩特", "乌钢", "五栋房"
]
# Ensure longer names match first for stations
STATIONS.sort(key=len, reverse=True)

CARGO_TYPES = [
    "铁矿粉", "铁矿", "铁", "工业盐", "盐", "大豆", "集装箱", "敞顶箱",
    "煤炭", "煤", "铜精矿", "铜", "镍矿", "镍", "氧化铝", "铝",
    "锌精矿", "锌矿", "沙子", "石料", "石子", "WPF粉", "混合粉", 
    "印粉", "印度粉", "巴西粉", "澳粉矿", "铜矿吨袋"
]
CARGO_TYPES.sort(key=len, reverse=True)

DEFECT_KEYWORDS = [
    "门缝大", "排", "临修", "大门划坏", "小门断带", "车皮毛刺", "车皮上沿开裂", 
    "地板漏", "空排", "车皮立柱开焊", "地板裂", "小门划缺失", "大门反关", 
    "大门折页断", "小门带铁坏"
]

AGENTS = [
    "沈阳盛京颐昇", "营口铁晟", "沿海物流", "赤峰远联", "霍煤鸿骏", 
    "锦港物流", "鞍钢集团朝阳钢铁", "新铁晟"
]

ENTRY_PROMPT = """你是一个图片版式分析专家。请仔细观察提供的图片，判断它是否为“检装车通知单”。
1. **统计标题数量**：图片中出现了几个完整的“锦州港...检、装车通知单”抬头标题？如果宽度很长且有两个标题，说明是横向拼接的双页单据（通常 50-100 车）。
2. **预估总车数**：观察序号或页头“节数”，是否超过 50 车？
3. **输出 JSON**：
{
  "is_inspection": true,
  "summary": "简短描述",
  "title_count": 1,
  "doc_type": "normal",
  "title_texts": ["标题原文"]
}"""

HEADER_PROMPT = """你是一个专业的 OCR 助手。请识别这张《检、装车通知单》顶部的页头信息和底部的页脚汇总信息。
只输出严格 JSON。
JSON 结构：
{
  "title": "",
  "meta": { "daoxian": "", "jieshu": 0, "date": "", "jiancheyuan": "" },
  "footer": { "zhuangche_jieshu": null, "paiche_jieshu": null }
}"""

NORMAL_ROWS_PROMPT = """你是一个专业的 OCR 数据录入助手。请提取表格中的每一行数据并返回 JSON 数组。

字段要求：
[
  {
    "seq": 0,
    "car_type": "车型",
    "car_no": "7位数字车号",
    "cargo_info_raw": "必须完整提取单元格内所有信息（包括：到站、货名、船舶名、代理名、节数）",
    "remark": "备注/高度",
    "defect": false
  }
]

核心规则：
1. **完整货描**：在 cargo_info_raw 字段中，必须保留原始单元格内的所有文字（例如：汐子/铁矿粉/金泰68/15节），严禁自行简化或省略。
2. **零遗漏**：严禁跳过任何一行，只要有序号就必须输出。
3. **划线检测**：如果该行被划掉，设置 "defect": true。
4. **输出要求**：只输出 JSON 数组，严禁任何解释。"""

NORMAL_ROWS_RETRY_PROMPT = """你是一个极致准确的 OCR 助手。请对这张{side_label}进行深度二次扫描。
你需要确保表格中的每一行都被提取出来，即使文字有些模糊。
只输出 JSON 数组，不要任何省略号或解释。
[
  {
    "seq": 0,
    "car_type": "",
    "car_no": "",
    "cargo_info_raw": "",
    "remark": "",
    "defect": false
  }
]
强化规则：
1. 严禁只返回第一行。
2. 必须按照图片中的序号(seq)顺序排列。
3. 即使某些字段为空，只要有序号和车号，就必须输出该行。"""

ALUMINA_ROWS_PROMPT = """你是氧化铝检车单OCR助手。请识别这张图片中的{side_label}主表数据。
字段固定为：
[
  {
    "seq": 0,
    "car_type": "",
    "car_no": "",
    "cargo_info_raw": "",
    "remark": "",
    "tarp_no": "",
    "piece_count": null,
    "defect": false
  }
]
规则：
1. **划线即排车**：如果该行被横线划掉，标记 `defect: true`。
2. **多行合并**：采集单元格内完整的货运信息（到站、单位等）。
3. **只识别有序号的行**。
4. **不要输出任何 Markdown 格式或额外文字**。"""

ALUMINA_ROWS_RETRY_PROMPT = """你是氧化铝检车单OCR助手。下面图片只包含{side_label}，请把该栏中所有可见序号行逐行完整输出。
只输出严格JSON数组，不要解释，不要示例，不要省略中间行。
每看到一个序号，就必须输出一个对象，不允许只返回第一行。
字段固定为：
[
  {
    "seq": 0,
    "car_type": "",
    "car_no": "",
    "cargo_info_raw": "",
    "remark": "",
    "tarp_no": "",
    "piece_count": null,
    "defect": false
  }
]
补充规则：
1. seq 必须等于图中左侧序号；
2. car_no 必须是7位数字字符串；
3. remark 只放“装载高度/备注”中的可见文本；
4. tarp_no 对应篷布号，piece_count 对应件数；
5. 不允许只返回首行；
6. 故障说明或横线仍要标 defect=true。"""


def _build_rows_prompt(doc_type: str, side_label: str, retry: bool = False) -> str:
    if doc_type == "alumina":
        template = ALUMINA_ROWS_RETRY_PROMPT if retry else ALUMINA_ROWS_PROMPT
    else:
        template = NORMAL_ROWS_RETRY_PROMPT if retry else NORMAL_ROWS_PROMPT
    return template.replace("{side_label}", side_label)


class InspectionSlipEngine:
    def __init__(
        self,
        service_url: str = API_URL,
        output_base: Optional[str] = None,
    ) -> None:
        self.service_url = service_url
        self.output_base = Path(output_base) if output_base else None
        if self.output_base is not None:
            self.output_base.mkdir(parents=True, exist_ok=True)

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

    def _extract_json_fragment(self, text: str) -> Any:
        text = text.strip()
        # Fallback: find the first '[' or '{'
        for idx, ch in enumerate(text):
            if ch in "[{":
                try:
                    # Attempt to extract matching JSON object
                    # We'll use a simpler approach if raw_decode fails
                    # but for now let's just use a try-except block
                    import json
                    decoder = json.JSONDecoder()
                    obj, _ = decoder.raw_decode(text[idx:])
                    return obj
                except json.JSONDecodeError:
                    continue
        
        # If no JSON found, log and return empty list to avoid crashing
        logger.warning(f"Could not parse JSON from model output: {text[:200]}...")
        return []

    def call_api(self, prompt: str, image_path: str, max_tokens: int) -> tuple[Any, float]:
        started = time.perf_counter()
        resp = requests.post(
            self.service_url,
            json={
                "prompt": prompt,
                "image_path": str(image_path),
                "max_tokens": max_tokens,
                "temperature": 0.0,
            },
            timeout=180,
        )
        resp.raise_for_status()
        text = resp.json().get("text", "")
        logger.debug("[InspectionEngine] Raw model output for prompt %s...: %s", prompt[:40], text)
        return self._extract_json_fragment(text), time.perf_counter() - started

    def _split_into_pages(self, image_path: Path, output_dir: Path, title_count: int) -> List[Path]:
        image = Image.open(image_path)
        width, height = image.size
        if title_count <= 1:
            page_path = output_dir / f"{image_path.stem}_page1.png"
            image.save(page_path)
            return [page_path]

        page_paths: List[Path] = []
        page_width = width / title_count
        for idx in range(title_count):
            left = int(round(idx * page_width))
            right = int(round((idx + 1) * page_width))
            crop = image.crop((left, 0, right, height))
            page_path = output_dir / f"{image_path.stem}_page{idx + 1}.png"
            crop.save(page_path)
            page_paths.append(page_path)
        return page_paths

    def _split_page_into_sides(self, page_path: Path, output_dir: Path) -> Dict[str, Path]:
        image = Image.open(page_path)
        width, height = image.size
        top = int(height * 0.02)
        bottom = int(height * 0.98)
        split_x = int(width * 0.50)

        left_crop = image.crop((0, top, split_x, bottom))
        right_crop = image.crop((max(0, split_x - 8), top, width, bottom))

        left_path = output_dir / f"{page_path.stem}_left.png"
        right_path = output_dir / f"{page_path.stem}_right.png"
        left_crop.save(left_path)
        right_crop.save(right_path)
        return {"left": left_path, "right": right_path}

    def _split_side_into_chunks(self, side_path: Path, output_dir: Path) -> List[Path]:
        image = Image.open(side_path)
        width, height = image.size
        # Split columns into 4 vertical chunks for ultra-high recall (approx. 6-7 rows per chunk)
        chunk_paths = []
        chunk_h = height / 4
        for i in range(4):
            # Increased overlap to 80px to ensure dense digital tables aren't missed on boundaries
            top = max(0, int(i * chunk_h) - 80)
            bottom = min(height, int((i + 1) * chunk_h) + 80)
            crop = image.crop((0, top, width, bottom))
            c_path = output_dir / f"{side_path.stem}_c{i}.png"
            crop.save(c_path)
            chunk_paths.append(c_path)
        return chunk_paths

    def _normalize_rows(self, doc_type: str, raw_rows: Any, page_index: int, side_name: str) -> List[Dict[str, Any]]:
        if isinstance(raw_rows, dict):
            raw_rows = raw_rows.get("rows", [raw_rows])
        if not isinstance(raw_rows, list):
            return []

        rows: List[Dict[str, Any]] = []
        for idx, row in enumerate(raw_rows, start=1):
            if not isinstance(row, dict):
                continue
            
            raw_seq = str(row.get("seq") or "").strip()
            # Robustly extract digits for seq, even if crossed out or annotated
            seq_match = re.search(r'(\d+)', raw_seq)
            seq = int(seq_match.group(1)) if seq_match else None

            normalized: Dict[str, Any] = {
                "page_index": page_index,
                "page_side": side_name,
                "row_index_in_side": idx,
                "seq": seq,
                "car_type": str(row.get("car_type", "")).strip(),
                "car_no": str(row.get("car_no", "")).strip(),
                "cargo_info_raw": str(row.get("cargo_info_raw", "")).strip(),
                "remark": str(row.get("remark", "")).strip(),
                "defect": bool(row.get("defect", False)),
            }
            if doc_type == "alumina":
                normalized["tarp_no"] = str(row.get("tarp_no", "")).strip()
                piece = str(row.get("piece_count", "")).strip()
                normalized["piece_count"] = int(piece) if piece.isdigit() else None
            rows.append(normalized)
        return rows

    def _should_retry_rows(self, rows: List[Dict[str, Any]], meta: Dict[str, Any]) -> bool:
        if not rows or len(rows) > 2:
            return False
        total = meta.get("jieshu")
        return isinstance(total, int) and total >= 20

    def _extract_cargo_description(self, raw_text: str) -> Optional[str]:
        if not raw_text or not raw_text.strip():
            return None
        
        text = raw_text.strip().replace("\n", "/").replace(" ", "")
        
        # Check if this row is a "Batch Anchor" (Station + Cargo)
        matched_station = next((s for s in STATIONS if s in text), None)
        matched_cargo = next((c for c in CARGO_TYPES if c in text), None)
        
        # Determine if it's a new anchor or just details
        is_anchor = matched_station is not None and matched_cargo is not None
        
        # Also check for agents or defects
        has_agent = any(a in text for a in AGENTS)
        has_defect_text = any(d in text for d in DEFECT_KEYWORDS)
        
        if is_anchor or has_agent or has_defect_text or len(text) >= 2:
            return {
                "text": text,
                "is_anchor": is_anchor,
                "station": matched_station,
                "cargo": matched_cargo
            }
        return None

    def _apply_inheritance(self, rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        current_base = "未知到站/未知货名"
        current_details = ""
        summary: Dict[str, List[str]] = {}
        
        for idx, row in enumerate(rows):
            raw_text = row.get("cargo_info_raw", "").strip()
            remark_text = row.get("remark", "").strip()
            
            # 1. Text-based Defect Detection
            if any(d in raw_text or d in remark_text for d in DEFECT_KEYWORDS):
                row["defect"] = True

            # 2. Cargo Description Logic
            extracted = self._extract_cargo_description(raw_text)
            
            if extracted:
                if extracted["is_anchor"]:
                    # NEW BATCH START: e.g., "汐子铁矿粉"
                    current_base = f"{extracted['station']}{extracted['cargo']}"
                    # Check if there is extra text in the same string for details
                    remaining = extracted["text"].replace(extracted["station"], "", 1).replace(extracted["cargo"], "", 1)
                    current_details = remaining.strip("/")
                else:
                    # Update details (Ship/Agent) for the current batch
                    current_details = extracted["text"]
            
            # Combine base and details
            effective_desc = current_base
            if current_details and current_details not in effective_desc:
                effective_desc = f"{current_base}/{current_details}"
            
            row["cargo_info_effective"] = effective_desc
            
            # 3. Add to summary if NOT a defect
            if not row.get("defect"):
                if effective_desc not in summary:
                    summary[effective_desc] = []
                car_no = row.get("car_no", "")
                if car_no:
                    summary[effective_desc].append(car_no)
        
        return summary

    def _validate_result(self, meta: Dict[str, Any], footer: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
        total_meta = meta.get("jieshu", 0)
        zhuangche = footer.get("zhuangche_jieshu")
        paiche = footer.get("paiche_jieshu")
        row_count = len(rows)

        msgs = []
        if total_meta and row_count != total_meta:
            msgs.append(f"车数不符: 预期 {total_meta}, 实得 {row_count}")

        if isinstance(zhuangche, (int, float)) and isinstance(paiche, (int, float)) and total_meta:
            if int(zhuangche + paiche) != total_meta:
                msgs.append(f"对账失败: 装车({zhuangche}) + 排车({paiche}) != 总数({total_meta})")

        return " | ".join(msgs)

    def _looks_like_inspection(self, layout: Dict[str, Any]) -> bool:
        title_texts = " ".join(layout.get("title_texts", [])).strip()
        summary = str(layout.get("summary", "")).strip()
        combined = f"{title_texts} {summary}"
        keywords = [
            "检车单",
            "装车通知单",
            "火运输港检",
            "氧化铝火运输港检",
            "铜精矿火运输港检",
            "锦州港杂码公司",
        ]
        return any(keyword in combined for keyword in keywords)

    def process_image(self, image_path: str) -> Dict[str, Any]:
        img_path = Path(image_path)
        logger.info("[InspectionSlipEngine] Starting pipeline for %s", img_path.name)
        preview_path = self._prepare_preview(img_path)
        layout, _ = self.call_api(ENTRY_PROMPT, str(preview_path), 180)
        if not isinstance(layout, dict):
            layout = {}

        inferred_inspection = self._looks_like_inspection(layout)
        if inferred_inspection:
            layout["is_inspection"] = True
            if layout.get("doc_type") not in {"normal", "alumina"}:
                layout["doc_type"] = "alumina" if "氧化铝" in " ".join(layout.get("title_texts", [])) else "normal"

        if not bool(layout.get("is_inspection", False)):
            summary = str(layout.get("summary", "")).strip() or "未识别到检车单结构"
            return {
                "rows_count": 0,
                "last_car_no": "",
                "car_nos": [],
                "rows": [],
                "meta": {},
                "doc_type": "unknown",
                "is_target": False,
                "message": f"图片并非检车单，主要信息为：{summary}",
                "preview_path": str(preview_path),
            }

        title_count_raw = layout.get("title_count", 1)
        try:
            title_count = int(title_count_raw)
        except (ValueError, TypeError):
            title_count = 1
        title_count = max(1, min(title_count, 4))

        doc_type = str(layout.get("doc_type", "normal")).strip().lower()
        if doc_type not in {"normal", "alumina"}:
            title_texts = "".join(layout.get("title_texts", []))
            doc_type = "alumina" if "氧化铝" in title_texts else "normal"

        temp_parent = str(self.output_base) if self.output_base is not None else None
        with tempfile.TemporaryDirectory(prefix="inspection_slip_", dir=temp_parent) as temp_dir:
            workspace = Path(temp_dir)
            pages = self._split_into_pages(img_path, workspace, title_count)
            header_data, _ = self.call_api(HEADER_PROMPT, str(pages[0]), 220)
            if not isinstance(header_data, dict):
                header_data = {}

            total_rows: List[Dict[str, Any]] = []
            for page_index, page_path in enumerate(pages, start=1):
                side_paths = self._split_page_into_sides(page_path, workspace)
                for side_name in ["left", "right"]:
                    # NEW: Triple nesting split (Page -> Side -> Chunk)
                    chunks = self._split_side_into_chunks(side_paths[side_name], workspace)
                    for chunk_idx, chunk_path in enumerate(chunks):
                        chunk_label = "上半部" if chunk_idx == 0 else "下半部"
                        side_label = f"第{page_index}页{side_name}栏{chunk_label}"
                        prompt = _build_rows_prompt(doc_type, side_label, retry=False)
                        raw_rows, _ = self.call_api(prompt, str(chunk_path), 1300)
                        normalized_rows = self._normalize_rows(doc_type, raw_rows, page_index, side_name)
                        total_rows.extend(normalized_rows)

        # De-duplicate rows by seq to handle overlaps between chunks
        seen_seqs = set()
        unique_rows = []
        for r in total_rows:
            s = r.get("seq")
            if s is not None and s in seen_seqs:
                continue
            if s is not None:
                seen_seqs.add(s)
            unique_rows.append(r)
        total_rows = unique_rows

        total_rows = [
            row
            for row in total_rows
            if row.get("seq") is not None
            and row.get("seq", 0) > 0
            and (
                row.get("car_no")
                or row.get("cargo_info_raw")
                or row.get("tarp_no")
                or row.get("piece_count") is not None
            )
        ]
        total_rows.sort(key=lambda item: (item.get("seq") or 9999, item.get("page_index"), item.get("row_index_in_side")))
        for idx, row in enumerate(total_rows, start=1):
            row["global_index"] = idx

        cargo_summary = self._apply_inheritance(total_rows)

        if not total_rows:
            title_text = " ".join(layout.get("title_texts", []))
            if self._looks_like_inspection(layout) or title_text or header_data.get("title"):
                return {
                    "rows_count": 0,
                    "last_car_no": "",
                    "car_nos": [],
                    "rows": [],
                    "meta": header_data.get("meta") or {},
                    "doc_type": doc_type,
                    "is_target": True,
                    "message": "检车单已识别，但表格明细提取失败，请人工复核或重试。",
                    "preview_path": str(preview_path),
                }
            summary = title_text or "未识别到有效表格数据"
            return {
                "rows_count": 0,
                "last_car_no": "",
                "car_nos": [],
                "rows": [],
                "meta": header_data.get("meta") or {},
                "doc_type": doc_type,
                "is_target": True,
                "message": f"图片并非检车单，主要信息为：{summary}",
                "preview_path": str(preview_path),
            }

        meta_data = header_data.get("meta") or {}
        footer_data = header_data.get("footer") or {}
        val_msg = self._validate_result(meta_data, footer_data, total_rows)

        last_car_no = str(total_rows[-1].get("car_no", "") or "").strip()
        car_nos: List[str] = []
        for row in total_rows:
            car_no = str(row.get("car_no", "") or "").strip()
            if car_no and car_no not in car_nos:
                car_nos.append(car_no)

        logger.info("[InspectionSlipEngine] OCR finished. JSON payload ready with %d row(s)", len(total_rows))
        return {
            "rows_count": len(total_rows),
            "last_car_no": last_car_no,
            "car_nos": car_nos,
            "rows": total_rows,
            "cargo_summary": cargo_summary,
            "meta": meta_data,
            "footer": footer_data,
            "doc_type": doc_type,
            "is_inspection": True,
            "message": val_msg,
            "preview_path": str(preview_path),
        }


# Backward-compatible alias for existing callers.
InspectionEngine = InspectionSlipEngine
