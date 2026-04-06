# CI Fix — Resolve Failing CI on Rebased Branch

You are fixing CI failures on a pull request branch that was just rebased.

## Pull Request: {TITLE}

**Branch**: `{BRANCH}` → `{BASE}`

---

## Failed CI Logs

```
{CI_LOGS}
```

---

## Current Diff (branch vs base)

```diff
{DIFF}
```

---

## Important Context

**These CI logs are from BEFORE the rebase.** The branch has since been rebased onto
`{BASE}` and review feedback may have been applied. Some failures shown below may
already be resolved by those changes. Before fixing anything, check whether the
failing code still exists in its current form — if the problem area was already
changed by the rebase or feedback step, skip it.

## Your Task

**IMPORTANT: Do NOT create new branches or switch branches with git checkout/switch.
Stay on the current branch. Your changes will be committed and pushed automatically.**

1. **Analyze the CI failure logs carefully.** Identify the root cause — is it a test failure, a lint error, a type error, a build failure?
2. **Cross-check against the current diff.** If the failing code was already modified by the rebase or feedback, the failure may no longer apply — skip it.
3. **Fix the code** to resolve the CI failures that still apply. Only fix what is broken — do not refactor, do not add features, do not "improve" unrelated code.
4. **If the failure is in tests**, determine whether the test expectation is wrong (needs updating) or the code is wrong (needs fixing). Fix the right one.
5. **If the failure is a lint/format issue**, apply the minimal fix.
6. **Do not run tests yourself.** The caller will re-run CI after your changes.
7. **If all failures appear to be already resolved**, make no changes and report that.

When you're done, output a concise summary of what you fixed and why.
