# Rebase — Apply Review Feedback

You are rebasing a pull request and applying changes requested by reviewers.

## Pull Request: {TITLE}

**Branch**: `{BRANCH}` → `{BASE}`

### PR Description

{BODY}

---

## Current Diff

```diff
{DIFF}
```

---

## Review Comments (inline on code)

{REVIEW_COMMENTS}

## Reviews (top-level)

{REVIEWS}

## Conversation Thread

{ISSUE_COMMENTS}

---

{COMMIT_CONVENTIONS}

---

{@include receiving-code-review}

---

## Your Task

**IMPORTANT: Do NOT create new branches or switch branches with git checkout/switch.
Stay on the current branch. Your changes will be committed and pushed automatically.**

1. **Process the review feedback through the protocol above.** Identify actionable
   change requests vs. discussion or questions, then run each substantive request
   through VERIFY→EVALUATE before implementing; fast-path trivial items.
2. **Implement the changes you agree with.** Edit the code to address each correct,
   actionable review comment.
   - Skip comments that are questions, acknowledgments, or discussion (not change requests).
   - If a reviewer requested a change you believe is wrong, push back with technical
     reasoning per the RESPOND step and note it in your summary rather than blindly
     implementing it.
3. **Be focused.** Only change what was requested — no drive-by refactoring, no extra improvements.
4. **Do not run tests.** The caller handles testing separately.

When you're done, report a concise summary using these two labeled sections, so
it renders unambiguously in the PR comment and commit message. Use the headers
verbatim, and **omit a section that has no items** (do not write "none"):

```
APPLIED:
- one bullet per change you actually made, specific and justified by the
  reviewer's request — e.g. "Renamed `get_user()` to `fetch_user()` per reviewer
  request", not vague like "Applied feedback".

SKIPPED:
- one bullet per reviewer point you deliberately did NOT change, each with the
  reason: already fixed in an earlier pass, advisory / below the severity you
  were asked to address, or you disagree (say which, with a short technical
  reason).
```

Put each reviewer point under exactly one section. If you changed everything
requested, omit `SKIPPED:`. If you changed **nothing** — because every point was
already addressed or advisory — include only `SKIPPED:`, so the comment clearly
shows no code change was needed and why (never present an unchanged point as if
you had just fixed it).

{COMMIT_SUBJECT_INSTRUCTION}
