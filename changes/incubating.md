# Incubating changelog

Running journal of what has landed on `incubating` since the last stable release.

Each entry is appended by `/koan.incubate` when `main` is merged into `incubating`.
When a stable release is cut with `/koan.release`, all entries below the
`## Unreleased` heading are moved into `changes/stable.md` under a version + tag,
and this file is reset to an empty `## Unreleased` section.

Do not hand-edit released entries — they are the source for `changes/stable.md`.

---

## Unreleased

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
