#!/usr/bin/env python3
"""Read-only LAN web view for the sop-data-hub freight dashboard."""
from __future__ import annotations

import argparse
import html
import ipaddress
import json
import re
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import cli_dashboard


ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>sop-data-hub 货运看板</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #111315;
      --line: #343a40;
      --text: #edf0f2;
      --muted: #99a1a8;
      --green: #57c785;
      --yellow: #e9bd58;
      --red: #ed6a5e;
      --cyan: #65b9d8;
    }
    * { box-sizing: border-box; }
    html, body {
      min-height: 100%;
      margin: 0;
      background: var(--bg);
      color: var(--text);
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
        "Microsoft YaHei", "PingFang SC", sans-serif;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 2;
      min-height: 48px;
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 8px 16px;
      border-bottom: 1px solid var(--line);
      background: rgba(17, 19, 21, 0.96);
    }
    header strong { font-size: 15px; }
    #status {
      margin-left: auto;
      color: var(--muted);
      font-size: 13px;
    }
    main { padding: 14px 16px 28px; }
    #dashboard {
      display: grid;
      gap: 14px;
      min-width: 0;
    }
    .dashboard-heading {
      color: var(--muted);
      font: 600 13px/1.35 "SFMono-Regular", Consolas, "Liberation Mono",
        "Microsoft YaHei UI", monospace;
    }
    .panel {
      min-width: 0;
      border: 1px solid #71808b;
      border-radius: 0;
      overflow: hidden;
      background: #14181b;
    }
    .panel-title {
      margin: 0;
      padding: 8px 12px;
      border-bottom: 1px solid #59656f;
      background: #1b2025;
      font: 700 14px/1.35 "SFMono-Regular", Consolas, "Liberation Mono",
        "Microsoft YaHei UI", monospace;
    }
    .panel-body {
      margin: 0;
      padding: 9px 12px 11px;
      overflow-x: auto;
      margin: 0;
      font: 14px/1.45 "SFMono-Regular", Consolas, "Liberation Mono",
        "Microsoft YaHei UI", monospace;
      letter-spacing: 0;
      white-space: pre;
      tab-size: 2;
    }
    .cycle-flow {
      margin: 10px 12px 0;
      border: 1px solid #59656f;
      background: #111518;
      overflow-x: auto;
    }
    .cycle-loop {
      display: grid;
      grid-template-columns:
        minmax(10rem, 1fr) 2.8rem minmax(10rem, 1fr) 2.8rem
        minmax(10rem, 1fr);
      grid-template-rows: auto 2.6rem auto;
      min-width: 40rem;
      padding: 12px;
      align-items: stretch;
    }
    .cycle-node {
      min-height: 4.4rem;
      padding: 8px 10px;
      border: 1px solid #59656f;
      background: #181e23;
    }
    .cycle-node strong { display: block; font-size: 13px; }
    .cycle-node span {
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font: 13px/1.3 "SFMono-Regular", Consolas, "Liberation Mono",
        "Microsoft YaHei UI", monospace;
    }
    .cycle-node.empty { grid-column: 1; grid-row: 1; }
    .cycle-node.loaded { grid-column: 3; grid-row: 1; }
    .cycle-node.transit { grid-column: 5; grid-row: 1; }
    .cycle-node.station { grid-column: 5; grid-row: 3; }
    .cycle-node.line330 { grid-column: 3; grid-row: 3; }
    .cycle-node.returning { grid-column: 1; grid-row: 3; }
    .cycle-node small {
      display: block;
      margin-top: 5px;
      color: var(--cyan);
      font: 12px/1.25 "SFMono-Regular", Consolas, "Liberation Mono",
        "Microsoft YaHei UI", monospace;
    }
    .cycle-link { position: relative; min-width: 0; }
    .cycle-link::before {
      content: "";
      position: absolute;
      background: #9ca8b2;
    }
    .cycle-link::after {
      position: absolute;
      color: #9ca8b2;
      font-size: 16px;
    }
    .cycle-link.east::before,
    .cycle-link.west::before {
      top: 50%; left: 0; width: 100%; height: 1px;
    }
    .cycle-link.east::after { content: "▶"; top: calc(50% - 12px); right: -3px; }
    .cycle-link.west::after { content: "◀"; top: calc(50% - 12px); left: -3px; }
    .cycle-link.drop::before,
    .cycle-link.up::before {
      left: 50%; top: 0; width: 1px; height: 100%;
    }
    .cycle-link.drop::after { content: "▼"; bottom: -8px; left: calc(50% - 6px); }
    .cycle-link.up::after { content: "▲"; top: -8px; left: calc(50% - 6px); }
    .cycle-link.top-1 { grid-column: 2; grid-row: 1; }
    .cycle-link.top-2 { grid-column: 4; grid-row: 1; }
    .cycle-link.down-right { grid-column: 5; grid-row: 2; }
    .cycle-link.bottom-1 { grid-column: 4; grid-row: 3; }
    .cycle-link.bottom-2 { grid-column: 2; grid-row: 3; }
    .cycle-link.up-left { grid-column: 1; grid-row: 2; }
    .bold { font-weight: 700; }
    .dim { color: var(--muted); }
    .red { color: var(--red); }
    .green { color: var(--green); }
    .yellow { color: var(--yellow); }
    .cyan { color: var(--cyan); }
    @media (max-width: 720px) {
      header { padding: 8px 10px; }
      main { padding: 10px; }
      .panel-title { padding: 8px 10px; font-size: 12px; }
      .panel-body { padding: 9px 10px 11px; font-size: 12px; }
      .cycle-flow { margin: 10px 10px 0; }
      .cycle-loop { padding: 10px; }
      .cycle-node { min-height: 4.2rem; padding: 7px 8px; }
      .cycle-node strong { font-size: 12px; }
      .cycle-node span { font-size: 11px; }
      .cycle-node small { font-size: 10px; }
    }
  </style>
</head>
<body>
  <header>
    <strong>货运看板</strong>
    <span id="status">正在连接...</span>
  </header>
  <main><pre id="dashboard" aria-live="polite"></pre></main>
  <script>
    const target = document.getElementById("dashboard");
    const status = document.getElementById("status");
    async function refresh() {
      try {
        const response = await fetch("/api/dashboard", {cache: "no-store"});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        target.innerHTML = data.html;
        status.textContent = `已更新 ${data.generated_at}`;
      } catch (error) {
        status.textContent = `更新失败: ${error.message}`;
      }
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


def client_is_allowed(address: str, network: ipaddress._BaseNetwork) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_loopback or ip in network


def ansi_to_html(text: str) -> str:
    """Convert the dashboard's ANSI color subset into escaped HTML."""
    classes: set[str] = set()
    parts: list[str] = []
    cursor = 0

    def emit(chunk: str) -> None:
        if not chunk:
            return
        escaped = html.escape(chunk)
        if classes:
            names = " ".join(sorted(classes))
            parts.append(f'<span class="{names}">{escaped}</span>')
        else:
            parts.append(escaped)

    for match in ANSI_RE.finditer(text):
        emit(text[cursor:match.start()])
        codes = [int(item or "0") for item in match.group(1).split(";")]
        for code in codes:
            if code == 0:
                classes.clear()
            elif code == 1:
                classes.add("bold")
            elif code == 2:
                classes.add("dim")
            elif code == 31:
                classes.add("red")
            elif code == 32:
                classes.add("green")
            elif code == 33:
                classes.add("yellow")
            elif code == 36:
                classes.add("cyan")
        cursor = match.end()
    emit(text[cursor:])
    return "".join(parts)


def _frame_title(line: str) -> str:
    """Extract a CLI panel title without its terminal-drawn frame."""
    end = line.rfind("┐")
    title = line[2:end] if end >= 2 else line[2:]
    return title.rstrip("─ ").strip()


def _frame_body_line(line: str) -> str:
    """Strip a CLI panel's left/right frame while retaining fixed-width rows."""
    body = line[1:] if line.startswith("│") else line
    end = body.rfind("│")
    if end >= 0:
        body = body[:end]
    return body.strip()


def _cycle_metric(flow: list[str], label: str, fallback: str = "—") -> str:
    """Extract a visible node metric from the CLI cycle diagram."""
    for line in flow:
        match = re.search(rf"{re.escape(label)}\s*([-]?\d+)", ANSI_RE.sub("", line))
        if match:
            return match.group(1)
    return fallback


def _cycle_node_contexts(details: list[str]) -> dict[str, list[str]]:
    """Map the CLI cycle detail rows to the six visible transport nodes."""
    contexts: dict[str, list[str]] = {
        "empty": [], "loaded": [], "transit": [], "station": [],
        "line330": [], "returning": [],
    }
    for line in details:
        plain = ANSI_RE.sub("", line).strip()
        match = re.match(r"(#\d+)\s+(?:\d+|-)\s+(\d+车/\d+箱|-)", plain)
        if not match:
            continue
        cycle = match.group(1)
        trip = (match.group(2) or "").strip()
        if trip == "-":
            continue
        summary = f"{cycle} {trip}"
        if "三三零" in plain or "330" in plain:
            key = "line330"
        elif "返空" in plain:
            key = "returning"
        elif "新台子" in plain:
            key = "station"
        elif "途重" in plain or "在途" in plain:
            key = "transit"
        elif "制票" in plain or "待发" in plain or "港重" in plain:
            key = "loaded"
        elif "返港" in plain or "港内" in plain:
            key = "empty"
        else:
            continue
        if summary not in contexts[key]:
            contexts[key].append(summary)
    return contexts


def _cycle_node_html(
    css_class: str,
    label: str,
    metric: str,
    contexts: list[str],
) -> str:
    detail = " · ".join(contexts) if contexts else "无循环列"
    return (
        f'<div class="cycle-node {css_class}">'
        f"<strong>{html.escape(label)}</strong>"
        f"<span>{html.escape(metric)} 箱</span>"
        f"<small>{html.escape(detail)}</small>"
        "</div>"
    )


def _jiusan_panel_body_html(body: list[str]) -> str:
    """Give the cycle diagram its own stable browser frame.

    The first block before the `列状态` header is the seven-line terminal flow
    diagram. Its right edge must not depend on browser glyph widths.
    """
    detail_index = next(
        (
            index for index, line in enumerate(body)
            if "列状态" in ANSI_RE.sub("", line)
        ),
        len(body),
    )
    flow = body[:detail_index]
    while flow and not ANSI_RE.sub("", flow[0]).strip():
        flow.pop(0)
    while flow and not ANSI_RE.sub("", flow[-1]).strip():
        flow.pop()
    details = body[detail_index:]
    port_empty = _cycle_metric(flow, "港空")
    port_loaded = _cycle_metric(flow, "港重")
    transit_loaded = _cycle_metric(flow, "途重")
    xtz = _cycle_metric(flow, "新台子")
    line330 = _cycle_metric(flow, "三三0总")
    transit_empty = _cycle_metric(flow, "返空")
    contexts = _cycle_node_contexts(details)
    flow_html = "".join([
        '<div class="cycle-loop">',
        _cycle_node_html("empty", "港口空箱", port_empty, contexts["empty"]),
        '<div class="cycle-link east top-1"></div>',
        _cycle_node_html("loaded", "港口重箱", port_loaded, contexts["loaded"]),
        '<div class="cycle-link east top-2"></div>',
        _cycle_node_html("transit", "在途（重）", transit_loaded, contexts["transit"]),
        _cycle_node_html("station", "新台子站", xtz, contexts["station"]),
        '<div class="cycle-link drop down-right"></div>',
        _cycle_node_html("line330", "三三零专用线", line330, contexts["line330"]),
        '<div class="cycle-link west bottom-1"></div>',
        _cycle_node_html("returning", "在途（返空）", transit_empty, contexts["returning"]),
        '<div class="cycle-link west bottom-2"></div>',
        '<div class="cycle-link up up-left"></div>',
        "</div>",
    ])
    details_html = "\n".join(ansi_to_html(line) for line in details).rstrip()
    return (
        f'<div class="cycle-flow">{flow_html}</div>'
        f'<pre class="panel-body">{details_html}</pre>'
    )


def dashboard_to_html(text: str) -> str:
    """Render terminal panels as browser-native sections.

    The CLI uses East Asian terminal cell widths to draw box characters. Browser
    fonts do not share that metric, so its right edges drift. Keep the CLI data
    and ANSI colors, but let CSS draw the Web frame.
    """
    lines = text.splitlines()
    parts: list[str] = []
    index = 0
    while index < len(lines):
        visible = ANSI_RE.sub("", lines[index])
        if visible.startswith("┌─"):
            title = _frame_title(lines[index])
            body: list[str] = []
            index += 1
            while index < len(lines):
                current = lines[index]
                current_visible = ANSI_RE.sub("", current)
                if current_visible.startswith("└"):
                    break
                body.append(_frame_body_line(current))
                index += 1
            is_jiusan_cycle = ANSI_RE.sub("", title).startswith("大豆循环现状")
            body_html = (
                _jiusan_panel_body_html(body)
                if is_jiusan_cycle
                else "\n".join(ansi_to_html(line) for line in body).rstrip()
            )
            body_markup = (
                body_html
                if is_jiusan_cycle
                else f'<pre class="panel-body">{body_html}</pre>'
            )
            parts.append(
                '<section class="panel">'
                f'<h2 class="panel-title">{ansi_to_html(title)}</h2>'
                f"{body_markup}"
                "</section>"
            )
        elif visible.strip():
            parts.append(
                f'<div class="dashboard-heading">{ansi_to_html(lines[index])}</div>'
            )
        index += 1
    return "".join(parts)


class SnapshotCache:
    def __init__(self, renderer: Callable[[], str], ttl_seconds: float = 4.0):
        self.renderer = renderer
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._rendered_at = 0.0
        self._payload: dict[str, str] | None = None

    def get(self) -> dict[str, str]:
        now = time.monotonic()
        with self._lock:
            if self._payload is None or now - self._rendered_at >= self.ttl_seconds:
                self._payload = {
                    "html": dashboard_to_html(self.renderer()),
                    "generated_at": datetime.now().astimezone().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
                self._rendered_at = now
            return dict(self._payload)


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        allowed_network: ipaddress._BaseNetwork,
        renderer: Callable[[], str],
    ):
        super().__init__(server_address, DashboardRequestHandler)
        self.allowed_network = allowed_network
        self.snapshot_cache = SnapshotCache(renderer)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not client_is_allowed(
            self.client_address[0],
            self.server.allowed_network,
        ):
            self._send(HTTPStatus.FORBIDDEN, b"Forbidden\n", "text/plain")
            return
        if self.path == "/healthz":
            self._send(HTTPStatus.OK, b'{"status":"ok"}\n', "application/json")
            return
        if self.path == "/":
            self._send(
                HTTPStatus.OK,
                PAGE.encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if self.path == "/api/dashboard":
            body = json.dumps(
                self.server.snapshot_cache.get(),
                ensure_ascii=False,
            ).encode("utf-8")
            self._send(
                HTTPStatus.OK,
                body,
                "application/json; charset=utf-8",
            )
            return
        self._send(HTTPStatus.NOT_FOUND, b"Not found\n", "text/plain")

    def log_message(self, fmt: str, *args: object) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        print(f"{timestamp} {self.client_address[0]} {fmt % args}", flush=True)


def create_server(
    host: str,
    port: int,
    *,
    allowed_network: str,
    renderer: Callable[[], str],
) -> DashboardHTTPServer:
    network = ipaddress.ip_network(allowed_network, strict=False)
    return DashboardHTTPServer(
        (host, port),
        allowed_network=network,
        renderer=renderer,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allowed-network", default="10.1.2.0/24")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = create_server(
        args.host,
        args.port,
        allowed_network=args.allowed_network,
        renderer=lambda: cli_dashboard.render_once(
            refresh_weights=False,
            include_paths=False,
            terminal_controls=False,
        ),
    )
    print(
        f"dashboard-web listening on {args.host}:{args.port}; "
        f"allowed={args.allowed_network}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
