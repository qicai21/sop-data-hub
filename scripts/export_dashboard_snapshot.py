#!/usr/bin/env python3
"""Export the current freight dashboard to a short-lived PDF snapshot."""
from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


DEFAULT_URL = "http://127.0.0.1:8765/"
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


def dashboard_health_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid dashboard URL: {url}")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/healthz", "", ""))


def dashboard_snapshot_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid dashboard URL: {url}")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/snapshot", "", ""))


def require_healthy_dashboard(url: str, *, timeout: float = 5.0) -> None:
    request = urllib.request.Request(
        dashboard_health_url(url),
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"dashboard health check failed: {exc}") from exc
    if payload.get("status") != "ok":
        raise RuntimeError(f"dashboard health check returned: {payload!r}")


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
        dashboard_snapshot_url(url),
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


def export_snapshot(
    *,
    url: str,
    output_dir: Path,
    chrome: Path,
) -> Path:
    require_healthy_dashboard(url)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    output_path = output_dir / f"freight-dashboard-{timestamp}.pdf"
    with tempfile.TemporaryDirectory(prefix="sop-dashboard-chrome-") as profile:
        command = build_chrome_command(
            chrome,
            url=url,
            output_path=output_path,
            profile_dir=Path(profile),
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
    parser.add_argument("--url", default=DEFAULT_URL)
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
            url=args.url,
            output_dir=output_dir,
            chrome=find_chrome(args.chrome),
        )
    print(json.dumps({
        "output_path": str(output_path) if output_path else None,
        "removed": [str(path) for path in removed],
        "retention_hours": retention_seconds // 3600,
        "source_url": args.url,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
