# Tasks: Haze CLI Provider (stream-json compatible)

**Input**: Design documents from `/specs/006-haze-cli-provider/`

**Prerequisites**: plan.md, spec.md (3 user stories), research.md (R1–R9), data-model.md, contracts/

**Tests**: INCLUDED — the spec mandates them (FR-010: recorded-sample tests, never a live CLI) and the constitution gates commits on lint + tests. Write each story's tests first and watch them fail before implementing.

**Organization**: Grouped by user story. US1 (missions stream through haze) is the MVP; US2 (accounting/failure truth) and US3 (onboarding/docs) build on the same foundation and are independently testable.

**Conventions**: All pytest runs need `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest …`. Never invoke a real `haze` binary in tests — recorded fixtures only. No haze-specific edits outside `koan/app/provider/` except the two shape-based parser extensions (see contracts/haze-provider-contract.md "contract of restraint").

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

**Purpose**: Branch, contract-first durable spec change, shared fixtures

- [ ] T001 Create feature branch `koan.atoomic/006-haze-cli-provider` off up-to-date `main` (never commit to main; constitution I)
- [ ] T002 Contract-first durable spec update in `specs/components/providers.md`: add haze to the provider registry enumeration and a capability row (stream-json: yes; resume: no; usage: envelope-based camelCase translation in shared parsers; quota/auth: generic multi-backend patterns; lock: `haze-cli`; stdin prompt: flag-removal rewrite). Commit this FIRST and standalone so the architectural change is reviewable before code (constitution II; PR must check "Architectural change"; `scripts/spec_change_guard.py` enforces)
- [ ] T003 [P] Create shared recorded-fixture module `koan/tests/haze_samples.py`: verbatim haze ≥0.7.0 output samples per contracts/haze-cli-interface.md — complete stream-json transcript (turn_start → message/tool events → turn_end → result envelope with camelCase usage), `failed` envelope, `aborted` envelope, quota-style stderr/stdout error samples, auth-style samples, benign-prose-mentioning-"rate limit" success sample, mid-stream-truncated transcript. Use only generic placeholder content (no private identifiers; constitution V)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The provider module skeleton + registry entry every story depends on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T004 Create `koan/app/provider/haze.py` with `HazeProvider(CLIProvider)` skeleton per contracts/haze-provider-contract.md: `name = "haze"`, `binary()` (`_binary_override` → `"haze"`), `is_available()`, `invocation_lock_name() = "haze-cli"`, capability declarations (`supports_stream_json() = True`, `supports_session_resume() = False`, `supports_system_prompt_file() = False`, `supports_last_message_file() = False`, `has_api_quota() = True`), module docstring citing the haze ≥0.7.0 contract
- [ ] T005 Register haze in `koan/app/provider/__init__.py`: top-level import + `_PROVIDERS["haze"] = HazeProvider` (single source of truth — `known_providers()`, config validation, dashboard forms, per-role `cli:` resolution all derive)

**Checkpoint**: `from app.provider import get_provider` resolves `KOAN_CLI_PROVIDER=haze`; user stories can start

---

## Phase 3: User Story 1 — Run missions through the haze harness (Priority: P1) 🎯 MVP

**Goal**: Missions execute end-to-end through haze with live streamed progress feeding the existing watchdog/stagnation machinery — zero agent-loop changes

**Independent Test**: Recorded-transcript replay through `run_command_streaming()` produces per-event progress lines, the final result text, and no loop modifications; live tier-2 mission run per quickstart.md

### Tests for User Story 1 (write first, watch fail)

- [ ] T006 [P] [US1] Command-construction tests in `koan/tests/test_haze_provider.py`: `build_command` composition (`haze [-m sel] --output stream-json -p <prompt>`), model arg only when set, fallback-model warn+skip, output-format mapping (`stream-json`/`json`/empty), system prompt prepended to prompt text, `system_prompt_file`/`resume_session_id` warn+skip, unsupported builders (tools/MCP/plugins/max-turns/effort) return `[]` with `log_safe` warning — mirror `test_codex_provider.py` structure
- [ ] T007 [P] [US1] Stdin-rewrite tests in `koan/tests/test_haze_provider.py`: `rewrite_prompt_for_stdin` removes `-p <prompt>` entirely and returns the prompt; no `-p` present → `(cmd, None)`; `-p` as last token without value → `(cmd, None)`; `supports_stdin_prompt_passing()` is True; integration with `cli_exec.prepare_prompt_file` yields argv without `-p` and a prompt temp file
- [ ] T008 [P] [US1] Streaming replay test in `koan/tests/test_haze_provider.py`: feed the recorded transcript from `koan/tests/haze_samples.py` through `run_command_streaming()` (mock `app.cli_exec.popen_cli` to emit the fixture lines; never a real subprocess) and assert: one `[cli]` summary line per event (watchdog liveness), final return value equals the envelope's `result`, truncated-transcript fixture falls back to accumulated `message_end` text (non-hidden only)

### Implementation for User Story 1

- [ ] T009 [US1] Implement flag builders + `build_command()` in `koan/app/provider/haze.py` per the contract table (binary → model → output → prompt ordering; prompt args last)
- [ ] T010 [US1] Implement `supports_stdin_prompt_passing() = True` + the flag-removal `rewrite_prompt_for_stdin()` override in `koan/app/provider/haze.py` (haze reads stdin only when `-p` is absent; base marker substitution would send the literal marker as the prompt — see research.md R3)
- [ ] T011 [US1] Extend `_summarize_stream_event()` in `koan/app/provider/__init__.py` with haze's shape-keyed event vocabulary per data-model.md treatment column: `turn_start`/`turn_end`(+status), `message_start`, `message_update` (cheap tag — high frequency), `message_end` (first-line text preview, skip hidden), `tool_start`/`tool_end` (name/success/durationMs/error), `retry`, `context_overflow`. Shape-based only — no provider-name checks (constitution IV)
- [ ] T012 [US1] Extend `_extract_assistant_text_chunks()` in `koan/app/provider/__init__.py` to collect non-hidden `message_end` text keyed by `id` (never `message_update` — cumulative duplicates), and add `turn_end` non-`complete` status + `context_overflow` `recovered:false` as error-preview candidates for `_format_cli_error()` context

**Checkpoint**: US1 tests green; a recorded mission transcript streams, summarizes, and returns the right text — MVP demonstrable via quickstart Tier 2 steps 1–3

---

## Phase 4: User Story 2 — Accurate accounting & failure classification (Priority: P2)

**Goal**: camelCase usage lands in both accounting pipelines; `failed`/`aborted` never report success; quota/auth classified without false positives

**Independent Test**: Replay recorded success/failure/quota/auth samples through the parsers and detectors; assert recorded totals and classifications (SC-002/003/004)

### Tests for User Story 2 (write first, watch fail)

- [ ] T013 [P] [US2] Stream-usage tests in `koan/tests/test_haze_provider.py`: `_usage_snapshot_from_event()` on the fixture envelope returns the mapped snapshot (camelCase → snake_case per data-model.md table incl. cache-read subtraction and reasoningTokens folding); replay test asserts `KOAN_STREAM_USAGE_FILE` sidecar contents; all-zero usage → no snapshot, no crash
- [ ] T014 [P] [US2] Mission-stdout usage tests in `koan/tests/test_token_parser.py`: `extract_tokens`/`extract_tokens_detailed` on a camelCase envelope dict and on a full NDJSON transcript (last usage-bearing event wins) return correct totals; snake_case behavior unchanged (regression guard)
- [ ] T015 [P] [US2] Detection-table tests in `koan/tests/test_haze_provider.py`: `detect_quota_exhaustion` positives (429, insufficient quota, billing, retry-after in stderr; error-marker stdout lines with exit≠0), negatives (benign prose mentioning "rate limit" with exit 0 — SC-003), `detect_auth_failure` positives (401, invalid api key) and exit-0 gating; status mapping — `failed`/`aborted` envelopes with exit≠0 surface as failure through the streaming path (RuntimeError/error path, never success — SC-004)

### Implementation for User Story 2

- [ ] T016 [US2] Add the camelCase usage branch to `_usage_snapshot_from_event()` in `koan/app/provider/__init__.py` (shape-keyed on `inputTokens`/`outputTokens` presence; mapping per data-model.md; feeds existing `_persist_stream_usage_snapshot` unchanged)
- [ ] T017 [US2] Add the same camelCase shape to `koan/app/token_parser.py` dict + JSONL extraction (mission-stdout path via `usage_estimator.cmd_update`; keep snake_case branches untouched)
- [ ] T018 [US2] Implement `_HAZE_QUOTA_PATTERNS`/`_HAZE_AUTH_PATTERNS` + `detect_quota_exhaustion()`/`detect_auth_failure()` in `koan/app/provider/haze.py`, salvaged from PR #2211's reviewed patterns: stderr trusted fully; stdout only when exit≠0 AND `_line_has_error_marker()` passes (research.md R7)
- [ ] T019 [US2] Implement `check_quota_available()` in `koan/app/provider/haze.py`: minimal `--output json` "ok" probe via `run_cli` (stdin piping + invocation lock honored), classified through the two detectors; ANY probe error/timeout → `(True, "")`; add probe tests (mock `subprocess.run`/`run_cli`, incl. timeout → available) in `koan/tests/test_haze_provider.py`

**Checkpoint**: US1 + US2 independently green; usage and failure truth verified against recorded samples (providers.md change-protocol requirement)

---

## Phase 5: User Story 3 — Operator onboarding, visibility & documentation (Priority: P3)

**Goal**: haze is discoverable, validated, and documented everywhere providers surface

**Independent Test**: `is_known_provider("haze")` true across surfaces; onboarding lists haze; the doc alone takes an operator install → first mission (SC-006)

### Tests for User Story 3 (write first, watch fail)

- [ ] T020 [P] [US3] Registry/resolution tests: `known_providers()` contains `"haze"` + instantiation via registry in `koan/tests/test_provider_modules.py`; `KOAN_CLI_PROVIDER=haze` resolution + per-role `cli:` acceptance in `koan/tests/test_cli_provider.py`

### Implementation for User Story 3

- [ ] T021 [US3] Add haze to `koan/app/onboarding.py`'s three literal provider structures: both provider→binary maps (`"haze": "haze"`) and the choice list (`("haze", "haze (multi-backend agentic CLI)")`) — these do not derive from the registry (research.md R8)
- [ ] T022 [P] [US3] Write `docs/providers/haze.md` following the established provider-doc structure (Quick Setup: npm install ≥0.7.0 → in-haze `/provider` + `/model` config → `cli_provider: haze` → verify; Model Configuration incl. `provider:model` selector format + per-project overrides; Tool Configuration: fixed toolset, koan tool restrictions warn-and-skip; Advanced: custom binary, per-role `cli:`, stdin prompt delivery, probe token cost; Troubleshooting: "No model provider configured", bad selector, <0.7.0 version error), and link it from `docs/providers/index.md`
- [ ] T023 [P] [US3] Add the haze provider mention to `docs/users/user-manual.md` (parity with other providers' mentions)

**Checkpoint**: All three stories independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T024 Run `make lint` and the full suite `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/` — zero regressions (SC-005); fix anything surfaced
- [ ] T025 Run quickstart.md Tier 2 live validation (haze installed): direct contract check, provider selection, real mission with live progress + usage delta comparison, bogus-model failure classification. Record outcomes honestly in the PR body (constitution VII)
- [ ] T026 [P] Wiki bookkeeping via `/brain sync` (frontmatter + index entries for `docs/providers/haze.md` and touched pages)
- [ ] T027 Pre-PR hygiene: leak-pattern check (`instance/.leak-patterns` diff filter) returns empty; confirm `.specify/feature.json` is NOT in `git diff --name-only main..`; verify the T002 spec commit sits first
- [ ] T028 Open draft PR against `Anantys-oss/koan` (push branch to `origin` fork, `gh pr create --repo Anantys-oss/koan --head atoomic:<branch>`): body references issue #2206, explains the PR #2211 drift/supersession, and has the "Architectural change" box CHECKED. Then close legacy PR #2211 with a comment linking the new PR (FR-012/SC-007)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: T001 first; T002 immediately after (first commit); T003 parallel with T002
- **Foundational (Phase 2)**: T004 → T005; blocks all stories
- **US1 (Phase 3)**: after Phase 2. Tests T006–T008 parallel; T009–T010 then T011–T012 (T011/T012 are different concerns in the same file — sequential)
- **US2 (Phase 4)**: after Phase 2 (independent of US1's parser work except sharing `provider/__init__.py` — coordinate T016 with T011/T012 edits). Tests T013–T015 parallel; T016/T017 parallel (different files); T018 → T019
- **US3 (Phase 5)**: after Phase 2 only. T020–T023 all parallelizable except T021 vs T020 ordering is free
- **Polish (Phase 6)**: after all stories; T024 → T025 → T027 → T028; T026 parallel with T025

### Parallel Opportunities

- T002 ∥ T003; T006 ∥ T007 ∥ T008; T013 ∥ T014 ∥ T015; T016 ∥ T017; T020 ∥ T022 ∥ T023; T026 ∥ T025
- US3 can proceed fully in parallel with US1/US2 (different files, except T020's test files which US2 doesn't touch)

## Implementation Strategy

**MVP first**: Phases 1–3 (T001–T012) deliver a demonstrable haze mission run with live progress. **Stop and validate** with the streaming replay test + quickstart Tier 2 steps 1–3 before continuing. Then US2 (accounting truth — required before any real quota-bearing use), then US3, then polish + PR. Commit after each logical group; every commit lint-clean and test-green. Single-developer sequential order: T001→T028 as numbered.

## Notes

- The "contract of restraint" is a hard gate: if any task seems to require editing `run.py`, `stagnation_monitor.py`, `quota_handler.py`, `reset_parser.py`, or `cli_errors.py`, STOP — the design is being violated; re-read contracts/haze-provider-contract.md
- `token_parser.py` camelCase work (T017) must not disturb existing snake_case extraction — T014's regression guard exists for exactly this
- Fixtures in `koan/tests/haze_samples.py` are the "recorded sample" the providers.md change protocol demands — keep them verbatim-shaped (field names, casing, event order) per contracts/haze-cli-interface.md
