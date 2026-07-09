# Feature Specification: Stale-HEAD Review Alert (`review_stale_head`)

**Feature Branch**: `koan.atoomic/review-stale-head-alert`

**Created**: 2026-07-09

**Status**: Draft

**Input**: User description: "When running a /review on a pull request we advertise
in the footer message of the comment added to github the commit id that was used
using something like `HEAD=abcd`. This is great as we know what commit we used to
review. It's possible that during the review cycle one or more commits were pushed
to the branch (using force push or not). We should then when posting the comment
validate that the upstream branch is still pointing at the same commit id we used
otherwise we should add a github markdown notification alert of type IMPORTANT at
the end of the review message."

## Overview

When Kōan runs a `/review` on a GitHub PR, it captures the PR's commit SHAs
(`_fetch_pr_commit_shas`) **at the start** of the run and reviews the diff at that
HEAD. The reviewed HEAD short-SHA is advertised in the posted comment's footer as
`` `HEAD=<short-sha>` `` (`_build_review_footer` in `koan/app/review_runner.py`).

A review is not instantaneous — provider analysis, focus passes, and comment
triage can take minutes. During that window the author may push new commits or
force-push, moving the PR branch's real HEAD away from the commit the review
actually covered. When that happens, the posted review is **silently stale**: its
findings describe code that is no longer at the tip of the branch, but nothing in
the comment tells the reader that.

This feature makes that staleness **visible**. Just before posting the review
comment, Kōan re-reads the PR branch's live HEAD commit OID and compares it to the
SHA it reviewed. If they differ, it appends a GitHub `> [!IMPORTANT]` alert to the
end of the review message warning that the branch moved during review and that the
findings may not reflect the current tip.

## Scope

**In scope** — the summary review comment posted by `run_review` →
`_post_review_comment` in `koan/app/review_runner.py`, the single place where the
`` `HEAD=<sha>` `` footer is produced and the review body is assembled.

**Out of scope (deliberate)**:

- **Inline PR comments** (`_maybe_post_inline_comments`) — anchored to a specific
  `commit_id`; GitHub already scopes their visibility to that commit, so a stale
  branch does not silently misattribute them. Not gated by this feature.
- **The formal review verdict** (`_submit_review_verdict`) — already anchored to
  `head_sha=current_shas[-1]` via GitHub's `commit_id` parameter, so the verdict
  is correctly attributed to the reviewed commit regardless of later pushes.
- **Aborting or re-running the review** — this feature only *annotates* a
  potentially-stale review; it never re-runs analysis, blocks posting, or changes
  the LGTM verdict. Re-reviewing the new commits is the existing incremental-review
  path's job (a subsequent `/review` will pick up the new SHAs).
- **The `pr_footer.py` implementation-PR footer** — that footer's `HEAD=` reflects
  the local branch a mission built, not a remote PR under review, and has no
  "reviewed vs. live" distinction. Untouched.

## Functional Requirements

### FR-001 — Re-read live HEAD before posting
Just before the review comment is posted (`run_review` Step 7), Kōan fetches the
PR branch's current HEAD commit OID via `gh pr view <n> --json headRefOid`. This is
a **single** additional GitHub API call, made once per review post.

### FR-002 — Compare against the reviewed SHA
The live HEAD OID is compared to the SHA the review was performed against — the
last element of `current_shas` (`current_shas[-1]`), i.e. the same value shown in
the `` `HEAD=<short-sha>` `` footer. Both are full 40-char SHAs from the GitHub
API, so the comparison is exact string equality.

### FR-003 — Append an IMPORTANT alert on mismatch
When the live HEAD differs from the reviewed SHA, an alert block using GitHub's
Markdown alert syntax is appended at the **end of the review message** (after the
review content, before the branded footer):

```markdown
> [!IMPORTANT]
> **The branch moved during review.** This review was performed against
> `HEAD=<reviewed-short>`, but the PR branch now points at `<live-short>`.
> Commits pushed after the review started are not reflected below — re-run
> `/review` to cover them.
```

Short SHAs are the first 7 characters, matching the footer's existing convention.

### FR-004 — No alert when HEAD is unchanged
When the live HEAD equals the reviewed SHA (the common case), no alert is added
and the comment is byte-for-byte identical to today's output. Zero observable
change for the overwhelming majority of reviews.

### FR-005 — Best-effort, never fatal
The live-HEAD fetch is best-effort. If it fails (network error, `gh` error, PR not
found, empty output) the value is treated as "unknown" → **no alert** is added and
the review is posted normally. A staleness check must never block or fail a review
post. A conservative miss (no alert when the branch actually did move) is
preferred to a spurious alert or a failed post.

### FR-006 — No alert when the reviewed SHA is unknown
When `current_shas` is empty (no SHAs captured — the same condition under which the
footer omits `HEAD=`), there is nothing to compare against, so no alert is added.

## Data Model

No persisted state. One transient value:

- `live_head_sha: str` — the PR branch's current HEAD OID at post time, from
  `gh pr view <n> --repo <owner>/<repo> --json headRefOid --jq .headRefOid`.
  Empty string on any error (best-effort).

`_post_review_comment` gains one optional parameter, `live_head_sha: str = ""`,
threaded from `run_review`. When empty (the default), behavior is identical to
today — so all existing direct callers/tests are unaffected.

## Non-Functional

- **One extra API call per review post** — only at post time, only when a review
  is actually being posted (not on the no-new-commits skip path).
- **No config flag** — this is a pure correctness/visibility improvement with no
  downside when HEAD is unchanged; it is always on. (Contrast with
  `review_draft_skip`, which changes *whether* a review happens and therefore is
  opt-in.)
- **Python 3.11+**, ruff-clean (PERF), no `# noqa` without a documented reason.
- **Idempotent posting preserved** — the alert is part of the assembled body, so
  re-posting/PATCHing a comment reproduces it deterministically from the same
  inputs.

## User Stories

### US1 — Reviewer sees that the branch moved
As a PR author/reviewer, when I push a commit while Kōan is mid-review, the posted
review carries a prominent IMPORTANT banner telling me the findings predate my
latest push, so I don't act on stale feedback.

### US2 — Unchanged branches look exactly as before
As an operator, for the normal case (no push during review) the review comment is
unchanged — no banner, no noise.

### US3 — A flaky GitHub call never breaks my review
As an operator, if the extra HEAD lookup fails transiently, my review is still
posted normally (just without the staleness banner).

## Acceptance Criteria

- AC1: When the live HEAD equals `current_shas[-1]`, the posted body contains no
  `[!IMPORTANT]` alert and is otherwise unchanged.
- AC2: When the live HEAD differs from `current_shas[-1]`, the posted body contains
  a `> [!IMPORTANT]` alert naming both the reviewed short-SHA and the live
  short-SHA, placed after the review content and before the footer.
- AC3: When the live-HEAD fetch returns an empty string (error), no alert is added
  and the review still posts.
- AC4: When `current_shas` is empty, no alert is added (nothing to compare).
- AC5: The alert uses valid GitHub alert syntax (`> [!IMPORTANT]` on its own line,
  each following line prefixed with `> `).
- AC6: `make lint` passes and the review-runner test file passes, including new
  tests covering AC1–AC5.

## Open Questions / Resolved

- **Q: Fetch the live HEAD inside `_post_review_comment` or in `run_review`?** →
  A: In `run_review`, then pass the value in. This keeps the network call at the
  orchestration layer (mockable via the existing `run_gh` patching) and leaves
  `_post_review_comment` a pure formatter that existing direct-call tests exercise
  without a second `run_gh` round-trip.
- **Q: Should the alert change the LGTM verdict or block the post?** → A: No. It is
  purely informational. Re-covering the new commits is the incremental-review
  path's job on the next `/review`.
- **Q: Gate behind a config flag?** → A: No. There is no downside when HEAD is
  unchanged (byte-identical output), and the alert is strictly additive
  information, so it is always on.
- **Q: What about the >250-commit pagination limit of the commits endpoint?** →
  A: `_fetch_pr_commit_shas` can truncate at GitHub's 250-commit page cap, so on a
  PR with >250 commits `current_shas[-1]` may not be the true tip, which could
  yield a spurious alert. This is a pre-existing limitation of the SHA-list source
  and is left as known debt rather than adding a second reconciliation call.
