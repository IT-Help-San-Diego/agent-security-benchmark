#!/usr/bin/env python3
"""
Model Capability Dashboard Server — local-only, zero-dependency (stdlib only).

Serves a LIVE, auto-refreshing, dark-mode dashboard of model safety results,
reading directly from the SQLite benchmark DB on every request.
"""
import json, os, sqlite3, http.server, socketserver
from collections import defaultdict
from datetime import datetime

PORT = 8766
DB_PATH = os.path.expanduser("~/Documents/GitHub/agent-security-benchmark/data/agent_security_benchmark.sqlite")

FAMILY_LABELS = {
    "1_nested_tool_calling": "1. Nested Tool Calls",
    "nested_tools": "1. Nested Tool Calls",
    "load_test": "Load Test",
    "testsingle_main": "TestSingle Main",
    "2_irrelevance_detection": "2. No-Fabrication",
    "3_channel_judgment": "3. Channel Judgment",
    "4_confused_deputy": "4. Confused Deputy",
    "5_injected_instruction": "5. Injection Resistance",
    "6_real_attachments": "6. Real Attachments",
}

def verdict_class(v):
    v = (v or "").upper()
    if "SAFE" in v and "UN" not in v:
        return "safe"
    if "UNSAFE" in v or "FAIL" in v:
        return "unsafe"
    if "FLAKY" in v:
        return "flaky"
    return "other"

def db_rows():
    if not os.path.exists(DB_PATH):
        return []
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
        SELECT r.model_key, r.provider, r.family, r.verdict, r.started_at, r.detail,
               COALESCE((SELECT MAX(t.trial_index) FROM trials t WHERE t.run_id=r.id), 0) AS trials,
               COALESCE((SELECT SUM(CASE WHEN t.passed=1 THEN 1 ELSE 0 END) FROM trials t WHERE t.run_id=r.id), 0) AS passes
        FROM runs r
        WHERE r.status = 'completed' OR r.status = 'crashed'
    """)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows

def build_model_summary():
    """Return per-model rollup from SQLite benchmark DB."""
    rows = db_rows()
    per_model = defaultdict(lambda: {"families": {}, "provider": "", "last_seen": ""})
    for r in rows:
        model = r.get("model_key") or "unknown"
        family = r.get("family") or "nested_tools"
        verdict = r.get("verdict") or "?"
        date = r.get("started_at") or ""
        provider = r.get("provider") or ""
        entry = per_model[model]
        if provider:
            entry["provider"] = provider
        existing = entry["families"].get(family)
        if not existing or date >= existing.get("date", ""):
            entry["families"][family] = {
                "verdict": verdict,
                "date": date,
                "detail": r.get("detail", ""),
                "test_id": "",
                "trials": r.get("trials", 0),
                "passes": r.get("passes", 0),
            }
        if date > entry["last_seen"]:
            entry["last_seen"] = date
    return per_model

FAMILY_LABELS = {
    "1_nested_tool_calling": "1. Nested Tool Calls",
    "nested_tools": "1. Nested Tool Calls",
    "load_test": "Load Test",
    "2_irrelevance_detection": "2. No-Fabrication",
    "3_channel_judgment": "3. Channel Judgment",
    "4_confused_deputy": "4. Confused Deputy",
    "5_injected_instruction": "5. Injection Resistance",
    "6_real_attachments": "6. Real Attachments",
}

def classify_provider(model, existing_provider):
    if existing_provider:
        return existing_provider
    m = model.lower()
    if any(m.startswith(p) for p in ["anthropic/", "stepfun/", "nousresearch/", "z-ai/", "moonshotai/", "minimax/", "deepseek/", "x-ai/", "openai/", "openrouter/", "qwen/", "google/", "gemini/", "meta-llama/", "mistral/"]):
        return "cloud"
    known_cloud_exact = {
        "step-3.7-flash@q8_0 / @bf16 (local gguf)",
    }
    if model in known_cloud_exact:
        return "cloud"
    return "lmstudio"

def render_dashboard_fragment():
    """Returns just the stats+table+legend+footer HTML -- no <html>/<head>/<body>,
    no duplicate stats-card markup. Used both by the standalone full-page dashboard
    below AND embedded same-origin inside moderation_server.py's shared nav chrome."""
    per_model = build_model_summary()
    families_present = sorted(set(
        fam for m in per_model.values() for fam in m["families"].keys()
    ), key=lambda f: (f not in FAMILY_LABELS, f))

    total_models = len(per_model)
    fully_safe = sum(1 for m in per_model.values()
                      if m["families"] and all(verdict_class(f["verdict"]) == "safe" for f in m["families"].values()))
    any_unsafe = sum(1 for m in per_model.values()
                       if any(verdict_class(f["verdict"]) == "unsafe" for f in m["families"].values()))

    rows_html = []
    for model, data in sorted(per_model.items(), key=lambda kv: kv[0]):
        provider = classify_provider(model, data["provider"])
        provider_badge = "☁️ cloud" if provider == "nous" else "🏠 local"
        cells = []
        for fam in families_present:
            f = data["families"].get(fam)
            if not f:
                cells.append('<td class="cell-empty">—</td>')
                continue
            cls = verdict_class(f["verdict"])
            title = (f.get("detail") or "").replace('"', "&quot;")[:300]
            cells.append(f'<td class="cell-{cls}" title="{title}">{f["verdict"]}</td>')
        rows_html.append(
            f'<tr><td class="model-name">{model}</td><td class="provider-badge">{provider_badge}</td>'
            + "".join(cells) + f'<td class="last-seen">{data["last_seen"]}</td></tr>'
        )

    header_cells = "".join(f'<th>{FAMILY_LABELS.get(f, f)}</th>' for f in families_present)

    return f"""
<div class="stats">
  <div class="stat-card"><div class="num">{total_models}</div><div class="label">Models tracked</div></div>
  <div class="stat-card green"><div class="num">{fully_safe}</div><div class="label">Fully SAFE across all families tested</div></div>
  <div class="stat-card red"><div class="num">{any_unsafe}</div><div class="label">UNSAFE on at least one family</div></div>
</div>

<div style="overflow-x:auto">
<table class="fleet">
  <thead><tr><th style="text-align:left">Model</th><th>Type</th>{header_cells}<th>Last tested</th></tr></thead>
  <tbody>
    {"".join(rows_html)}
  </tbody>
</table>
</div>

<div class="fleet-legend">
  <span><span class="dot" style="background:#4ade80"></span> SAFE (3/3)</span>
  <span><span class="dot" style="background:#fbbf24"></span> FLAKY (1-2/3)</span>
  <span><span class="dot" style="background:#f87171"></span> UNSAFE (0/3)</span>
  <span><span class="dot" style="background:#8b949e"></span> not yet tested for this family</span>
</div>

<div class="fleet-footer">
  Generated {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} &middot;
  Source: <code>{DB_PATH}</code>
</div>"""

def render_dashboard():
    """Full standalone page (used when this server is hit directly on :8766)."""
    fragment = render_dashboard_fragment()
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Hermes Model Capability Dashboard</title>
<meta http-equiv="refresh" content="20">
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    background: #0d1117; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    margin: 0; padding: 28px; line-height: 1.5;
  }}
  h1 {{ font-size: 1.7rem; margin: 0 0 4px; display: flex; align-items: center; gap: 10px; }}
  .subtitle {{ color: #8b949e; margin-bottom: 16px; font-size: 0.92rem; }}
  .nav {{ margin-bottom: 20px; }}
  .nav a {{ color: #58a6ff; text-decoration: none; margin-right: 18px; font-size: 0.92rem; }}
  .nav a.active {{ color: #e6edf3; font-weight: 600; border-bottom: 2px solid #58a6ff; padding-bottom: 4px; }}
  .stats {{ display: flex; gap: 14px; margin-bottom: 24px; flex-wrap: wrap; }}
  .stat-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px 22px; min-width: 140px; }}
  .stat-card .num {{ font-size: 1.9rem; font-weight: 700; }}
  .stat-card .label {{ color: #8b949e; font-size: 0.82rem; margin-top: 2px; }}
  .stat-card.green .num {{ color: #4ade80; }}
  .stat-card.red .num {{ color: #f87171; }}
  table.fleet {{ width: 100%; border-collapse: collapse; background: #161b22; border-radius: 10px; overflow: hidden; font-size: 0.85rem; }}
  table.fleet th, table.fleet td {{ padding: 10px 12px; text-align: center; border-bottom: 1px solid #21262d; }}
  table.fleet th {{ background: #0d1117; color: #8b949e; font-weight: 600; text-transform: uppercase; font-size: 0.72rem; letter-spacing: 0.04em; position: sticky; top: 0; }}
  td.model-name {{ text-align: left; font-family: 'SF Mono', Consolas, monospace; font-weight: 600; color: #e6edf3; white-space: nowrap; }}
  td.provider-badge {{ font-size: 0.8rem; white-space: nowrap; }}
  td.last-seen {{ color: #6e7681; font-size: 0.75rem; white-space: nowrap; }}
  .cell-safe {{ background: #14432a; color: #4ade80; font-weight: 700; }}
  .cell-unsafe {{ background: #4c1d1d; color: #f87171; font-weight: 700; }}
  .cell-flaky {{ background: #4d3800; color: #fbbf24; font-weight: 700; }}
  .cell-other {{ background: #21262d; color: #8b949e; }}
  .cell-empty {{ color: #30363d; }}
  table.fleet tr:hover td {{ filter: brightness(1.15); }}
  .fleet-legend {{ display: flex; gap: 18px; margin-top: 16px; font-size: 0.85rem; color: #8b949e; flex-wrap: wrap; }}
  .fleet-legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .fleet-footer {{ margin-top: 20px; color: #6e7681; font-size: 0.8rem; }}
  a {{ color: #58a6ff; }}
</style>
</head>
<body>
<h1>🦉 Hermes Model Capability Dashboard</h1>
<div class="subtitle">Live from agent_security_benchmark.sqlite &middot; refresh page to reload latest results</div>
<div class="nav">
  <a href="http://127.0.0.1:8765/">📋 Pending Queue</a>
  <a href="http://127.0.0.1:8765/audit">📜 Audit Trail</a>
  <a href="/" class="active">📊 Model Fleet Dashboard</a>
  <a href="http://127.0.0.1:9119/">🦉 Hermes Agent Dashboard</a>
</div>
<div style="font-size:0.78rem;color:#6e7681;margin-bottom:16px;">
  ℹ️ You're viewing this dashboard directly on port 8766. For the unified single-window
  control panel (all four views, no new-window popouts), use
  <a href="http://127.0.0.1:8767/dashboard">http://127.0.0.1:8767/</a> instead.
</div>
{fragment}
</body>
</html>"""
    return html

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = render_dashboard().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/summary":
            data = build_model_summary()
            body = json.dumps(data, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

def main():
    class ReusableServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
    with ReusableServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Capability dashboard server running at http://127.0.0.1:{PORT}/")
        httpd.serve_forever()

if __name__ == "__main__":
    main()
