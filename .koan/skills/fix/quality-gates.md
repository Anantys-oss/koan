# Koan-repo fix gates

Extra must-checks for `/fix` on this repository.

## Do

- Prefer a **failing regression test first** when the bug is reproducible in unit/integration form.
- Run tests with `KOAN_ROOT` set to a temp dir (e.g. `/tmp/test-koan`).
- Mock external boundaries: `run_gh` / `api`, Telegram `format_and_send`, provider CLIs. Never hit real Claude/Telegram in tests.
- For `run_gh` error-path tests, mock at `run_gh` or `api` — not raw `subprocess.run` (avoids real retry backoff sleeps).
- Stay on a `koan/*` (or configured prefix) branch; never commit to `main`.
- If the fix touches a component with a durable spec under `specs/components/` or a skill under `specs/skills/`: **read the spec first**. Do not bend the durable contract to match code after the fact; contract-first + declare architectural change when intentional.
- Put LLM prompt text in `.md` files, not Python string literals.
- Keep system prompts generic (no private owner/operator names).

## Don’t

- Ship private identifiers in source, tests, fixtures, docs, or commit messages.
- Skip `make lint` for Python changes (3.11+ only; no post-3.11 syntax).
- Leave core skill behavior changed without user-manual / skills.md updates.
