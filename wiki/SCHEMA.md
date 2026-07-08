# Wiki Schema

This file is the configuration for this wiki. It documents the conventions, page types, tag taxonomy, and workflow customizations for **this specific wiki**. The LLM reads this first when entering the wiki, and its conventions override the defaults documented in the `llm-wiki` skill.

This wiki is not bootstrapped from the plugin's default template — it adopts koan's two pre-existing, deliberately-distinct knowledge stores as its content: `docs/` (operational "how to use", see `docs/README.md`) and the durable half of `specs/` (design "why/contract", see `specs/README.md`). Several defaults below deviate from the plugin's out-of-the-box assumptions; each deviation is called out explicitly.

This file is **co-evolved with the user**. When a recurring pattern in edits or feedback isn't reflected here, propose adding it. When something here stops fitting, prune it.

## Wiki location — spans two content roots, not one

`wiki/` is a real directory (not a symlink to a single tree, since there are two content roots) holding `SCHEMA.md`, `index.md`, `log.md`, plus three directory symlinks so the plugin's bundled scripts (which expect wiki pages to live *inside* the wiki tree) can walk into the actual content:

```
wiki/
  SCHEMA.md
  index.md
  log.md
  docs             -> ../docs
  specs-components  -> ../specs/components
  specs-skills      -> ../specs/skills
```

**`specs/<NNN-slug>/` (speckit's per-feature planning folders) are deliberately NOT symlinked in** — see "Speckit feature folders" below. They stay wiki-*visible* (referenced from `index.md`) but wiki-*lint-invisible* (outside the walked tree), so `/wiki:lint`'s frontmatter check doesn't perpetually flag them.

**No `raw/` layer.** Every page here is authored directly by the developer or Claude while implementing — there's no immutable external source material being compiled. **No `graph/` layer** — this is textual documentation/design-contract content, not relational data.

## Page types

- `doc` — the default type for everything under `docs/**` (via `wiki/docs/`). One doc per feature/subsystem/integration, following koan's existing structure (architecture/, design/, messaging/, operations/, providers/, security/, setup/, users/).
- `overview` — a routing/index doc for a whole tree: `docs/README.md` and `specs/README.md`. Both already exist as genuinely good, hand-curated prose indexes — this wiki does not replace them, it adds a machine-oriented catalog (`index.md`) alongside.
- `component-spec` — `specs/components/*.md` (via `wiki/specs-components/`), one per architectural module group (agent-loop, bridge, core, git-github, issue-tracking, providers, skills, web). Durable design contracts: why the component exists, what it upholds, what breaks if you change it.
- `skill-spec` — `specs/skills/*.md` (via `wiki/specs-skills/`), one per skill, **excluding `SKILL_SPEC_TEMPLATE.md`** (a template, not a page). Per `specs/README.md`'s own coverage policy, only ~10 of ~80 skills have a spec today — the rest are added on-demand as touched; the wiki index reflects only what exists, not a target list.
- `feature-plan` — speckit's `specs/<NNN-slug>/` folders. **Index-only: no frontmatter is ever injected into these files.** Each folder gets exactly one `index.md` entry, pointing at `spec.md` as the entry point, carrying a `status` computed from `tasks.md`'s checkbox ratio (`draft` = 0% and/or `Status: Draft` header, `in-progress` = partial, `shipped` = ~100% and code merged). Recomputed on every ingest/CI pass, never hand-maintained.

Add a new type only when a real category doesn't fit the above and needs distinct queryability (e.g. a future `decision` type if `docs/design/decisions.md`'s running ADR-style log ever splits into individual files) — not for a one-off; use tags instead.

## Why speckit feature folders get no frontmatter (resolves `TODO(SPECS_DIR_COLLISION)`)

`.specify/memory/constitution.md` has carried an open TODO since koan's speckit adoption: koan's own `specs/` already holds durable component/skill design contracts, while speckit's templates write ephemeral per-feature planning folders into that same `specs/` root — a collision that was flagged but never reconciled, and three speckit feature folders (`001`–`003`) already exist.

This wiki's answer: **don't rename or move anything.** The two populations are genuinely different things that coexist at different paths without literally colliding on disk:
- `specs/components/*.md` + `specs/skills/*.md` — durable, wiki-indexed, frontmattered.
- `specs/<NNN-slug>/*` — ephemeral planning scaffolding, owned entirely by speckit's own tooling (`/speckit-plan`, `/speckit-clarify`, etc. rewrite these files wholesale on each run). Injecting wiki frontmatter into them risks it being clobbered on the next regeneration, and their existing bold-label metadata convention (`**Feature Branch**: ... **Status**: Draft`) is speckit's own, not ours to change.

The durable artifact from a *shipped* speckit feature is the **updated `specs/components/<group>.md`** — per the existing, mandatory `CLAUDE.md` "Specs discipline" rule ("after implementing, UPDATE the spec... a PR that alters a contract without updating its spec is incomplete"). That update is what actually gets indexed and frontmattered; the speckit folder itself just flips its computed `index.md` status to `shipped` and remains as a historical record of how the feature was planned.

## Tag taxonomy

One tag per `docs/` topic folder, plus one per `specs/components/` subsystem. Keep this disciplined — a second tag only when a page genuinely spans two topics.

- `architecture` — daemon runtime, mission lifecycle, providers, skills system, memory, shared state
- `design` — durable decisions, design notes (`docs/design/`)
- `messaging` — Telegram/Slack/Matrix/Discord/GitHub/Jira integration
- `operations` — maintenance, troubleshooting, dashboard, REST API, auto-update, RTK, skill evals
- `providers` — CLI/local-model provider setup and behavior (shared between `docs/providers/*` and `specs/components/providers.md` — deliberate cross-link, both angles on the same subsystem)
- `security` — security review, threat models, prompt guard
- `setup` — installation, host runtime (Docker, Railway, systemd, launchd, ssh)
- `users` — user manual, onboarding, quickstart, skill reference
- `agent-loop`, `bridge`, `core`, `git-github`, `issue-tracking`, `web` — the remaining `specs/components/` subsystems with no direct `docs/` counterpart
- `skill` — shared tag for every `specs/skills/*.md` page (the filename already identifies which skill; a per-skill tag would be over-granular)

## Frontmatter requirements

Every page under `wiki/docs/`, `wiki/specs-components/`, `wiki/specs-skills/`, plus `specs/README.md`, must have:

```yaml
---
type: doc   # doc | overview | component-spec | skill-spec
title: "Human-readable title"
tags: [topic-tag]
created: 2026-06-27
updated: 2026-07-02
---
```

`created`/`updated` are real dates from git history for that file, not the backfill date. No `sources:` field (nothing here is compiled from raw sources). `specs/<NNN-slug>/**` is explicitly exempt — see "Page types" above.

## Page sizing

Soft cap 400 lines / ~2,000 words, hard cap 800 lines — same as upstream. Revisit if `docs/design/decisions.md` (a running log) or any `specs/components/*.md` approaches these.

## Link convention (deviation from plugin default)

Standard Markdown relative links (`[text](path.md)`), not Obsidian-style `[[wikilinks]]` — matches koan's existing convention and normal GitHub rendering. `/wiki:lint`'s orphan-page and broken-wikilink checks (which only scan for `[[bracket]]` syntax) don't apply here regardless of the limitation below — treat those two specific findings as not applicable.

## Known limitation: bundled scripts don't follow the wiki/ symlinks

`wiki_lint.py`, `wiki_stats.py`, and `wiki_search.py` all discover pages via `Path.rglob("*.md")`, which does **not** descend into symlinked directories. Since `wiki/docs`, `wiki/specs-components`, and `wiki/specs-skills` are symlinks (see "Wiki location" above), running `/wiki:lint` or `/wiki:stats` here reports **0 pages** — a false-clean result, not a real health check. `wiki_search.py`'s BM25 fallback is similarly blind to the real content.

This does **not** affect the two things that actually matter day to day:
- **`/wiki:query`'s primary path still works** — reading `wiki/index.md` then opening the specific page it names is a direct file read (through the symlink, which resolves fine for a single hop), not a recursive walk.
- **CI enforcement is unaffected** — `scripts/wiki_check.py` (repo root) reads `docs/`, `specs/components/`, `specs/skills/` directly by real path, no symlinks involved, and is what `.github/workflows/wiki-sync.yml` actually relies on.

What's genuinely unavailable until upstream fixes this (or this wiki drops the symlink layer): `/wiki:lint`'s automated frontmatter/oversized-page/staleness sweep, `/wiki:stats`' page-count/scaling view, and `wiki_search.py`'s BM25 fallback for fuzzy queries the index can't resolve. Use `scripts/wiki_check.py --base-ref origin/main` for hygiene checks instead, and plain `grep`/`Grep` across `docs/`/`specs/` for fuzzy search until this is fixed.

## Index structure

Flat `wiki/index.md` — ~81 index-worthy pages (56 docs + 8 component-specs + 12 skill-specs + specs/README.md + 3 feature-plan entries) is under the plugin's ~150-page / 300-line shard threshold. Sectioned: Docs (by topic folder), Specs — Components, Specs — Skills, Specs — Active Features (the `NNN-slug/` entries with computed status).

## Workflow customizations

- **`/wiki:init` and `/wiki:ingest` are not used.** No raw-source-compilation step. The "ingest" equivalent for `docs/`/`specs/components/`/`specs/skills/` is the existing `CLAUDE.md` rule: create/update the relevant page in the same change, bump its `updated:` frontmatter date, refresh its `wiki/index.md` entry, append a `wiki/log.md` line.
- **`/wiki:query`, `/wiki:lint`, `/wiki:stats` are used as shipped**, pointed at `wiki/` (whose symlinks resolve into the real content).
- **`/wiki:graph` is not used** — no graph layer.
- **Wiki bookkeeping is exempt from koan's human-in-the-loop discipline.** Frontmatter dates, `wiki/index.md` entries, `wiki/log.md` lines, and `specs/<NNN-slug>/` computed status are committed directly as part of the same change/PR — no separate review step for that part specifically. This does not extend to actual spec/contract or code changes, which keep the existing "no unsupervised merges" discipline. A CI job (`.github/workflows/wiki-sync.yml`) backstops this for anything an LLM session missed, pushing a same-branch fix commit rather than opening a separate PR.
- **Default query path is index-first**: read `wiki/index.md`, open the obviously-relevant page(s) directly. Escalate to `/wiki:query` only for open-ended/fuzzy questions where index summaries don't clearly surface a page.

## User preferences

(Empty initially. Capture recurring stylistic preferences here as they come up.)

## Lint cadence

Run `/wiki:lint` periodically (e.g. after a batch of features lands), not after every edit. The CI backstop in `wiki-sync.yml` covers the per-PR case.
