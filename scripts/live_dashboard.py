#!/usr/bin/env python3
"""
Live benchmark dashboard — lightweight, update-safe, repo-local.

- Reads agent_security_benchmark.sqlite directly
- Serves HTML + JSON API
- No Hermes coupling, no launchd, no magic paths
- Start: python3 scripts/live_dashboard.py
- Open: http://127.0.0.1:8768/
"""
import json, os, sqlite3, http.server, socketserver, time
from collections import defaultdict
from datetime import datetime

REPO = os.path.expanduser("~/Documents/GitHub/agent-security-benchmark")
DB_PATH = os.path.join(REPO, "data", "agent_security_benchmark.sqlite")
PORT = 8768

FAMILIES = [
    ("nested_tools", "Nested Tool Calls"),
    ("testsingle_main", "TestSingle Main"),
    ("load_test", "Load Test"),
    ("2_irrelevance_detection", "No-Fabrication"),
    ("3_channel_judgment", "Channel Judgment"),
    ("4_confused_deputy", "Confused Deputy"),
    ("5_injected_instruction", "Injection Resistance"),
    ("6_real_attachments", "Real Attachments"),
]

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def api_runs():
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT r.id, r.model_key, r.provider, r.family, r.verdict, r.started_at, r.finished_at, r.status, r.detail,
               COALESCE((SELECT COUNT(*) FROM trials t WHERE t.run_id=r.id),0) AS trials,
               COALESCE((SELECT SUM(CASE WHEN t.passed=1 THEN 1 ELSE 0 END) FROM trials t WHERE t.run_id=r.id),0) AS passes
        FROM runs r
        ORDER BY r.id DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows

def api_summary():
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT r.model_key, r.provider, r.family, r.verdict,
               COUNT(*) AS runs,
               SUM(CASE WHEN r.verdict='SAFE' THEN 1 ELSE 0 END) AS safe_runs,
               SUM(CASE WHEN r.verdict='FLAKY' THEN 1 ELSE 0 END) AS flaky_runs,
               SUM(CASE WHEN r.verdict='UNSAFE' THEN 1 ELSE 0 END) AS unsafe_runs,
               MAX(r.started_at) AS last_seen
        FROM runs r
        WHERE r.status IN ('completed','crashed')
        GROUP BY r.model_key, r.family
    """)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    by_model = defaultdict(list)
    for r in rows:
        by_model[r["model_key"]].append(r)
    return by_model

def api_decide(run_id, decision, note):
    con = db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO decisions (run_id, model_key, family, decided_at, decision, note) VALUES (?,?,?,?,?,?)",
        (run_id, None, None, datetime.now().isoformat(timespec="milliseconds"), decision, note or ""),
    )
    con.commit()
    con.close()
    return {"ok": True}

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agent Security Benchmark — Live</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { background:#0b0f13; color:#e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin:0; padding:24px; line-height:1.45; }
  h1 { font-size:1.4rem; margin:0 0 6px; }
  .subtitle { color:#8b949e; font-size:0.85rem; margin-bottom:18px; }
  .toolbar { display:flex; gap:10px; align-items:center; margin-bottom:14px; flex-wrap:wrap; }
  .toolbar button { background:#161b22; color:#e6edf3; border:1px solid #30363d; padding:8px 10px; border-radius:8px; cursor:pointer; }
  .toolbar button:hover { background:#21262d; }
  .status { color:#8b949e; font-size:0.82rem; }
  .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap:14px; }
  .card { background:#111820; border:1px solid #1f2937; border-radius:10px; padding:14px; }
  .card h3 { margin:0 0 8px; font-size:0.92rem; color:#c9d1d9; word-break:break-all; }
  .kpis { display:flex; gap:10px; margin-bottom:8px; }
  .pill { padding:4px 8px; border-radius:999px; font-size:0.8rem; font-weight:600; }
  .safe { background:#11301e; color:#4ade80; }
  .flaky { background:#3b2e00; color:#fbbf24; }
  .unsafe { background:#3b1515; color:#f87171; }
  .muted { background:#161b22; color:#8b949e; }
  table { width:100%; border-collapse:collapse; font-size:0.82rem; margin-top:4px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid #1f2937; }
  th { color:#8b949e; font-weight:600; text-transform:uppercase; letter-spacing:0.04em; font-size:0.72rem; }
  .mono { font-family:'SF Mono', Consolas, monospace; }
  .actions { display:flex; gap:8px; margin-top:8px; }
  .actions button { padding:6px 10px; border-radius:6px; border:1px solid #30363d; background:#161b22; color:#e6edf3; cursor:pointer; }
  .actions button:hover { background:#21262d; }
  .approve { border-color:#2f3f2f; color:#4ade80; }
  .reject { border-color:#3f2f2f; color:#f87171; }
  .note { width:100%; margin-top:6px; padding:8px; background:#0b0f13; color:#e6edf3; border:1px solid #30363d; border-radius:6px; }
</style>
</head>
<body>
<h1>⚡ Agent Security Benchmark — Live</h1>
<div class="subtitle">Direct SQLite view · manual reload · controls included</div>
<div class="toolbar">
  <button onclick="reload()">Reload now</button>
  <button onclick="autoToggle()">Auto: <span id="autoLabel">on</span></button>
  <span class="status" id="status">loading…</span>
</div>
<div id="app" class="grid">Loading…</div>
<script>
const API = '/api';
let auto = true;
let timer = null;
function status(msg) { document.getElementById('status').textContent = msg; }
function autoToggle() {
  auto = !auto;
  document.getElementById('autoLabel').textContent = auto ? 'on' : 'off';
  if (auto) schedule(); else clearTimeout(timer);
}
function schedule() { if (!auto) return; timer = setTimeout(reload, 1500); }
async function reload() {
  status('loading…');
  try {
    const res = await fetch(API + '?ts=' + Date.now(), {headers:{'Cache-Control':'no-store'}});
    const data = await res.json();
    render(data);
    status('updated ' + new Date().toLocaleTimeString());
  } catch (e) {
    status('error: ' + e.message);
  }
  schedule();
}
function verdictClass(v) {
  v = (v||'').toUpperCase();
  if (v==='SAFE') return 'safe';
  if (v==='UNSAFE'||v==='FAIL') return 'unsafe';
  if (v==='FLAKY') return 'flaky';
  return 'muted';
}
function render(data) {
  const app = document.getElementById('app');
  app.innerHTML = '';
  const order = ['nested_tools','testsingle_main','load_test','2_irrelevance_detection','3_channel_judgment','4_confused_deputy','5_injected_instruction','6_real_attachments'];
  const label = {nested_tools:'Nested Tool Calls',testsingle_main:'TestSingle Main',load_test:'Load Test','2_irrelevance_detection':'No-Fabrication','3_channel_judgment':'Channel Judgment','4_confused_deputy':'Confused Deputy','5_injected_instruction':'Injection Resistance','6_real_attachments':'Real Attachments'};
  for (const [model, families] of Object.entries(data.summary)) {
    const card = document.createElement('div');
    card.className = 'card';
    const latest = Object.values(families).sort((a,b) => (b.date||'').localeCompare(a.date||''))[0];
    const provider = latest && latest.provider ? (latest.provider==='openrouter'?'☁️ OpenRouter':'🏠 Local') : '';
    const safeRuns = latest && latest.safe_runs != null ? latest.safe_runs : Object.values(families).filter(f=>f.verdict==='SAFE').length;
    const flakyRuns = latest && latest.flaky_runs != null ? latest.flaky_runs : Object.values(families).filter(f=>f.verdict==='FLAKY').length;
    const unsafeRuns = latest && latest.unsafe_runs != null ? latest.unsafe_runs : Object.values(families).filter(f=>f.verdict==='UNSAFE').length;
    let rows = '';
    for (const fam of order) {
      const f = families[fam];
      if (!f) continue;
      rows += `<tr><td>${label[fam]||fam}</td><td><span class="pill ${verdictClass(f.verdict)}">${f.verdict||'—'}</span></td><td class="mono">${f.date||''}</td><td>${f.trials||0}/${f.passes||0}</td></tr>`;
    }
    card.innerHTML = `
      <h3>${model} <span style="font-weight:400;color:#8b949e;">${provider}</span></h3>
      <div class="kpis">
        <span class="pill safe">SAFE ${safeRuns}</span>
        <span class="pill flaky">FLAKY ${flakyRuns}</span>
        <span class="pill unsafe">UNSAFE ${unsafeRuns}</span>
      </div>
      <table>
        <thead><tr><th>Family</th><th>Verdict</th><th>Last</th><th>Trials</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="actions">
        <button class="approve" onclick="decide('${model}','approved')">Approve</button>
        <button class="reject" onclick="decide('${model}','rejected')">Reject</button>
      </div>
      <input class="note" id="note-${model.replace(/[^a-z0-9]/gi,'_')}" placeholder="Optional note…" />
    `;
    app.appendChild(card);
  }
}
async function decide(model, decision) {
  const key = 'note-' + model.replace(/[^a-z0-9]/gi,'_');
  const note = document.getElementById(key).value || '';
  status('saving…');
  try {
    await fetch('/api/decide', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({model_key:model, decision, note})});
    reload();
  } catch (e) { status('save failed: ' + e.message); }
}
reload();
</script>
</body>
</html>
"""

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, message, *args):
        pass

    def _send_json(self, obj):
        body = json.dumps(obj, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api"):
            if self.path.startswith("/api/decide"):
                self.send_response(404)
                self.end_headers()
                return
            data = {
                "summary": api_summary(),
                "runs": api_runs(),
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._send_json(data)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path == "/api/decide":
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = api_decide(payload.get("run_id"), payload.get("decision"), payload.get("note"))
            self._send_json(result)
            return
        self.send_response(404)
        self.end_headers()

def main():
    class ReusableServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
    with ReusableServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Live dashboard running at http://127.0.0.1:{PORT}/")
        httpd.serve_forever()

if __name__ == "__main__":
    main()
