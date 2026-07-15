"""Text message router for SOP Data Hub.

R67: Classifies text messages into SOP project/flow/node and writes back to
message_inbox. Replaces the old hard-coded keyword matching in
monitoring_plan_matcher._fallback_alignment_match and executor_runner's
`"四平" not in event.text` gate.

Rules (priority order):
  1. Departure text → jilin_jingang_jinzhou / jiusan / departure_flow / detect_departure_message
  2. Chaoyang business context → chaoyang_steel / dispatch_flow / capture_business_context
  3. Freight detail → project_id inferred / freight_detail_flow / enrich_release_batch
  4. Everything else → ignored
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sop_hub.sop.departure_text_parser import (
    _RE_CAR_COUNT,
    _strip_chinese_quotes,
    parse_departure_text,
)
from sop_hub.sop.inspection_source_policy import is_zhongtang_freight_only_group
from sop_hub.sop.monitoring_plan_matcher import MessageEvent

# ── 检验类项目(检装车通知单驱动)──────────────────────────────────────
# 文本只当触发器:提供 ship + 预期车数;车号顺序 / lot 归属的权威仍归
# 检装车通知单 JSON。只对这些项目的 yaml known_ships 做触发(#143)。
_INSPECTION_PROJECTS = ("chaoyang_steel", "zhongtang_special_steel")

# ── Known destination → project mapping ──────────────────────────────────
_DEST_PROJECT = {
    "四平": "jilin_jingang_jinzhou",
    "朝阳西": "chaoyang_steel",
    "汐子": "zhongtang_special_steel",
    "新台子": "jiusan",
}

# ── Departure text signals ──────────────────────────────────────────────
_DEPARTURE_DESTINATION_KEYWORDS = {"四平", "四平铁", "四平镍", "朝阳西", "朝阳铁", "汐子", "新台子", "新台"}
_DEPARTURE_LANE_PATTERNS = {"道", "煤一", "煤二", "煤三", "煤四", "煤五", "煤六", "煤七", "煤八", "煤九"}
_DEPARTURE_CAR_PATTERNS = {"节", "车"}

# ── Chaoyang business context signals ────────────────────────────────────
_CHAOYANG_SHIP_KEYWORDS = {"木森17", "合远9", "宝腾海"}
# 2026-06-04 贝拉从 chaoyang 移到 zhongtang:贝拉走汐子站,属于中唐特钢
# 业务范畴。chaoyang 历史里贝拉是误归类(从没真用过)。
_ZHONGTANG_SHIP_KEYWORDS = {"丰收散运", "鞍子河", "马兰探险", "贝拉", "环球信任", "宝丽"}
_CHAOYANG_DEST_KEYWORDS = {"朝阳西", "朝阳铁", "朝钢", "朝阳钢铁"}
_CHAOYANG_CARGO_KEYWORDS = {"铁矿", "印粉", "PB粉", "麦克粉", "纽曼粉"}

# ── Freight detail signals ───────────────────────────────────────────────
_FREIGHT_STRUCTURED_KEYWORDS = {
    "合同号", "入场合同号", "入厂合同号", "订单标识", "标识号", "订单号",
    "计划号", "货名", "货品", "品名", "详细货名", "矿种", "船名",
    "数量", "港口", "放货",
}

# ── Ignored message patterns ─────────────────────────────────────────────
_IGNORE_PATTERNS = {"ok", "好的", "收到", "谢谢", "嗯", "好", "1", "OK"}


@dataclass
class TextRouteResult:
    """Result of classifying a single text message."""

    message_id: str
    group_id: str
    is_sop_msg: bool = False
    sop_project_id: str = ""
    sop_flow: str = ""
    sop_node: str = ""
    summary: str = ""
    processing_status: str = "ignored"
    departure_candidate: dict[str, Any] = field(default_factory=dict)

    def as_inbox_update(self) -> dict[str, Any]:
        """Return the fields to UPDATE on message_inbox."""
        return {
            "is_sop_msg": 1 if self.is_sop_msg else 0,
            "sop_project_id": self.sop_project_id,
            "sop_flow": self.sop_flow,
            "sop_node": self.sop_node,
            "summary": self.summary,
            "processing_status": self.processing_status,
        }


def _is_departure_text(text: str) -> tuple[bool, str, str]:
    """Check if text looks like a departure notification.

    Returns (is_departure, destination_canonical, project_id).

    Requires BOTH:
      - A known destination keyword (四平/朝阳西/汐子)
      - A lane marker ("道" or "煤N") OR a car count ("节"/"车")

    This prevents false positives on messages like
    "朝阳西，15997 吨，宝腾海，PB粉" which mention the destination
    but are freight detail, not departure notifications.
    """
    from sop_hub.sop.departure_text_parser import parse_departure_text

    candidate = parse_departure_text(text)
    if candidate.status == "no_match":
        return False, "", ""

    dest = candidate.destination
    project = _DEST_PROJECT.get(dest, "")
    if not project:
        return False, "", ""

    # Require at least one structural signal beyond the destination keyword
    has_lane = bool(candidate.lane_or_track)
    has_cars = candidate.car_count >= 0
    if not has_lane and not has_cars:
        return False, "", ""

    return True, dest, project


def _is_chaoyang_context(text: str) -> bool:
    """Check if text is a Chaoyang business context message."""
    # Ship name signals
    if any(kw in text for kw in _CHAOYANG_SHIP_KEYWORDS):
        return True
    # Destination signals
    if any(kw in text for kw in _CHAOYANG_DEST_KEYWORDS):
        # Need at least one more signal (cargo, ship, or structured)
        if any(kw in text for kw in _CHAOYANG_CARGO_KEYWORDS):
            return True
        if any(kw in text for kw in _CHAOYANG_SHIP_KEYWORDS):
            return True
        # "放货 + 朝阳" or "朝阳西 + 量"
        if "放货" in text:
            return True
    return False


def _is_freight_detail(text: str) -> bool:
    """Check if text is a structured freight detail message."""
    hit_count = sum(1 for kw in _FREIGHT_STRUCTURED_KEYWORDS if kw in text)
    if hit_count >= 2:
        return True
    # Single keyword + strong structure (colon/colon-like patterns)
    if hit_count >= 1 and ("：" in text or ":" in text):
        return True
    return False


def _is_ignorable(text: str) -> bool:
    """Check if text is an ignorable short message."""
    stripped = text.strip().lower()
    return stripped in _IGNORE_PATTERNS or len(stripped) <= 1


def _build_summary(
    rule: str, text: str, destination: str = ""
) -> str:
    """Build a human-readable summary of the routing decision."""
    preview = text.strip()[:60].replace("\n", " ")
    parts = [f"[{rule}]", preview]
    if destination:
        parts.append(f"→{destination}")
    return " ".join(parts)


def _inspection_ship_project_map() -> dict[str, str]:
    """从 yaml project_meta.known_ships 建 ship → project 映射,只收检验类项目。

    yaml known_ships 是 per-project 权威清单(宝腾海→chaoyang,鞍子河/丰收散运/
    马兰探险/贝拉→zhongtang)。歧义船名(如 长航滨海,可能走四平镍/jilin)不在
    检验类 yaml 里,自然不被收 → 保守不触发,避免错误的工厂上传。
    """
    mapping: dict[str, str] = {}
    try:
        import yaml as _yaml
        from pathlib import Path as _Path
        ypdir = _Path(__file__).resolve().parents[3] / "config" / "project_sops"
        for f in ypdir.glob("*.yaml"):
            d = _yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            pm = d.get("project_meta") or {}
            proj = str(pm.get("project_id") or pm.get("id") or "").strip()
            # yaml 文件名兜底(project_meta 没显式 project_id 时)
            if proj not in _INSPECTION_PROJECTS:
                fname = f.stem
                guess = {
                    "chaoyang": "chaoyang_steel",
                    "zhongtang": "zhongtang_special_steel",
                }.get(fname, "")
                proj = guess or proj
            if proj not in _INSPECTION_PROJECTS:
                continue
            for s in pm.get("known_ships") or []:
                if s:
                    mapping[str(s).strip()] = proj
    except Exception:
        pass
    return mapping


def _project_default_dest(project_id: str) -> str:
    """读 yaml project_meta.destination_station 作为该项目默认到站。"""
    try:
        import yaml as _yaml
        from pathlib import Path as _Path

        ypdir = _Path(__file__).resolve().parents[3] / "config" / "project_sops"
        for yp in ypdir.glob("*.yaml"):
            d = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
            if d.get("project_id") != project_id:
                continue
            return str((d.get("project_meta") or {}).get("destination_station") or "").strip()
    except Exception:
        return ""
    return ""


def extract_inspection_text_triggers(text: str) -> list[dict[str, Any]]:
    """从(可能复合多船的)发运文本里抽出检验类触发器段。

    每段返回 {project_id, ship, destination, expected_count, segment}。文本只做
    触发 + 预期车数,**不**决定车号顺序或 lot 归属(那是检装车通知单的活)。

    保守原则:
      - 只认 yaml known_ships 里属于检验类项目的船名(歧义船不猜)。
      - 每个船名取其后到下一个船名之间的第一个 "N节/N车";前向找不到再
        在船名前 8 字符内回找一次(兼容 "15节宝腾海" 这种数量前置写法)。
      - 同船多次出现只保留第一段。
    """
    raw = text or ""
    if not raw.strip():
        return []
    norm = _strip_chinese_quotes(raw)
    ship_map = _inspection_ship_project_map()
    if not ship_map:
        return []

    # 找所有船名出现位置
    occ: list[tuple[int, str, str]] = []
    for ship, proj in ship_map.items():
        start = norm.find(ship)
        while start != -1:
            occ.append((start, ship, proj))
            start = norm.find(ship, start + 1)
    if not occ:
        return []
    occ.sort(key=lambda x: x[0])

    triggers: list[dict[str, Any]] = []
    seen_ships: set[str] = set()
    dest_cache: dict[str, str] = {}
    for i, (pos, ship, proj) in enumerate(occ):
        if ship in seen_ships:
            continue
        # 本船的"领地"= 上一个船名之后 ~ 下一个船名之前。车数优先在船名后
        # (常态 "鞍子河5节"),没有再到船名前回找(数量前置,如
        # "14道52节 朝阳西铁 中联发" —— 52节 在前、隔了"朝阳西铁")。
        seg_start = (occ[i - 1][0] + len(occ[i - 1][1])) if i > 0 else 0
        seg_end = occ[i + 1][0] if i + 1 < len(occ) else len(norm)
        forward = norm[pos + len(ship):seg_end]
        m = _RE_CAR_COUNT.search(forward)
        if not m:
            # 回找整个船名前领地(不再限 8 字符),取最靠近本船的(最后一个)车数
            backs = list(_RE_CAR_COUNT.finditer(norm[seg_start:pos]))
            m = backs[-1] if backs else None
        if not m:
            continue
        count = int(m.group(1))
        if count <= 0:
            continue
        if proj not in dest_cache:
            dest_cache[proj] = _project_default_dest(proj)
        triggers.append({
            "project_id": proj,
            "ship": ship,
            "destination": dest_cache[proj],
            "expected_count": count,
            "segment": norm[pos:seg_end].strip(),
        })
        seen_ships.add(ship)
    return triggers


def _infer_project_from_text(text: str) -> str:
    """Infer project_id from freight detail keywords."""
    if any(kw in text for kw in ("四平", "吉林金钢", "入场合同", "入场合同号", "红土镍矿")):
        return "jilin_jingang_jinzhou"
    if any(kw in text for kw in ("朝阳西", "朝阳", "合远9", "木森17", "宝腾海")):
        return "chaoyang_steel"
    # 中唐:显式"汐子/中唐"关键字 OR 已知船名 OR 合同号前缀 "ZLZT-"(中唐
    # 特钢合同号格式)。补充货运信息模板正文里没有"汐子/中唐",但有合同号
    # 或船名即可推断。
    if any(kw in text for kw in ("汐子", "中唐", "ZLZT-")):
        return "zhongtang_special_steel"
    if any(kw in text for kw in _ZHONGTANG_SHIP_KEYWORDS):
        return "zhongtang_special_steel"
    return ""


def classify_text_message(event: MessageEvent) -> TextRouteResult:
    """Classify a single text message and return a routing result.

    Priority:
      1. Departure text (四平/朝阳西/汐子 + lane/car count patterns)
      2. Chaoyang business context
      3. Freight detail (structured cargo/contract info)
      4. Ignorable short messages
      5. Default → ignored (non-SOP)
    """
    text = (event.text or "").strip()
    group_id = event.group_id or ""
    group_name = str(event.metadata.get("group_name") or group_id or "").strip()
    message_id = event.message_id

    if not text:
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=False,
            processing_status="ignored",
            summary="[empty] no text content",
        )

    # 反馈环防护(#工单-2026-06-27):数据单发群是系统输出/屏障群(只有用户+我,无客户),
    # 系统自己发到那的超时告警/合成通知/excel伴随消息若含"船名+N节",会被重抓 → 误触发
    # 新的 inspection_text_trigger → 自激死循环(实证:贝拉超时告警每轮重触发新触发器)。
    #   ① 数据单发群消息一律不当触发源;
    #   ② ⚠️/✅ 开头的系统告警/通知跨群兜底忽略(别的群里转发了也不触发)。
    if group_id == "数据单发群" or text.lstrip().startswith(("⚠️", "⚠", "✅")):
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=False,
            processing_status="ignored",
            summary="[barrier/system] 输出屏障群或系统告警,不触发 SOP",
        )

    # 2026-07-12 用户决策:中唐特钢发运群只跟踪出港计划/货运信息,不再承接
    # 发车文本、检装车文本或检装车图片的旁路判断,避免与铁晟业务工作群多来源串扰。
    if is_zhongtang_freight_only_group(group_id=group_id, group_name=group_name):
        if _is_freight_detail(text):
            project = _infer_project_from_text(text)
            summary = _build_summary(
                "freight_detail" if project else "freight_detail_unknown_project",
                text,
            )
            return TextRouteResult(
                message_id=message_id,
                group_id=group_id,
                is_sop_msg=True,
                sop_project_id=project or "",
                sop_flow="freight_detail_flow",
                sop_node="enrich_release_batch",
                summary=summary,
                processing_status="matched_sop",
            )
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=False,
            processing_status="ignored",
            summary="[group_policy] 中唐特钢发运群仅跟踪货运信息",
        )

    # Rule 0: 检验类文本触发器(#143)。复合多船文本或单船 chaoyang/zhongtang
    # 发运文本里,认得出 yaml known_ships 的船 + 车数 → 标 inspection_text_trigger。
    # 任务创建时按段扇出成 N 个触发器 task,各自与检装车通知单 rendezvous。
    # 优先级最高:必须先于 chaoyang_context / departure_text,否则会被它们吞掉。
    insp_triggers = extract_inspection_text_triggers(text)
    if insp_triggers:
        projects = {t["project_id"] for t in insp_triggers}
        ships = "/".join(
            f"{t['ship']}{t['expected_count']}节" for t in insp_triggers
        )
        summary = _build_summary("inspection_text_trigger", text, ships)
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=True,
            # 多项目混合时 project 留空,各段 task 自带 project_id
            sop_project_id=next(iter(projects)) if len(projects) == 1 else "",
            sop_flow="inspection_text_trigger_flow",
            sop_node="inspection_text_trigger",
            summary=summary,
            processing_status="matched_sop",
        )

    # Rule 1: Departure text
    is_dep, dest, project = _is_departure_text(text)
    if is_dep:
        candidate = parse_departure_text(event)
        summary = _build_summary("departure_text", text, dest)
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=True,
            sop_project_id=project,
            sop_flow="departure_flow",
            sop_node="detect_departure_message",
            summary=summary,
            processing_status="matched_sop",
            departure_candidate=candidate.to_dict(),
        )

    # Rule 2: Chaoyang business context
    if _is_chaoyang_context(text):
        summary = _build_summary("chaoyang_context", text)
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=True,
            sop_project_id="chaoyang_steel",
            sop_flow="dispatch_flow",
            sop_node="capture_business_context",
            summary=summary,
            processing_status="matched_sop",
        )

    # Rule 3: Freight detail
    if _is_freight_detail(text):
        project = _infer_project_from_text(text)
        if not project:
            # Mark as SOP but project unknown — needs later enrichment
            summary = _build_summary("freight_detail_unknown_project", text)
            return TextRouteResult(
                message_id=message_id,
                group_id=group_id,
                is_sop_msg=True,
                sop_project_id="",
                sop_flow="freight_detail_flow",
                sop_node="enrich_release_batch",
                summary=summary,
                processing_status="matched_sop",
            )
        summary = _build_summary("freight_detail", text)
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=True,
            sop_project_id=project,
            sop_flow="freight_detail_flow",
            sop_node="enrich_release_batch",
            summary=summary,
            processing_status="matched_sop",
        )

    # Rule 4: Ignorable
    if _is_ignorable(text):
        return TextRouteResult(
            message_id=message_id,
            group_id=group_id,
            is_sop_msg=False,
            processing_status="ignored",
            summary=f"[ignored_short] {text[:30]}",
        )

    # Rule 5: Default — not an SOP message
    return TextRouteResult(
        message_id=message_id,
        group_id=group_id,
        is_sop_msg=False,
        processing_status="ignored",
        summary=f"[no_match] {text[:50]}",
    )


# ── message_inbox write-back ────────────────────────────────────────────


def update_message_inbox_with_route(
    message_id: str,
    route: TextRouteResult,
    *,
    db_path: str | None = None,
) -> bool:
    """Write a text route result back to its own group-scoped inbox row."""
    import sqlite3
    from pathlib import Path

    if db_path:
        db = Path(db_path)
    else:
        from pathlib import Path as _Path
        db = _Path(__file__).resolve().parents[3] / "data" / "sop_agent.db"

    if not db.exists():
        return False

    try:
        conn = sqlite3.connect(str(db))
        updates = route.as_inbox_update()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        group_id = route.group_id or ""
        if not group_id:
            return False
        values = list(updates.values()) + [group_id, message_id]
        conn.execute(
            f"UPDATE message_inbox SET {set_clause} WHERE group_id = ? AND message_id = ?",
            values,
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False
