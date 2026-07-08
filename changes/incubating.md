# Incubating changelog

Running journal of what has landed on `incubating` since the last stable release.

Each entry is appended by `/koan.incubate` when `main` is merged into `incubating`.
When a stable release is cut with `/koan.release`, all entries below the
`## Unreleased` heading are moved into `changes/stable.md` under a version + tag,
and this file is reset to an empty `## Unreleased` section.

Do not hand-edit released entries — they are the source for `changes/stable.md`.

---

## Unreleased

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
