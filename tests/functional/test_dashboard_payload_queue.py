"""Functional test for R15 dashboard payload queue output.

Scope:
- chat_records/*.jsonl -> WxOpsSourceWatcher -> MessageEvent -> Matcher -> WorkflowTask
- WorkflowTask -> DashboardIntent -> DashboardPayloadQueue -> runtime/dashboard_intents/*.json
- no dashboard HTML writes, no database, no runtime daemon, no report sending, no 九三 logic
"""

from __future__ import annotations

import json
from pathlib import Path

from ops_hub.sop.dashboard_payload_queue import build_dashboard_payload_queue, write_dashboard_payload_queue
from ops_hub.sop.monitoring_plan_matcher import match_message_event
from ops_hub.sop.monitoring_plan_preview import build_real_sop_monitoring_plan_preview
from ops_hub.sop.source_watcher import WxOpsSourceWatcher
from ops_hub.sop.workflow_task import build_workflow_task_queue


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sops"

CASES = [
    {
        "group_name": "铁晟业务工作群",
        "group_wxid": "GROUP001",
        "source_file_name": "2026-04.jsonl",
        "local_id": 21,
        "message_text": "朝阳西 实装54节",
        "project_id": "chaoyang_steel",
    },
    {
        "group_name": "铁晟业务工作群",
        "group_wxid": "GROUP001",
        "source_file_name": "2026-04.jsonl",
        "local_id": 2000,
        "message_text": "煤六 四平铁矿箱 春日莲花 42节",
        "project_id": "jilin_jingang_jinzhou",
    },
    {
        "group_name": "中唐特钢发运群",
        "group_wxid": "GROUP003",
        "source_file_name": "2026-05.jsonl",
        "local_id": 2,
        "message_text": "汐子铁 实装32节",
        "project_id": "zhongtang_special_steel",
    },
]


def _write_chat_record(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def test_real_messages_produce_dashboard_payload_json_files(tmp_path):
    chat_records_root = tmp_path / "chat_records"
    output_dir = tmp_path / "runtime" / "dashboard_intents"
    plan = build_real_sop_monitoring_plan_preview(FIXTURE_DIR).plan
    watcher = WxOpsSourceWatcher(chat_records_root=chat_records_root)

    for case in CASES:
        source_file_path = chat_records_root / case["group_name"] / case["source_file_name"]
        _write_chat_record(
            source_file_path,
            {
                "local_id": case["local_id"],
                "group_name": case["group_name"],
                "group_wxid": case["group_wxid"],
                "msg-type": "text",
                "msg-content": case["message_text"],
                "time": "2026-05-25 10:00:00",
                "server_id": 900000 + case["local_id"],
                "message_key": f"msg-{case['local_id']}",
                "sender": "测试发送者",
                "sender_wxid": "wxid_test_sender",
            },
        )

        event = next(
            watcher.iter_message_events(
                group_name=case["group_name"],
                source_file_path=source_file_path,
                limit=1,
            )
        )
        match_result = match_message_event(event, plan)
        workflow_queue = build_workflow_task_queue(event, event.raw_asset_bundle, match_result)
        payload_queue = build_dashboard_payload_queue(
            workflow_queue,
            created_at="2026-05-25T10:00:00Z",
            output_dir=output_dir,
        )
        written_paths = write_dashboard_payload_queue(payload_queue)

        assert payload_queue.dashboard_payloads, case
        assert len(payload_queue.dashboard_payloads) == 1, case
        payload = payload_queue.dashboard_payloads[0]
        assert payload.message_id == f"wx_{case['local_id']}"
        assert payload.project_id == case["project_id"]
        assert payload.status == "ready"
        assert payload.dashboard_action == "upsert_payload"
        assert payload.payload_version == "r15"
        assert payload.source == str(source_file_path)
        assert payload.created_at == "2026-05-25T10:00:00Z"
        assert payload.watch_item.get("candidate_projects") == [case["project_id"]]
        assert payload.target_sop_node in payload.watch_item.get("target_sop_nodes", {}).get(case["project_id"], [])

        expected_path = output_dir / f"wx_{case['local_id']}.json"
        assert expected_path in written_paths
        assert expected_path.exists()

        written_payload = json.loads(expected_path.read_text(encoding="utf-8"))
        assert written_payload["message_id"] == f"wx_{case['local_id']}"
        assert written_payload["project_id"] == case["project_id"]
        assert written_payload["group_id"] == case["group_wxid"]
        assert written_payload["dashboard_action"] == "upsert_payload"
        assert written_payload["status"] == "ready"
        assert written_payload["payload_version"] == "r15"
        assert written_payload["source"] == str(source_file_path)
        assert written_payload["created_at"] == "2026-05-25T10:00:00Z"
        assert written_payload["watch_item"]["candidate_projects"] == [case["project_id"]]
        assert written_payload["target_sop_node"] in written_payload["watch_item"]["target_sop_nodes"][case["project_id"]]
