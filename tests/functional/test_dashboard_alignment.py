"""Functional tests for local dashboard alignment payload generation.

Scope:
- real chat_records -> WxOpsSourceWatcher -> MessageEvent -> Matcher -> WorkflowTask -> DashboardIntent
- no dashboard HTML writes, no database, no runtime, no report sending
"""

from pathlib import Path

from sop_hub.sop.dashboard_intent import DashboardIntent, resolve_dashboard_intent
from sop_hub.sop.monitoring_plan_matcher import match_message_event
from sop_hub.sop.monitoring_plan_preview import build_real_sop_monitoring_plan_preview
from sop_hub.sop.source_watcher import WxOpsSourceWatcher
from sop_hub.sop.workflow_task import build_workflow_task_queue


REPO_ROOT = Path(__file__).resolve().parents[2]
WX_OPS_AGENT_ROOT = REPO_ROOT.parent / "wx-ops-agent"
CHAT_RECORDS_ROOT = WX_OPS_AGENT_ROOT / "data" / "chat_records"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "sops"


CASES = [
    {
        "group_name": "铁晟业务工作群",
        "source_file_path": CHAT_RECORDS_ROOT / "铁晟业务工作群" / "2026-04.jsonl",
        "text_fragments": ["朝阳西", "实装54节"],
        "project_id": "chaoyang_steel",
        "message_id": "wx_21",
    },
    {
        "group_name": "铁晟业务工作群",
        "source_file_path": CHAT_RECORDS_ROOT / "铁晟业务工作群" / "2026-04.jsonl",
        "text_fragments": ["煤六", "四平铁矿箱", "42节"],
        "project_id": "jilin_jingang_jinzhou",
        "message_id": "wx_2000",
    },
    {
        "group_name": "中唐特钢发运群",
        "source_file_path": CHAT_RECORDS_ROOT / "中唐特钢发运群" / "2026-05.jsonl",
        "text_fragments": ["汐子铁", "实装32节"],
        "project_id": "zhongtang_special_steel",
        "message_id": "wx_2",
    },
]


def _real_event(watcher: WxOpsSourceWatcher, *, group_name: str, source_file_path: Path, text_fragments: list[str]):
    for event in watcher.iter_message_events(group_name=group_name, source_file_path=source_file_path):
        if all(fragment in event.text for fragment in text_fragments):
            return event
    raise AssertionError(f"real message not found: {group_name} / {source_file_path} / {text_fragments}")


def test_real_messages_produce_dashboard_intents_for_ordinary_freight_projects():
    watcher = WxOpsSourceWatcher(chat_records_root=CHAT_RECORDS_ROOT)
    plan = build_real_sop_monitoring_plan_preview(FIXTURE_DIR).plan

    for case in CASES:
        event = _real_event(
            watcher,
            group_name=case["group_name"],
            source_file_path=case["source_file_path"],
            text_fragments=case["text_fragments"],
        )
        match_result = match_message_event(event, plan)
        queue = build_workflow_task_queue(event, event.raw_asset_bundle, match_result)

        assert queue.workflow_tasks, case
        task = queue.workflow_tasks[0]
        intent = resolve_dashboard_intent(task)

        assert isinstance(intent, DashboardIntent)
        assert intent.project_id == case["project_id"]
        assert intent.message_id == case["message_id"]
        assert intent.watch_item
        assert intent.target_sop_node
        assert intent.status == "ready"
        assert intent.dashboard_action == "upsert_payload"
        assert intent.project_id in intent.watch_item.get("candidate_projects", [])
        assert intent.target_sop_node in intent.watch_item.get("target_sop_nodes", {}).get(intent.project_id, [])
        assert intent.to_dict()["dashboard_action"] == "upsert_payload"
