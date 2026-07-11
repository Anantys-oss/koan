# Implementation Plan: Haze CLI Provider (stream-json compatible)

**Branch**: `koan.atoomic/006-haze-cli-provider` (to be created at implementation) | **Date**: 2026-07-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-haze-cli-provider/spec.md`

## Summary

Add haze (https://github.com/DenizOkcu/haze) as a first-class Kōan CLI provider, built against haze's **current** headless contract (≥ 0.7.0): one-shot `-p`-less stdin prompt intake, `--output stream-json` NDJSON progress events, and a terminal `{type:"result", status, result, usage}` envelope with camelCase usage fields. The provider rides Kōan's **existing** stream-json plumbing (`supports_stream_json() = True`) so the agent loop needs **zero** provider-specific branches — deliberately discarding legacy PR #2211's `emits_incremental_progress()` capability and its ~239-line `run.py` workaround layer, which predate haze's streaming support. Core work: a new `HazeProvider`, camelCase usage translation in the two shared token-parsing paths, haze event shapes in the shared stream summarizer/text extractors, registry + onboarding + docs surfaces, and closing legacy PR #2211 with a cross-link when the replacement PR opens.

## Technical Context

**Language/Version**: Python 3.11+ (no post-3.11 syntax/stdlib; CI tests multiple versions)

**Primary Dependencies**: stdlib only (`subprocess`, `json`, `re`, `shutil`); haze CLI ≥ 0.7.0 as external runtime dependency (npm `@denizokcu/haze`, current 0.8.0)

**Storage**: N/A (usage snapshots flow through existing `KOAN_STREAM_USAGE_FILE` sidecar and `usage_state.json` mechanisms; no new state)

**Testing**: pytest with `KOAN_ROOT=/tmp/test-koan`; recorded haze output fixtures; never a live CLI subprocess (constitution Testing discipline)

**Target Platform**: macOS/Linux daemon host (wherever Kōan's run loop executes)

**Project Type**: Provider plugin inside an existing Python daemon (`koan/app/provider/`)

**Performance Goals**: No regression to the agent loop; stream events rendered line-by-line as they arrive (watchdog liveness signal ≤ 1 line per event)

**Constraints**: Provider Isolation (constitution IV) — no `if provider == "haze"` anywhere outside `koan/app/provider/`; prompt delivery via stdin (spec clarification 2026-07-10); quota probe consumes a few tokens and must never block work; Linux `MAX_ARG_STRLEN` (~128KB/arg) is the reason argv prompt passing is rejected

**Scale/Scope**: 1 new provider module (~200 lines), ~4 shared-parser touch points, 1 new test module + extensions to ~4 existing test modules, 1 docs page, 1 durable spec contract update (declared architectural)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Status |
|---|---|---|
| I. Human Authority | Work lands on a `koan.atoomic/*` branch; draft PR; no merge/auto-merge changes | PASS — no change to merge behavior; PR #2211 closure is a human-visible comment + close, not a merge |
| II. Specs Are the Source of Truth | `specs/components/providers.md` read before design (done); its update is contract-first and DECLARED (checked "Architectural change" box in PR body) | PASS — contract delta identified in Phase 1; `scripts/spec_change_guard.py` will enforce declaration |
| III. Local Files by Default | No new state; reuses stream-usage sidecar + `usage_state.json` via existing writers | PASS |
| IV. Provider Isolation | All haze behavior inside `koan/app/provider/haze.py`; shared parsers extended with *shape-based* (not provider-named) branches; no loop branching | PASS — this is the core design driver (reject PR #2211's loop workarounds) |
| V. Untrusted Inputs, Audited Outputs | Quota/auth detection reads stderr + gated stdout error lines, never trusts assistant prose on success; no private identifiers in fixtures | PASS |
| VI. Single Writer, Single Read Path | Provider resolution via existing registry/accessors; no new config read paths | PASS |
| VII. Simplicity | Extends existing mechanisms (registry, stream parsers, cline-style probe) instead of new capability flags | PASS — Complexity Tracking empty |

**Post-Phase-1 re-check**: PASS — design introduces no new state, no new config paths, no loop branches; the only durable-contract change (providers.md registry + haze row) is declared.

## Project Structure

### Documentation (this feature)

```text
specs/006-haze-cli-provider/
├── plan.md              # This file
├── spec.md              # Feature specification (+ Clarifications 2026-07-10)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── haze-cli-interface.md      # External contract: what haze ≥0.7.0 guarantees
│   └── haze-provider-contract.md  # Internal contract: HazeProvider vs CLIProvider ABC
├── checklists/requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
koan/app/provider/
├── haze.py                    # NEW — HazeProvider
├── __init__.py                # registry entry + haze event shapes in shared parsers
└── base.py                    # unchanged (existing hooks suffice — no new capability)

koan/app/
├── token_parser.py            # camelCase usage shapes (mission stdout path)
├── onboarding.py              # haze in provider menus/detection maps
└── (run.py, quota_handler.py, mission_executor.py, …)  # UNCHANGED — explicit non-goal

koan/tests/
├── test_haze_provider.py      # NEW — flag building, stdin rewrite, detection, probe
├── test_provider_modules.py   # registry/known-provider assertions
├── test_cli_provider.py       # resolution assertions
└── test_token_parser.py       # camelCase usage fixtures

docs/providers/
├── haze.md                    # NEW — setup/models/capabilities/troubleshooting
└── index.md                   # link

docs/users/user-manual.md      # provider mention (parity with other providers)
specs/components/providers.md  # DURABLE CONTRACT — contract-first, declared
```

**Structure Decision**: Single provider module following the established `koan/app/provider/<name>.py` pattern (closest analogs: `cline.py` for multi-backend detection patterns, `codex.py` for custom stdin rewrite). Shared-parser changes are shape-based additions to `provider/__init__.py` and `token_parser.py`, exactly where equivalent Claude/Codex shapes already live.

## Complexity Tracking

No constitution violations — table intentionally empty. Notable *rejected* complexity (Principle VII "document what we chose not to do"): PR #2211's `emits_incremental_progress()` capability + run.py stagnation bypass (obsoleted by haze 0.7.0 stream-json); a `--output json` non-streaming fallback mode for haze < 0.7.0 (out of scope per spec assumption); per-tool restriction emulation (haze has no such flags — warn-and-skip per FR-008).
