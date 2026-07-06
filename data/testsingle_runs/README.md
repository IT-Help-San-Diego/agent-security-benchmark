# TestSingle Run — Quick Resume Protocol

This directory contains one complete TestSingle run for a single local model.

## Run layout
- `config_snapshot.yaml` — Hermes config at test time
- `run.log` — stdout/stderr from the test runner
- `verdict.json` — parsed pass/fail result (present if test completed)
- `crash.json` — present ONLY if the test aborted/errored

## How to resume without re-explaining everything
1. Open `verdict.json` or `run.log`. The last line tells you the verdict.
2. If `crash.json` exists, read its `error` field — that is the exact blocker.
3. If verdict is `SAFE`/`FLAKY`/`UNSAFE`, the test completed. See `Next action` below.

## Current state
- Test date: 2026-07-05
- Current blocker: `~/.hermes/config.yaml` has no `openrouter.api_key`
- Last attempted command: `python3 scripts/testsingle_runner.py openai/gpt-oss-20b --provider openrouter --trials 3`
- Last result: crash.json written; all trials errored before sending request

## Next action
Do NOT rerun the same command yet.
First choose one:
- A) Add OpenRouter API key to Hermes config, then rerun
- B) Switch this run to a provider that is already configured (nous), using a model slug from `nested_tools_canary_cloud.py`

Then run: `python3 scripts/testsingle_runner.py <model_key> --provider <openrouter|nous> --trials 3`
