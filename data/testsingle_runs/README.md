# TestSingle Run — Quick Resume Protocol

This directory contains one complete TestSingle run for a single model under a cloud-assisted protocol.

## Run layout
- `config_snapshot.yaml` — Hermes config at test time
- `run.log` — stdout/stderr from the test runner
- `verdict.json` — parsed pass/fail result if test completed
- `crash.json` — present ONLY if the test aborted/errored

## Canonical source of truth
- `data/agent_security_benchmark.sqlite` — canonical result DB for all runs/trials
- `data/model_capability_matrix.csv` — append-only legacy summary
- This README — resumes the current run without re-explaining context

## How to resume without re-explaining everything
1. Open the latest run directory under `data/testsingle_runs/`.
2. Read `verdict.json` or `run.log`. The last line tells you the verdict.
3. If `crash.json` exists, read its `error` field. That is the exact blocker.
4. If this is a `provider=openrouter` run and `openrouter.api_key` is missing in `~/.hermes/config.yaml`, do NOT rerun until the key is added.
5. If you want a safe immediate path, use `provider=local` against LM Studio.

## Current state
- Test date: 2026-07-05
- Last run: `data/testsingle_runs/20260705_201650_openai_gpt-oss-20b`
- Last result: crash.json written; blocker = `openrouter.api_key missing` from local harness
- Canonical DB: `data/agent_security_benchmark.sqlite`

## Next action
Do NOT rerun the same command blindly.
Choose one:
- A) Add OpenRouter API key to Hermes config, then rerun
- B) Run local-only: `python3 scripts/testsingle_runner.py openai/gpt-oss-20b --provider local --trials 3`

After any run, inspect `data/testsingle_runs/README.md` first in future sessions.
