# Quickstart / Validation: `review.always_check`

Proves the feature end-to-end. See [contracts/config-schema.md](./contracts/config-schema.md)
for signatures and [data-model.md](./data-model.md) for validation rules.

## Prerequisites

- `KOAN_ROOT` set (required by the test suite).
- Feature branch checked out; `make setup` done.

## Unit / integration validation

```bash
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest \
  koan/tests/test_project_koan.py \
  koan/tests/test_diff_compressor.py \
  koan/tests/test_review_runner.py -v
```

Expected: new tests pass —

1. **Config reader** (`test_project_koan.py`): absent file → `{}` / `[]`; valid
   `review.always_check` → the list; malformed YAML / non-list / non-str items →
   fail-safe `[]` (no raise); >100 patterns capped.
2. **Pin-aware compression** (`test_diff_compressor.py`): with a diff larger than the
   budget, a small file matching a pin (`SKILL.md`) is present in `diff_text` and NOT in
   `skipped_files`, while an unpinned large file IS skipped. `pinned_patterns=None` →
   output byte-identical to the pre-feature result.
3. **Char-backstop** pinning: `truncate_diff_with_skips(..., pinned_patterns=["*.md"])`
   keeps the `*.md` block and skips others when over `max_chars`.
4. **End-to-end wiring** (`test_review_runner.py`): `build_review_prompt` with a
   `project_path` whose `.koan/config.yaml` pins `SKILL.md` → the returned prompt's DIFF
   contains the SKILL.md block and the returned `coverage_note` does NOT list it.

## Manual behavioral check (no live Claude)

```python
from app.diff_compressor import compress_diff, path_matches_any

assert path_matches_any("plugins/x/SKILL.md", ["SKILL.md"])     # basename match
assert path_matches_any("docs/deep/guide.md", ["*.md"])          # path match, * spans /
assert not path_matches_any("main.go", ["*.md"])

# Pin survives compression on an oversized diff:
res = compress_diff(big_diff_touching_skill_md_and_big_go,
                    token_budget=200, pinned_patterns=["SKILL.md"])
assert "SKILL.md" in res.diff_text
assert not any("SKILL.md" in s for s in res.skipped_files)
```

## Docs / sample validation

- `docs/reference/koan-config.sample.yaml` exists, is valid YAML (`yaml.safe_load`
  succeeds), demonstrates `review.always_check`, and shows future keys commented out.
- `docs/users/koan-md.md` documents `.koan/config.yaml`, the schema, matching semantics,
  fail-safe behavior, and the diff-compression relationship.
- `make lint` passes; `/brain sync` bookkeeping done.

## Regression guard

- With NO `.koan/config.yaml`, a review over the same PR produces byte-identical output
  (SC-002): confirmed by the `pinned_patterns=None` byte-identical assertions above and
  the untouched existing compressor/backstop tests.
