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

- **Temp files & provider locks** live under a per-uid directory from `utils.koan_tmp_dir()` (`$XDG_RUNTIME_DIR/koan`, else `/tmp/koan-<uid>/`, mode `0700`), overridable via `KOAN_TMP_DIR`. This keeps multiple users running Kōan on the same host from colliding on shared `/tmp` paths (notably the provider invocation lock). The dir is per-_uid_, not per-instance, because provider auth state is per-user. New code that needs a scratch file in `/tmp` MUST pass `dir=koan_tmp_dir()` to `tempfile.*`; agent prompts that create scratch files MUST use `mktemp "${TMPDIR:-/tmp}/koan-<purpose>-XXXXXX"` (never a fixed name or a bare `/tmp` path). Missions and parallel sessions run with a per-mission `TMPDIR` under `koan_tmp_dir()` (created by `run_claude_task` / `spawn_session` via `create_mission_tmp_dir()`), reaped when the mission ends; stale dirs from crashed runs are swept at startup by `reap_stale_mission_tmp_dirs()`.
- Tests use temp directories and isolated env vars — no real Telegram calls
- **No inline prompts in Python code** — LLM prompts MUST be extracted to `.md` files. Skill-bound prompts go in `skills/<scope>/<name>/prompts/` and are loaded via `load_skill_prompt()`. Infrastructure prompts used by `koan/app/` modules stay in `koan/system-prompts/` and are loaded via `load_prompt()`. Reusable prompt fragments live in `koan/system-prompts/_partials/` and are included via `{@include partial-name}` directive (resolved at load time by `prompts.py`).

## Code structure & size limits

Keep units small so they stay readable, testable, and reviewable. These are **soft
limits** — a signal to stop and consider splitting, not a hard gate that blocks a PR.

- **Functions ≤ 30 lines.** When a function grows past ~30 lines, treat it as a prompt to
  extract cohesive steps into named helpers. A long function usually hides two or three
  smaller ones with clearer names. Don't split mechanically at line 31 — split at the
  seam where the logic changes concern.
- **Files ≤ 600 lines.** When a module passes ~600 lines, consider splitting it along a
  responsibility boundary into a package or sibling modules. `koan/app/` already favors
  many focused modules over few large ones (see `koan/app/CLAUDE.md`); follow that grain.
  Preserve existing `@patch("app.module.X")` test targets when extracting — use the lazy
  `import app.module as _mod` + `_mod.X` access pattern so name resolution still routes
  through the original namespace.
- **Prefer refactoring over growth.** If a change would push a function or file well past
  these limits, factor first, then add the feature to the smaller pieces. Don't append to
  a module that's already too large just because it's where the related code lives.
- These limits are guidance, not enforced by CI. Exceeding them is acceptable when a split
  would genuinely hurt clarity (e.g. a single cohesive state machine or a generated file) —
  but the default is to split, and the burden is on the large unit to justify itself.

## SDLC hygiene

- **One concern per commit, one concern per PR.** Keep changes focused and reviewable;
  don't mix a refactor with a feature. A reviewer should be able to hold the whole diff in
  their head. Unrelated cleanups go in a separate branch.
- **Test-first for behavior changes.** For a bug fix, write the failing test first, then
  fix it (failing test → fix → passing test). New behavior ships with the tests that cover
  it — never a feature with zero new coverage.
- **Test observable behavior, not implementation.** Assert on return values, exceptions,
  and observable state — not on how internals were called. See the test-suite section above
  and `koan/tests/testing-anti-patterns.md`.
- **Names carry intent.** Prefer a clear name over a comment. Comment the *why*, not the
  *what*; code says what it does, comments say why it does it that way.
- **No dead code.** Delete unused code rather than commenting it out — git remembers. Avoid
  speculative generality: build what the mission asks for, not what it might need later.
- **Fail loud at boundaries.** Validate untrusted input (user, network, filesystem, external
  APIs) at the edge; never swallow errors silently. A bare `except:` that hides a failure is
  a latent bug.
- **Leave it cleaner than you found it.** Small, in-scope improvements to code you're already
  touching are welcome; sweeping unrelated rewrites are not.
