# KOAN.md — autonomous-agent guidance for this repository

Interactive Claude Code sessions load `CLAUDE.md` / `AGENTS.md`, not this file.
These rules apply only when Kōan runs missions here.

## Priorities

1. **The agent proposes. The human decides.** Never merge to `main` unsupervised.
2. **Docs/specs first** for non-trivial work: start at `wiki/index.md`, then the matching pages under `docs/` and durable `specs/components/` / `specs/skills/`. Trust current code if docs disagree; update docs in the same change.
3. **Branches:** create `<prefix>/*` (default `koan/`); never commit directly to `main`.
4. **Privacy:** the public tree must not contain private operator identifiers (private slash commands, bot display names, Jira key prefixes, customer project names). Use placeholders (`my_fix`, `@koan-bot`, `PROJ-NNN`, `my-toolkit`) in tests, docs, examples, and commit messages.
5. **After behavior changes:** update the matching `docs/` page (and durable specs only contract-first, declared as architectural). Core skill add/remove/change → `docs/users/user-manual.md` + `docs/users/skills.md`. REST route changes → `make openapi` and commit `koan/openapi.yaml`.

## Per-skill quality gates

Mission skills load extra checklists from `.koan/skills/<skill>/` (review, fix, implement, rebase, plan, pr). Prefer those for skill-specific must-checks; keep this file short.
