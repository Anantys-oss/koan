# Acquisition procedures for `/brain ingest`

This is the detailed procedure `SKILL.md`'s `ingest` subcommand delegates to. It
follows an acquisition-then-compile pattern established elsewhere for markdown
knowledge bundles, adapted to koan's own `raw/` + `docs/reference/` layout.

## Why a `raw/` layer at all

Feature docs under `docs/`'s topic folders (`architecture/`, `providers/`, etc.) are
authored directly by whoever implements the feature — there's no external source to
snapshot. Ingested material is different: it originates outside the codebase (a web
article, an existing file, a stray idea), so a durable, unedited copy of the source is
worth keeping locally even though the *compiled* reference page is what actually gets
committed and cited. `raw/` is that local snapshot layer — a sibling of both `docs/`
and `specs/` at the repo root, git-ignored except for its three `.gitkeep`s, so neither
OKF bundle ever needs to exclude it from conformance checks.

## Branch 1 — a web URL

1. Fetch the page (`WebFetch`), convert to clean markdown.
2. Save to `raw/articles/<slug>.md`, prefixed with a one-line HTML comment:
   `<!-- source: <URL> | fetched: YYYY-MM-DD -->`.
3. If the fetch fails (paywall, 404, JS-only page), save whatever partial content you
   got and add a note about the gap — don't silently skip the ingest.
4. Compiled page (`docs/reference/<slug>.md`) frontmatter: `resource: <URL>` (the
   durable, citable reference) and `raw: raw/articles/<slug>.md` (local provenance —
   not dereferenceable by someone else's clone, since `raw/` is git-ignored).

## Branch 2 — a local file path

1. **Copy** (never move) the file into `raw/files/<slug>.<ext>`, preserving the
   original extension.
2. If it's a text file, prefix it with `<!-- source: <original path or description> |
   copied: YYYY-MM-DD -->`. Leave binaries untouched.
3. Compiled page frontmatter: `raw: raw/files/<slug>.<ext>`. Add `resource:` only if
   the original file itself carried an external identifier (e.g. it was itself
   downloaded from somewhere traceable).

## Branch 3 — a pasted idea / free-text sentence

1. Save verbatim to `raw/notes/YYYY-MM-DD-<slug>.md`, prefixed with `<!-- source:
   conversation | captured: YYYY-MM-DD -->`.
2. Prefer updating an existing `reference` page over minting a new, near-empty one if
   the idea clearly extends something already captured.
3. Compiled page frontmatter: no `resource:` (nothing external to cite beyond the
   conversation itself); `raw: raw/notes/YYYY-MM-DD-<slug>.md`.

## Naming

Kebab-case slugs derived from the title or URL. On a collision, append `-2`, `-3`, …
— never overwrite an existing `raw/` snapshot or compiled page.

## Compiling into `docs/reference/`

- Create `docs/reference/` (and its `index.md`, via `scripts/okf_backfill.py indexes`)
  on first use — don't pre-create it speculatively.
- Frontmatter: `type: reference`, `title`, `description` (one sentence), `tags:
  [reference]`, `created`/`updated` (today), plus `raw:` and optionally `resource:` as
  above.
- Body ends with a `# Citations` section (`docs/SPEC.md` §7) — at minimum, one entry
  pointing back at `resource:` (if set) or describing the conversation/file origin.
- Link the new page from any existing page it's clearly related to.

## Contradictions

If the ingested material conflicts with something already documented, never silently
overwrite the existing claim. State both, cited, in the new (or updated) page's body,
note the page as contested, and flag it to the user rather than picking a side
unilaterally.
