# Contract: `scripts/spec_change_guard.py`

## Public functions (unit-tested)

```python
def is_durable_contract(path: str) -> bool
```
True iff `path` is a durable design contract: matches `specs/components/*.md` or
`specs/skills/**.md`, is not an `index.md`, and is not
`specs/skills/SKILL_SPEC_TEMPLATE.md`. Accepts POSIX repo-relative paths.

```python
def has_architecture_declaration(pr_body: str | None) -> bool
```
True iff `pr_body` contains a checked declaration line
(`^\s*[-*]\s*\[x\]\s*.*architectural change`, `re.IGNORECASE | re.MULTILINE`). `None`,
empty, unchecked, or phrase-absent → False.

```python
def is_frontmatter_only_change(old_text: str, new_text: str) -> bool
```
True iff the Markdown body below the YAML frontmatter is unchanged `old_text` ->
`new_text`. The durable *contract* is the body; a diff touching only frontmatter (a
`/brain sync` or wiki-sync `updated:` date bump) is bookkeeping, exempt per the
constitution's wiki-bookkeeping exemption (Principle I). `_body_below_frontmatter()`
strips a leading `---`…`---` block (an unterminated block is treated as all body, so a
malformed file can't masquerade as bookkeeping).

```python
def evaluate(changed_files: list[str], pr_body: str | None) -> GuardVerdict
```
Pure decision function (see data-model.md state transitions). Returns a verdict with
`ok`, `undeclared_contracts`, `fail_closed`.

```python
def changed_files(base_ref: str) -> list[str]
```
Wraps `git diff --name-only --diff-filter=AMD --no-renames <base_ref>...HEAD`. The
`AMD` filter includes **D**eletions (retiring a contract is architectural too — a bare
`git rm specs/components/core.md` must not bypass the gate); `--no-renames` splits a
rename into delete-old + add-new so both sides are evaluated. It then reads each
contract's content at the merge base and at HEAD (`_file_at_ref`, `_merge_base`) and
drops frontmatter-only changes via `is_frontmatter_only_change()`; a newly added or
deleted contract is never treated as bookkeeping. The only impure function; not
exercised by unit tests (integration-only).

## CLI

```
python3 scripts/spec_change_guard.py \
    [--base-ref origin/main] \
    [--pr-body-file PATH | --pr-body -] \
    [--changed-file PATH ...]     # test/override hook, bypasses git
```

- `--base-ref` (default `origin/main`): diff base for `changed_files()`.
- `--pr-body-file PATH`: read the PR body from a file; `--pr-body -` reads stdin.
- `--changed-file` (repeatable): supply the changed set explicitly (used by CI when the
  diff is computed elsewhere, and to keep the tool git-independent in tests).

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | No durable contract changed, OR changed with a valid declaration |
| 1 | Durable contract changed without a declaration, OR fail-closed (contracts changed, no PR body supplied) |
| 2 | Usage error (bad args) |

### Output

On failure (exit 1), prints to stderr:
- the list of undeclared durable contracts,
- the exact declaration line to add (copyable),
- a one-line pointer to `docs/design/spec-changes-are-architectural.md`.

On success, prints a one-line confirmation to stdout.

## CI contract (`.github/workflows/spec-change-guard.yml`)

- Trigger: `pull_request` (`opened`, `synchronize`, `reopened`, `edited`) → `main`.
  (`edited` so re-checking the box on an already-open PR re-runs the gate.)
- Permissions: `contents: read` only. No push, no write.
- Steps: checkout (full depth) → setup-python 3.11 → write
  `${{ github.event.pull_request.body }}` to a file → run the guard with
  `--base-ref origin/${{ base.ref }} --pr-body-file <file>`.
- Fork PRs: runs read-only; a failing check is visible to maintainers. No secret needed.
