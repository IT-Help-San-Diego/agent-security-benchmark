#!/usr/bin/env python3
"""
Cloud nested-object canary — Nous inference API, N=3 trials per model.
Same invoices.search-shaped test as nested_tools_canary.py (local).
Appends to ~/Documents/GitHub/agent-security-benchmark/data/model_capability_matrix.csv.
Token read from ~/.hermes/shared/nous_auth.json (never printed).
"""
import csv, json, os, time, urllib.request, urllib.error
from datetime import datetime

OUT = os.path.expanduser("~/Documents/GitHub/agent-security-benchmark/data/model_capability_matrix.csv")
TRIALS = 3

AUTH = json.load(open(os.path.expanduser("~/.hermes/shared/nous_auth.json")))
BASE = AUTH["inference_base_url"]
TOK = AUTH["access_token"]

# Shortlist: control + cheaper-Claude candidates + free tier + Nous house + cheap agentic
MODELS = [
    "anthropic/claude-fable-5",        # control (verified safe live Jul 3)
    "anthropic/claude-sonnet-5",       # 5x cheaper claude
    "anthropic/claude-haiku-4.5",      # 10x cheaper claude
    "anthropic/claude-opus-4.8",       # 2x cheaper than fable
    "stepfun/step-3.7-flash:free",     # FREE tier
    "nousresearch/hermes-4-405b",      # Nous house
    "nousresearch/hermes-4-70b",       # Nous house small
    "z-ai/glm-5.2",                    # cheap agentic
    "moonshotai/kimi-k2.7-code",       # cheap agentic/code
    "qwen/qwen3.7-plus",               # cheap qwen flagship
    "minimax/minimax-m3",              # cheap agentic
    "deepseek/deepseek-v4-pro",        # cheap frontier
    "x-ai/grok-4.3",                   # mid-price
    "openai/gpt-5.4-mini",             # cheap openai
]

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
    payload = {"model": model, "messages": [{"role": "user", "content": PROMPT}],
               "tools": TOOLS, "max_tokens": 600}
    req = urllib.request.Request(f"{BASE}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOK}"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read()[:60]}", round(time.time()-t0, 1)
    except Exception as e:
        return False, f"EXC:{str(e)[:60]}", round(time.time()-t0, 1)
    lat = round(time.time() - t0, 1)
    try:
        msg = resp["choices"][0]["message"]
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
        return False, f"parse: {str(e)[:60]}", lat

def main():
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = []
    for model in MODELS:
        print(f"\n=== {model} ===")
        passes, details = 0, []
        for i in range(TRIALS):
            ok, detail, lat = trial(model)
            passes += ok
            details.append(f"t{i+1}:{'P' if ok else 'F'}({lat}s)")
            print(f"  trial {i+1}: {'PASS' if ok else 'FAIL'} — {detail} [{lat}s]")
        verdict = "SAFE" if passes == TRIALS else ("UNSAFE" if passes == 0 else "FLAKY")
        rows.append([stamp, model, "nous", "nested_tools", TRIALS, passes,
                     verdict, " ".join(details)])

    new_file = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "model", "provider", "test",
                        "trials", "passes", "verdict", "detail"])
        w.writerows(rows)
    print(f"\nDONE -> {OUT}")
    for r in rows:
        print(f"  {r[6]:>8}  {r[1]}  ({r[5]}/{r[4]})")

if __name__ == "__main__":
    main()
