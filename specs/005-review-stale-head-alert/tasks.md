---

description: "Ordered implementation tasks for the stale-HEAD review alert (review_stale_head)"

---

# Tasks: Stale-HEAD Review Alert (`review_stale_head`)

**Input**: `specs/005-review-stale-head-alert/plan.md`

One commit per task; skip empty commits.

## T1 — `_fetch_pr_head_oid` helper
Add `_fetch_pr_head_oid(owner, repo, pr_number) -> str` to
`koan/app/review_runner.py`, next to `_fetch_pr_commit_shas`. Returns the PR
branch's live HEAD OID via `gh pr view <n> --json headRefOid --jq .headRefOid`;
`""` on `RuntimeError`. (Plan D1.)

## T2 — `_build_stale_head_alert` helper
Add `_build_stale_head_alert(reviewed_sha, live_sha) -> str` to
`review_runner.py`. Returns `""` when either SHA is empty or they are equal;
otherwise a leading-blank-line GitHub `> [!IMPORTANT]` alert block naming both
7-char short SHAs. (Plan D2.)

## T3 — `live_head_sha` param on `_post_review_comment`
Add optional `live_head_sha: str = ""` to `_post_review_comment`; append
`_build_stale_head_alert(head_sha, live_head_sha)` to the review content before
the footer separator in both body-assembly branches. Default keeps output
byte-identical for existing callers. (Plan D3.)

## T4 — Fetch + pass in `run_review`
In `run_review` Step 7, fetch `_live_head = _fetch_pr_head_oid(...) if current_shas
else ""` and pass it as `live_head_sha` to `_post_review_comment`. (Plan D4.)

## T5 — Tests
Add to `koan/tests/test_review_runner.py`:
- `_build_stale_head_alert`: empty/equal → `""`; differing → block with both short
  SHAs + `[!IMPORTANT]`.
- `_fetch_pr_head_oid`: OID on success (mock `run_gh`); `""` on `RuntimeError`.
- `_post_review_comment`: equal `live_head_sha` → no alert; differing → alert
  before footer; default `""` → unchanged.
- `run_review` integration: mocked live HEAD ≠ reviewed tip → posted body carries
  the alert.

Mock at `app.review_runner.run_gh` only.

## T6 — Spec + docs Capture
- `specs/skills/review.md`: add a "stale-HEAD alert" invariant and mention it under
  Outputs / side effects; bump `updated:`.
- `docs/messaging/github-commands.md` (or `docs/users/skills.md`): one-line note
  that a review whose branch moved mid-review carries an IMPORTANT banner.
- Run `make lint` and the review-runner test file; fix any findings.
