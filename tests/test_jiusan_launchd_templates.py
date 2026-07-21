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
        "com.qicai21.sop-data-hub.status-sync": "scripts/sync_active_shipment_status.py",
    }
    for label, command in expected.items():
        path = REPO / "deploy" / "launchd" / f"{label}.plist"
        with path.open("rb") as fh:
            payload = plistlib.load(fh)
        assert payload["Label"] == label
        assert command in payload["ProgramArguments"]
        assert payload["RunAtLoad"] is True
        if label.endswith("status-sync"):
            assert payload["StartInterval"] == 7200
            assert "--jiusan-cycle-tracking" in payload["ProgramArguments"]
        elif "jiusan-" in label:
            assert payload["StartInterval"] == 1800
        else:
            assert payload["KeepAlive"] is True


def test_jiusan_install_and_health_scripts_include_cycle_tracking_host_job() -> None:
    label = "com.qicai21.sop-data-hub.status-sync"
    install = (REPO / "scripts" / "install_jiusan_launchd_jobs.sh").read_text(encoding="utf-8")
    health = (REPO / "scripts" / "check_jiusan_launchd_health.sh").read_text(encoding="utf-8")
    assert label in install
    assert label in health
