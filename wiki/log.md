# Wiki Log

Append-only chronological record of operations on the wiki. Each entry begins with `## [YYYY-MM-DD] <op> | <description>` so it's parseable with `grep "^## \[" log.md | tail -N`.

Operations:
- `ingest` — not used in this wiki (no raw-source-compilation layer); see `SCHEMA.md` "Workflow customizations". Docs/specs are created/updated directly per the existing `CLAUDE.md` discipline.
- `query` — a question was answered against the wiki (typically only logged when the answer was filed back as synthesis).
- `lint` — a health check was run.
- `schema` — the schema was modified.
- `shard` — an index was sharded.
- `status` — a `specs/<NNN-slug>/` feature's computed status changed (draft → in-progress → shipped).

---

## [2026-07-04] schema | Bootstrapped LLM Wiki tooling (praneybehl/llm-wiki-plugin) spanning docs/ and the durable half of specs/ (specs/components, specs/skills): added wiki/SCHEMA.md documenting conventions and resolving the long-standing TODO(SPECS_DIR_COLLISION) between koan's own specs/ and speckit's per-feature folders, backfilled YAML frontmatter onto 77 pre-existing pages (56 docs, 8 component-specs, 12 skill-specs, specs/README.md), built wiki/index.md (including computed draft/in-progress status for the 3 active speckit feature folders), and set up wiki/docs, wiki/specs-components, wiki/specs-skills symlinks.
