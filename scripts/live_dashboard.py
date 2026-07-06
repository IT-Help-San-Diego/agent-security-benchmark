#!/usr/bin/env python3
"""
Live benchmark dashboard — three clean views, direct SQLite.

Views:
  /        → Fleet Dashboard (model summary + verdicts)
  /queue   → Pending / test queue
  /audit   → Audit trail / decisions

Data: agent_security_benchmark.sqlite
Start: python3 scripts/live_dashboard.py
"""

import json, os, sqlite3, http.server, socketserver
from collections import defaultdict
from datetime import datetime

REPO = os.path.expanduser("~/Documents/GitHub/agent-security-benchmark")
DB_PATH = os.path.join(REPO, "data", "agent_security_benchmark.sqlite")
PORT = 8768

FAMILY_LABELS = {
    "nested_tools": "Nested Tool Calls",
    "testsingle_main": "TestSingle Main",
    "load_test": "Load Test",
    "2_irrelevance_detection": "No-Fabrication",
    "3_channel_judgment": "Channel Judgment",
    "4_confused_deputy": "Confused Deputy",
    "5_injected_instruction": "Injection Resistance",
    "6_real_attachments": "Real Attachments",
}


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def classify_provider(model, existing_provider):
    if existing_provider:
        return existing_provider
    m = model.lower()
    if any(m.startswith(p) for p in [
        "anthropic/", "stepfun/", "nousresearch/", "z-ai/", "moonshotai/",
        "minimax/", "deepseek/", "x-ai/", "openai/", "openrouter/",
        "qwen/", "google/", "gemini/", "meta-llama/", "mistral/"
    ]):
        return "cloud"
    return "local"


def verdict_class(v):
    v = (v or "").upper()
    if v == "SAFE":
        return "safe"
    if v in ("UNSAFE", "FAIL"):
        return "unsafe"
    if v == "FLAKY":
        return "flaky"
    return "muted"


# ── API helpers ──────────────────────────────────────────────────────────────

def api_fleet():
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

    by_model = defaultdict(dict)
    for r in rows:
        fam = r["family"] or "legacy"
        provider = r["provider"] or classify_provider(r["model_key"], None)
        by_model[r["model_key"]][fam] = {
            "verdict": r["verdict"],
            "date": r["last_seen"] or "",
            "provider": provider,
            "trials": None,
            "passes": None,
            "safe_runs": r["safe_runs"],
            "flaky_runs": r["flaky_runs"],
            "unsafe_runs": r["unsafe_runs"],
            "last_seen": r["last_seen"],
            "detail": "",
        }
    return by_model


def api_runs():
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT r.id, r.model_key, r.provider, r.family, r.verdict,
               r.started_at, r.finished_at, r.status, r.detail,
               COALESCE((SELECT COUNT(*) FROM trials t WHERE t.run_id=r.id),0) AS trials,
               COALESCE((SUM(CASE WHEN t.passed=1 THEN 1 ELSE 0 END)),0) AS passes
        FROM runs r
        LEFT JOIN trials t ON t.run_id = r.id
        GROUP BY r.id
        ORDER BY r.id DESC
        LIMIT 200
    """)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def api_decisions():
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT id, run_id, model_key, family, decided_at, decision, note
        FROM decisions
        ORDER BY id DESC
        LIMIT 200
    """)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


# ── HTML views ───────────────────────────────────────────────────────────────

def fleet_html(data, runs):
    family_order = [
        "nested_tools", "testsingle_main", "load_test",
        "2_irrelevance_detection", "3_channel_judgment",
        "4_confused_deputy", "5_injected_instruction", "6_real_attachments"
    ]
    family_label = {
        "nested_tools": "Nested Tool Calls",
        "testsingle_main": "TestSingle Main",
        "load_test": "Load Test",
        "2_irrelevance_detection": "No-Fabrication",
        "3_channel_judgment": "Channel Judgment",
        "4_confused_deputy": "Confused Deputy",
        "5_injected_instruction": "Injection Resistance",
        "6_real_attachments": "Real Attachments",
    }

    cards = []
    for model, families in sorted(data.items()):
        provider_badge = "🏠 Local"
        sample = next(iter(families.values()), {})
        if sample.get("provider") == "cloud" or sample.get("provider") == "openrouter":
            provider_badge = "☁️ Cloud"

        safe_runs = sum((f.get("safe_runs") or 0) for f in families.values())
        flaky_runs = sum((f.get("flaky_runs") or 0) for f in families.values())
        unsafe_runs = sum((f.get("unsafe_runs") or 0) for f in families.values())

        rows = ""
        for fam in family_order:
            f = families.get(fam)
            if not f:
                continue
            rows += (
                "<tr>"
                f"<td>{family_label.get(fam, fam)}</td>"
                f"<td><span class=\"pill {verdict_class(f.get('verdict'))}\">{f.get('verdict') or '—'}</span></td>"
                f"<td class=\"mono\">{f.get('date','')}</td>"
                f"<td>{f.get('trials') or '—'}</td>"
                "</tr>"
            )

        cards.append(
            "<div class=\"card\">"
            f"<h3>{model} <span class=\"muted\">{provider_badge}</span></h3>"
            "<div class=\"kpis\">"
            f"<span class=\"pill safe\">SAFE {safe_runs}</span>"
            f"<span class=\"pill flaky\">FLAKY {flaky_runs}</span>"
            f"<span class=\"pill unsafe\">UNSAFE {unsafe_runs}</span>"
            "</div>"
            "<div style=\"overflow-x:auto\"><table>"
            "<thead><tr><th>Family</th><th>Verdict</th><th>Last</th><th>Trials</th></tr></thead>"
            f"<tbody>{rows}</tbody>"
            "</table></div>"
            "</div>"
        )

    timeline = ""
    for r in runs[:20]:
        timeline += (
            "<tr>"
            f"<td class=\"mono\">{r['model_key']}</td>"
            f"<td>{r['family']}</td>"
            f"<td><span class=\"pill {verdict_class(r['verdict'])}\">{r['verdict']}</span></td>"
            f"<td class=\"mono\">{r['started_at']}</td>"
            f"<td class=\"mono\">{r.get('trials', '—')}/{r.get('passes', '—')}</td>"
            "</tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Fleet — Agent Security Benchmark</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ background:#0b0f13; color:#e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin:0; padding:24px; line-height:1.45; }}
  h1 {{ font-size:1.3rem; margin:0 0 4px; }}
  .subtitle {{ color:#8b949e; font-size:0.82rem; margin-bottom:18px; }}
  .nav a {{ color:#58a6ff; text-decoration:none; margin-right:14px; font-size:0.88rem; }}
  .nav a.active {{ color:#e6edf3; font-weight:700; border-bottom:2px solid #58a6ff; padding-bottom:3px; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:14px; }}
  .card {{ background:#111820; border:1px solid #1f2937; border-radius:10px; padding:14px; }}
  .card h3 {{ margin:0 0 8px; font-size:0.92rem; color:#c9d1d9; word-break:break-all; }}
  .muted {{ color:#8b949e; font-weight:400; font-size:0.85rem; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.78rem; margin-top:6px; }}
  th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid #1f2937; }}
  th {{ color:#8b949e; font-weight:600; text-transform:uppercase; letter-spacing:0.04em; font-size:0.7rem; }}
  .pill {{ padding:3px 8px; border-radius:999px; font-size:0.75rem; font-weight:600; }}
  .safe {{ background:#11301e; color:#4ade80; }}
  .flaky {{ background:#3b2e00; color:#fbbf24; }}
  .unsafe {{ background:#3b1515; color:#f87171; }}
  .muted {{ background:#161b22; color:#8b949e; }}
  .section {{ margin-top:28px; }}
</style>
</head>
<body>
<h1>📊 Model Fleet</h1>
<div class="subtitle">Live from SQLite · refreshed on reload</div>
<div class="nav">
  <a href="/" class="active">Fleet</a>
  <a href="/queue">Pending Queue</a>
  <a href="/audit">Audit Trail</a>
</div>
<div class="grid">
  {''.join(cards) if cards else '<div class="card">No model data yet.</div>'}
</div>
<div class="section">
  <h2>Latest Runs</h2>
  <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Model</th><th>Family</th><th>Verdict</th><th>Started</th><th>Trials</th></tr></thead>
      <tbody>{''.join(timeline) if timeline else '<tr><td colspan="5">No runs.</td></tr>'}</tbody>
    </table>
  </div>
</div>
</body>
</html>"""


def queue_html(items):
    rows = ""
    for r in items:
        rows += (
            "<tr>"
            f"<td class=\"mono\">{r['model_key']}</td>"
            f"<td>{r['family']}</td>"
            f"<td><span class=\"pill {verdict_class(r['verdict'])}\">{r['verdict']}</span></td>"
            f"<td class=\"mono\">{r['started_at']}</td>"
            "</tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pending Queue — Agent Security Benchmark</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ background:#0b0f13; color:#e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin:0; padding:24px; line-height:1.45; }}
  h1 {{ font-size:1.3rem; margin:0 0 4px; }}
  .subtitle {{ color:#8b949e; font-size:0.82rem; margin-bottom:18px; }}
  .nav a {{ color:#58a6ff; text-decoration:none; margin-right:14px; font-size:0.88rem; }}
  .nav a.active {{ color:#e6edf3; font-weight:700; border-bottom:2px solid #58a6ff; padding-bottom:3px; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.82rem; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #1f2937; }}
  th {{ color:#8b949e; font-weight:600; text-transform:uppercase; letter-spacing:0.04em; font-size:0.72rem; }}
  .muted {{ color:#8b949e; }}
  .pill {{ padding:3px 8px; border-radius:999px; font-size:0.75rem; font-weight:600; }}
  .safe {{ background:#11301e; color:#4ade80; }}
  .flaky {{ background:#3b2e00; color:#fbbf24; }}
  .unsafe {{ background:#3b1515; color:#f87171; }}
  .muted {{ background:#161b22; color:#8b949e; }}
</style>
</head>
<body>
<h1>📋 Pending Queue</h1>
<div class="subtitle">Recent model-runs feed · refreshed on reload</div>
<div class="nav">
  <a href="/">Fleet</a>
  <a href="/queue" class="active">Pending Queue</a>
  <a href="/audit">Audit Trail</a>
</div>
<div style="overflow-x:auto">
  <table>
    <thead><tr><th>Model</th><th>Family</th><th>Verdict</th><th>Started</th></tr></thead>
    <tbody>{rows if rows else '<tr><td colspan="4">Queue empty.</td></tr>'}</tbody>
  </table>
</div>
</body>
</html>"""


def audit_html(decisions, runs):
    rows = ""
    for d in decisions:
        rows += (
            "<tr>"
            f"<td class=\"mono\">{d.get('model_key') or '—'}</td>"
            f"<td>{d.get('family') or '—'}</td>"
            f"<td>{d.get('decision')}</td>"
            f"<td class=\"mono\">{d.get('decided_at')}</td>"
            f"<td>{d.get('note') or ''}</td>"
            "</tr>"
        )

    latest_runs = ""
    for r in runs[:20]:
        latest_runs += (
            "<tr>"
            f"<td class=\"mono\">{r['model_key']}</td>"
            f"<td><span class=\"pill {verdict_class(r['verdict'])}\">{r['verdict']}</span></td>"
            f"<td class=\"mono\">{r['started_at']}</td>"
            "</tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Audit Trail — Agent Security Benchmark</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ background:#0b0f13; color:#e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin:0; padding:24px; line-height:1.45; }}
  h1 {{ font-size:1.3rem; margin:0 0 4px; }}
  .subtitle {{ color:#8b949e; font-size:0.82rem; margin-bottom:18px; }}
  .nav a {{ color:#58a6ff; text-decoration:none; margin-right:14px; font-size:0.88rem; }}
  .nav a.active {{ color:#e6edf3; font-weight:700; border-bottom:2px solid #58a6ff; padding-bottom:3px; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.82rem; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #1f2937; }}
  th {{ color:#8b949e; font-weight:600; text-transform:uppercase; letter-spacing:0.04em; font-size:0.72rem; }}
  .pill {{ padding:3px 8px; border-radius:999px; font-size:0.75rem; font-weight:600; }}
  .safe {{ background:#11301e; color:#4ade80; }}
  .flaky {{ background:#3b2e00; color:#fbbf24; }}
  .unsafe {{ background:#3b1515; color:#f87171; }}
  .muted {{ background:#161b22; color:#8b949e; }}
  .section {{ margin-top:28px; }}
</style>
</head>
<body>
<h1>📜 Audit Trail</h1>
<div class="subtitle">Decisions and live verdict feed</div>
<div class="nav">
  <a href="/">Fleet</a>
  <a href="/queue">Pending Queue</a>
  <a href="/audit" class="active">Audit Trail</a>
</div>
<div>
  <h2>Decisions</h2>
  <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Model</th><th>Family</th><th>Decision</th><th>Decided</th><th>Note</th></tr></thead>
      <tbody>{rows if rows else '<tr><td colspan="5">No decisions recorded yet.</td></tr>'}</tbody>
    </table>
  </div>
</div>
<div class="section">
  <h2>Latest Verdicts</h2>
  <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Model</th><th>Verdict</th><th>Started</th></tr></thead>
      <tbody>{latest_runs if latest_runs else '<tr><td colspan="3">No runs.</td></tr>'}</tbody>
    </table>
  </div>
</div>
</body>
</html>"""


# ── Server ───────────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, message, *args):
        pass

    def _send_html(self, body):
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, obj):
        payload = json.dumps(obj, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        fleet_data = api_fleet()
        runs = api_runs()

        if self.path in ("/", "/fleet"):
            self._send_html(fleet_html(fleet_data, runs))
            return

        if self.path == "/queue":
            self._send_html(queue_html(runs))
            return

        if self.path == "/audit":
            decisions = api_decisions()
            self._send_html(audit_html(decisions, runs))
            return

        if self.path == "/api/fleet":
            self._send_json({"summary": fleet_data, "runs": runs})
            return
        if self.path == "/api/runs":
            self._send_json(runs)
            return
        if self.path == "/api/decisions":
            self._send_json(api_decisions())
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path == "/api/decide":
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            con = db()
            cur = con.cursor()
            cur.execute(
                "INSERT INTO decisions (model_key, family, decided_at, decision, note) VALUES (?,?,?,?,?)",
                (
                    payload.get("model_key"),
                    payload.get("family"),
                    datetime.now().isoformat(timespec="milliseconds"),
                    payload.get("decision", ""),
                    payload.get("note", "") or "",
                ),
            )
            con.commit()
            con.close()
            self._send_json({"ok": True})
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
