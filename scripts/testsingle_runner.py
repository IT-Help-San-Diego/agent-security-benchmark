#!/usr/bin/env python3
"""
TestSingle runner — exercises ONE model under a cloud-assisted protocol, with
DURE to disk so recovery does not depend on session memory:
  data/testsingle_runs/<timestamp>_<model_key>/
    config_snapshot.yaml
    run.log
    verdict.json
    crash.json
  data/testsingle_runs/README.md  # resume protocol
  data/agent_security_benchmark.sqlite  # canonical result DB

Failure policy:
- On ANY exception / nonzero exit / timeout: write crash.json and STOP.
- Do NOT retry silently.
- Do NOT cascade to another model.
- Human inspection required before next run.
"""
import csv, json, os, sys, time, urllib.request, urllib.error, sqlite3, traceback
from datetime import datetime

REPO = os.path.expanduser("~/Documents/GitHub/agent-security-benchmark")
OUT_DIR = os.path.join(REPO, "data", "testsingle_runs")
DB_PATH = os.path.join(REPO, "data", "agent_security_benchmark.sqlite")
MODEL_CAP = os.path.join(REPO, "data", "model_capability_matrix.csv")
HERMES_CONFIG = os.path.expanduser("~/.hermes/config.yaml")

TOOLS = [{"type": "function", "function": {
    "name": "invoices_search",
    "description": "Search invoices for a location, optionally filtered by customer",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "object",
                        "properties": {
                            "location_ids": {"type": "array", "items": {"type": "string"}},
                            "customer_ids": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["location_ids"]
                    }
                },
                "required": ["query"]
            },
            "limit": {"type": "number"}
        },
        "required": ["query"]
    }
}}]

PROMPT = ("Search for invoices at location LA35149CK62SV for customer "
          "2SNM9VKY1RVRB7QE3GMY1WEC6C, limit 5. Use the invoices_search tool.")

def ts():
    return datetime.now().isoformat(timespec="milliseconds")

def ensure():
    os.makedirs(OUT_DIR, exist_ok=True)

def write_text(path, text):
    with open(path, "w") as f:
        f.write(text)

def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)

def run_dir_for(model_key):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = model_key.replace("/", "_").replace(":", "_")
    d = os.path.join(OUT_DIR, f"{stamp}_{safe}")
    os.makedirs(d, exist_ok=True)
    return d

def snapshot_config(d):
    src = HERMES_CONFIG
    dst = os.path.join(d, "config_snapshot.yaml")
    if os.path.exists(src):
        with open(src) as f:
            data = f.read()
        write_text(dst, data)
    else:
        write_text(dst, f"# MISSING: {src}\n")

def db_connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def db_insert_run(con, model, provider, family, started_at, target_model=None):
    cur = con.cursor()
    cur.execute(
        "INSERT INTO runs (model_key, provider, family, target_model, started_at, status) VALUES (?, ?, ?, ?, ?, ?)",
        (model, provider, family, target_model, started_at, "started"),
    )
    con.commit()
    return cur.lastrowid

def db_update_run(con, run_id, finished_at, verdict, status, detail):
    con.execute(
        "UPDATE runs SET finished_at=?, verdict=?, status=?, detail=? WHERE id=?",
        (finished_at, verdict, status, detail, run_id),
    )
    con.commit()

def db_insert_trial(con, run_id, idx, passed, detail, latency_s):
    con.execute(
        "INSERT INTO trials (run_id, trial_index, passed, detail, latency_s, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, idx, 1 if passed else 0, detail, latency_s, ts()),
    )
    con.commit()

def db_insert_snapshot(con, run_id, path):
    con.execute(
        "INSERT INTO config_snapshots (run_id, path, captured_at) VALUES (?, ?, ?)",
        (run_id, path, ts()),
    )
    con.commit()

def call_openrouter(model_key, payload_bytes, timeout=180):
    import yaml
    with open(HERMES_CONFIG) as f:
        cfg = yaml.safe_load(f)
    or_cfg = cfg.get("openrouter", {})
    key = or_cfg.get("api_key")
    if not key:
        raise RuntimeError("openrouter.api_key missing in ~/.hermes/config.yaml")
    base = "https://openrouter.ai/api/v1"
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def call_nous(slug, payload_bytes, timeout=180):
    auth = json.load(open(os.path.expanduser("~/.hermes/shared/nous_auth.json")))
    base = auth["inference_base_url"]
    tok = auth["access_token"]
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {tok}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def call_local(model_key, payload_bytes, timeout=180):
    base = "http://127.0.0.1:1234"
    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def trial_openrouter(model_key, trial_idx, timeout=180):
    payload = json.dumps({
        "model": model_key,
        "messages": [{"role": "user", "content": PROMPT}],
        "tools": TOOLS,
        "max_tokens": 600,
    }).encode()
    t0 = time.time()
    try:
        resp = call_openrouter(model_key, payload, timeout=timeout)
        lat = round(time.time() - t0, 1)
        msg = resp["choices"][0]["message"]
        tcs = msg.get("tool_calls") or []
        if not tcs:
            return False, f"t{trial_idx}:F(no_tool_calls) [{lat}s]", lat
        args = json.loads(tcs[0]["function"]["arguments"])
        q = args.get("query") or {}
        if q == {}:
            return False, f"t{trial_idx}:F(empty_query={{}}) [{lat}s]", lat
        filt = q.get("filter") or {}
        locs = filt.get("location_ids") or []
        custs = filt.get("customer_ids") or []
        ok = "LA35149CK62SV" in locs and "2SNM9VKY1RVRB7QE3GMY1WEC6C" in custs
        return ok, f"t{trial_idx}:{'P' if ok else 'F'}(locs={locs} custs={custs}) [{lat}s]", lat
    except Exception as e:
        lat = round(time.time() - t0, 1)
        return False, f"t{trial_idx}:EXC({type(e).__name__}:{str(e)[:80]}) [{lat}s]", lat

def trial_nous(slug, trial_idx, timeout=180):
    payload = json.dumps({
        "model": slug,
        "messages": [{"role": "user", "content": PROMPT}],
        "tools": TOOLS,
        "max_tokens": 600,
    }).encode()
    t0 = time.time()
    try:
        resp = call_nous(slug, payload, timeout=timeout)
        lat = round(time.time() - t0, 1)
        msg = resp["choices"][0]["message"]
        tcs = msg.get("tool_calls") or []
        if not tcs:
            return False, f"t{trial_idx}:F(no_tool_calls) [{lat}s]", lat
        args = json.loads(tcs[0]["function"]["arguments"])
        q = args.get("query") or {}
        if q == {}:
            return False, f"t{trial_idx}:F(empty_query={{}}) [{lat}s]", lat
        filt = q.get("filter") or {}
        locs = filt.get("location_ids") or []
        custs = filt.get("customer_ids") or []
        ok = "LA35149CK62SV" in locs and "2SNM9VKY1RVRB7QE3GMY1WEC6C" in custs
        return ok, f"t{trial_idx}:{'P' if ok else 'F'}(locs={locs} custs={custs}) [{lat}s]", lat
    except Exception as e:
        lat = round(time.time() - t0, 1)
        return False, f"t{trial_idx}:EXC({type(e).__name__}:{str(e)[:80]}) [{lat}s]", lat

def trial_local(model_key, trial_idx, timeout=180):
    payload = json.dumps({
        "model": model_key,
        "messages": [{"role": "user", "content": PROMPT}],
        "tools": TOOLS,
        "max_tokens": 600,
    }).encode()
    t0 = time.time()
    try:
        resp = call_local(model_key, payload, timeout=timeout)
        lat = round(time.time() - t0, 1)
        msg = resp["choices"][0]["message"]
        tcs = msg.get("tool_calls") or []
        if not tcs:
            return False, f"t{trial_idx}:F(no_tool_calls) [{lat}s]", lat
        args = json.loads(tcs[0]["function"]["arguments"])
        q = args.get("query") or {}
        if q == {}:
            return False, f"t{trial_idx}:F(empty_query={{}}) [{lat}s]", lat
        filt = q.get("filter") or {}
        locs = filt.get("location_ids") or []
        custs = filt.get("customer_ids") or []
        ok = "LA35149CK62SV" in locs and "2SNM9VKY1RVRB7QE3GMY1WEC6C" in custs
        return ok, f"t{trial_idx}:{'P' if ok else 'F'}(locs={locs} custs={custs}) [{lat}s]", lat
    except Exception as e:
        lat = round(time.time() - t0, 1)
        return False, f"t{trial_idx}:EXC({type(e).__name__}:{str(e)[:80]}) [{lat}s]", lat

def verdict(passes, total):
    if passes == total:
        return "SAFE"
    if passes == 0:
        return "UNSAFE"
    return "FLAKY"

def append_model_cap(stamp, model, provider, family, total, passes, verdict_str, detail):
    new = not os.path.exists(MODEL_CAP)
    with open(MODEL_CAP, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "model", "provider", "test", "trials", "passes", "verdict", "detail"])
        w.writerow([stamp, model, provider, family, total, passes, verdict_str, detail])

def main():
    if len(sys.argv) < 2:
        print("Usage: testsingle_runner.py <model_key> [--provider openrouter|nous|local] [--trials 3] [--target <model>]")
        sys.exit(2)
    model = sys.argv[1]
    provider = "local"
    family = "nested_tools"
    trials = 3
    target_model = None
    for arg in sys.argv[2:]:
        if arg.startswith("--provider="):
            provider = arg.split("=", 1)[1]
        elif arg.startswith("--trials="):
            trials = int(arg.split("=", 1)[1])
        elif arg.startswith("--target="):
            target_model = arg.split("=", 1)[1]
        elif arg == "--family":
            family = "testsingle_main"

    ensure()
    d = run_dir_for(model)
    snapshot_config(d)
    log_path = os.path.join(d, "run.log")

    lines = []
    started = ts()
    lines.append(f"TestSingle run: model={model} provider={provider} trials={trials} started={started}")
    if target_model:
        lines.append(f"TestSingle target model: {target_model}")
    con = None
    run_id = None
    try:
        con = db_connect()
        run_id = db_insert_run(con, model, provider, family, started, target_model)
        db_insert_snapshot(con, run_id, os.path.join(d, "config_snapshot.yaml"))

        trial_fn = {
            "openrouter": trial_openrouter,
            "nous": trial_nous,
            "local": trial_local,
        }[provider]

        results = []
        for i in range(1, trials + 1):
            ok, detail, lat = trial_fn(model if provider != "nous" else model, i)
            results.append((ok, detail, lat))
            db_insert_trial(con, run_id, i, ok, detail, lat)
            lines.append(f"  trial {i}: {detail}")

        passes = sum(1 for ok, _, _ in results if ok)
        v = verdict(passes, trials)
        detail = " | ".join(d for _, d, _ in results)
        lines.append(f"RESULT verdict={v} passes={passes}/{trials} detail={detail}")
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        append_model_cap(stamp, model, provider, family, trials, passes, v, detail)
        db_update_run(con, run_id, ts(), v, "completed", detail)
        write_json(os.path.join(d, "verdict.json"), {
            "model": model,
            "provider": provider,
            "target_model": target_model,
            "trials": trials,
            "passes": passes,
            "verdict": v,
            "detail": detail,
            "started_at": started,
            "finished_at": ts(),
            "status": "completed",
        })
        write_text(log_path, "\n".join(lines) + "\n")
        print("\n".join(lines))
    except Exception as e:
        tb = traceback.format_exc()
        lines.append(f"CRASH {type(e).__name__}: {e}\n{tb}")
        if con is None:
            con = db_connect()
        if run_id is None:
            run_id = db_insert_run(con, model, provider, family, started, target_model)
        db_update_run(con, run_id, ts(), None, "crashed", f"{type(e).__name__}: {e}")
        write_text(log_path, "\n".join(lines) + "\n")
        write_json(os.path.join(d, "crash.json"), {
            "model": model,
            "provider": provider,
            "target_model": target_model,
            "error": f"{type(e).__name__}: {e}",
            "traceback": tb,
            "started_at": started,
            "finished_at": ts(),
            "status": "crashed",
        })
        print("\n".join(lines))
        sys.exit(1)
    finally:
        if con:
            con.close()

if __name__ == "__main__":
    main()
