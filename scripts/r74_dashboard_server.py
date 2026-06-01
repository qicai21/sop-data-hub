#!/usr/bin/env python3
"""
R74: SOP Dashboard Server — 常驻运行观察面板
提供 API 端点 + 自动刷新 HTML 页面
"""
import json, os, sqlite3, sys, time, threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# R77: self-bootstrap sys.path so `python scripts/r74_dashboard_server.py`
# works without PYTHONPATH=src (Homebrew Python is PEP 668 externally
# managed; pip install -e . isn't a friendly workflow here).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

DB_PATH = "/Users/qicai21/projects/repos/sop-data-hub/data/sop_agent.db"
REPO = "/Users/qicai21/projects/repos/sop-data-hub"
RUNTIME = f"{REPO}/runtime"
DASHBOARD_DIR = f"{REPO}/dashboard"
RAIL95306_DB = "/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3"
PORT = 8787
HOST = "0.0.0.0"

STATIC_WHITELIST = {
    "/dispatch_board.html": ("dispatch_board.html", "text/html; charset=utf-8"),
    "/dispatch_board_data.json": ("dispatch_board_data.json", "application/json; charset=utf-8"),
    "/jiusan_dashboard.html": ("jiusan_dashboard.html", "text/html; charset=utf-8"),
    "/jiusan_dashboard_data.json": ("jiusan_dashboard_data.json", "application/json; charset=utf-8"),
}

def query_db(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    return rows

def query_one(sql, params=()):
    rows = query_db(sql, params)
    return rows[0] if rows else {}

def api_status():
    """live_service 状态"""
    state_file = f"{RUNTIME}/live_service_state.json"
    state = {}
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)

    cursor_file = f"{RUNTIME}/cursors/live_service_cursor.json"
    cursor_info = {}
    if os.path.exists(cursor_file):
        with open(cursor_file) as f:
            c = json.load(f)
            cursor_info = {
                "bootstrap": c.get("bootstrap_cursor"),
                "sources_count": len(c.get("sources", {})),
                "updated_at": c.get("updated_at"),
            }

    # PID
    pid_file = f"{RUNTIME}/live_service.pid"
    pid = None
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            pid = f.read().strip()

    return {
        "alive": state.get("alive", False),
        "pid": pid,
        "last_processed_message_id": state.get("last_processed_message_id"),
        "last_processed_time": state.get("last_processed_time"),
        "cursor": cursor_info,
        "timestamp": datetime.now().isoformat(),
    }

def api_message_inbox(limit=20):
    """最新 message_inbox"""
    return query_db("""
        SELECT id, message_id, group_name, msg_type, media_status, processing_status,
               document_type, sop_project_id, sop_flow, sop_node,
               text_content, created_at, updated_at
        FROM message_inbox ORDER BY id DESC LIMIT ?
    """, (limit,))

def api_workflow_tasks():
    """workflow_task 统计"""
    counts = query_db("""
        SELECT task_status, count(*) as cnt
        FROM workflow_task_db GROUP BY task_status
    """)
    result = {"pending": 0, "running": 0, "succeeded": 0, "failed": 0, "skipped": 0}
    for r in counts:
        s = r["task_status"]
        if s in result:
            result[s] = r["cnt"]
    result["total"] = sum(result.values())
    return result

def api_external_actions():
    """external_action_log 统计"""
    counts = query_db("""
        SELECT action_status, count(*) as cnt
        FROM external_action_log GROUP BY action_status
    """)
    result = {"planned": 0, "executed": 0, "failed": 0, "skipped_dry_run": 0}
    for r in counts:
        s = r["action_status"]
        if s in result:
            result[s] = r["cnt"]
    result["total"] = sum(result.values())
    return result

def api_waiting_media():
    """waiting_media 计数"""
    r = query_one("SELECT count(*) as cnt FROM message_inbox WHERE media_status='waiting_media'")
    return r.get("cnt", 0)

def api_last_error():
    """最近错误"""
    r = query_one("SELECT error_message, updated_at FROM message_inbox WHERE error_message IS NOT NULL AND error_message != '' ORDER BY updated_at DESC LIMIT 1")
    return r

def save_health_snapshot():
    """生成 health snapshot"""
    snap_dir = f"{RUNTIME}/health_snapshots"
    os.makedirs(snap_dir, exist_ok=True)
    snap = {
        "timestamp": datetime.now().isoformat(),
        "live_service": api_status(),
        "workflow_tasks": api_workflow_tasks(),
        "external_actions": api_external_actions(),
        "waiting_media": api_waiting_media(),
        "message_inbox_total": query_one("SELECT count(*) as cnt FROM message_inbox").get("cnt", 0),
        "wagon_shipments_total": query_one("SELECT count(*) as cnt FROM wagon_shipments").get("cnt", 0),
        "last_error": api_last_error(),
    }
    # latest.json
    with open(f"{snap_dir}/latest.json", "w") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    # timestamped
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(f"{snap_dir}/{ts}.json", "w") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    return snap

HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="8">
<title>SOP Dashboard — R74</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #0d1117; color: #c9d1d9; font: 13px/1.5 -apple-system, sans-serif; padding: 16px; }
h1 { color: #58a6ff; font-size: 18px; margin-bottom: 4px; }
h2 { color: #8b949e; font-size: 14px; margin: 12px 0 4px; border-bottom: 1px solid #21262d; padding-bottom: 4px; }
.row { display: flex; flex-wrap: wrap; gap: 12px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 10px 14px; flex: 1; min-width: 200px; }
.card h3 { font-size: 12px; color: #8b949e; margin-bottom: 4px; text-transform: uppercase; letter-spacing: .5px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
.badge-green { background: #238636; color: #fff; }
.badge-red { background: #da3633; color: #fff; }
.badge-yellow { background: #9e6a03; color: #fff; }
.badge-gray { background: #30363d; color: #8b949e; }
table { width: 100%; border-collapse: collapse; margin-top: 4px; font-size: 12px; }
th, td { text-align: left; padding: 3px 6px; border-bottom: 1px solid #21262d; }
th { color: #8b949e; font-weight: 500; }
.mono { font-family: monospace; font-size: 11px; }
.refresh { color: #484f58; font-size: 11px; text-align: right; margin-bottom: 8px; }
.stat-num { font-size: 20px; font-weight: 700; color: #58a6ff; }
</style>
</head>
<body>
<h1>🛰 SOP Dashboard — R74 常驻运行观察</h1>
<div class="refresh">🔄 每 8 秒自动刷新 | 最后刷新: <span id="refresh_time"></span></div>

<div class="row">
  <div class="card">
    <h3>Live Service</h3>
    <div style="margin-top:4px;">
      <span id="ls_alive" class="badge badge-gray">...</span>
      PID: <span id="ls_pid" class="mono">-</span>
    </div>
    <div style="margin-top:4px;font-size:11px;color:#8b949e;">
      Last msg: <span id="ls_last_msg" class="mono">-</span><br>
      Cursor updated: <span id="ls_cursor" class="mono">-</span>
    </div>
  </div>
  <div class="card">
    <h3>Message Inbox</h3>
    <div class="stat-num" id="mi_total">-</div>
    <div style="font-size:11px;color:#8b949e;">waiting_media: <span id="mi_waiting">-</span></div>
  </div>
  <div class="card">
    <h3>Workflow Tasks</h3>
    <div style="font-size:11px;">
      pending:<span id="wt_pending" class="badge badge-yellow">-</span>
      succeeded:<span id="wt_ok" class="badge badge-green">-</span>
      failed:<span id="wt_fail" class="badge badge-red">-</span>
    </div>
  </div>
  <div class="card">
    <h3>External Actions</h3>
    <div style="font-size:11px;">
      executed:<span id="ea_exec" class="badge badge-green">-</span>
      skipped:<span id="ea_skip" class="badge badge-gray">-</span>
    </div>
  </div>
</div>

<h2>📥 Latest Messages (Inbox)</h2>
<table><thead><tr><th>ID</th><th>message_id</th><th>Group</th><th>Type</th><th>Status</th><th>Project</th><th>Flow/Node</th></tr></thead>
<tbody id="mi_table"></tbody></table>

<h2>🩺 Last Error</h2>
<div id="last_error" style="font-size:12px;color:#da3633;">-</div>

<script>
const API = window.location.origin;

async function load() {
  try {
    let [status, mi, wt, ea, err] = await Promise.all([
      fetch(API + '/api/status').then(r => r.json()),
      fetch(API + '/api/message-inbox?limit=10').then(r => r.json()),
      fetch(API + '/api/workflow-tasks').then(r => r.json()),
      fetch(API + '/api/external-actions').then(r => r.json()),
      fetch(API + '/api/last-error').then(r => r.json()),
    ]);

    // Status
    document.getElementById('ls_alive').textContent = status.alive ? '🟢 ALIVE' : '🔴 DEAD';
    document.getElementById('ls_alive').className = 'badge ' + (status.alive ? 'badge-green' : 'badge-red');
    document.getElementById('ls_pid').textContent = status.pid || '-';
    document.getElementById('ls_last_msg').textContent = status.last_processed_message_id || '-';
    document.getElementById('ls_cursor').textContent = (status.cursor || {}).updated_at || '-';
    document.getElementById('refresh_time').textContent = new Date().toLocaleTimeString();

    // Message inbox total
    document.getElementById('mi_total').textContent = mi.length;
    let waiting = mi.filter(m => m.media_status === 'waiting_media').length;
    document.getElementById('mi_waiting').textContent = waiting;

    // Message table
    let rows = mi.map(m => `<tr>
      <td>${m.id}</td><td class="mono">${m.message_id||'-'}</td><td>${m.group_name||'-'}</td>
      <td>${m.msg_type||'-'}</td><td>${m.processing_status||'-'}</td>
      <td>${m.sop_project_id||'-'}</td><td>${(m.sop_flow||'') + '/' + (m.sop_node||'')}</td>
    </tr>`).join('');
    document.getElementById('mi_table').innerHTML = rows;

    // Workflow tasks
    document.getElementById('wt_pending').textContent = wt.pending||0;
    document.getElementById('wt_ok').textContent = wt.succeeded||0;
    document.getElementById('wt_fail').textContent = wt.failed||0;

    // External actions
    document.getElementById('ea_exec').textContent = ea.executed||0;
    document.getElementById('ea_skip').textContent = ea.skipped_dry_run||0;

    // Error
    document.getElementById('last_error').textContent = err.error_message || 'none';
  } catch(e) {
    console.error(e);
  }
}

load();
setInterval(load, 8000);
</script>
</body>
</html>"""

def api_refresh():
    """Regenerate dashboard/dispatch_board_data.json from the live DBs."""
    import sys
    src_path = os.path.join(REPO, "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    out_path = os.path.join(DASHBOARD_DIR, "dispatch_board_data.json")
    try:
        from sop_hub.data_agent.dispatch_board import generate_dispatch_board_data
        payload = generate_dispatch_board_data(
            business_db_path=DB_PATH,
            rail_db_path=RAIL95306_DB,
            refresh_reason="api_refresh_endpoint",
        )
        Path(out_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"ok": True, "out_path": out_path,
                "timestamp": datetime.now().isoformat(),
                "summary": payload.get("summary", {})}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "type": type(exc).__name__}


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/" or path == "/index.html":
            self._respond_html(HTML_PAGE)
        elif path == "/api/status":
            self._respond_json(api_status())
        elif path == "/api/message-inbox":
            self._respond_json(api_message_inbox())
        elif path == "/api/workflow-tasks":
            self._respond_json(api_workflow_tasks())
        elif path == "/api/external-actions":
            self._respond_json(api_external_actions())
        elif path == "/api/waiting-media":
            self._respond_json({"count": api_waiting_media()})
        elif path == "/api/last-error":
            self._respond_json(api_last_error() or {})
        elif path == "/api/health/latest":
            snap = save_health_snapshot()
            self._respond_json(snap)
        elif path == "/api/refresh":
            self._respond_json(api_refresh())
        elif path in STATIC_WHITELIST:
            filename, ctype = STATIC_WHITELIST[path]
            self._respond_static(os.path.join(DASHBOARD_DIR, filename), ctype)
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')

    def _respond_json(self, data):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_static(self, file_path, ctype):
        if not os.path.exists(file_path):
            self.send_response(404)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"error":"file missing on disk"}')
            return
        with open(file_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # quiet


def snapshot_loop(interval=600):
    """每 10 分钟生成一次 health snapshot"""
    while True:
        time.sleep(interval)
        try:
            snap = save_health_snapshot()
            print(f"[snapshot] {snap['timestamp']} — mi:{snap['message_inbox_total']} wt:{snap['workflow_tasks']['succeeded']}/{snap['workflow_tasks']['total']}")
        except Exception as e:
            print(f"[snapshot error] {e}")


if __name__ == "__main__":
    # 启动 snapshot 线程
    t = threading.Thread(target=snapshot_loop, args=(600,), daemon=True)
    t.start()

    # 初始化 snapshot
    save_health_snapshot()
    print(f"Dashboard server starting on http://{HOST}:{PORT}")
    print(f"API: http://localhost:{PORT}/api/status")
    print(f"HTML: http://localhost:{PORT}/")

    server = HTTPServer((HOST, PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
