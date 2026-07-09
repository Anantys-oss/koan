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

## [2026-07-04] lint | Ran the bundled wiki_lint.py/wiki_stats.py directly (plugin not yet installed via marketplace) and found they report 0 pages — Path.rglob() doesn't follow the wiki/docs, wiki/specs-components, wiki/specs-skills symlinks. Documented as a known limitation in SCHEMA.md (does not affect /wiki:query's index→page path or scripts/wiki_check.py, both of which read real paths directly). Performed a manual cross-link improvement pass instead: specs/components/core.md, agent-loop.md, bridge.md (previously zero docs/ references) now point to their matching docs/architecture/ pages; all 12 specs/skills/*.md now reference docs/users/skills.md + docs/users/user-manual.md; 7 of 8 docs/architecture/*.md pages now reference their matching specs/components/ page (docs/architecture/memory.md deliberately skipped — no genuine component-spec counterpart exists for the memory subsystem).

## [2026-07-08] query | Answered "explain koan hooks" by reading koan/app/hooks.py, koan/app/automation_rules.py, and instance.example/hooks/README.md (the lifecycle-hook system had no dedicated docs/ page — only a one-line mention in shared-state.md). Filed the synthesized answer back as a new page, docs/architecture/hooks.md (events, instance-wide vs skill-bound hooks, fire-and-forget/trust model, the declarative automation-rules layer and its loop guard), added it to docs/README.md's Implementation Reference list and wiki/index.md's Architecture section, and cross-linked it from docs/architecture/shared-state.md's `hooks/` bullet.
