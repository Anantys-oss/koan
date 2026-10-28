# Kōan changelog

Released versions of Kōan, newest on top. Each section corresponds to a
git tag cut by the release workflow from the `## ${NEXT}` section curated
by `/koan.incubate` in `changes/incubating.md`.

---

## v0.9.0 — 2026-10-28

**Added**

- Repositories can now steer how Kōan reviews them with an optional
  `.koan/config.yaml` at their root. Its first setting, `review.always_check`,
  pins file globs that must never be dropped when a large pull request is
  compressed to fit the review budget — so your skill manifests, docs, or
  config-as-code stay reviewed instead of landing in the "Partial review" note.
  The file is entirely optional and an absent one changes nothing; see
  `docs/reference/koan-config.sample.yaml` for the annotated template. (#2466)
- Quota tracking can now be anchored to Anthropic's own usage figures instead of
  Kōan's local token estimate, so session and weekly percentages match what you
  see on your account. Controlled by `usage.authoritative_source` in
  `instance/config.yaml`, which defaults to `auto` — it switches itself on when
  you run a Claude provider with an OAuth login and silently falls back to the
  local estimate otherwise, so no action is needed. Set it to `off` to always use
  the local estimate. Poll interval and staleness are tunable via
  `usage.authoritative_poll_seconds` and
  `usage.authoritative_max_staleness_seconds`; all three are documented in
  `instance.example/config.yaml`. (#2455)
- Reviews are now consistent across re-runs. Asking for a review again on an
  unchanged pull request reproduces the previous one instead of re-deriving it,
  and when code has genuinely changed the new review is reconciled against the
  old: issues you already fixed are listed as resolved rather than silently
  dropped, and a new non-critical complaint about code that has not changed since
  the last review is suppressed — no more review whiplash. Critical findings
  always come through, labelled `[Pre-Existing Issue]`. Enabled by default and
  tunable through `review_consistency`, `review_reconcile` and
  `review_snippet_validation` in `instance/config.yaml`; see
  `docs/design/review-consistency.md` for the full behavior. (#2465)
- The reviewer now reads the reviewed repository's own convention documents
  (`AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, and a `docs/` knowledge bundle
  when present) before commenting, so it applies your house style instead of
  flagging it as a defect. On by default, a no-op for repos that ship none of
  these, and adjustable via `review_convention_docs` in `instance/config.yaml`.
- Quoted code in a review finding is now checked against the real file before the
  comment is posted, so a review can no longer show stale, pre-fix source under a
  permalink pointing at already-fixed code. (#2465)
- You are now notified when a review request is skipped because the pull request
  has no new commits, instead of the request appearing to vanish. (#2457)
- Kōan can now run as an MCP server, so MCP clients (editors, other agents) can
  drive missions and query state — over stdio, or over authenticated Streamable
  HTTP. Off by default; enable and tune it under the `mcp:` block of
  `instance/config.yaml` (`enabled`, `transport`, `host`, `port`,
  `tools_allow_destructive`). Destructive tools stay hidden unless you opt in.
  See `docs/operations/mcp-server.md`. (#2513, #2521)
- Gemini CLI is now a supported provider: set `cli_provider: gemini`. Per-role
  models are configured under `models.gemini` (defaults: `pro` for missions and
  reviews, `flash` for chat, `flash-lite` for lightweight calls). See
  `docs/providers/gemini.md`. (#2501)
- Every mission now runs inside its own systemd scope, so nothing it starts — a
  Gradle daemon, a test container — outlives it and slowly eats the host's memory.
  A memory cap keeps one mission from starving the machine. On by default, no
  action needed; tune or disable it with `mission_limits` (`enabled`,
  `memory_reserve`, `memory_min`, `memory_max`) in `instance/config.yaml`. Hosts
  without systemd fall back to a process-group cleanup. (#2500)
- A repository's `.koan/config.yaml` can now run shell commands before and after a
  mission (`pre_hooks` / `post_hooks`, per mission type) — e.g. start a database
  before a fix. Because this runs repo-controlled shell on your host it is off
  until you set `mission_hooks.enabled: true` in `instance/config.yaml` (or
  `mission_hooks: true` per project). See `docs/security/mission-hooks.md` and
  `docs/reference/koan-config.sample.yaml`. (#2478)
- A repository can also name its own Claude Code skills to run when a lifecycle
  event fires (e.g. refresh docs after every review), via `hooks:` in its
  `.koan/config.yaml`. Off until you set `hook_skills.enabled: true` in
  `instance/config.yaml` (or per project); rate-limited and read only from the
  default branch. See `docs/architecture/hooks.md`. (#2497)
- Before a force-push, `/rebase` now checks that no content was lost from the pull
  request, and stops instead of pushing if it was.
- Review depth is now configurable with `review_effort` (`low` to `max`) in
  `instance/config.yaml` or per project; leave it unset to keep the provider's
  default. (#2523)
- Mentioning the bot explicitly on a pull request now always triggers a fresh
  review, even when nothing changed since the last one.
- Reviews now also flag structural problems — places where a restructuring would
  make whole branches, flags or layers unnecessary. (#2506)
- `/pr` now ends with a readability-only simplify pass after its quality review.
  (#938)
- `/restart --force` restarts the daemons in an emergency, even when they are not
  responding. (#2502)
- A new command-line REST client lets you drive Kōan's API from a terminal. See
  `docs/users/koan-cli.md`. (#2509)
- Jira plans that are too long for one comment are now split across several, and
  each delivery is verified so a plan never silently goes missing. (#2507)
- `!command` shortcuts now work on every messaging provider, not just Slack.
- When the Codex provider is active, the Codex CLI is updated automatically at
  startup and once a day. On by default; set `codex_update.enabled: false` to
  manage it yourself, or `codex_update.notify: false` to skip the update notice.
  See `docs/providers/codex.md`. (#2482)
- Kōan now mentions Skuggi, the hosted AI engineering team from Kōan's makers:
  once at launch, in occasional contextual nudges (each at most once a week), and
  in report, `/help` and `/status` footers. On by default; set
  `skuggi.promo: false` in `instance/config.yaml` to turn every mention off. See
  `docs/users/skuggi.md`. (#2530)

**Changed**

- Project-level `KOAN.md` guidance now also reaches skills that run through the
  agent runner, so per-project instructions apply consistently across skills
  rather than only in some of them. See `docs/users/koan-md.md`. (#2464)
- The time allowed for each round of automatic rebase conflict resolution is now
  configurable via `rebase_conflict_timeout` (default 600 seconds) in
  `instance/config.yaml` — raise it for large repositories where a round runs
  long.
- Each mission now ends with one consistent notification built from a fixed
  template, instead of several rephrased variants that could drop the link.
  (#2527)
- Reviews are now strictly read-only: they run on a pinned checkout of the pull
  request head with a shell limited to read commands, enforced by the CLI rather
  than by the prompt.
- Review prose now follows your configured reply language. (#2517)
- `cli_provider` now accepts a binary path (`flavor:path`), which every role
  inherits unless it overrides it — see `instance.example/config.yaml`. Chat,
  rituals and message formatting now use the provider and model of their own role.
- Long foreground commands in a mission are no longer cut at the Claude CLI's
  two-minute default: they get up to the mission budget, adjustable with
  `bash_foreground_timeout` in `instance/config.yaml` (default: mission timeout
  minus 120 seconds; no action needed). See `docs/architecture/daemon.md`. (#2522)
- Steering instructions loaded before each Claude call are reported once, as a
  single deduplicated summary. (#2480)
- A mission re-queued after stalling now carries guidance from its failed attempt.

**Fixed**

- Rebase progress messages now report the CLI provider and model actually in use
  instead of always saying "Claude". (#2467)
- A code mission that ends without a pull request is now reported as failed
  instead of done. (#2518)
- The Telegram bridge no longer stalls for tens of seconds every hour while it
  cleans up old worktrees. (#2503)
- A provider that stops sending output no longer hangs the mission forever.
  (#2515)
- Rebases no longer fail on `--continue` after a conflict is resolved, no longer
  link unrelated issues, and correctly credit commits made by the feedback agent.
  (#2512, #2514, #2511)
- `GET /v1/missions` now returns the actual mission queue. (#2516)
- Quota no longer pauses the agent on false positives.
- A corrupted event file no longer crashes the agent loop. (#2498)
- Slack links and bang-prefixed commands are handled correctly.
- Repositories with dots in their name are recognized on GitHub. (#2486)
- Leftover review worktrees are reclaimed, freeing disk space. (#2484)
- Review findings get unique ids, and findings rejected on reflection no longer
  come back.
- Skills no longer misread multi-line command footers as arguments. (#2504)

## v0.8.1 — 2026-07-22

### Merged 2026-07-21 — main @ 376b048a (11 commits)

**Added**

- Brainstorming now creates rich, properly formatted Jira issues with native
  links between them, so a brainstorm lands as a readable, connected set of
  tickets instead of flat text. (#2443)
- Config changes now take effect on the running agent without a restart —
  edit settings in the dashboard and the daemon picks them up live. (#2208)
- A new post-review hook lets you run your own automation once a review
  finishes, and the agent captures lessons from each review it performs. (#2440)

**Changed**

- The agent reclaims leftover working directories from finished missions,
  noticeably reducing disk usage (and hosting cost) on long-running
  instances. (#2454)
- Transition notices now point you to `/fix` instead of the older
  `/rebase --fix`. (#2452)
- Published Docker images no longer ship Kōan's own development tooling,
  making them smaller and cleaner. (#2442)

**Fixed**

- Sending `/fix` with a pull-request URL no longer stalls in the queue — it is
  routed to the right handler automatically. (#2459)
- Replies default to English when no language preference is set, instead of
  falling back unpredictably. (#2456)

## v0.8.0 — 2026-07-18

**Added**

- **Chat stays responsive during missions** — your chat messages now get a priority lane, so the bot answers you even while it is busy running a mission (#1084).
- **Split `/rebase`** — a bare `/rebase <pr>` now only rebases the PR onto its base branch. Use `/rebase --fix` (implied when you add a focus area or severity after the URL) to also apply review feedback (#2439).
- **More CLI providers** — added the Grok Build and Haze providers, so you can run missions on additional backends. `/models` slots can now be annotated with a per-role CLI, and MCP tools can be enabled per execution role (#2422).
- **Plain-English GitHub mentions** — mentioning the bot in natural language now routes to the right skill instead of requiring an exact command (#2402).
- **Authoritative mission store** — missions now live in a SQLite store (`missions.md` becomes a read-only export). New `/list <state>` shows done/failed history, and `make missions` is a break-glass CLI when the bridge is down.
- **Live mission timeline** — the dashboard `/progress` view now shows a structured, live timeline; you can edit a project's GitHub URL and auto-merge settings from the dashboard and via `PATCH /v1/projects/<name>`.
- **GitHub "Running" indicator** — a label and commit status now mark a PR while a mission is working on it (#2365).
- **`/claudemd <project>`** keeps a managed learnings block in `CLAUDE.md`; added the `/rereview` alias and a configurable label to pause automatic PR reviews.
- **Review upgrades** — severity-graded verdict alerts, stale-HEAD warnings on posted reviews, and per-mission token usage/cost reported over the REST API.
- **`.koan/` steering tree** — koan-only `KOAN.md` and per-skill instructions that apply to the autonomous agent without affecting interactive sessions (#2293).
- **Cloud deploy** — hydrate `instance/` from a repo on cold boot, with an opt-in periodic pull.
- **Non-blocking CI checks** — CI-fix missions no longer stall the queue behind other work.
- Prettier `make logs` output (set `raw=1` to bypass), and per-mission-type reasoning-effort configuration.

**Changed**

- **Large review diffs are no longer silently truncated** — the old 32k-character cap is now a configurable budget, and a review that couldn't cover the whole diff says so with a partial-coverage warning (#2292).
- **The bot stays up when the CLI binary is missing** at startup, entering a degraded no-mission mode instead of failing outright.
- **Unified release pipeline** — releases are now cut from the `incubating` branch through the release workflow, and `CHANGES.md` is the published changelog.

**Fixed**

- No more duplicate command output when Telegram re-delivers an update, and no more double-resume when pausing/resuming in quick succession (#2437).
- Missions no longer wrongly resurrect after removal, and a mission is re-queued when verification fails (#2210).
- Grok: credit/spending-limit exhaustion is now correctly treated as quota, false burn-rate alerts and pauses are gone, and headless tool calls are approved so `/implement` can commit.
- The Telegram bridge no longer grows its memory footprint over long uptime (#2360), and long `/report` messages now deliver reliably.
- Slack: the bot no longer replies in threads addressed to someone else.
- Security: closed a shell-injection path in CI dispatch-tag validation.

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
