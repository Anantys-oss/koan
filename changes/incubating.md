# Incubating changelog

Running journal of what has landed on `incubating` since the last stable release.

Each entry is appended by `/koan.incubate` when `main` is merged into `incubating`.
When a stable release is cut with `/koan.release`, all entries below the
`## Unreleased` heading are moved into `changes/stable.md` under a version + tag,
and this file is reset to an empty `## Unreleased` section.

Do not hand-edit released entries — they are the source for `changes/stable.md`.

---

## Unreleased

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
