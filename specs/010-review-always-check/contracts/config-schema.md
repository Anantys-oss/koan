# Contract: `.koan/config.yaml` schema + function signatures

## 1. `.koan/config.yaml` (repo-owner-facing schema)

```yaml
# <target-repo>/.koan/config.yaml — optional, checked into the target repo.
review:
  # File globs. Any changed file whose path OR basename matches is PINNED:
  # included in the reviewed diff ahead of budgeted files, never silently
  # skipped by diff compression while budget remains.
  always_check:
    - "SKILL.md"      # matches at any depth (basename match)
    - "*.md"          # matches any Markdown file (path match; '*' spans '/')
    - "docs/api/*"    # matches files under docs/api/
```

- All keys optional. Absent file / empty / malformed ⇒ no-op (no pins).
- Unknown top-level keys and unknown `review.*` keys are ignored (forward-compatible).
- Glob semantics: `fnmatch` (`*`, `?`, `[seq]`), case-sensitive, matched against both the
  full repo-relative path and the basename.

## 2. `app.project_koan` additions

```python
def read_koan_config(project_path: str) -> dict:
    """Parse <project_path>/.koan/config.yaml. Returns {} when absent, empty,
    unparseable, or not a mapping (fail-safe; at most one diagnostic logged).
    Never raises."""

def get_review_always_check(project_path: str) -> list[str]:
    """Return the honored review.always_check glob list (str items only, blanks
    dropped, capped at _MAX_ALWAYS_CHECK_PATTERNS=100 patterns / 200 chars each).
    Returns [] when unset/malformed. Never raises."""
```

Constants: `_MAX_ALWAYS_CHECK_PATTERNS = 100`, `_MAX_PATTERN_LEN = 200`.

## 3. `app.diff_compressor` additions

```python
def path_matches_any(path: str, patterns: list[str]) -> bool:
    """True iff `path` or its basename matches any glob in `patterns`
    (fnmatchcase, case-sensitive). Empty patterns ⇒ False."""

def compress_diff(
    raw_diff: str,
    token_budget: int = 80_000,
    pinned_patterns: Optional[list[str]] = None,   # NEW, default no-pin
) -> CompressedDiff:
    """Unchanged behavior when pinned_patterns is falsy (byte-identical).
    When set, files matching a pin sort first in the inclusion order
    (key gains a leading `0 if pinned else 1` term) so they consume budget
    before non-pinned files and are not fully skipped while budget remains."""
```

## 4. `app.utils` addition

```python
def truncate_diff_with_skips(
    diff: str,
    max_chars: int,
    pinned_patterns: Optional[list[str]] = None,   # NEW, default no-pin
) -> tuple[str, list[str]]:
    """Unchanged when pinned_patterns is falsy. When set, `diff --git` blocks
    matching a pin are stably moved to the front before the greedy fit, so they
    are offered budget first. Reuses diff_compressor.path_matches_any."""
```

`truncate_diff` (the single-return wrapper) keeps its signature; it forwards no pins
(callers that need pins use the `_with_skips` form).

## 5. `app.review_runner.build_review_prompt` wiring

- Compute `always_check = project_koan.get_review_always_check(project_path)` (empty when
  `project_path` is None/unset).
- Pass `pinned_patterns=always_check` into both `compress_diff(...)` and
  `truncate_diff_with_skips(...)`.
- When `always_check` produced ≥1 actual pin, log one `log("review", ...)` line.
- `_build_coverage_note(...)` is called unchanged; pinned-and-included files are absent
  from `budget_skipped`, so they never appear in the note.

### Backwards compatibility

- Every existing caller of `compress_diff` / `truncate_diff_with_skips` /
  `fetch_pr_context` that omits `pinned_patterns` is byte-identical.
- The review output **schema** (`review_schema.py`) and **prompt templates** are
  unchanged ⇒ eval golden cases / baseline need no reconciliation.
