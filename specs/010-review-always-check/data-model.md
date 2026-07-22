# Data Model: Repo-level `.koan/config.yaml` — `review.always_check`

No persistent storage. These are the in-memory shapes read per-review.

## Entity: Repo review config (`.koan/config.yaml`)

Read from `<project_path>/.koan/config.yaml` at the target repo root.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `review` | mapping | no | `{}` | Container for review-scoped keys. |
| `review.always_check` | list[str] | no | `[]` | Ordered file-glob patterns. Files matching any pattern are pinned. |

- Top-level unknown keys are ignored (forward-compatible surface).
- Unknown keys under `review` are ignored.
- The file, `review`, and `always_check` are each independently optional.

### Validation rules (fail-safe → default)

| Input shape | Result |
|---|---|
| file absent | `{}` → `always_check = []` (byte-identical no-op) |
| unparseable YAML | `{}` + one diagnostic |
| top-level not a mapping | `{}` + one diagnostic |
| `review` not a mapping | `always_check = []` |
| `always_check` not a list | `[]` |
| `always_check` list with non-str items | non-str items dropped; str items kept |
| blank / whitespace-only pattern | dropped |
| > 100 patterns | first 100 kept, rest dropped + one diagnostic |
| pattern length > 200 chars | that pattern dropped + one diagnostic |

## Entity: Pinned file (derived, per-review)

A changed file in the PR diff whose repo-relative path or basename matches ≥1
`always_check` pattern.

- **Match rule**: `fnmatch.fnmatchcase(path, pat) or fnmatch.fnmatchcase(basename(path), pat)`
  for any `pat` in the honored `always_check` list (case-sensitive).
- **Effect**: sorts ahead of non-pinned files during diff-size reduction on both the
  compressor path and the char-backstop path; consumes budget first; never fully dropped
  while budget remains.
- **Not a filter**: can only affect files already present in the PR diff; never injects
  content.

## State / lifecycle

Stateless. Config is re-read on each `build_review_prompt` call; no caching across
reviews, no writes, no migration.
