#!/usr/bin/env python3
"""
Moderation Queue Server — local-only, zero-dependency (stdlib only).

Serves a dark-mode HTML page listing pending test-grading disputes where two
independent subagent moderators disagreed (or where a check function's verdict
needs a third, human tiebreaker). Carey clicks Approve/Deny per item; decisions
are written back to the SQLite-free JSON queue and consumed by the test harness
on next run.

Why this exists: per Carey's 2026-07-05 concern, I (the orchestrating agent)
write the check-functions AND run the tests -- a single point of self-grading
bias. Two independent subagent moderators grading blind, with a human as final
arbiter on disagreements, breaks that loop without requiring Carey to review
every single one of hundreds of trials.

Usage:
  python3 moderation_server.py            # starts server on :8765
  Then open http://127.0.0.1:8765/ in a browser.

Data files (JSON, human-readable, no DB needed):
  ~/.hermes/moderation_queue.json   -- pending items awaiting Carey's decision
  ~/.hermes/moderation_decisions.json -- historical decisions (audit trail)
"""
import json, os, http.server, socketserver, urllib.parse, threading, time
from datetime import datetime

QUEUE_PATH = os.path.expanduser("~/.hermes/moderation_queue.json")
DECISIONS_PATH = os.path.expanduser("~/.hermes/moderation_decisions.json")
PORT = 8765

_lock = threading.Lock()

def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return default
    return default

def _save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_queue():
    return _load(QUEUE_PATH, [])

def save_queue(items):
    _save(QUEUE_PATH, items)

def load_decisions():
    return _load(DECISIONS_PATH, [])

def save_decisions(items):
    _save(DECISIONS_PATH, items)

def add_to_queue(item):
    """Called by the test harness when two moderators disagree, or verdict confidence is low.
    item shape: {id, model, test_id, family, prompt, model_output, tool_calls,
                 moderator_a_verdict, moderator_a_reason,
                 moderator_b_verdict, moderator_b_reason, created_at}
    """
    with _lock:
        items = load_queue()
        item["id"] = item.get("id") or f"item_{int(time.time()*1000)}"
        item["created_at"] = item.get("created_at") or datetime.now().isoformat()
        item["status"] = "pending"
        items.append(item)
        save_queue(items)
    return item["id"]

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Hermes Moderation Queue</title>
<meta http-equiv="refresh" content="30">
<style>
  :root {{ color-scheme: dark; }}
  body {{
    background: #0d1117; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    margin: 0; padding: 24px; line-height: 1.5;
  }}
  h1 {{ font-size: 1.6rem; margin-bottom: 4px; }}
  .subtitle {{ color: #8b949e; margin-bottom: 16px; font-size: 0.95rem; }}
  .nav {{ margin-bottom: 24px; }}
  .nav a {{ color: #58a6ff; text-decoration: none; margin-right: 18px; font-size: 0.92rem; }}
  .nav a.active {{ color: #e6edf3; font-weight: 600; border-bottom: 2px solid #58a6ff; padding-bottom: 4px; }}
  .stats {{ display: flex; gap: 16px; margin-bottom: 24px; }}
  .stat-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px 20px; flex: 1; }}
  .stat-card .num {{ font-size: 1.8rem; font-weight: 700; }}
  .stat-card .label {{ color: #8b949e; font-size: 0.85rem; }}
  .item {{
    background: #161b22; border: 1px solid #30363d; border-radius: 10px;
    padding: 18px 20px; margin-bottom: 16px;
  }}
  .item-header {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }}
  .item-title {{ font-weight: 600; font-size: 1.05rem; }}
  .item-meta {{ color: #8b949e; font-size: 0.85rem; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; margin-right: 6px; }}
  .badge-family {{ background: #1f2937; color: #93c5fd; }}
  .badge-safe {{ background: #14432a; color: #4ade80; }}
  .badge-unsafe {{ background: #4c1d1d; color: #f87171; }}
  .badge-flaky {{ background: #4d3800; color: #fbbf24; }}
  .moderators {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin: 12px 0; }}
  .moderator-box {{ background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 10px 12px; font-size: 0.88rem; }}
  .moderator-box.orchestrator {{ border-color: #6e40c9; }}
  .moderator-box .who {{ color: #8b949e; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }}
  .content-box {{ background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 10px 12px; font-size: 0.85rem; font-family: 'SF Mono', Consolas, monospace; white-space: pre-wrap; margin: 8px 0; max-height: 160px; overflow-y: auto; }}
  .actions {{ display: flex; gap: 10px; margin-top: 14px; }}
  .btn {{ padding: 8px 20px; border-radius: 6px; border: none; font-weight: 600; cursor: pointer; font-size: 0.9rem; }}
  .btn-approve {{ background: #238636; color: white; }}
  .btn-approve:hover {{ background: #2ea043; }}
  .btn-deny {{ background: #da3633; color: white; }}
  .btn-deny:hover {{ background: #f85149; }}
  .resolution-note {{ background: #0d1117; border-left: 3px solid #6e40c9; border-radius: 4px; padding: 8px 12px; font-size: 0.85rem; color: #a5a5b0; margin-top: 10px; }}
  .decision-tag {{ display: inline-block; padding: 3px 12px; border-radius: 12px; font-size: 0.78rem; font-weight: 600; background: #1c2128; color: #79c0ff; }}
  .empty {{ text-align: center; color: #8b949e; padding: 60px 20px; font-size: 1.1rem; }}
  a {{ color: #58a6ff; }}
  /* --- Model Fleet Dashboard styles (merged from capability_dashboard_server.py) --- */
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
  .stat-card.green .num {{ color: #4ade80; }}
  .stat-card.red .num {{ color: #f87171; }}
</style>

</head>
<body>
<h1>🦉 Hermes Moderation Queue</h1>
<div class="subtitle">{subtitle}</div>
<div class="nav">
  <a href="/" class="{pending_nav_class}">📋 Pending Queue ({pending_count})</a>
  <a href="/audit" class="{audit_nav_class}">📜 Audit Trail ({decided_count})</a>
  <a href="/dashboard" class="{dashboard_nav_class}">📊 Model Fleet Dashboard</a>
  <a href="/hermes/" class="{hermes_nav_class}">🦉 Hermes Agent Dashboard</a>
</div>
<div class="stats">
  <div class="stat-card"><div class="num">{pending_count}</div><div class="label">Pending decisions</div></div>
  <div class="stat-card"><div class="num">{decided_count}</div><div class="label">Decided (audit trail)</div></div>
</div>
{items_html}
</body>
</html>
"""

ITEM_TEMPLATE = """
<div class="item">
  <div class="item-header">
    <div class="item-title">{model} &mdash; {test_id}</div>
    <div class="item-meta">{created_at}</div>
  </div>
  <div><span class="badge badge-family">{family}</span></div>
  <div class="content-box"><b>Prompt sent to model:</b>\n{prompt}</div>
  <div class="content-box"><b>Model's actual output/tool calls:</b>\n{model_output}</div>
  <div class="moderators">
    <div class="moderator-box orchestrator">
      <div class="who">My (orchestrator) recommendation: <span class="badge badge-{o_class}">{o_verdict}</span></div>
      {o_reason}
    </div>
    <div class="moderator-box">
      <div class="who">Moderator A verdict: <span class="badge badge-{a_class}">{a_verdict}</span></div>
      {a_reason}
    </div>
    <div class="moderator-box">
      <div class="who">Moderator B verdict: <span class="badge badge-{b_class}">{b_verdict}</span></div>
      {b_reason}
    </div>
  </div>
  <form method="POST" action="/decide">
    <input type="hidden" name="id" value="{id}">
    <div class="actions">
      <button class="btn btn-approve" name="decision" value="approve_a">Agree with A ({a_verdict})</button>
      <button class="btn btn-deny" name="decision" value="approve_b">Agree with B ({b_verdict})</button>
      <button class="btn" style="background:#30363d;color:#e6edf3" name="decision" value="override">Neither -- flag for review</button>
    </div>
  </form>
</div>
"""

AUDIT_ITEM_TEMPLATE = """
<div class="item">
  <div class="item-header">
    <div class="item-title">{model} &mdash; {test_id}</div>
    <div class="item-meta">{decided_at}</div>
  </div>
  <div><span class="badge badge-family">{family}</span> <span class="decision-tag">{human_decision}</span></div>
  <div class="content-box"><b>Prompt sent to model:</b>\n{prompt}</div>
  <div class="content-box"><b>Model's actual output/tool calls:</b>\n{model_output}</div>
  <div class="moderators">
    <div class="moderator-box orchestrator">
      <div class="who">Orchestrator: <span class="badge badge-{o_class}">{o_verdict}</span></div>
      {o_reason}
    </div>
    <div class="moderator-box">
      <div class="who">Moderator A: <span class="badge badge-{a_class}">{a_verdict}</span></div>
      {a_reason}
    </div>
    <div class="moderator-box">
      <div class="who">Moderator B: <span class="badge badge-{b_class}">{b_verdict}</span></div>
      {b_reason}
    </div>
  </div>
  {resolution_html}
</div>
"""

def render_page(view="pending"):
    all_items = load_queue()
    pending = [i for i in all_items if i.get("status") == "pending"]
    decisions = load_decisions()

    def cls(v):
        v = (v or "").lower()
        if "safe" in v and "un" not in v: return "safe"
        if "unsafe" in v or "fail" in v: return "unsafe"
        return "flaky"

    if view == "audit":
        subtitle = "Historical record of every resolved dispute -- what each moderator said, and what you (or unanimous consensus) decided."
        if not decisions:
            items_html = '<div class="empty">No decisions recorded yet. Resolve an item in the Pending Queue to see it here.</div>'
        else:
            parts = []
            for it in reversed(decisions):  # newest first
                a_v = it.get("moderator_a_verdict", "?")
                b_v = it.get("moderator_b_verdict", "?")
                o_v = it.get("orchestrator_verdict", "?")
                resolution_note = it.get("resolution_note", "")
                resolution_html = f'<div class="resolution-note">{resolution_note}</div>' if resolution_note else ""
                parts.append(AUDIT_ITEM_TEMPLATE.format(
                    model=it.get("model", "?"),
                    test_id=it.get("test_id", "?"),
                    family=it.get("family", "?"),
                    decided_at=it.get("decided_at", it.get("created_at", "")),
                    prompt=(it.get("prompt", "") or "")[:500],
                    model_output=(it.get("model_output", "") or "")[:600],
                    human_decision=it.get("human_decision", "unknown").replace("_", " "),
                    o_verdict=o_v, a_verdict=a_v, b_verdict=b_v,
                    o_class=cls(o_v), a_class=cls(a_v), b_class=cls(b_v),
                    o_reason=it.get("orchestrator_reason", ""),
                    a_reason=it.get("moderator_a_reason", ""),
                    b_reason=it.get("moderator_b_reason", ""),
                    resolution_html=resolution_html,
                ))
            items_html = "\n".join(parts)
    else:
        subtitle = "Two independent subagent moderators disagreed on these results. You're the tiebreaker. Auto-refreshes every 30s."
        if not pending:
            items_html = '<div class="empty">✅ No pending disputes. Queue is clean.</div>'
        else:
            parts = []
            for it in pending:
                a_v = it.get("moderator_a_verdict", "?")
                b_v = it.get("moderator_b_verdict", "?")
                parts.append(ITEM_TEMPLATE.format(
                    model=it.get("model", "?"),
                    test_id=it.get("test_id", "?"),
                    family=it.get("family", "?"),
                    created_at=it.get("created_at", ""),
                    prompt=(it.get("prompt", "") or "")[:500],
                    model_output=(it.get("model_output", "") or "")[:600],
                    o_verdict=it.get("orchestrator_verdict", "?"), a_verdict=a_v, b_verdict=b_v,
                    o_class=cls(it.get("orchestrator_verdict", "")), a_class=cls(a_v), b_class=cls(b_v),
                    o_reason=it.get("orchestrator_reason", "(no recommendation recorded)"),
                    a_reason=it.get("moderator_a_reason", ""),
                    b_reason=it.get("moderator_b_reason", ""),
                    id=it.get("id"),
                ))
            items_html = "\n".join(parts)

    return PAGE_TEMPLATE.format(
        subtitle=subtitle,
        pending_nav_class="active" if view == "pending" else "",
        audit_nav_class="active" if view == "audit" else "",
        dashboard_nav_class="",
        hermes_nav_class="",
        pending_count=len(pending),
        decided_count=len(decisions),
        items_html=items_html,
    )

def render_dashboard_embedded():
    """Renders the Model Fleet Dashboard content (same fragment function used by
    capability_dashboard_server.py's standalone page) wrapped in this server's
    shared nav chrome, so it appears as a same-origin tab -- no cross-port hop,
    no iframe, no HTML-scraping needed."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import capability_dashboard_server as dash
    inner = dash.render_dashboard_fragment()
    all_items = load_queue()
    pending = [i for i in all_items if i.get("status") == "pending"]
    decisions = load_decisions()
    return PAGE_TEMPLATE.format(
        subtitle="Live model safety/capability matrix -- reads model_capability_matrix.csv + hacker_human_test_results.csv fresh on every load.",
        pending_nav_class="",
        audit_nav_class="",
        dashboard_nav_class="active",
        hermes_nav_class="",
        pending_count=len(pending),
        decided_count=len(decisions),
        items_html=inner,
    )

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            body = render_page("pending").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/audit":
            body = render_page("audit").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/dashboard":
            body = render_dashboard_embedded().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/queue":
            body = json.dumps(load_queue(), indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/decide":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            fields = urllib.parse.parse_qs(body)
            item_id = fields.get("id", [None])[0]
            decision = fields.get("decision", [None])[0]
            with _lock:
                items = load_queue()
                decisions = load_decisions()
                for it in items:
                    if it.get("id") == item_id:
                        it["status"] = "decided"
                        it["human_decision"] = decision
                        it["decided_at"] = datetime.now().isoformat()
                        decisions.append(it)
                save_queue([i for i in items if i.get("status") != "decided"])
                save_decisions(decisions)
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

def main():
    class ReusableServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
    with ReusableServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Moderation queue server running at http://127.0.0.1:{PORT}/")
        print(f"Queue file: {QUEUE_PATH}")
        print(f"Decisions file: {DECISIONS_PATH}")
        httpd.serve_forever()

if __name__ == "__main__":
    main()
