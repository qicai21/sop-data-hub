from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import cli_dashboard  # noqa: E402
import dashboard_web  # noqa: E402


@pytest.fixture
def web_server():
    server = dashboard_web.create_server(
        "127.0.0.1",
        0,
        allowed_network="127.0.0.0/8",
        renderer=lambda: "\x1b[1m看板\x1b[0m\n\x1b[32m正常\x1b[0m",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_root_opens_without_authentication(web_server):
    with urllib.request.urlopen(f"{web_server}/", timeout=2) as response:
        page = response.read().decode("utf-8")

    assert response.status == 200
    assert "货运看板" in page
    assert "setInterval(refresh, 5000)" in page
    assert "WWW-Authenticate" not in response.headers


def test_dashboard_api_returns_colored_snapshot(web_server):
    with urllib.request.urlopen(
        f"{web_server}/api/dashboard",
        timeout=2,
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))

    assert response.status == 200
    assert 'class="dashboard-heading"' in payload["html"]
    assert '<span class="bold">看板</span>' in payload["html"]
    assert '<span class="green">正常</span>' in payload["html"]
    assert payload["generated_at"]


def test_health_check_does_not_expose_dashboard(web_server):
    with urllib.request.urlopen(f"{web_server}/healthz", timeout=2) as response:
        payload = json.loads(response.read().decode("utf-8"))

    assert payload == {"status": "ok"}


def test_unknown_path_returns_not_found(web_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{web_server}/unknown", timeout=2)

    assert exc.value.code == 404


def test_client_network_filter_allows_loopback_and_configured_subnet():
    network = dashboard_web.ipaddress.ip_network("10.1.2.0/24")

    assert dashboard_web.client_is_allowed("127.0.0.1", network)
    assert dashboard_web.client_is_allowed("10.1.2.88", network)
    assert not dashboard_web.client_is_allowed("10.1.3.88", network)


def test_snapshot_cache_avoids_duplicate_rendering():
    calls = 0

    def render() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    cache = dashboard_web.SnapshotCache(render, ttl_seconds=60)

    assert cache.get()["html"] == '<div class="dashboard-heading">ok</div>'
    assert cache.get()["html"] == '<div class="dashboard-heading">ok</div>'
    assert calls == 1


def test_ansi_conversion_escapes_content():
    converted = dashboard_web.ansi_to_html(
        "\x1b[33m警告 <script>\x1b[0m"
    )

    assert '<span class="yellow">' in converted
    assert "&lt;script&gt;" in converted
    assert "<script>" not in converted


def test_dashboard_web_replaces_terminal_frames_with_css_panels():
    rendered = dashboard_web.dashboard_to_html(
        "看板\n"
        "┌─ 项目 A ───────────────┐\n"
        "│ \x1b[32m正常\x1b[0m  42 车          │\n"
        "└─────────────────────────┘"
    )

    assert 'class="panel"' in rendered
    assert 'class="panel-title">项目 A</h2>' in rendered
    assert '<span class="green">正常</span>' in rendered
    assert "┌" not in rendered
    assert "┐" not in rendered


def test_project_batch_panel_is_rendered_as_an_html_table():
    row = (
        cli_dashboard._pad_disp("马兰希望(金布巴粉)", 22)
        + cli_dashboard._pad_disp("lot06", 6)
        + cli_dashboard._pad_disp("2026-07-16", 10)
        + cli_dashboard._pad_disp("16208", 8, "right")
        + cli_dashboard._pad_disp("16817.9", 8, "right")
        + cli_dashboard._pad_disp("-609.9", 8, "right")
        + cli_dashboard._pad_disp("521", 11, "right")
        + "  "
        + cli_dashboard._pad_disp("loading", 15)
        + "  "
        + cli_dashboard._pad_disp("CGR20260612094028", 17)
    )
    rendered = dashboard_web.dashboard_to_html(
        "┌─ 吉林金钢 ───────────────────────────┐\n"
        "│ 船名(品名)              lot   下达日      计划t     已发t     剩 t    箱数(+当日)  状态             计划号 │\n"
        f"│ {row} │\n"
        "└──────────────────────────────────────┘"
    )

    assert 'class="dashboard-table"' in rendered
    assert "马兰希望(金布巴粉)" in rendered
    assert '<td class="status green">loading</td>' in rendered


def test_jiusan_cycle_flow_uses_its_own_stable_web_frame():
    rendered = dashboard_web.dashboard_to_html(
        "┌─ 大豆循环现状 260808 ─────────┐\n"
        "│ 港空 100 ──▶ 新台子 20 ─────┐ │\n"
        "│                              │\n"
        "│ 列状态  列号                │\n"
        "│ #1      50                  │\n"
        "└──────────────────────────────┘"
    )

    assert 'class="cycle-flow"' in rendered
    assert 'class="cycle-loop"' in rendered
    assert "港口空箱" in rendered
    assert "三三零专用线" in rendered
    assert "在途（返空）" in rendered
    assert "返回锦州港" not in rendered
    assert "新台子 20 ─────┐" not in rendered


def test_jiusan_cycle_places_departed_train_in_loaded_transit_node():
    contexts = dashboard_web._cycle_node_contexts([
        "#2 50 55车/110箱 途重 轨迹 2026-08-08 12:00:00",
    ])

    assert contexts["transit"] == ["#2 55车/110箱"]


def test_read_only_render_skips_weight_refresh(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("weight refresh must not run for web requests")

    monkeypatch.setattr(
        cli_dashboard,
        "refresh_active_shipped_weights",
        fail_if_called,
    )
    monkeypatch.setattr(cli_dashboard, "query_projects_with_batches", lambda: {})
    monkeypatch.setattr(cli_dashboard, "query_reserved_wagon_counts", lambda: {})
    monkeypatch.setattr(cli_dashboard, "panel_paths", lambda: [])
    monkeypatch.setattr(cli_dashboard, "panel_system", lambda: [])
    monkeypatch.setattr(cli_dashboard, "PROJECT_DISPLAY", {})

    rendered = cli_dashboard.render_once(
        refresh_weights=False,
        include_paths=False,
        terminal_controls=False,
    )

    assert "sop-data-hub 看板" in cli_dashboard._strip_ansi(rendered)
    assert "常用数据库 / 路径" not in rendered
    assert "Ctrl-C 退出" not in rendered
