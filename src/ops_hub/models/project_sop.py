import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import yaml
from pathlib import Path

@dataclass
class RoutingRule:
    message_type: str
    trigger_condition: str
    target_node: str
    save_db: bool = False
    send_report_to: Optional[str] = None
    report_targets: Optional[Dict[str, Any]] = None

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
    content = path.read_text(encoding='utf-8')
    title = ''
    for line in content.splitlines():
        if line.startswith('# '):
            title = line[2:].strip()
            break
    return MarkdownSOPFixture(file_path=path, title=title, content=content)


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
                report_targets=r_data.get("report_targets", None)
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
