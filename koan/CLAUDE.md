# koan/ — Python package guidance

This file is auto-loaded by Claude Code when working anywhere under `koan/`
(all project Python lives here: `app/`, `tests/`, `skills/`, `system-prompts/`).

## Test suite

- **`KOAN_ROOT` must be set** when running tests. Many modules (`utils.py`, `awake.py`) check for `KOAN_ROOT` at import time and raise `SystemExit` if it's missing. Use `KOAN_ROOT=/tmp/test-koan` (or any path) as a prefix: `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/ -v`
- Never call Claude (subprocess) in tests. Mock `format_and_send` which invokes Claude CLI for message formatting.
- With `runpy.run_module()` (CLI tests), patch both `app.<module>.format_and_send` **and** `app.notify.format_and_send` — `runpy` re-executes the module so the import-level binding escapes the first patch.
- When `load_dotenv()` would reload env vars from `.env` (defeating `monkeypatch.delenv`), patch `app.notify.load_dotenv` too.
- **Test behavior, not implementation.** Unless the project's own conventions say otherwise, tests should validate what code does (inputs → outputs, side effects, observable state), not how it does it. Mocking internal dependencies of the unit under test is fine, but tests must never read or inspect actual source code to verify whether specific code is present or absent — that couples tests to implementation text rather than behavior. Prefer asserting on return values, raised exceptions, file contents, or other observable outcomes.
- **Mock above retry_with_backoff, not below.** When testing error handling for `run_gh()`/`api()` callers, mock at the `run_gh` or `api` level — never at `app.github.subprocess.run`. Mocking subprocess.run causes `retry_with_backoff` to sleep 1+2+4s between retries, adding 7+ seconds per test. See `testing-anti-patterns.md` Anti-Pattern 6.

## Python compatibility

All code must support **Python 3.11+**. Do not use syntax or stdlib features introduced after Python 3.11 (e.g., `type` statements from 3.12, `TypeVar` defaults from 3.13). CI tests against multiple Python versions — if it doesn't run on 3.11, it doesn't ship.

## Linting

All Python code must pass **ruff** (`make lint`) before committing. The ruff configuration lives in `pyproject.toml` under `[tool.ruff]`.

- Run `make lint` to check for violations. Fix all errors before pushing.
- Currently enforced rule sets: **PERF** (performance anti-patterns). New rule sets will be added incrementally as existing violations are cleaned up.
- Test files (`koan/tests/*`) are exempt from PERF rules via `per-file-ignores`.
- When adding new code, avoid introducing violations from rule sets not yet enforced project-wide (E, F, W, I, B are good hygiene even though not yet gated in CI).
- Do not disable ruff rules with `# noqa` comments unless there is a clear, documented reason. Prefer fixing the violation.

## Python conventions

- **Temp files & provider locks** live under a per-uid directory from `utils.koan_tmp_dir()` (`$XDG_RUNTIME_DIR/koan`, else `/tmp/koan-<uid>/`, mode `0700`), overridable via `KOAN_TMP_DIR`. This keeps multiple users running Kōan on the same host from colliding on shared `/tmp` paths (notably the provider invocation lock). The dir is per-_uid_, not per-instance, because provider auth state is per-user. New code that needs a scratch file in `/tmp` MUST pass `dir=koan_tmp_dir()` to `tempfile.*`; agent prompts that write to `/tmp` MUST use a `mktemp` pattern (never a fixed name).
- Tests use temp directories and isolated env vars — no real Telegram calls
- **No inline prompts in Python code** — LLM prompts MUST be extracted to `.md` files. Skill-bound prompts go in `skills/<scope>/<name>/prompts/` and are loaded via `load_skill_prompt()`. Infrastructure prompts used by `koan/app/` modules stay in `koan/system-prompts/` and are loaded via `load_prompt()`. Reusable prompt fragments live in `koan/system-prompts/_partials/` and are included via `{@include partial-name}` directive (resolved at load time by `prompts.py`).
