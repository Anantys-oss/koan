---
type: doc
title: "Web Dashboard"
description: "Documents the local Flask web dashboard's architecture, blueprints, pages, passphrase gate, and design-system integration."
tags: [operations]
created: 2026-05-31
updated: 2026-07-17
---

# Web Dashboard

The Kōan web dashboard is a local, read-mostly Flask app for monitoring and interacting
with the agent. Start it with `make dashboard` (defaults to `http://127.0.0.1:5001`).

## Running

```bash
make dashboard
```

Configuration via environment:

| Variable | Default | Purpose |
|----------|---------|---------|
| `KOAN_DASHBOARD_HOST` | `127.0.0.1` | Bind host |
| `KOAN_DASHBOARD_PORT` | `5001` | Bind port |
| `KOAN_DASHBOARD_PWD` | _(unset)_ | Shared passphrase that gates access (see below) |
| `KOAN_CHAT_TIMEOUT` | `180` | Seconds to wait for a chat reply from the CLI |

The dashboard reads shared state from `instance/` (missions, journal, signals, memory,
config) and exposes a JSON/SSE API under `/api/*`.

## Architecture

The dashboard is a Flask **blueprint package** at `koan/app/dashboard/`, built by a
`create_app()` factory that mirrors the REST-API factory (`koan/app/api/__init__.py`).
Routes are grouped by domain into one blueprint per file:

| Blueprint | Owns |
|-----------|------|
| `core` | `/`, `/login`, `/logout`, `/api/forecast`, `/api/status`, `/api/health`, `/api/provider` |
| `missions` | `/missions*`, `/api/missions*`, `/api/projects`, `/api/attention*` |
| `chat` | `/chat*`, `/progress`, `/api/progress*`, `/api/state/stream` |
| `usage` | `/usage`, `/api/usage*`, `/api/metrics`, `/api/efficiency`, `/api/skill-metrics`, `/journal`, `/api/journal/<day>`, `/logs`, `/api/logs` |
| `agent` | `/agent`, `/skills`, `/api/agent/*` |
| `config` | `/config`, `/api/config/*`, `/api/nickname`, `/rules`, `/api/rules*`, `/recurring`, `/api/recurring*` |
| `prs` | `/prs`, `/api/prs*`, `/plans`, `/api/plans*` |

Supporting modules:

- **`dashboard/state.py`** — patchable module globals (paths, `CHAT_TIMEOUT`, `DASHBOARD_PWD`,
  caches, regexes). Route handlers read `state.X` at request time so a single patch target
  affects everything.
- **`dashboard/_helpers.py`** — passphrase gate, static cache-buster, context processor, and
  template filters, attached via `register_helpers(app)`.
- **`dashboard_service/`** — pure business logic (mission parsing, journal/rule-history readers,
  plan-progress parsing, progress timeline entries, forecast & metric computation),
  unit-testable without a Flask client.

Templates live under `koan/templates/dashboard/`; static assets under `koan/static/`. The
runnable entry point is `koan/app/dashboard/__main__.py` (invoked by `make dashboard` and
`pid_manager.start_dashboard()`).

### Passphrase gate

By default the dashboard is unauthenticated and meant for local-only use
(`127.0.0.1`). Setting `KOAN_DASHBOARD_PWD` turns on a passphrase gate: every
request must carry an authenticated session, unauthenticated HTML requests are
redirected to a `/login` page, and `/api/*` routes return `401`. Entering the
passphrase unlocks a cookie-based session (HttpOnly, SameSite=Lax) whose secret
is derived from the passphrase, so sessions survive restarts. Use this whenever
the dashboard is exposed beyond loopback. On Railway (`KOAN_DEPLOY=railway`) the
dashboard starts automatically on `0.0.0.0:5000` and **requires** the passphrase
— it refuses to start without one. See [Railway setup](../setup/railway.md).

## Pages

| Route | Description |
|-------|-------------|
| `/` | Dashboard — agent status, mission counts, attention zone, health, projects |
| `/missions` | Pending / in-progress / done missions, with drag-reorder, edit, cancel |
| `/chat` | Chat with the agent or queue a mission |
| `/usage` | Token usage analytics (Chart.js): spend, by project, outcomes, types, and daily mission activity from `instance/usage/*.jsonl` |
| `/prs` | Open pull requests across projects with CI and review status, sorted by last activity (`updatedAt`, fallback `createdAt`) |
| `/plans` | Plan issues with phase progress |
| `/progress` | Structured live mission timeline from `pending.md` (SSE) |
| `/journal` | Journal entries grouped by date and project |
| `/logs` | Recent log lines with source filter and search |
| `/agent` | Read-only introspection: soul, memory, skills, config |
| `/rules` | Automation rules CRUD |
| `/projects` | Project registry / welcome screen — one card per project (mission counts, GitHub link, provider/model, config checklist) + add-project modal. `/` redirects here when 2+ projects are configured |
| `/config` | View/edit `config.yaml` & `projects.yaml`; structured **Projects (form)** and **Settings** tabs (see below) |

## Operator config tabs

The `/config` page is a first-class operator console — change configuration without
hand-editing YAML or going through Telegram:

- **config.yaml / projects.yaml** — raw, comment-preserving textarea editors with
  validation (secrets are redacted in the view).
- **Projects (form)** — pick a project, toggle an allow-listed set of per-project
  overrides (`cli_provider`, `autoreview`, `focus`, `exploration`, `rtk`,
  `devcontainer`, `max_open_prs`, `max_pending_branches`, `github_url`,
  `git_auto_merge.enabled`, `git_auto_merge.base_branch`,
  `git_auto_merge.strategy`). Saved to `instance/projects.yaml` (persistent on
  a hosted deployment's volume) via `apply_project_patch()`. `path`, secrets,
  and nested-dict sections not explicitly listed are never editable from the
  form. Backed by `GET/POST /api/projects/<name>` (dashboard) and
  `PATCH /v1/projects/<name>` (REST API) — both call the same
  `apply_project_patch()`.
- **Settings** — single-field `config.yaml` edits (dashboard nickname, auto-merge,
  CI fix dispatch, review-comment dispatch, auto-update). Each control writes one
  allow-listed dotted key through `PUT /api/config/setting`, comment-preserving.
  Off-list keys are rejected with HTTP 422.

### Usage activity backfill

`/usage` reads daily mission activity solely from `instance/usage/*.jsonl`. Runs whose token
usage cannot be extracted (e.g. some skill-dispatch missions) are now recorded with a
placeholder row so activity stays visible. To reconstruct activity for **historical** days that
predate that fix, run the one-shot backfill from `instance/session_outcomes.json`:

```bash
# dry-run (default) — shows per-day counts that would be written
python -m app.backfill_usage --start 2026-05-30
# apply
python -m app.backfill_usage --start 2026-05-30 --apply
```

Synthetic rows carry a `"backfill": true` marker (zero tokens/cost — counts only, since token
data is unrecoverable) and the tool is idempotent: re-running writes nothing already present and
credits any real rows so days are never over-counted.

### Projects page

`/projects` is the multi-project welcome screen. Each configured project (from
`projects.yaml`, falling back to `KOAN_PROJECTS`) renders as a card showing:

- Name and clickable GitHub URL (shown only when `github_url` is set)
- Pending / in-progress mission counts (from `missions.md`, grouped by `[project:name]` tag)
- Last activity — the newest mtime among that project's `instance/journal/YYYY-MM-DD/<project>.md`
  files, or **N/A** when none exist (best-effort; no subprocess, so nothing can hang)
- CLI provider and `mission` model from the merged per-project config
- A **configuration checklist** that flags a missing `github_url` (and notes when no
  `cli_provider` is set), each linking to `/config` for an inline fix
- Quick actions: **PRs** (`/prs?project=…`), **Add mission** (`/missions?project=…`),
  **Settings** (`/config`)

`GET /api/projects/<name>/status` returns a single project's card as JSON for async refresh.
The add-project modal validates the GitHub URL client-side, then `POST`s to `/projects/add`,
which runs the `add_project` skill in-process and appends the project to `projects.yaml`.

> **Global pause caveat:** the card's **"Pause agent (global)"** button calls
> `/api/agent/pause`, which pauses the **entire agent** via `pause_manager.create_pause()`.
> Kōan has no per-project pause; the label is explicit so this is not mistaken for pausing
> a single project. Resume from the agent controls or `/api/agent/resume`.

Routing note: `/` (the `core.index` route) redirects to `/projects` when 2+ projects are
configured; a single-project install keeps the classic single-project dashboard at `/`.
An explicit `?project=<name>` bypasses the redirect, so the classic single-project view
stays reachable on multi-project installs (e.g. the registry's per-project links).

## Layout

The dashboard uses a left **sidebar app-shell**: a fixed sidebar with the brand, the
navigation links, a project filter, and the theme/shortcuts controls; a sticky topbar
showing the current page title (and any page-specific actions); and a scrollable content
area. On screens narrower than 880px the sidebar collapses into an off-canvas drawer
toggled by the menu button in the topbar.

Keyboard shortcuts: press `?` for the full list (single-key navigation to each page).

## Theme

The dashboard supports light and dark themes. Toggle with the button in the sidebar
footer; the preference is saved in `localStorage` under `koan-theme`. On first load (no
saved preference) the dashboard follows the operating system's color-scheme preference
and falls back to **dark** when none is expressed. A small inline script in `<head>`
applies the theme before first paint to avoid a flash.

## Design system

The dashboard adopts the **Kōan Design System** (`docs/design-system/`). The system's
stylesheet and runtime are **vendored** into the dashboard so Flask can serve them:

| Source (canonical) | Vendored copy |
|--------------------|---------------|
| `docs/design-system/assets/koan.css` | `koan/static/css/koan.css` |
| `docs/design-system/assets/koan.js`  | `koan/static/js/koan.js` |

`koan.css` provides the design tokens (dark-first, with a `[data-theme="light"]`
override), layout primitives, and the `k-`-prefixed component library
(`.k-app`, `.k-nav`, `.k-card`, `.k-stat`, `.k-badge`, `.k-table`, `.k-btn`,
`.k-progress`, `.k-empty`, …). `koan.js` provides `window.koanToggleTheme()`.

> **Updating the design system:** edit the files under `koan/static/css/` and
> `koan/static/js/` directly — they are the source of truth the dashboard serves,
> with no separate copy to keep in sync.

`koan/static/css/dashboard.css` is a **thin application layer** on top of the system: it
aliases a few legacy dashboard variables (`--bg`, `--accent`, `--green`, …) onto design
tokens so existing markup follows the theme, defines the app-shell chrome (sidebar,
topbar, mobile drawer), and restyles dashboard-specific components (chat, attention zone,
activity dots). It does **not** redefine design tokens — `koan.css` owns those.

All third-party assets are **vendored** so the local-only dashboard works fully offline: Lucide icons ship as `koan/static/js/lucide.min.js` (pinned) and the Space Grotesk, Inter and JetBrains Mono webfonts (latin `.woff2` subset) are self-hosted in `koan/static/fonts/`, declared via `@font-face` in `koan/static/css/koan-fonts.css` with `font-display: swap` and system-font fallbacks.

### Live progress (`/progress`)

Streams `instance/journal/pending.md` via SSE (`/api/progress/stream`).
Recognizable `[cli] …` lines are classified with the same grammar as
`make logs` (`log_fmt.classify_cli`) into a **timeline**:

| Kind | UI |
|------|----|
| `tool_use` | Icon + tool name + input preview |
| `text` | Prominent “what I’m doing” narrative |
| `thinking` | Collapsed activity dots (`count`) |
| `result` / `tool_error` / `warning` | Status-colored rows |
| `raw` | Unrecognized lines, monospace |

**Raw view** toggle shows the full `content` blob. Mission header fields
come from the pending.md header; elapsed/state also track
`/api/state/stream`. Writers of `pending.md` are unchanged.

Snapshot and stream payloads are additive: `{active, content, header, entries}`.
`content` remains the raw file text for fallback and older clients.
Pure parsing lives in `dashboard_service/progress.py` (display-only).

## Architecture

- Server-rendered Flask templates (Jinja2); all UI text is in **English**
- Sidebar app-shell adopting the Kōan Design System (vendored `koan.css`/`koan.js`)
- Real-time updates via Server-Sent Events (SSE) for agent state and progress
- No build step — static CSS/JS served directly from `koan/static/`
- Per-page inline styles and scripts where needed, built on design tokens

> Note: journal entries, memory, and raw mission text are user/agent-generated and may
> contain any language; the dashboard chrome and all of its own labels are English.

## Related

- Design system: vendored in `koan/static/css/koan.css` and `koan/static/js/koan.js` (see the Design system section above)
- Shared state files: see `docs/architecture/`
- REST API: [`docs/operations/rest-api.md`](rest-api.md) — programmatic HTTP control layer
