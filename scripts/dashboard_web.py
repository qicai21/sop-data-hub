#!/usr/bin/env python3
"""Authenticated LAN connectivity page for the future freight dashboard."""
from __future__ import annotations

import argparse
import base64
import hmac
import ipaddress
import json
import os
import secrets
import string
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CREDENTIALS = ROOT / "runtime" / "dashboard_web_credentials.json"

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>货运看板连通测试</title>
  <style>
    html, body {
      height: 100%;
      margin: 0;
      background: #f4f6f7;
      color: #171a1c;
      font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    body {
      display: grid;
      place-items: center;
    }
    main {
      font-size: 32px;
      font-weight: 600;
    }
  </style>
</head>
<body>
  <main>你好 sb</main>
</body>
</html>
"""


def initialize_credentials(
    path: Path,
    *,
    username: str = "freight",
    force: bool = False,
) -> dict[str, str]:
    if path.exists() and not force:
        raise FileExistsError(f"Credentials already exist: {path}")
    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(16))
    payload = {"username": username, "password": password}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return payload


def load_credentials(path: Path) -> tuple[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    if not username or not password:
        raise ValueError(f"Invalid credentials file: {path}")
    return username, password


def client_is_allowed(address: str, network: ipaddress._BaseNetwork) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_loopback or ip in network


def basic_auth_matches(
    authorization: str | None,
    username: str,
    password: str,
) -> bool:
    if not authorization or not authorization.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(
            authorization.removeprefix("Basic ").strip(),
            validate=True,
        ).decode("utf-8")
        supplied_user, supplied_password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(supplied_user, username) and hmac.compare_digest(
        supplied_password,
        password,
    )


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        username: str,
        password: str,
        allowed_network: ipaddress._BaseNetwork,
    ):
        super().__init__(server_address, DashboardRequestHandler)
        self.username = username
        self.password = password
        self.allowed_network = allowed_network


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        authenticate: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
        )
        if authenticate:
            self.send_header("WWW-Authenticate", 'Basic realm="sop-data-hub"')
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
        if not basic_auth_matches(
            self.headers.get("Authorization"),
            self.server.username,
            self.server.password,
        ):
            self._send(
                HTTPStatus.UNAUTHORIZED,
                b"Authentication required\n",
                "text/plain; charset=utf-8",
                authenticate=True,
            )
            return
        if self.path == "/":
            self._send(
                HTTPStatus.OK,
                PAGE.encode("utf-8"),
                "text/html; charset=utf-8",
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
    username: str,
    password: str,
    allowed_network: str,
) -> DashboardHTTPServer:
    network = ipaddress.ip_network(allowed_network, strict=False)
    return DashboardHTTPServer(
        (host, port),
        username=username,
        password=password,
        allowed_network=network,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allowed-network", default="10.1.2.0/24")
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--init-credentials", action="store_true")
    parser.add_argument("--username", default="freight")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.init_credentials:
        payload = initialize_credentials(
            args.credentials,
            username=args.username,
            force=args.force,
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    username, password = load_credentials(args.credentials)
    server = create_server(
        args.host,
        args.port,
        username=username,
        password=password,
        allowed_network=args.allowed_network,
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
