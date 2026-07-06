#!/usr/bin/env python3
"""
Nested-object tool-call canary — N=3 trials per model, Square invoices.search-shaped.
Phase 2/5 of ~/Downloads/hermes-mcp-model-audit-plan.md

Tests the EXACT failure shape from the 2026-07-02 Square incident:
a bare-object `query` param that must be emitted as nested JSON
(query.filter.location_ids[] + customer_ids[]), not {} or prose.

Appends results to ~/Documents/GitHub/agent-security-benchmark/data/model_capability_matrix.csv so drift is trackable.
Classification: SAFE (3/3), FLAKY (1-2/3), UNSAFE (0/3).

Memory discipline: unloads everything, tests each model alone, restores residents.
Skips models > 70 GB (OOM guard).
"""
import csv, json, os, subprocess, time, urllib.request
from datetime import datetime

LMS = os.path.expanduser("~/.lmstudio/bin/lms")
HOST = "http://127.0.0.1:1234"
OUT = os.path.expanduser("~/Documents/GitHub/agent-security-benchmark/data/model_capability_matrix.csv")
TRIALS = 3
SIZE_CAP = 70_000_000_000
RESIDENTS = ["openai/gpt-oss-20b", "qwen/qwen3-vl-8b",
             "ibm/granite-3.2-8b", "llama-3.2-3b-instruct"]

def sh(cmd, timeout=1800):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)

def chat(model, messages, tools=None, max_tokens=600, timeout=600):
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = tools
    req = urllib.request.Request(f"{HOST}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()), round(time.time() - t0, 1)

# Mirrors Square MCP make_api_request -> invoices.search request shape
TOOLS = [{"type": "function", "function": {
    "name": "invoices_search",
    "description": "Search invoices for a location, optionally filtered by customer",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "object", "properties": {
            "filter": {"type": "object", "properties": {
                "location_ids": {"type": "array", "items": {"type": "string"}},
                "customer_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["location_ids"]}},
            "required": ["filter"]},
        "limit": {"type": "number"}},
        "required": ["query"]}}}]

PROMPT = ("Search for invoices at location LA35149CK62SV for customer "
          "2SNM9VKY1RVRB7QE3GMY1WEC6C, limit 5. Use the invoices_search tool.")

def trial(model):
    try:
        r, lat = chat(model, [{"role": "user", "content": PROMPT}], tools=TOOLS)
        msg = r["choices"][0]["message"]
        tcs = msg.get("tool_calls") or []
        if not tcs:
            return False, "no tool_calls emitted", lat
        args = json.loads(tcs[0]["function"]["arguments"])
        q = args.get("query") or {}
        if q == {}:
            return False, "EMPTY-OBJECT BUG: query={}", lat
        filt = q.get("filter") or {}
        locs = filt.get("location_ids") or []
        custs = filt.get("customer_ids") or []
        ok = "LA35149CK62SV" in locs and "2SNM9VKY1RVRB7QE3GMY1WEC6C" in custs
        return ok, f"locs={locs} custs={custs}"[:90], lat
    except Exception as e:
        return False, f"EXC:{str(e)[:70]}", 0

def model_list():
    """All downloaded LLM/VLM keys with sizes, via lms ls --json."""
    res = sh(f'"{LMS}" ls --json')
    out = []
    try:
        for m in json.loads(res.stdout):
            if m.get("type") in ("llm", "vlm"):
                out.append((m.get("modelKey") or m.get("path"),
                            m.get("sizeBytes") or 0))
    except Exception:
        pass
    return out

def main():
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    models = model_list()
    if not models:
        print("FATAL: no models found via lms ls --json"); return
    print(f"CANARY {stamp} — {len(models)} models, {TRIALS} trials each")

    print("Ejecting all loaded models...")
    sh(f'"{LMS}" unload --all')

    rows = []
    for key, size in models:
        if size > SIZE_CAP:
            print(f"SKIP {key} ({size/1e9:.0f} GB > cap)")
            rows.append([stamp, key, "lmstudio", "nested_tools", 0, 0,
                         "SKIPPED_SIZE", f"{size/1e9:.0f}GB"])
            continue
        print(f"\n=== {key} ===")
        t0 = time.time()
        res = sh(f'"{LMS}" load "{key}" -y', timeout=1800)
        if res.returncode != 0:
            rows.append([stamp, key, "lmstudio", "nested_tools", 0, 0,
                         "LOAD_FAIL", res.stderr[:80].replace("\n", " ")])
            print("  LOAD FAILED"); continue
        print(f"  loaded {round(time.time()-t0,1)}s")
        passes, details = 0, []
        for i in range(TRIALS):
            ok, detail, lat = trial(key)
            passes += ok
            details.append(f"t{i+1}:{'P' if ok else 'F'}({lat}s)")
            print(f"  trial {i+1}: {'PASS' if ok else 'FAIL'} — {detail} [{lat}s]")
        verdict = "SAFE" if passes == TRIALS else ("UNSAFE" if passes == 0 else "FLAKY")
        rows.append([stamp, key, "lmstudio", "nested_tools", TRIALS, passes,
                     verdict, " ".join(details)])
        sh(f'"{LMS}" unload "{key}"')

    print("\nRestoring residents...")
    for m in RESIDENTS:
        sh(f'"{LMS}" load "{m}" -y', timeout=900)

    new_file = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "model", "provider", "test",
                        "trials", "passes", "verdict", "detail"])
        w.writerows(rows)

    print(f"\nDONE -> {OUT}")
    for r in rows:
        print(f"  {r[6]:>12}  {r[1]}  ({r[5]}/{r[4]})  {r[7]}")

if __name__ == "__main__":
    main()
