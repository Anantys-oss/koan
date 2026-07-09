---
type: doc
title: "OKF Specification (v0.1)"
description: "Normative Open Knowledge Format rules the docs/ and specs/ bundles conform to: frontmatter, index/log files, and conformance requirements."
tags: [architecture]
created: 2026-07-08
updated: 2026-07-08
---

# OKF Specification (v0.1)

OKF ("Open Knowledge Format") is a small, generic convention for representing knowledge —
the metadata, context, and curated insight around a system — as a directory of markdown
files with YAML frontmatter. It is not specific to koan: it borrows from an established
pattern for markdown knowledge bundles used elsewhere, adapted here as the shared
contract both `docs/` and `specs/` conform to (see `docs/SCHEMA.md` and
`specs/SCHEMA.md` for the repo-specific conventions layered on top of this spec).

The format is intentionally minimal. There is no schema registry, no central authority,
and no required tooling — a bundle is just files on disk that any reader (human or
agent) can open directly.

## 1. Terminology

- **Knowledge Bundle** — a self-contained, hierarchical collection of knowledge
  documents; the unit of conformance. koan has two: `docs/` and `specs/`.
- **Concept** — a single unit of knowledge: one markdown document.
- **Concept ID** — the file's path relative to the bundle root, minus `.md`
  (e.g. `architecture/overview.md` → `architecture/overview`).
- **Frontmatter** — the YAML block delimited by `---` lines at the top of a file.
- **Body** — everything after the frontmatter block.
- **Link** — a standard markdown link expressing a relationship to another concept.
- **Citation** — a link to an external source supporting a claim in the body.

## 2. Bundle structure

```
<bundle-root>/
├── index.md                  # Optional. Directory listing for progressive disclosure.
├── log.md                    # Optional. Chronological history of updates.
├── <concept>.md               # A concept at the bundle root.
└── <subdirectory>/            # Subdirectories group concepts by topic.
    ├── index.md
    ├── <concept>.md
    └── ...
```

**Reserved filenames**: `index.md` and `log.md` MUST NOT be used as concept documents —
they have their own structure (§4, §5). Every other `.md` file in the bundle is a
concept document.

## 3. Concept documents and frontmatter

Every concept is a UTF-8 markdown file: a frontmatter block followed by a body.

```yaml
---
type: <type name>            # REQUIRED
title: <display name>        # Recommended
description: <one-sentence summary>  # Recommended
tags: [<tag>, ...]           # Recommended
created: <date>              # koan extension, see below
updated: <date>              # koan extension, see below
resource: <canonical URI>    # Recommended for pages bound to an external asset
# ... other producer-defined fields
---
```

- **`type`** is the only required field: a short string used for routing, filtering, or
  presentation (e.g. `doc`, `overview`, `component-spec`, `reference`). OKF does not
  register types centrally — a bundle defines its own vocabulary (see `docs/SCHEMA.md` /
  `specs/SCHEMA.md`), and any reader MUST tolerate an unrecognized `type` rather than
  reject the page.
- **Recommended fields**, in priority order: `title`, `description` (a single sentence,
  used by index generators and search), `resource` (a canonical URI for the underlying
  asset a page documents, when one exists), `tags`, and a last-modified timestamp.
- **koan's `created`/`updated` extension**: upstream OKF recommends a single `timestamp`
  field. koan's two bundles instead carry `created` and `updated` — real dates pulled
  from git history for that file — because tracking both first-write and last-change
  dates is more useful for a fast-moving codebase than a single timestamp. This is a
  valid producer-defined extension: OKF requires consumers to preserve and tolerate
  unrecognized frontmatter keys, not to reject them.
- **Extensions**: a producer MAY add any additional key/value pairs. A consumer SHOULD
  preserve unknown keys when rewriting a file and SHOULD NOT reject a page for carrying
  fields it doesn't recognize.

### Body

The body is ordinary markdown with no required sections. A few heading names carry
conventional meaning when present:

- `# Schema` — structural details of what the concept documents (columns, fields, API
  shape).
- `# Examples` — worked examples.
- `# Citations` — external sources supporting claims in the body (see §6).

## 4. Cross-linking

Links between concepts are standard markdown links, in one of two forms:

- **Bundle-root-absolute** — begins with `/`, resolved relative to the bundle root, e.g.
  `[Provider Architecture](/architecture/providers.md)`. Recommended: stable even if the
  linking page moves.
- **Relative** — a normal relative path, e.g. `[Skills System](./skills-system.md)`.

The meaning of a link (e.g. "see also," "supersedes," "documents the same subsystem as")
is conveyed by the surrounding prose, not by link syntax. A consumer MUST tolerate a
broken link — it may point at knowledge that hasn't been written yet.

## 5. Index files

An `index.md` MAY appear at any directory level. It carries **no frontmatter**, with one
exception: the bundle-root `index.md` MAY declare the bundle's target OKF version via
`okf_version: "0.1"` — the only place frontmatter is permitted on a file named
`index.md` (§8).

Body format — one or more headed groups of bulleted links:

```markdown
# Section / Group Heading

* [Title 1](relative-url-1) - short description of item 1
* [Title 2](relative-url-2) - short description of item 2

# Another Section

* [Subdirectory](subdir/) - short description of the subdirectory
```

Entries SHOULD reuse the linked concept's `description` frontmatter rather than
inventing new prose. An `index.md` may be generated mechanically from that frontmatter
(koan's is — see `scripts/okf_backfill.py`).

## 6. Log files

A `log.md` MAY appear at any directory level as a flat, chronological, newest-first
record of changes to that directory:

```markdown
# Directory Update Log

## 2026-07-08
* **Update**: Refreshed [Provider Architecture](/architecture/providers.md) for the new CLI provider.
* **Creation**: Added [Skill Evaluation Harness](/operations/skill-evals.md).
```

Date headings MUST be ISO 8601 (`YYYY-MM-DD`). The leading bold word per entry is
convention, not a requirement.

## 7. Citations

A body's `# Citations` section lists numbered references to external sources:

```markdown
# Citations

[1] [Upstream provider docs](https://example.com/docs)
[2] [Internal design note](../design/decisions.md)
```

Citation targets may be absolute URLs, bundle-relative paths, or paths into a shared
references directory.

## 8. Conformance

A bundle is **OKF v0.1 conformant** if and only if:

1. Every non-reserved `.md` file in the bundle contains a parseable YAML frontmatter
   block.
2. Every such frontmatter block contains a non-empty `type` field.
3. Any `index.md` or `log.md` present follows the structure in §5/§6.

These three rules are the **only** hard requirements. Everything else in this spec —
recommended fields, tag taxonomies, link conventions, page sizing — is soft guidance. A
conformance checker MUST NOT hard-fail a bundle for a missing optional field, an
unrecognized `type`, a broken link, or the absence of an `index.md`; it may only warn.

## 9. Versioning

OKF versions are `<major>.<minor>`. A minor version is a backward-compatible addition; a
major version may break existing bundles. A bundle declares the version it targets via
`okf_version: "0.1"` in its root `index.md` frontmatter (§5).

koan currently has two independently-versioned bundles: `docs/index.md` and
`specs/index.md`, both targeting `"0.1"`.
