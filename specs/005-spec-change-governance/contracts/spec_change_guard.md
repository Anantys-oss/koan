# Contract: `scripts/spec_change_guard.py`

The gate is **relaxed to additive-friendly**: adding a new contract file, or appending
new lines/paragraphs to an existing one, passes freely — growing the specs never
invalidates prior design. Only a **destructive** change — deleting a contract, or
rewriting/removing existing body lines — needs an architectural-change declaration,
because that is what can silently contradict a previously reviewed contract.

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
def is_addition_only(old_text: str, new_text: str) -> bool
```
True iff the Markdown body `old_text` -> `new_text` only *inserts* lines — every
`difflib.SequenceMatcher` opcode is `equal` or `insert`, so no existing body line is
deleted or replaced. Appending a paragraph or inserting a new section between existing
ones is addition-only; rewriting, deleting, or reordering a line is not. Additive growth
can never contradict a reviewed contract, so it is exempt from the declaration.

```python
@dataclass
class ContractChange:
    path: str
    kind: str = "rewritten"      # "added" | "deleted" | "rewritten" (human-readable)
    destructive: bool = True     # the gate signal
```
One classified durable-contract change. `destructive` drives the gate; `kind` is for
output only.

```python
def evaluate(changes: list, pr_body: str | None) -> GuardVerdict
```
Pure decision function (see data-model.md state transitions). `changes` is a list of
`ContractChange` **or** bare path strings; a bare string carries no diff info, so it is
treated conservatively as a `destructive` rewrite (used by `--changed-file` and tests).
Passes when no destructive change is present (additive-only), or when every destructive
change is covered by a declaration. Returns a verdict with `ok`, `undeclared_contracts`,
`allowed_additions`, `flagged`, `fail_closed`.

```python
def contract_changes(base_ref: str) -> list[ContractChange]
```
Wraps `git diff --name-status --diff-filter=AMD --no-renames <base_ref>...HEAD`. The
`AMD` filter includes **D**eletions (retiring a contract is destructive — a bare
`git rm specs/components/core.md` must not bypass the gate); `--no-renames` splits a
rename into delete-old + add-new so both sides are evaluated. Each contract is then
classified via `_classify()`: `A` → additive; `D` → destructive; `M` → compare
merge-base body to HEAD body (`_file_at_ref`, `_merge_base`), dropping frontmatter-only
bookkeeping (`is_frontmatter_only_change()`), marking `is_addition_only()` diffs additive
and all others destructive. The only impure function; not exercised by unit tests
(integration-only).

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
| 0 | No destructive contract change (additive-only or none), OR destructive change with a valid declaration |
| 1 | Durable contract removed/rewritten without a declaration, OR fail-closed (destructive change, no PR body supplied) |
| 2 | Usage error (bad args) |

### Output

A rich, unicode-framed block (`━` rule, `🏛️` header) so issues and conflicting files are
easy to spot in the CI log.

On failure (exit 1), prints to stderr:
- a `❌ FAILED` banner,
- the `⚠️` list of flagged contracts with their kind (`deleted` / `rewritten`),
- the `➕` list of additive contracts (allowed, informational),
- the exact declaration line to add (copyable),
- an `ℹ️` pointer to `docs/design/spec-changes-are-architectural.md`.

On success (exit 0), prints a `✅ PASSED` block to stdout, listing any `➕` additive
contract changes that were allowed without a declaration.

## CI contract (`.github/workflows/spec-change-guard.yml`)

- Trigger: `pull_request` (`opened`, `synchronize`, `reopened`, `edited`) → `main`.
  (`edited` so re-checking the box on an already-open PR re-runs the gate.)
- Permissions: `contents: read` only. No push, no write.
- Steps: checkout (full depth) → setup-python 3.11 → write
  `${{ github.event.pull_request.body }}` to a file → run the guard with
  `--base-ref origin/${{ base.ref }} --pr-body-file <file>`.
- Fork PRs: runs read-only; a failing check is visible to maintainers. No secret needed.
