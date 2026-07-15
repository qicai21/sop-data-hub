"""Keep the Jiusan periodic launchd jobs valid after config edits."""

from __future__ import annotations

import plistlib
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_jiusan_launchd_templates_are_valid() -> None:
    expected = {
        "com.qicai21.sop-data-hub.live-service": "scripts/run_live_service.py",
        "com.qicai21.sop-data-hub.text-watch": "sop_hub.sop.text_watch_daemon",
        "com.qicai21.sop-data-hub.jiusan-sync": "scripts/sync_jiusan_all.py",
        "com.qicai21.sop-data-hub.jiusan-bulk-report-ingest": "scripts/jiusan_bulk_report_ingest.py",
    }
    for label, command in expected.items():
        path = REPO / "deploy" / "launchd" / f"{label}.plist"
        with path.open("rb") as fh:
            payload = plistlib.load(fh)
        assert payload["Label"] == label
        assert command in payload["ProgramArguments"]
        assert payload["RunAtLoad"] is True
        if "jiusan-" in label:
            assert payload["StartInterval"] == 1800
        else:
            assert payload["KeepAlive"] is True
