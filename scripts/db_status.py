#!/usr/bin/env python3
"""Quick DB status for agent_security_benchmark.sqlite."""
import sqlite3, os, json
from datetime import datetime

REPO = os.path.expanduser("~/Documents/GitHub/agent-security-benchmark")
DB = os.path.join(REPO, "data", "agent_security_benchmark.sqlite")

def db_connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def status():
    if not os.path.exists(DB):
        print("DB missing:", DB)
        return
    print("DB:", DB)
    print("size_bytes:", os.path.getsize(DB))
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM runs")
    print("runs:", cur.fetchone()["n"])
    cur.execute("SELECT COUNT(*) AS n FROM trials")
    print("trials:", cur.fetchone()["n"])
    cur.execute("SELECT COUNT(*) AS n FROM decisions")
    print("decisions:", cur.fetchone()["n"])
    cur.execute("SELECT id, model_key, provider, verdict, status, started_at, finished_at FROM runs ORDER BY id DESC LIMIT 5")
    rows = cur.fetchall()
    print("recent_runs:")
    for r in rows:
        print(" ", dict(r))
    cur.execute("SELECT COUNT(*) AS n FROM runs WHERE status='crashed'")
    print("crashed_runs:", cur.fetchone()["n"])
    con.close()

if __name__ == "__main__":
    status()
