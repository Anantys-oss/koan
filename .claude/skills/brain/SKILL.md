---
name: "brain"
description: "Consult, capture, and acquire knowledge in koan's OKF-conformant docs/ and specs/ bundles. Use for /brain ask <question> (alias: /brain search <question> — consult docs/ and specs/ before planning/research/refactor, index-first, with page citations), /brain sync (close the loop after implementing a feature — refresh frontmatter/description and regenerate indexes), /brain ingest <path|url|\"idea sentence\"> (capture external material as a durable, cited reference page), /brain lint (OKF conformance health check), /brain init (verify or repair the scaffold), /brain help (print the cheat sheet), and bare /brain with no arguments (prints the cheat sheet, then asks which action to take). Trigger on \"what does the wiki say about X\", \"check the docs before we plan this\", \"update the wiki\", \"sync the docs\", \"ingest this article/file/idea\", \"save this as a reference doc\", \"lint the wiki\", \"check OKF conformance\", or when finishing a feature and CLAUDE.md's documentation step is due."
argument-hint: "ask <question> | search <question> | sync | ingest <path|url|\"idea\"> | lint [--strict] | init | help"
user-invocable: true
disable-model-invocation: false
---

## User Input

```text
$ARGUMENTS
```

`brain` is self-sufficient: it owns both *acquisition* (`raw/` → `docs/reference/*.md`)
and index-first navigation for `docs/` and `specs/` directly — it does not depend on or
escalate to the `llm-wiki` plugin's `/wiki:*` commands. That plugin's differentiating
capabilities beyond plain index-first reading (BM25 fallback search, graph-assisted
lookup, `[[wikilink]]` backlink search, synthesis-page file-back) are either blind here
(the plugin's bundled scripts don't follow the `wiki/` symlinks) or architecturally
inapplicable (koan has no graph layer, uses plain markdown links not wikilinks, and has
no `synthesis` page type) — see `wiki/SCHEMA.md` for the full rationale.

**Before any subcommand except `help`, read `wiki/SCHEMA.md`** (wiki-directory-level
conventions), then whichever of `docs/SCHEMA.md` / `specs/SCHEMA.md` is relevant to the
bundle in question. Those files are the binding conventions — `docs/SPEC.md` is the
normative, bundle-agnostic OKF v0.1 spec they build on.

Parse `$ARGUMENTS` for the subcommand (first word) and its remaining argument. If
`$ARGUMENTS` is empty, go to "Bare invocation" below.

## `help`

Print this cheat sheet verbatim, no tool calls:

```
**🧠 `/brain` — koan's docs/ and specs/ knowledge bundles.** Both are OKF v0.1
bundles that compound across sessions: consult them before planning, extend them
after implementing.

| You want to…                                          | Do this                          |
| ------------------------------------------------------ | --------------------------------- |
| Consult before planning/implementing/refactoring        | `/brain ask <question>` (alias: `/brain search <question>`) |
| Close the loop after implementing a feature             | `/brain sync`                     |
| Save an article, file, or idea as a durable reference    | `/brain ingest <path|url|"idea">` |
| Health-check OKF conformance                             | `/brain lint [--strict]`          |
| Repair the folder scaffold                               | `/brain init`                     |
| Show this help                                           | `/brain help`                     |

Notes: `/brain sync` is a soft pre-PR reminder, not a hard gate. `/brain ingest` always
needs an explicit argument (no bulk backlog). `/brain search` is a plain alias of
`/brain ask`. See `docs/SCHEMA.md` / `specs/SCHEMA.md` for the frontmatter/tagging
conventions, `docs/SPEC.md` for the underlying format spec.
```

## Bare invocation (no arguments)

Print the same cheat sheet as `help`, then you **MUST** call `AskUserQuestion` —
"Which /brain action do you want?", header `Action`, one option per subcommand:
`ask`, `sync`, `ingest`, `lint`, `init`. Never guess the intended action from
surrounding conversation context.

## `ask <question>` / `search <question>`

`search` is a plain alias — identical behavior.

1. Read `wiki/index.md` first, always — it's the flattest, fastest, already-summarized
   catalog spanning both bundles. Never grep `docs/`/`specs/` blindly before reading it.
2. From the one-line summaries there, pick the candidate page(s) that look relevant.
3. Open only those pages, plus their backlinks if useful context (`grep -rl
   "(path/to/page.md)" docs/ specs/` — plain relative/root-absolute markdown links, per
   `docs/SPEC.md` §4, never `[[wikilinks]]`).
4. Synthesize an answer, citing pages by path.
5. If coverage is missing: say so plainly — never confabulate an answer. Suggest
   `/brain ingest` (if it's external material worth capturing) or just flag the gap.
   Don't fall back to a plugin search command for this — there isn't one that works
   here (see the note above); a genuinely open-ended question that the index can't
   resolve is itself the signal that coverage is missing.
6. Descend into `docs/index.md` / `specs/index.md` → per-folder `index.md` instead of
   `wiki/index.md` only for an OKF-conformance/browsing task (e.g. "what topics exist
   under specs/components?") or if `wiki/index.md` is visibly missing an entry for a
   page that clearly exists (a stale-index signal — rare, since `/brain sync` and the
   CI backstop keep it current).
7. **In Plan Mode**, state in the plan's Context section what `docs/`/`specs/` said (or
   that nothing relevant was found) before the recommended approach — this formalizes
   the existing CLAUDE.md requirement.

## `sync`

The interactive equivalent of the CI auto-fix pass (`wiki_sync_ci.py`) — run this after
implementing a feature, before finishing the change:

1. Identify the page(s) under `docs/` or `specs/` touched or created by the current
   change.
2. Ensure frontmatter is complete per `docs/SCHEMA.md` / `specs/SCHEMA.md`: `type`,
   `title`, `tags`, `created` (unchanged), `updated` (bump to today from real git-log
   dates, don't guess), and add `description` if missing (one sentence — organic
   backfill only, never touch *other* untouched pages).
3. Run `python3 scripts/okf_backfill.py indexes` to regenerate any stale bundle-root or
   per-folder `index.md` — **never hand-edit a generated `index.md`**.
4. Add or update the page's entry in `wiki/index.md`.
5. Present the frontmatter/index changes as a normal diff — not a separate
   "propose, wait for approval" step (wiki bookkeeping is exempt from that, per
   `wiki/SCHEMA.md`'s "Workflow customizations").

## `ingest <path|url|"idea sentence">`

Captures **external** material — an article, an existing file, or a stray idea worth
preserving — as a durable, cited `docs/reference/*.md` page. Full procedure in
`references/acquisition.md`; summary:

1. **Acquire** the source verbatim into the git-ignored `raw/` scratch directory (never
   `git add -f` it): a URL → `raw/articles/<slug>.md`; a file path → `raw/files/<slug>.<ext>`
   (copy, never move); a pasted idea → `raw/notes/YYYY-MM-DD-<slug>.md`.
2. **Compile** a `type: reference` page under `docs/reference/` (create the folder and
   its `index.md` on first use — `okf_backfill.py indexes` picks it up automatically,
   no script change needed). Frontmatter carries the usual fields plus `raw:` (local
   provenance, not dereferenceable by other clones) and, if the source had one,
   `resource:` (the external URI — a durable citation). Body ends with a `# Citations`
   section (`docs/SPEC.md` §7).
3. **Contradictions**: never silently overwrite conflicting existing content — state
   both claims with citations, note the page as contested in its body, and flag it to
   the user.
4. Link the new page from related pages where it makes sense; run
   `python3 scripts/okf_backfill.py indexes`; add a `wiki/index.md` entry.
5. Commit via the project's normal commit flow — only the compiled page + the
   regenerated indexes are staged, **never `raw/`**.
6. Discuss the takeaway with the user.

## `lint [--strict]`

Run `python3 scripts/wiki_check.py --full` (add `--strict` when asked for a hard
conformance-only check — see the module docstring in `scripts/wiki_check.py` for the
HARD-vs-SOFT split). This is the real health check for both bundles.

Don't fall back to `/wiki:lint` — its bundled `wiki_lint.py` uses `Path.rglob()`, which
doesn't follow the `wiki/docs`, `wiki/specs-components`, `wiki/specs-skills` symlinks, so
it reports 0 pages (see `wiki/SCHEMA.md`). `/brain lint` is the only conformance check
this repo relies on.

Present findings as proposals — never silently rewrite content. For HARD findings, fix
the frontmatter directly. For SOFT findings that are index-shaped (stale/missing
`index.md`), run `python3 scripts/okf_backfill.py indexes` rather than hand-editing.

## `init`

Verify or repair the scaffold (not content conformance — that's `lint`):

- `raw/{articles,notes,files}/.gitkeep` present
- `docs/SPEC.md`, `docs/SCHEMA.md`, `specs/SCHEMA.md`, `docs/index.md`, `specs/index.md`
  present
- `wiki/` symlinks (`docs`, `specs-components`, `specs-skills`) intact
- `.gitignore` carries the `.claude/skills/brain` re-inclusion and `raw/` exclusion
  entries

Idempotent — never overwrites existing content, only creates what's missing. Offer to
run `python3 scripts/okf_backfill.py indexes` if any generated index.md is absent.
