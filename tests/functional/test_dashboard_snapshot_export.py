from __future__ import annotations

import os
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import export_dashboard_snapshot as snapshot  # noqa: E402


def test_cleanup_only_removes_owned_expired_pdf_files(tmp_path):
    now = 1_800_000_000.0
    expired = tmp_path / "freight-dashboard-20260801-080000.pdf"
    recent = tmp_path / "freight-dashboard-20260808-080000.pdf"
    unrelated_pdf = tmp_path / "another-report.pdf"
    unrelated_file = tmp_path / "freight-dashboard-not-a-pdf.txt"
    for path in (expired, recent, unrelated_pdf, unrelated_file):
        path.write_bytes(b"content")
    os.utime(expired, (now - 72 * 3600 - 1, now - 72 * 3600 - 1))
    os.utime(recent, (now - 72 * 3600, now - 72 * 3600))
    os.utime(unrelated_pdf, (now - 90 * 3600, now - 90 * 3600))

    removed = snapshot.cleanup_expired_snapshots(
        tmp_path,
        retention_seconds=72 * 3600,
        now=now,
    )

    assert removed == [expired]
    assert not expired.exists()
    assert recent.exists()
    assert unrelated_pdf.exists()
    assert unrelated_file.exists()


def test_chrome_command_uses_local_static_page_and_suppresses_headers(tmp_path):
    command = snapshot.build_chrome_command(
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        url=(tmp_path / "snapshot.html").as_uri(),
        output_path=tmp_path / "snapshot.pdf",
        profile_dir=tmp_path / "profile",
    )

    assert "--no-pdf-header-footer" in command
    assert f"--print-to-pdf={tmp_path / 'snapshot.pdf'}" in command
    assert command[-1] == (tmp_path / "snapshot.html").as_uri()


def test_structured_snapshot_page_has_landscape_print_contract_and_escapes_values():
    page = snapshot.render_snapshot_html({
        "schema_version": "freight-dashboard.v1", "generated_at": "2026-09-18T18:00:00+08:00",
        "pending_items": {"candidate_total": 1, "pending_release_notices": 2},
        "jiusan_cycle": {"status": "ok"},
        "projects": [{"id": "p", "display_name": "项目<&>", "batches": [{
            "ship_name": "船<&>", "batch_sequence": "lot1", "cargo_name": "矿石",
            "planned_tons": 10, "shipped_tons": 4, "remaining_tons": 6,
            "unit_count": 1, "unit": "wagons", "dispatch_status": "loading", "plan_number": "P1",
        }]}],
    })
    assert "@page { size: A4 landscape; margin: 8mm; }" in page
    assert "项目&lt;&amp;&gt;" in page
    assert "船&lt;&amp;&gt;" in page
    assert "http://127.0.0.1:8765" not in page


def test_pdf_completion_requires_header_size_and_eof(tmp_path):
    path = tmp_path / "snapshot.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
    assert not snapshot.pdf_is_complete(path)

    path.write_bytes(b"%PDF-1.7\n" + b"x" * 2048 + b"\n%%EOF\n")
    assert snapshot.pdf_is_complete(path)
