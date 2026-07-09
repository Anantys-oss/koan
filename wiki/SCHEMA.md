# Wiki Schema

This file documents the `wiki/` directory's own structure — the cross-bundle index,
the symlink topology, and why koan's docs pipeline doesn't rely on the `llm-wiki`
plugin's `/wiki:*` commands. The LLM reads this first when entering the wiki.

**Bundle-specific conventions live elsewhere, not here.** koan has two independent,
OKF-conformant knowledge bundles — `docs/` (operational "how to use", see
`docs/README.md` and `docs/SCHEMA.md`) and the durable half of `specs/` (design
"why/contract", see `specs/README.md` and `specs/SCHEMA.md`). Page types, tag taxonomy,
and frontmatter requirements are defined per-bundle in those two `SCHEMA.md` files, both
of which point back to `docs/SPEC.md` — the normative OKF v0.1 spec shared by both
bundles. This file only covers what's specific to the `wiki/` directory itself.

This file is **co-evolved with the user**. When a recurring pattern in edits or feedback
isn't reflected here, propose adding it. When something here stops fitting, prune it.

## Why koan doesn't rely on the `llm-wiki` plugin

`docs/` and `specs/` predate the `llm-wiki` plugin and were adopted as an LLM-wiki-style
catalog on top of koan's own pre-existing structure, not bootstrapped from the plugin's
template. `wiki/` may still appear enabled as a Claude Code plugin in this project (see
`.claude/settings.json` history), but koan's own workflow — `/brain`
(`.claude/skills/brain/`) plus `scripts/wiki_check.py` / `scripts/okf_backfill.py` — is
fully self-sufficient and doesn't invoke or depend on any `/wiki:*` command. This isn't
a stopgap pending an upstream fix; it's the intended design, for two reasons:

1. **The plugin's bundled scripts don't follow the `wiki/` symlinks.** `wiki_lint.py`,
   `wiki_stats.py`, and `wiki_search.py` all discover pages via `Path.rglob("*.md")`,
   which does not descend into symlinked directories. Since `wiki/docs`,
   `wiki/specs-components`, and `wiki/specs-skills` are symlinks (see "Wiki location"
   below), `/wiki:lint` and `/wiki:stats` report **0 pages** here — a false-clean
   result, not a real health check — and `wiki_search.py`'s BM25 fallback is similarly
   blind to the real content.
2. **The plugin's remaining differentiators don't fit koan's design even where they
   aren't blind.** `/wiki:query`'s graph-assisted lookup step needs
   `wiki/graph/graph.sqlite`, which never exists here (no graph layer — this is
   textual documentation, not relational data). Its backlink search greps for
   `[[wikilink]]` syntax, which koan never uses (plain markdown links only, see "Link
   convention" below). Its file-back-as-synthesis step writes `wiki/synthesis/*.md`
   with `type: synthesis` frontmatter, a page type koan's bundles don't define.

Once those four differentiators are set aside, what's left of `/wiki:query` — read the
index, pick candidate pages, read them, cite them — is exactly what `/brain ask` does
directly. There is no fallback capability being given up by not invoking the plugin.

`scripts/wiki_check.py` (repo root) reads `docs/`, `specs/components/`, `specs/skills/`
directly by real path, no symlinks involved, and is what both `.github/workflows/wiki-sync.yml`
and `/brain lint` actually rely on — this was never affected by the symlink limitation.

## Wiki location — spans two content roots, not one

`wiki/` is a real directory (not a symlink to a single tree, since there are two content
roots) holding `SCHEMA.md`, `index.md`, `log.md`, plus three directory symlinks,
inherited from when the wiki was first set up so that plugin tooling expecting pages to
live *inside* the wiki tree could walk into the actual content. They're kept because
they cost nothing and don't hurt, not because current tooling depends on them:

```
wiki/
  SCHEMA.md
  index.md
  log.md
  docs             -> ../docs
  specs-components  -> ../specs/components
  specs-skills      -> ../specs/skills
```

**`specs/<NNN-slug>/` (speckit's per-feature planning folders) are deliberately NOT
symlinked in** — they stay wiki-*visible* (referenced from `index.md`) but excluded
from `scripts/wiki_check.py`'s walk. See `specs/SCHEMA.md` for the full rationale (this
is a `specs/`-bundle-specific concern).

**No `raw/` layer inside `wiki/`.** `/brain ingest`'s acquisition scratch lives at
repo-root `raw/` (a sibling of both `docs/` and `specs/`, git-ignored except
`.gitkeep`s) — deliberately outside the wiki tree and outside both OKF bundles, so
neither bundle's conformance checks ever need to exclude it. **No `graph/` layer** —
this is textual documentation/design-contract content, not relational data.

## Page sizing

Soft cap 400 lines / ~2,000 words, hard cap 800 lines. Revisit if
`docs/design/decisions.md` (a running log) or any `specs/components/*.md` approaches
these. This applies uniformly to both bundles.

## Link convention

Standard Markdown relative or bundle-root-absolute links (`[text](path.md)` /
`[text](/path.md)`, per `docs/SPEC.md` §4), not Obsidian-style `[[wikilinks]]` — matches
koan's existing convention and normal GitHub rendering. Use `/brain lint` for
orphan/broken-link detection (see "Workflow customizations" below); it understands
plain markdown links, unlike the plugin's wikilink-based checks.

## Index structure — three layers, one job each

1. **`wiki/index.md`** — flat, cross-bundle catalog spanning both `docs/` and `specs/`
   (~82 entries, sectioned Docs / Specs — Components / Specs — Skills / Specs — Active
   Features). Hand-maintained, unchanged role. This is the fastest, flattest, most
   complete source of one-sentence summaries and is what `/brain ask` reads first.
2. **`docs/index.md`, `specs/index.md`** — the OKF-conformant bundle-root catalog for
   each bundle (the only place `okf_version: "0.1"` frontmatter is permitted, per
   `docs/SPEC.md` §5/§8). Mechanically generated by `scripts/okf_backfill.py indexes`,
   never hand-edited — their job is satisfying OKF conformance and giving a one-click
   "what topics exist in this bundle" view to someone browsing on disk.
3. **Per-topic-folder `index.md`** (`docs/architecture/index.md`, …,
   `specs/components/index.md`, `specs/skills/index.md`) — OKF §5 progressive-disclosure
   listings one level down, also mechanically generated, reusing the exact same
   `description:` text as layers 1 and 2. No summary is ever hand-maintained in more
   than one place.

## Workflow customizations

- **`/brain` is the entrypoint** (`.claude/skills/brain/`) for consulting and extending
  both bundles — it owns acquisition (`raw/` → `docs/reference/*.md`), index-first
  navigation, and the OKF-conformance side of wiki bookkeeping, with no dependency on
  the `llm-wiki` plugin (see "Why koan doesn't rely on the llm-wiki plugin" above).
  `/brain ingest <path|url|"idea">` is the acquisition path; `/brain sync` is the
  interactive equivalent of the CI auto-fix pass; `/brain lint` wraps the broadened
  `scripts/wiki_check.py`.
- **Wiki bookkeeping is exempt from koan's human-in-the-loop discipline.** Frontmatter
  fields, `wiki/index.md` / `docs/index.md` / `specs/index.md` / per-folder `index.md`
  entries, and `wiki/log.md` lines are committed directly as part of the same change/PR
  — no separate review step for that part specifically. This does not extend to actual
  spec/contract or code changes, which keep the existing "no unsupervised merges"
  discipline. A CI job (`.github/workflows/wiki-sync.yml`) backstops this for anything a
  session missed, pushing a same-branch fix commit rather than opening a separate PR.
- **Default query path is index-first**: read `wiki/index.md`, open the obviously
  relevant page(s) directly. If the index doesn't clearly surface a page, say so
  plainly rather than guessing — see `.claude/skills/brain/SKILL.md`'s `ask` section.

## User preferences

(Empty initially. Capture recurring stylistic preferences here as they come up.)

## Lint cadence

Run `/brain lint` periodically, e.g. after a batch of features lands — not after every
edit. The CI backstop in `wiki-sync.yml` covers the per-PR case.
