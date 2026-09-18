#!/usr/bin/env python3
"""Export the current freight dashboard to a short-lived PDF snapshot."""
from __future__ import annotations

import argparse
import html
import json
import os
import signal
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import dashboard_snapshot


DEFAULT_OUTPUT_DIR = Path.home() / "Library/Caches/Codex/sop-dashboard-snapshots"
SNAPSHOT_GLOB = "freight-dashboard-*.pdf"
CHROME_CANDIDATES = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
)


def cleanup_expired_snapshots(
    output_dir: Path,
    *,
    retention_seconds: int,
    now: float | None = None,
) -> list[Path]:
    """Remove only tool-owned PDF snapshots older than the retention window."""
    if retention_seconds < 0:
        raise ValueError("retention_seconds must be non-negative")
    if not output_dir.exists():
        return []

    cutoff = (time.time() if now is None else now) - retention_seconds
    removed: list[Path] = []
    for path in output_dir.glob(SNAPSHOT_GLOB):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed.append(path)
    return sorted(removed)


def find_chrome(explicit: str | None = None) -> Path:
    candidates = ([Path(explicit).expanduser()] if explicit else []) + list(
        CHROME_CANDIDATES
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for command in ("google-chrome", "chromium", "chromium-browser"):
        located = shutil.which(command)
        if located:
            return Path(located)
    raise FileNotFoundError("Google Chrome or Chromium was not found")


def build_chrome_command(
    chrome: Path,
    *,
    url: str,
    output_path: Path,
    profile_dir: Path,
) -> list[str]:
    return [
        str(chrome),
        "--headless=new",
        "--disable-gpu",
        "--disable-extensions",
        "--hide-scrollbars",
        "--no-pdf-header-footer",
        "--no-first-run",
        "--disable-default-apps",
        f"--user-data-dir={profile_dir}",
        f"--print-to-pdf={output_path}",
        url,
    ]


def validate_pdf(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"Chrome did not create the PDF: {path}")
    if path.stat().st_size < 1024:
        raise RuntimeError(f"generated PDF is unexpectedly small: {path}")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise RuntimeError(f"generated file is not a PDF: {path}")


def pdf_is_complete(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            return False
        handle.seek(max(0, path.stat().st_size - 2048))
        return b"%%EOF" in handle.read()


def stop_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


def _number(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def _cell(value: Any) -> str:
    return html.escape(str(value or "—"))


def render_snapshot_html(payload: dict[str, Any]) -> str:
    """Render the versioned local snapshot; never fetch the dashboard web server."""
    if payload.get("schema_version") != dashboard_snapshot.SCHEMA_VERSION:
        raise ValueError("incompatible dashboard snapshot schema")
    rows: list[str] = []
    for project in payload.get("projects") or []:
        project_name = _cell(project.get("display_name") or project.get("id"))
        batches = project.get("batches") or []
        if not batches:
            rows.append(f"<tr><td>{project_name}</td><td colspan=8>当前无活跃批次</td></tr>")
        for batch in batches:
            rows.append(
                "<tr>"
                f"<td>{project_name}</td>"
                f"<td>{_cell(batch.get('ship_name'))} · {_cell(batch.get('batch_sequence'))}</td>"
                f"<td>{_cell(batch.get('cargo_product_name') or batch.get('cargo_name'))}</td>"
                f"<td>{_number(batch.get('planned_tons'))}</td>"
                f"<td>{_number(batch.get('shipped_tons'))}</td>"
                f"<td>{_number(batch.get('remaining_tons'))}</td>"
                f"<td>{_number(batch.get('unit_count'))} {_cell(batch.get('unit'))}</td>"
                f"<td>{_cell(batch.get('dispatch_status'))}</td>"
                f"<td>{_cell(batch.get('plan_number'))}</td>"
                "</tr>"
            )
    pending = payload.get("pending_items") or {}
    cycle = payload.get("jiusan_cycle") or {}
    return f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>货运看板快照</title>
<style>
@page {{ size: A4 landscape; margin: 8mm; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, \"Microsoft YaHei\", sans-serif; color:#1f2933; font-size:9pt; }}
h1 {{ font-size:18pt; margin:0 0 3mm; }} .meta {{ color:#52616b; margin-bottom:4mm; }}
.summary {{ display:flex; gap:6mm; margin:3mm 0 5mm; }} .card {{ border:1px solid #cfd6dc; border-radius:4px; padding:3mm 4mm; min-width:38mm; }}
table {{ border-collapse:collapse; width:100%; }} th,td {{ border:1px solid #cfd6dc; padding:2mm; text-align:left; vertical-align:top; }} th {{ background:#edf2f5; }}
td:nth-child(n+4) {{ text-align:right; }}
</style></head><body>
<h1>货运看板快照</h1>
<div class=\"meta\">数据生成：{_cell(payload.get('generated_at'))} · 数据契约：{_cell(payload.get('schema_version'))}</div>
<div class=\"summary\"><div class=\"card\">挂起候选<br><strong>{_number(pending.get('candidate_total'))}</strong></div>
<div class=\"card\">待货运信息<br><strong>{_number(pending.get('pending_release_notices'))}</strong></div>
<div class=\"card\">九三循环<br><strong>{_cell(cycle.get('status'))}</strong></div></div>
<table><thead><tr><th>项目</th><th>船名 / lot</th><th>货物</th><th>计划吨</th><th>已发吨</th><th>剩余吨</th><th>车 / 箱</th><th>状态</th><th>计划号</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></body></html>"""


def write_snapshot_html(path: Path, snapshot_builder: Callable[[], dict[str, Any]]) -> None:
    path.write_text(render_snapshot_html(snapshot_builder()), encoding="utf-8")


def export_snapshot(
    *,
    output_dir: Path,
    chrome: Path,
    snapshot_builder: Callable[[], dict[str, Any]] = dashboard_snapshot.build_snapshot,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    output_path = output_dir / f"freight-dashboard-{timestamp}.pdf"
    with tempfile.TemporaryDirectory(prefix="sop-dashboard-pdf-") as temp:
        temp_path = Path(temp)
        html_path = temp_path / "freight-dashboard.html"
        write_snapshot_html(html_path, snapshot_builder)
        command = build_chrome_command(
            chrome,
            url=html_path.as_uri(),
            output_path=output_path,
            profile_dir=temp_path / "chrome-profile",
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                if pdf_is_complete(output_path):
                    break
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Chrome exited before creating the PDF (code {process.returncode})"
                    )
                time.sleep(0.2)
            else:
                raise RuntimeError("Chrome PDF export timed out after 30 seconds")
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
        finally:
            stop_process_group(process)
        if not pdf_is_complete(output_path):
            output_path.unlink(missing_ok=True)
            raise RuntimeError("Chrome did not create a complete PDF")
    validate_pdf(output_path)
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--retention-days", type=float, default=3.0)
    parser.add_argument("--chrome")
    parser.add_argument("--cleanup-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.retention_days < 0:
        raise SystemExit("--retention-days must be non-negative")
    output_dir = args.output_dir.expanduser().resolve()
    retention_seconds = int(args.retention_days * 86400)
    removed = cleanup_expired_snapshots(
        output_dir,
        retention_seconds=retention_seconds,
    )
    output_path = None
    if not args.cleanup_only:
        output_path = export_snapshot(
            output_dir=output_dir,
            chrome=find_chrome(args.chrome),
        )
    print(json.dumps({
        "output_path": str(output_path) if output_path else None,
        "removed": [str(path) for path in removed],
        "retention_hours": retention_seconds // 3600,
        "source": dashboard_snapshot.SCHEMA_VERSION,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
