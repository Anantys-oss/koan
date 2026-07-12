# Quickstart: Spec-change guard

## What it does

Fails a PR that changes a **durable design contract** (`specs/components/**`,
`specs/skills/**`, excluding index files and the skill template) unless the PR body
carries an explicit **architectural-change declaration**. See
`docs/design/spec-changes-are-architectural.md` for the why.

## Declare an architectural change

When your PR changes a durable contract, check this box in the PR description (the PR
template includes it):

```markdown
- [x] **Architectural change** — this PR modifies a durable design contract
  (`specs/components/**` or `specs/skills/**`). The new architecture needs review
  before approval. Rationale: <one line>
```

## Run the guard locally

```bash
# Against your branch vs. main, feeding your PR body from a file:
python3 scripts/spec_change_guard.py --base-ref origin/main --pr-body-file /tmp/body.md

# Or pass the changed set explicitly (no git needed):
printf '%s' "$(cat /tmp/body.md)" | \
  python3 scripts/spec_change_guard.py --pr-body - \
    --changed-file specs/components/core.md
```

- Exit `0` → clean (no contract changed, or declared).
- Exit `1` → a durable contract changed without a declaration (message names the files
  and prints the exact line to add).

## Run the tests

```bash
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_spec_change_guard.py -v
```

## CI

`.github/workflows/spec-change-guard.yml` runs the guard on every PR to `main` as a
blocking check. It is read-only and never pushes.
