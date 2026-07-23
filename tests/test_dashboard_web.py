from __future__ import annotations

import base64
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import dashboard_web  # noqa: E402


@pytest.fixture
def web_server():
    server = dashboard_web.create_server(
        "127.0.0.1",
        0,
        username="viewer",
        password="secret123",
        allowed_network="127.0.0.0/8",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _authorized_request(url: str) -> urllib.request.Request:
    token = base64.b64encode(b"viewer:secret123").decode("ascii")
    return urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})


def test_root_requires_authentication(web_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{web_server}/", timeout=2)

    assert exc.value.code == 401
    assert exc.value.headers["WWW-Authenticate"] == 'Basic realm="sop-data-hub"'


def test_authenticated_connectivity_page(web_server):
    with urllib.request.urlopen(
        _authorized_request(f"{web_server}/"),
        timeout=2,
    ) as response:
        page = response.read().decode("utf-8")

    assert "你好 sb" in page
    assert "/api/dashboard" not in page


def test_dashboard_api_is_not_exposed_during_connectivity_phase(web_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(
            _authorized_request(f"{web_server}/api/dashboard"),
            timeout=2,
        )

    assert exc.value.code == 404


def test_health_check_does_not_expose_dashboard(web_server):
    with urllib.request.urlopen(f"{web_server}/healthz", timeout=2) as response:
        payload = json.loads(response.read().decode("utf-8"))

    assert payload == {"status": "ok"}


def test_client_network_filter_allows_loopback_and_configured_subnet():
    network = dashboard_web.ipaddress.ip_network("10.1.2.0/24")

    assert dashboard_web.client_is_allowed("127.0.0.1", network)
    assert dashboard_web.client_is_allowed("10.1.2.88", network)
    assert not dashboard_web.client_is_allowed("10.1.3.88", network)


def test_invalid_basic_authorization_is_rejected():
    assert not dashboard_web.basic_auth_matches(None, "u", "p")
    assert not dashboard_web.basic_auth_matches("Basic !!!", "u", "p")
    assert not dashboard_web.basic_auth_matches(
        "Basic " + base64.b64encode(b"u:wrong").decode("ascii"),
        "u",
        "p",
    )


def test_initialize_credentials_is_git_ignored_runtime_secret(tmp_path):
    path = tmp_path / "credentials.json"

    credentials = dashboard_web.initialize_credentials(path, username="freight")

    assert credentials["username"] == "freight"
    assert len(credentials["password"]) == 16
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert dashboard_web.load_credentials(path) == (
        credentials["username"],
        credentials["password"],
    )
