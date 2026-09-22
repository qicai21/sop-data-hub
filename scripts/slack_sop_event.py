#!/usr/bin/env python3
"""JSON CLI for the bounded Slack single-event adapter."""
import argparse
import json
import sys
from pathlib import Path

from sop_hub.sop.slack_single_event_adapter import execute_event, preflight_event


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("preflight", "execute"))
    p.add_argument("--candidate-id")
    p.add_argument("--source-message-id")
    p.add_argument("--task-id", type=int)
    p.add_argument("--token-file")
    args = p.parse_args()
    if args.mode == "preflight":
        result = preflight_event(candidate_id=args.candidate_id, source_message_id=args.source_message_id, task_id=args.task_id)
    else:
        if not args.token_file:
            p.error("execute requires --token-file")
        raw = sys.stdin.read() if args.token_file == "-" else Path(args.token_file).read_text(encoding="utf-8")
        result = execute_event(json.loads(raw))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") not in ("failed", "rejected") else 2


if __name__ == "__main__":
    sys.exit(main())
