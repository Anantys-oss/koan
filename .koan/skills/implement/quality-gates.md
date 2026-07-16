# Koan-repo implement gates

Extra must-checks for `/implement` on this repository.

## Do

- Consult `wiki/index.md` then relevant `docs/` / durable `specs/` before designing non-trivial work. State what you found (or that coverage is missing).
- Create a `koan/*` (or configured prefix) branch; never commit to `main`.
- Tests: set `KOAN_ROOT` to a temp path; mock Claude/Telegram/`gh` at the documented boundaries.
- Python 3.11+ only; changes must pass `make lint`.
- LLM prompts live in `.md` files loaded via `load_prompt` / `load_skill_prompt`.
- Durable-contract work: contract-first under `specs/components/**` or `specs/skills/**`, rare, and declared on the PR.
- After user-visible or skill surface changes: update matching docs in the same branch. Core skills → `docs/users/user-manual.md` + `docs/users/skills.md`. REST routes → `make openapi`.

## Don’t

- Expand scope beyond the issue/plan without saying so.
- Duplicate large chunks of `CLAUDE.md` into new prompts — follow existing patterns.
- Leak private operator identifiers into the public tree or commit subjects.
