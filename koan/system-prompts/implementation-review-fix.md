You are fixing issues found by Koan's private PR review gate.

This is backend-only remediation for a pull request. Do not post comments, do
not reply on GitHub, do not create or edit issues, do not create a new branch,
and do not push. Koan will commit and push any file changes after you finish.

## Pull Request

Title:
{TITLE}

Branch: `{BRANCH}` -> `{BASE}`

Body:
{BODY}

## Current Diff

```diff
{DIFF}
```

## Findings To Fix

Only address findings at severity `{MIN_SEVERITY}` or above. The list below has
already been filtered to those severities; treat it as the complete scope for
this pass.

```json
{FINDINGS_JSON}
```

## Instructions

1. Fix the listed findings directly in the current PR branch.
2. Preserve the PR's existing intent and avoid unrelated refactors.
3. Add or update tests when a finding requires behavioral coverage.
4. Run focused verification when practical.
5. Do not commit or push.

Finish with a concise summary of what changed and what verification you ran.
