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
| `KOAN_CHAT_TIMEOUT` | `180` | Seconds to wait for a chat reply from the CLI |

The dashboard reads shared state from `instance/` (missions, journal, signals, memory,
config) and exposes a JSON/SSE API under `/api/*`.

## Pages

| Route | Description |
|-------|-------------|
| `/` | Dashboard — agent status, mission counts, attention zone, health, projects |
| `/missions` | Pending / in-progress / done missions, with drag-reorder, edit, cancel |
| `/chat` | Chat with the agent or queue a mission |
| `/usage` | Token usage analytics (Chart.js): spend, by project, outcomes, types |
| `/prs` | Open pull requests across projects with CI and review status |
| `/plans` | Plan issues with phase progress |
| `/progress` | Live stream of the current run's output (SSE) |
| `/journal` | Journal entries grouped by date and project |
| `/logs` | Recent log lines with source filter and search |
| `/agent` | Read-only introspection: soul, memory, skills, config |
| `/rules` | Automation rules CRUD |

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

## Architecture

- Server-rendered Flask templates (Jinja2); all UI text is in **English**
- Sidebar app-shell adopting the Kōan Design System (vendored `koan.css`/`koan.js`)
- Real-time updates via Server-Sent Events (SSE) for agent state and progress
- No build step — static CSS/JS served directly from `koan/static/`
- Per-page inline styles and scripts where needed, built on design tokens

> Note: journal entries, memory, and raw mission text are user/agent-generated and may
> contain any language; the dashboard chrome and all of its own labels are English.

## Related

- Design system: `docs/design-system/` (and its `docs/developer-handoff.md`)
- Shared state files: see `docs/architecture/`
- REST API: [`docs/operations/rest-api.md`](rest-api.md) — programmatic HTTP control layer
