# Quickstart: Validating the Haze CLI Provider

**Spec**: [spec.md](./spec.md) | **Contracts**: [contracts/](./contracts/)

Two validation tiers: the automated suite (no haze required — recorded fixtures only) and a live end-to-end pass (haze installed and configured).

## Prerequisites

- Kōan dev setup: `make setup` (creates `.venv`)
- Automated tier: nothing else
- Live tier: `npm install -g @denizokcu/haze` (≥ 0.7.0; verify `haze --version` ≥ 0.7.0), then inside `haze` run `/provider` to add a backend + API key and `/model` to pick a model

## Tier 1 — Automated (required before commit)

```bash
make lint
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_haze_provider.py -v
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_provider_modules.py \
  koan/tests/test_cli_provider.py koan/tests/test_token_parser.py -v
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/   # full suite: zero regressions (SC-005)
```

Expected: all pass. The haze suite must cover the recorded-transcript replay (NDJSON fixture through `run_command_streaming`: summaries printed, result text extracted, camelCase usage persisted to the sidecar — SC-001/SC-002), detection tables (quota pause / auth / benign-prose negatives — SC-003), and status mapping (`failed`/`aborted` never success — SC-004).

## Tier 2 — Live end-to-end (before opening the PR)

1. **Direct contract check** (validates the external contract still holds):
   ```bash
   echo "Reply with exactly: pong" | haze --output stream-json | tee /tmp/haze-transcript.jsonl
   ```
   Expect NDJSON events ending in a `{"type":"result","status":"complete",...}` line with all five camelCase usage fields; exit code 0.
2. **Provider selection**: in a scratch KOAN_ROOT, set `cli_provider: "haze"` in `config.yaml` (or `KOAN_CLI_PROVIDER=haze`); `make status` / onboarding shows haze detected.
3. **Mission run**: queue a trivial mission (`make say m="..."` or edit missions.md per your instance), `make run`, and observe: live `[cli]` progress lines during the run (P1), mission finalized Done with result text, usage recorded (compare `usage.md` delta against the envelope totals — SC-002).
4. **Failure path**: temporarily break the haze model config (select a bogus model via `-m` per-project model override) and confirm the mission fails with a precise error, classified as launch/config — not quota (edge case).

## Documentation validation (SC-006)

Follow `docs/providers/haze.md` from a clean machine-state reading: it alone must take an operator from install → configured → first mission.

## PR gates (SC-007 + constitution)

- Branch `koan.atoomic/*`, draft PR, "Architectural change" box **checked** (providers.md delta) — `scripts/spec_change_guard.py` green.
- `.specify/feature.json` NOT in the diff; leak-pattern pre-commit check clean.
- On PR creation: comment on + close legacy PR #2211 linking the new PR; reference issue #2206 in the PR body.
