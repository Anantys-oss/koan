# Incubating changelog

Running journal of what has landed on `incubating` since the last stable release.

Each entry is appended by `/koan.incubate` under the `## ${NEXT}` heading when
`main` is merged into `incubating`. When a release is dispatched, `release.yml`
replaces `${NEXT}` with the version + date, writes the result to `CHANGES.md`,
and resets this file to an empty `## ${NEXT}` section on top of the released
history.

Do not hand-edit released entries — they are the source for `CHANGES.md`.

---

## ${NEXT}

### Merged 2026-07-18 — main @ 51bba29c (95 commits)

**Features**
- **Unified artifact DB schema + migration harness** (#2193).
- **Chat priority lane** — keep chat responsive during missions (#1084, #2436).
- **`/rebase` split** — rebase-only default with an explicit `/rebase --fix` for the review-feedback leg (#2439).
- **Grok Build CLI provider** — new provider with stream shapes, recorded samples, and unit tests.
- **Fail-closed fake CLI provider foundation** — hidden from the dashboard picker; refusal hint derived from the registry.
- **GitHub NL @mention intent ladder** — promote natural-language mentions to named skills (#2402).
- **Per-role MCP opt-in** across execution roles (#2422).
- **Release pipeline unification** — `incubate → incubating → release.yml`; ships `/koan.incubate` and `/koan.release` skills, `${NEXT}` changelog token, and the `latest` tag flow.
- **Dashboard** — structured live mission timeline on `/progress` (enriched `/api/progress`); edit `github_url` and auto-merge settings via allow-listed `PATCH /v1/projects/<name>`.
- **`/claudemd <project>`** — managed CLAUDE.md learnings-sync block; `/rereview` + `/re_review` aliases; degraded no-mission mode when the CLI binary is missing at startup; `/models` slots annotated with per-role CLI; configurable label to pause automatic PR reviews.

**Refactors / perf**
- Mission-store: extract shared SQLite connect + pending-select helpers (#2427).
- Extract shared config section/override resolver.
- Provider: hide fake from dashboard picker; derive refusal hint from registry.
- Delete orphaned `scripts/release.sh` (tag-from-main bypass removed).

**Fixes** — highlights
- Concurrency/safety: lock focus/passive read-check-act against expiry clobber; serialize pause/resume to close a TOCTOU double-resume race (#2437); skip re-delivered Telegram updates (double command output); dedup idempotent lifecycle notices across restarts (#2429); re-queue mission on verification failure (#2210).
- Grok: detect 403 credit/spending-limit exhaustion as quota; stop false burn-rate alerts and pauses; always-approve headless tools so `/implement` can commit.
- Misc: unwrap provider stream/json envelopes before tier parse; suppress project tooling at CLI for KOAN_ROOT sessions (#2404); resolve project skills via `find_by_command` (#2385); skip Slack thread replies addressed to another user; review verdict-consistency and graceful-degradation fixes.

**Docs / tests / CI**
- Specs updated contract-first for the `/rebase` split, chat priority lane (008), providers, bridge, git-github, skills, and web components; user manual + skills reference kept in sync; large test additions across run, skill dispatch, providers, and usage tracking.


### Merged 2026-07-13 — main @ bc71e88e (2 commits)

**Features**
- **GitHub "Running" indicator (#2365)** — new `mission_status.py`: applies a `koan:working` label and a `koan/mission` commit status while a mission runs, with hooks in `github.py`, `run.py`, `pr_submit.py`, and `startup_manager.py`; new config options; documented in `docs/architecture/github-and-trackers.md`, `docs/messaging/github-commands.md`, and `specs/components/git-github.md`.
- **Pretty `make logs` formatter (#2366)** — new `log_fmt.py` human-readable formatter with a `raw=1` Makefile bypass; new `docs/operations/log-formatting.md`.

**Docs / tests / CI**
- ~760 lines of new tests across 7 files covering both features; user manual, troubleshooting, and wiki index updated.

### Merged 2026-07-13 — main @ 51262b36 (35 commits)

**Features**
- **Bridge memory retention (#2360)** — fixes `awake.py` RSS ratcheting up over uptime: bounded caches/buffers in the bridge, memory-footprint monitoring, new `docs/architecture/bridge-memory.md` and `docs/operations/memory-footprint.md`.
- Authoritative mission outcomes: append-only `OutcomeStore`, `mission_outcome` façade (`classify_failure` + `record_outcome`), terminal outcome recorded at mission finalization and reported on `GET /v1/missions/{id}`.
- Experience capture: new `experience_capture` module hooked into `run_post_mission`, structured experience fields in memory entries, capture of stagnation-cap and CI-fix outcomes.
- CI: manual GHCR publish workflow, sharing build code with release.

**Refactors / perf**
- `resolve_workspace_dir` extracted as single source of truth for workspace resolution.

**Fixes** — highlights
- Security: CI shell-injection closed in dispatch-tag validation (bind tag to env var).
- API: stale outcomes no longer resurrect removed missions; outcome override gated to non-live missions; dropped outcome writes surfaced; narrowed finalize catch.
- Telegram: long `/report` HTML chunked at `<pre>` boundaries so delivery succeeds.
- Workspace: `add_project` clones into the resolved workspace dir; projects cache watches the resolved dir.
- Plan gate restored to fail-open for `/implement` (assumptions audit advisory); caveman mode disabled in `/explain`; wiki-check excludes generated `index.md`.

**Docs / tests / CI**
- Codex docs updated to GPT-5.6 model tiers; shared-workspace resolution contract documented; large test additions (experience capture, mission outcomes, locked file, plan runner, wiki check).

### Merged 2026-07-12 — main @ 9f9467b6 (188 commits)

**Features**
- SQLite mission store (specs/004): `MissionStore` port + `SqliteMissionStore` (`instance/missions.db`, WAL), sibling CI/Ideas/Quarantine stores, one-time boot ingest of `missions.md`, S8 flip — the store is authoritative and `missions.md` becomes a read-only export; break-glass `mission_ctl` CLI (`make missions`); `/list <state>` for done/failed history.
- Haze CLI provider (specs/006): stream-json mission execution, usage accounting, failure classification, onboarding + docs.
- Cloud deploy: cold-boot `instance/` hydration from `KOAN_INSTANCE_REPO`, opt-in periodic pull (`KOAN_INSTANCE_SYNC_INTERVAL`).
- `.koan/` steering tree: koan-only `KOAN.md` (#2293), `.koan/KOAN.md` + per-skill instructions from `.koan/skills/<name>/`; one-time feature notice to users.
- Review: stale-HEAD alert on posted reviews, severity-graded verdict alert, `[!IMPORTANT]`/`[!CAUTION]` callouts via shared `build_alert()`.
- REST API: auto-generated + CI-enforced OpenAPI 3.1 spec, per-mission token usage/cost on `GET /v1/missions/{id}`, structured review results.
- Non-blocking bounded `/ci_check` (specs/005): fix missions no longer starve the serial queue; `ci_check.timeout`, `ci_check.idle_timeout`, `ci_check.max_fix_attempts_per_mission`.
- Per-mission-type reasoning effort config; spec-change governance CI guard; speckit polish.

**Fixes** — highlights
- **Review diff truncation (#2292 / PR #2294): large diffs are no longer silently truncated at 32k chars** — the hard cap becomes a configurable `review_compressor.token_budget` (derived `max_diff_chars`), fetch-time skips are surfaced via `truncate_diff_with_skips`, and posted reviews carry a unified partial-coverage warning (preserved on compressor-off and hunter-append paths).
- Mission store hardening: quarantine (prompt-injection record) migration is fatal on failure and retried next boot; cutover double-ingest guard; 7+ silent-failure read paths closed; stale-export gates.
- Rebase: strict target remote, fail-closed fetch, pre-push sanity gate; "applied" vs "not changed" separated in PR comments.
- Deploy: tri-state `instance/` pull, failed `rebase --abort` surfaced, partial-hydrate wipe.
- git-prep self-heals interrupted merge/rebase before stashing; `/projects` deduped by resolved path + normalized name (#2339); unregistered-repo @mention alert reset on registration (#2279); Telegram `chat_id` coerced to int (#2281); Jira alert-block flattening; GitHub exception handlers now log instead of swallowing.

**Refactors / perf**
- Dedicated `ci_check.idle_timeout` decoupled from `first_output_timeout`; review/rebase alerts routed through `build_alert()`; `prune_memory_log` streams line-by-line.

**Docs / tests / CI**
- Constitution v2.0.0 (Principles III & VI for the SQLite store); specs 004/005/006 + reconciled component specs; mission-cli, haze, koan-md, github-alerts docs; large test additions (mission store conformance/ingest/startup/render, haze provider, spec-change guard, OpenAPI gen); fork-safe spec-change-guard CI; OpenAPI drift check workflow.


## v0.79 — 2026-07-08

### Merged 2026-07-08 — main @ 78eb231e (1 commit)

**Fixes**

- **Telegram chat-ID coercion** (#2281) — coerce a numeric `chat_id` to `int` in the outgoing Telegram API payload, with a supporting helper in `utils.py`. Follows the earlier `#2276` whitespace-strip fix for `KOAN_TELEGRAM_CHAT_ID`.

**Docs / tests**

- `docs/messaging/telegram.md` and `specs/components/bridge.md` updated for the coercion behavior.
- New coverage in `test_telegram_provider.py`, `test_notify.py`, and `test_utils.py`.

### Merged 2026-07-08 — main @ 7411209c (92 commits)

**Features**

- **Skill-eval harness** — new `koan/app/skill_evals.py` (~1.3k LoC) with golden datasets, scorers, and live adapters for fix/plan/brainstorm/rebase/review skills; documented in `docs/operations/skill-evals.md`.
- **Dashboard operator config tabs** (#2122) — live-edit projects/settings from the dashboard (`config_form.py`), with strict bool validation, config-write error surfacing, 404 on unknown project, and `cli_provider` validation.
- **Review draft-PR gate** — `review_draft_skip` config flag defers auto-review of draft PRs when the bot is attached as reviewer.
- **Orphans PR titles** — derive PR title/body from branch commits (length-capped).
- **LLM Wiki adoption** — `wiki/` spanning `docs/` + `specs/`, `wiki-sync.yml` CI backstop, and `wiki_check.py` / `wiki_sync_ci.py` scripts.
- `review_history.preserve_previous` config flag.

**Refactors / perf**

- `memory_db.py` — streaming JSONL reindex/migration (no whole-file buffering), FTS5 vacuum of expired rows, rollback on partial reindex, memoized learnings search.
- Deploy: gate optional API process on Railway; lower `api.threads` default 8→2.
- Review: dedupe custom-binary basename logic into a base helper; signature shows pinned CLI binary name.

**Fixes** — highlights

- **Config robustness**: tolerate null `projects:` section (#2273/#2274); disable `skip_permissions` when running as root; `strip_co_authored_by` validator entry.
- **Telegram chat-ID fix** (#2276, closes #2256) — strip whitespace/trailing newline from `KOAN_TELEGRAM_CHAT_ID` (injected by Railway or copy-paste) that caused "Bad Request: chat not found"; add `utils.get_telegram_chat_id()`.
- **Security-adjacent**: `memory_db` guards non-dict JSONL lines / OSError skips; dashboard rejects bogus settings writes.
- Per-run `$TMPDIR` for missions, reaped on completion; agent scratch files routed through it.
- `deep_research` word-boundary topic dedup; `pr_feedback` `--limit` (silent 30-cap); `session_tracker` calendar-day drift off-by-one.
- Two reverts of an earlier review CI-safe eval harness, superseded by the new `skill_evals` harness.

**Docs / tests / CI**

- Spec-kit artifacts (002 review-skill-evals, 003 core-skill-evals, 003 review-skip-draft-pr), `specs/components/*` + `specs/skills/*` frontmatter, dashboard/railway/telegram docs.
- Test coverage for eval scorers, review draft gate, and session-tracker drift.

### Merged 2026-07-01 — main @ 3d9508ed (1 commit)

**Features**

- **Memory RSS watchdog** (#2232/#2233) — new `koan/app/memory_monitor.py`
  monitors process RSS, self-restarts on threshold breach, with `tracemalloc`
  diagnostics. Wired into `run.py`, surfaced in the dashboard, and configurable
  via new `config.py` / `instance.example/config.yaml` knobs.

**Docs / tests** — new `docs/operations/memory-watchdog.md` + README index
entry; coverage added in `test_memory_monitor.py`, `test_run.py`,
`test_config.py`, `test_dashboard.py`.

### Merged 2026-07-01 — main @ c38dd873 (168 commits)

**Features**

- **Speckit native support** — `/speckit` skill with code-enforced orchestration:
  chat trigger, issue-URL trigger, `repo:`/`branch:` override forwarding,
  from-branch handler.
- **Per-role CLI provider** — `cli:` section lets each role use a different
  provider; review-specific Claude CLI binary override, surfaced in `/status`
  and the startup banner; relative `KOAN_CLAUDE_CLI_PATH` support; custom CLI
  binary flavor shown at startup.
- **New provider wrappers** — `zai-claude` (Z.ai GLM), `ollama-claude` (local
  Ollama via the Claude provider), `oc-claude` (Claude CLI via OpenCode Go).
- **Provider liveness signal** — truthful execution-state observability.
- **systemd-user service-manager mode** — install/uninstall scripts, docs.
- **Dashboard** — `/projects` registry welcome screen.
- **missions.md startup integrity self-heal** + size bounds.
- **Post-merge review outcome tracking** feeds `/reflect` calibration.
- **`/implement`** — assumptions pressure-test before the plan gate.
- **deep_research** — git-churn hotspot detection in topic selection.
- **`/status`** — CLI binary name, service manager, and server hostname + IP.
- **`/question`** alias for `/ask`; outbound messages separate trailing
  punctuation from URLs.

**Refactors / perf**

- Dashboard god-object split into a blueprint package + service layer.
- deep_research caches open-issue and pr-feedback fetches; hooks resolve the
  loop-guard config once per event.

**Fixes (54)** — highlights:

- **Security** — dashboard open-redirect rejected; security review fails closed
  with audit log; prompt_guard no longer blanks benign memory.
- **provider (5)** — skip launch fallback when the binary is unavailable;
  review model/footer attributed to the review-mode provider; role provider
  threaded into parallel sessions.
- **review (4) / rebase (3)** — token redaction on push timeout, retry
  force-push with the owning account's token on 403, self-review posted as
  COMMENT instead of failing, hardened calibration outcome tracking.
- **dashboard (4)** — add_project moved off the request thread, silent failures
  surfaced, 404 on unknown projects.
- **deep_research / contemplative / quota / observability** — zombie-flapping
  debounce, minute-precision reset parsing, clearer failure surfacing.
- Date/tz robustness across reset_parser, event_scheduler, iteration_manager.

**Docs / tests / CI** — 17 docs, 7 tests, 3 CI updates (per-file coverage report,
simplified reviewer assignment).

**Note:** the incubating-local `fix(railway): pin dashboard to port 5000` commit
was reverted in favor of main's behavior (`KOAN_DASHBOARD_PORT` → `PORT` → 5000).
