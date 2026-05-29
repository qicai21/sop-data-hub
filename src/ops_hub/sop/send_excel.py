"""Send departure Excel to WeChat contact via wx-ui-bridge CLI — R55.

Uses wx-ui-bridge's `python3 -m src.main <target> <message> [file]` CLI:
  1. WeChat opens, searches for contact
  2. Inputs text message
  3. Attaches file (if provided)
  4. Sends

wx-ui-bridge repo: /Users/qicai21/projects/ai-tools/mcp/wx-ui-bridge

Usage:
  PYTHONPATH=src /Users/qicai21/projects/ai-tools/mcp/wx-ui-bridge/.venv/bin/python3 \\
    -m ops_hub.sop.send_excel \\
    --target "郭东北" \\
    --message "吉林金钢发运数据 蓝鳍 lot02" \\
    --file "output/excel/xxx.xlsx"
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

WX_BRIDGE_DIR = Path("/Users/qicai21/projects/ai-tools/mcp/wx-ui-bridge")
WX_BRIDGE_PYTHON = WX_BRIDGE_DIR / ".venv" / "bin" / "python3"


@dataclass
class SendResult:
    target: str
    message: str
    file_path: str
    success: bool
    output: str = ""
    error: str = ""


def send_to_wechat(
    target: str,
    message: str,
    file_path: str | Path | None = None,
    *,
    dry_run: bool = False,
) -> SendResult:
    """Send a message + optional file to a WeChat contact.

    Uses: python3 -m src.main <target> <message> [file_path]

    Args:
        target: WeChat contact display name (e.g. "郭东北").
        message: Text message to send.
        file_path: Optional file to attach.
        dry_run: Report only, no send.
    """
    fp = str(Path(file_path).resolve()) if file_path else ""

    if dry_run:
        attachment = f" +file={fp}" if fp else ""
        return SendResult(
            target=target,
            message=message,
            file_path=fp,
            success=True,
            output=(
                f"[dry_run] wx-ui-bridge: "
                f"src.main {target} \"{message}\"{attachment}"
            ),
        )

    if not WX_BRIDGE_PYTHON.exists():
        return SendResult(
            target=target,
            message=message,
            file_path=fp,
            success=False,
            error=f"wx-ui-bridge python not found: {WX_BRIDGE_PYTHON}",
        )

    cmd = [str(WX_BRIDGE_PYTHON), "-m", "src.main", target, message]
    if fp:
        cmd.append(fp)

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(WX_BRIDGE_DIR),
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = (proc.stdout + proc.stderr).strip()
        return SendResult(
            target=target,
            message=message,
            file_path=fp,
            success=proc.returncode == 0,
            output=output,
            error="" if proc.returncode == 0 else f"exit_code={proc.returncode}",
        )
    except subprocess.TimeoutExpired:
        return SendResult(
            target=target, message=message, file_path=fp,
            success=False, error="timeout (60s)",
        )
    except Exception as exc:
        return SendResult(
            target=target, message=message, file_path=fp,
            success=False, error=str(exc),
        )


# ── CLI ──────────────────────────────────────────────────────────────────

def _build_cli_parser():
    import argparse
    p = argparse.ArgumentParser(description="Send file to WeChat via wx-ui-bridge CLI")
    p.add_argument("--target", required=True, help="WeChat contact name")
    p.add_argument("--message", default="", help="Text message")
    p.add_argument("--file", default=None, help="File path to attach")
    p.add_argument("--dry-run", action="store_true")
    return p


def main():
    args = _build_cli_parser().parse_args()
    result = send_to_wechat(
        target=args.target,
        message=args.message or f"发运数据: {Path(args.file).name}" if args.file else "",
        file_path=args.file,
        dry_run=args.dry_run,
    )
    print(f"target: {result.target}")
    print(f"success: {result.success}")
    if result.error:
        print(f"error: {result.error}")
    if result.output:
        print(result.output)
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
