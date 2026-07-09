---

description: "Technical plan for the stale-HEAD review alert (review_stale_head)"

---

# Plan: Stale-HEAD Review Alert (`review_stale_head`)

**Input**: `specs/005-review-stale-head-alert/spec.md`

## Context

`docs/`/`specs/` review of the affected area:

- `specs/skills/review.md` — the `/review` skill spec. "Outputs / side effects"
  documents that the review posts a comment "with a branded footer
  (`pr_footer.py`)". Invariants cover verdict-follows-severity and re-review
  comment handling, but say nothing about stale-HEAD detection — this feature adds
  a new invariant there.
- `specs/components/git-github.md` — lists `head_tracker.py` (a *different* HEAD
  concept: remote default-branch renames, not PR-branch tip movement). No overlap.
- No component spec owns `review_runner.py` end-to-end; the review pipeline is
  spec'd at the skill level (`specs/skills/review.md`). That skill spec is where
  the contract change is recorded (Capture step).
- `docs/messaging/github-commands.md` documents `/review`; the review footer/HEAD
  is an implementation detail not currently surfaced to end users, so the primary
  Capture target is the skill spec plus a short note where the review comment
  structure is described.

The posting logic all lives in one file, `koan/app/review_runner.py`:

- `_fetch_pr_commit_shas(owner, repo, pr_number)` (≈L2205) returns the PR's full
  commit SHAs oldest-first; `current_shas[-1]` is the tip the review covers.
- `run_review(...)` (≈L2624) fetches `current_shas` early (Step 1d, L2755), runs
  analysis, then at Step 7 (L2952) calls `_post_review_comment(..., commit_shas=current_shas)`.
- `_post_review_comment(...)` (≈L1814) sets `head_sha = commit_shas[-1]`, builds
  the body + footer (`_build_review_footer` → `` `HEAD=<sha[:7]>` ``), and
  POSTs/PATCHes via `run_gh`.

The gap: `current_shas` is captured *before* analysis; the branch tip can move
during the run, and the posted comment never says so.

## Design

### D1 — Live-HEAD fetch helper (`review_runner.py`)
Add `_fetch_pr_head_oid(owner, repo, pr_number) -> str` next to
`_fetch_pr_commit_shas`. Implementation:

```python
def _fetch_pr_head_oid(owner, repo, pr_number):
    """Return the PR branch's current HEAD commit OID, or "" on any error."""
    try:
        return run_gh(
            "pr", "view", pr_number,
            "--repo", f"{owner}/{repo}",
            "--json", "headRefOid",
            "--jq", ".headRefOid",
        ).strip()
    except RuntimeError:
        return ""
```

Mirrors the best-effort, `RuntimeError`-swallowing style of `_fetch_pr_commit_shas`
and `_fetch_pr_state`. `headRefOid` is always the true branch tip (reflects
force-push), unlike the paginated commits list.

### D2 — Alert builder (`review_runner.py`)
Add `_build_stale_head_alert(reviewed_sha, live_sha) -> str` returning the GitHub
alert block (FR-003), using 7-char short SHAs to match the footer. Returns `""`
when either SHA is empty or they are equal, so callers can unconditionally append
its result.

### D3 — Thread `live_head_sha` into `_post_review_comment` (`review_runner.py`)
Add an optional `live_head_sha: str = ""` parameter. After computing
`head_sha = commit_shas[-1] if commit_shas else ""`, build
`alert = _build_stale_head_alert(head_sha, live_head_sha)` and append it to
`review_text` at the end of the review content, before the `---`/footer separator:

```python
if review_text.startswith("## "):
    body = f"{SUMMARY_TAG}\n{review_text}{alert}\n\n---\n{footer}"
else:
    body = f"{SUMMARY_TAG}\n## Code Review\n\n{review_text}{alert}\n\n---\n{footer}"
```

`_build_stale_head_alert` returns `"\n\n> [!IMPORTANT]\n> …"` (leading blank line)
when firing and `""` otherwise, so the unchanged-HEAD path is byte-identical to
today (FR-004). Default empty `live_head_sha` ⇒ existing direct callers/tests see
no change.

### D4 — Fetch + pass at the post site (`run_review`)
In `run_review` Step 7, just before calling `_post_review_comment`, fetch the live
HEAD only when there is a reviewed SHA to compare against:

```python
_live_head = _fetch_pr_head_oid(owner, repo, pr_number) if current_shas else ""
posted, post_error = _post_review_comment(
    owner, repo, pr_number, review_body, post_target,
    commit_shas=current_shas or None,
    provider_name=review_provider_name, model=review_model,
    duration_seconds=_review_duration,
    live_head_sha=_live_head,
)
```

Best-effort: `_fetch_pr_head_oid` returns `""` on failure ⇒ no alert (FR-005). The
call is skipped entirely on the no-new-commits early-return skip path (that path
returns before Step 2), so the extra API call only happens when a review is
actually posted.

## Files changed

| File | Change |
|---|---|
| `koan/app/review_runner.py` | D1 `_fetch_pr_head_oid`, D2 `_build_stale_head_alert`, D3 `live_head_sha` param, D4 fetch+pass in `run_review` |
| `koan/tests/test_review_runner.py` | Unit tests for the two helpers + `_post_review_comment` alert behavior + a `run_review` integration assertion that the alert appears on a moved branch |
| `specs/skills/review.md` | New invariant: stale-HEAD alert on the posted comment |
| `docs/messaging/github-commands.md` (or `docs/users/skills.md`) | Short note that a moved-branch review carries an IMPORTANT banner |

## Test strategy

- `_build_stale_head_alert`: returns `""` for equal/empty SHAs; returns a block
  containing both short SHAs and `[!IMPORTANT]` for differing SHAs.
- `_fetch_pr_head_oid`: returns the OID on success; `""` when `run_gh` raises
  (mock at `run_gh`, per `koan/CLAUDE.md`).
- `_post_review_comment` with `live_head_sha` equal to `commit_shas[-1]` ⇒ body has
  no `[!IMPORTANT]`; with a differing `live_head_sha` ⇒ body has the alert before
  the footer; default (`""`) ⇒ unchanged.
- `run_review` end-to-end (existing harness): with a mocked live HEAD differing
  from the reviewed tip, the posted body contains the alert.

All GitHub interactions mocked at `app.review_runner.run_gh` (never at
`subprocess.run`) to avoid `retry_with_backoff` sleeps.

## Rollout / risk

- No config, no migration, no new state files. Pure additive behavior.
- Worst case on a bug: a spurious or missing banner on a review comment — never a
  failed post (best-effort fetch, `""`-on-error).
- Known debt (from spec): >250-commit PRs can truncate `current_shas`, risking a
  spurious alert; documented, not fixed here.
