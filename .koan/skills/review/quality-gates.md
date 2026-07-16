# Koan-repo review gates

Extra must-checks for `/review` on this repository. Append-only: do not ignore the built-in review prompt.

## Always flag when present

- **Undeclared durable-contract change** — edits under `specs/components/**` or `specs/skills/**` without the PR declaring an architectural change.
- **Privacy leak on added lines** — private slash-command names, bot handles, Jira key prefixes, customer project names, concrete private case numbers. Placeholders only in public artifacts.
- **Tests that call real boundaries** — Claude/Telegram/provider subprocesses, or unmocked `gh` that would sleep on retry. Prefer mocks at `run_gh` / `api` / `format_and_send`.
- **Missing `KOAN_ROOT`** in new tests that import app modules which require it.
- **Inline LLM prompts** in Python — prompts belong in `.md` files (`load_prompt` / `load_skill_prompt`).
- **Core skill surface drift** — skill add/remove/rename without updating `docs/users/user-manual.md` and `docs/users/skills.md`.
- **REST API drift** — route/method/auth changes without regenerating `koan/openapi.yaml` (`make openapi`).
- **Specs/docs ignored** — non-trivial design change with no consult of `wiki/index.md` / relevant docs (note when the PR should have done so).

## Severity calibration (this repo)

- Undeclared durable-spec edits, privacy leaks, and real external calls in tests → **critical** or **warning**, never suggestion-only.
- Do not set `lgtm: false` on suggestion-only findings.

## Verify before asserting

Use Read/Grep on surrounding code. Unverified claims about callers, mocks, or existing helpers → mark unverified or drop.
