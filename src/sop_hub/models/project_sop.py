import os
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from pathlib import Path

import yaml

@dataclass
class RoutingRule:
    message_type: str
    trigger_condition: str
    target_node: str
    save_db: bool = False
    send_report_to: Optional[str] = None
    report_targets: Optional[Dict[str, Any]] = None
    text_patterns: Optional[List[str]] = None

@dataclass
class ListeningTask:
    group_id: str
    group_name: str
    wxid: str
    listen_options: Dict[str, bool]
    pull_image: bool
    group_lookup_id: Optional[str] = None
    routing: List[RoutingRule] = field(default_factory=list)

@dataclass
class ProjectSOP:
    project_id: str
    project_name: str
    status: str
    listening_tasks: List[ListeningTask] = field(default_factory=list)

@dataclass
class MarkdownSOPFixture:
    file_path: Path
    title: str
    content: str


@dataclass
class NormalizedMonitoringEntry:
    source_type: str
    channel: str
    group_token: Optional[str] = None
    group_name: Optional[str] = None
    keywords: List[str] = field(default_factory=list)
    document_keywords: List[str] = field(default_factory=list)
    message_keywords: List[str] = field(default_factory=list)
    raw_source: str = ""


@dataclass
class NormalizedProjectSOP:
    project_id: str
    project_name: str
    source_path: Path
    source_title: str
    group_tokens: List[str] = field(default_factory=list)
    document_keywords: List[str] = field(default_factory=list)
    message_keywords: List[str] = field(default_factory=list)
    monitoring_entries: List[NormalizedMonitoringEntry] = field(default_factory=list)


GROUP_TOKEN_RE = re.compile(r"\[?(GROUP\d+)\]?")
PIPE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
KEYWORD_SPLIT_RE = re.compile(r"[、，,/;；\n]+")

PROJECT_ID_ALIASES = {
    "中唐特钢铁矿发运项目": "zhongtang_special_steel",
    "中唐特钢铁矿发运项目 SOP": "zhongtang_special_steel",
    "朝阳钢铁铁矿发运项目": "chaoyang_steel",
    "朝阳钢铁铁矿发运项目 SOP": "chaoyang_steel",
    "吉林金钢-锦州港铁矿发运项目": "jilin_jingang_jinzhou",
    "吉林金钢-锦州港铁矿发运项目 SOP": "jilin_jingang_jinzhou",
    "九三大豆铁路发运项目": "jiusan",
    "九三大豆铁路发运项目 SOP": "jiusan",
}

DOCUMENT_PHRASES = [
    "出港计划通知单",
    "检装车通知单",
    "手写箱号表",
    "放货单",
    "发运报表",
    "发车单",
    "合同文件",
    "补充协议文件",
]

MESSAGE_PHRASES = [
    "文字放货信息",
    "文字放货消息",
    "文字报告",
    "发运动态",
    "到站消息",
    "铁路消息",
]


@dataclass
class TrackingTask:
    project_id: str
    group_id: str
    group_name: str
    wxid: str
    listen_options: Dict[str, bool]
    pull_image: bool
    group_lookup_id: Optional[str]
    routing: List[RoutingRule]


def load_markdown_sop_fixture(file_path: str | Path) -> MarkdownSOPFixture:
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")
    title = ""
    for line in content.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return MarkdownSOPFixture(file_path=path, title=title, content=content)


def _clean_cell_text(value: str) -> str:
    return value.replace("`", "").strip()


def _split_keyword_values(value: str) -> List[str]:
    tokens = []
    for token in KEYWORD_SPLIT_RE.split(_clean_cell_text(value)):
        token = token.strip()
        if token:
            tokens.append(token)
    return tokens


def _classify_keyword(keyword: str) -> Optional[str]:
    normalized = _clean_cell_text(keyword)
    if not normalized:
        return None
    if any(phrase in normalized for phrase in DOCUMENT_PHRASES):
        return "document"
    if any(phrase in normalized for phrase in MESSAGE_PHRASES):
        return "message"
    if normalized == "文字":
        return "message"
    return None


def _extract_meta_rows(content: str) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    for line in content.splitlines():
        if not PIPE_ROW_RE.match(line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 2:
            continue
        key, value = cells[0], _clean_cell_text(cells[1])
        if not key or key in {"字段", "术语", "来源类型", "项目范围", "说明"}:
            continue
        if set(value) <= {"-", ":"}:
            continue
        meta.setdefault(key, value)
    return meta


def _extract_group_tokens(content: str) -> List[str]:
    seen = []
    for match in GROUP_TOKEN_RE.finditer(content):
        token = match.group(1)
        if token not in seen:
            seen.append(token)
    return seen


def _extract_section_lines(content: str, heading: str) -> List[str]:
    lines = content.splitlines()
    start_index = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start_index = index + 1
            break
    if start_index is None:
        return []

    section_lines = []
    for line in lines[start_index:]:
        if line.startswith("## "):
            break
        section_lines.append(line)
    return section_lines


def _extract_keywords_from_content(content: str, phrases: List[str]) -> List[str]:
    found = []
    for phrase in phrases:
        if phrase in content and phrase not in found:
            found.append(phrase)
    return found


class SopNormalizer:
    def normalize(self, file_path: str | Path) -> NormalizedProjectSOP:
        fixture = load_markdown_sop_fixture(file_path)
        meta = _extract_meta_rows(fixture.content)

        project_name = meta.get("项目名称") or fixture.title.removesuffix(" SOP") or fixture.title
        project_id = (
            meta.get("项目 key")
            or PROJECT_ID_ALIASES.get(project_name)
            or PROJECT_ID_ALIASES.get(fixture.title)
            or Path(fixture.file_path).stem
        )

        group_tokens = _extract_group_tokens(fixture.content)
        monitoring_entries = self._extract_monitoring_entries(fixture.content)

        document_keywords = []
        message_keywords = []
        for entry in monitoring_entries:
            for keyword in entry.document_keywords:
                if keyword not in document_keywords:
                    document_keywords.append(keyword)
            for keyword in entry.message_keywords:
                if keyword not in message_keywords:
                    message_keywords.append(keyword)

        for keyword in _extract_keywords_from_content(fixture.content, DOCUMENT_PHRASES):
            if keyword not in document_keywords:
                document_keywords.append(keyword)
        for keyword in _extract_keywords_from_content(fixture.content, MESSAGE_PHRASES):
            if keyword not in message_keywords:
                message_keywords.append(keyword)

        return NormalizedProjectSOP(
            project_id=project_id,
            project_name=project_name,
            source_path=fixture.file_path,
            source_title=fixture.title,
            group_tokens=group_tokens,
            document_keywords=document_keywords,
            message_keywords=message_keywords,
            monitoring_entries=monitoring_entries,
        )

    def normalize_dir(self, sops_dir: str | Path) -> List[NormalizedProjectSOP]:
        sops_path = Path(sops_dir)
        normalized = []
        if sops_path.exists() and sops_path.is_dir():
            for file in sorted(sops_path.glob("*.md")):
                normalized.append(self.normalize(file))
        return normalized

    def _extract_monitoring_entries(self, content: str) -> List[NormalizedMonitoringEntry]:
        entries: List[NormalizedMonitoringEntry] = []
        section_lines = _extract_section_lines(content, "## 4. 数据源与群") or _extract_section_lines(content, "## 4. 数据源与入口")
        for line in section_lines:
            if not PIPE_ROW_RE.match(line):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 4:
                continue

            if len(cells) >= 5:
                source_type, source_label, keyword_cell, purpose, is_primary = cells[:5]
            else:
                source_type = "来源"
                source_label, keyword_cell, purpose, is_primary = cells[:4]

            if source_label in {"群 / 系统", "类型"} or keyword_cell in {"消息类型", "当前用途"}:
                continue
            if source_type in {"来源类型", "---", "------", "来源"} and source_label in {"群 / 系统", "类型"}:
                continue

            raw_keywords = _split_keyword_values(keyword_cell)
            document_keywords = [kw for kw in raw_keywords if _classify_keyword(kw) == "document"]
            message_keywords = [kw for kw in raw_keywords if _classify_keyword(kw) == "message"]

            group_match = GROUP_TOKEN_RE.search(source_label)
            group_token = group_match.group(1) if group_match else None
            if group_match:
                group_name = _clean_cell_text(GROUP_TOKEN_RE.sub("", source_label)).strip("（）() ")
            else:
                group_name = _clean_cell_text(source_label)

            channel = "rail95306" if "95306" in source_label or "95306" in purpose or "95306" in source_type else "wechat"

            entries.append(
                NormalizedMonitoringEntry(
                    source_type=source_type,
                    channel=channel,
                    group_token=group_token,
                    group_name=group_name or None,
                    keywords=raw_keywords,
                    document_keywords=document_keywords,
                    message_keywords=message_keywords,
                    raw_source=source_label,
                )
            )
        return entries


def normalize_markdown_sop_fixture(file_path: str | Path) -> NormalizedProjectSOP:
    return SopNormalizer().normalize(file_path)


def load_normalized_project_sops(sops_dir: str | Path) -> List[NormalizedProjectSOP]:
    return SopNormalizer().normalize_dir(sops_dir)


def load_project_sop(file_path: str | Path) -> ProjectSOP:
    with open(file_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}

    tasks = []
    for t_data in data.get("listening_tasks", []):
        routing = []
        for r_data in t_data.get("routing", []):
            routing.append(RoutingRule(
                message_type=r_data.get("message_type", "*"),
                trigger_condition=r_data.get("trigger_condition", "always"),
                target_node=r_data.get("target_node", ""),
                save_db=r_data.get("save_db", False),
                send_report_to=r_data.get("send_report_to", None),
                report_targets=r_data.get("report_targets", None),
                text_patterns=r_data.get("text_patterns") or None,
            ))
        tasks.append(ListeningTask(
            group_id=t_data.get("group_id", ""),
            group_name=t_data.get("group_name", ""),
            wxid=t_data.get("wxid", ""),
            listen_options=t_data.get("listen_options", {}),
            pull_image=t_data.get("pull_image", False),
            group_lookup_id=t_data.get("group_lookup_id", None),
            routing=routing
        ))

    return ProjectSOP(
        project_id=data.get("project_id", ""),
        project_name=data.get("project_name", ""),
        status=data.get("status", "active"),
        listening_tasks=tasks
    )

def build_tracking_tasks_from_sops(sops: List[ProjectSOP]) -> List[TrackingTask]:
    tasks = []
    for sop in sops:
        if sop.status != "active":
            continue
        for lt in sop.listening_tasks:
            tasks.append(TrackingTask(
                project_id=sop.project_id,
                group_id=lt.group_id,
                group_name=lt.group_name,
                wxid=lt.wxid,
                listen_options=lt.listen_options,
                pull_image=lt.pull_image,
                group_lookup_id=lt.group_lookup_id,
                routing=lt.routing
            ))
    return tasks

def load_all_tracking_tasks(sops_dir: str | Path) -> List[TrackingTask]:
    sops_path = Path(sops_dir)
    sops = []
    if sops_path.exists() and sops_path.is_dir():
        for file in sorted(sops_path.glob("*.yaml")):
            sops.append(load_project_sop(file))
    return build_tracking_tasks_from_sops(sops)
