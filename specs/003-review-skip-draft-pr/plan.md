---

description: "Technical plan for the draft-PR auto-review gate (review_draft_skip)"

---

# Plan: Draft-PR Auto-Review Gate (`review_draft_skip`)

**Input**: `specs/003-review-skip-draft-pr/spec.md`

## Context

The auto-review trigger lives in
`koan/app/github_command_handler.py::_try_assignment_notification`. The dispatch
entry point `process_single_notification` (same module) processes **all
`@mention` comments first**, and only when none are found falls back to the
assignment path (`review_requested` → `/review`, `assign` → `/implement`).
Because explicit `/review` mentions are handled on the mention path, gating the
assignment path satisfies "human `/review` is always honored" structurally — no
change to the mention path is needed.

`_fetch_subject_info` (same module) already does one GitHub API call returning
`{state, merged, head_sha}` for the closed/merged check and the dedup key. We
extend that same call to also return `draft` — zero new API calls.

Review config getters follow a uniform pattern in `koan/app/config.py`
(`get_review_inline_comments_config`, `get_review_bot_triage_config`, …):
load `_load_config()`, read a nested section, coerce types, default safely. The
config validator (`koan/app/config_validator.py`) recognizes nested review
sections via the top-level `CONFIG_SCHEMA` (`_NESTED`) plus a `SECTION_SCHEMAS`
entry — the pattern used by `review_concurrency` and `review_ignore`.

## Design

### D1 — Config getter (`koan/app/config.py`)
Add `get_review_draft_skip_config() -> dict` returning `{"enabled": bool}`,
default `False`, mirroring `get_review_bot_triage_config` / the
`get_review_inline_comments_config` shape (non-dict section → `{}`, non-bool
`enabled` → `False`). Placed adjacent to the other `get_review_*` functions.

### D2 — Validator registration (`koan/app/config_validator.py`)
- Add `"review_draft_skip": _NESTED` to `CONFIG_SCHEMA` (grouped with
  `review_concurrency` / `review_ignore`).
- Add a `SECTION_SCHEMAS["review_draft_skip"] = {"enabled": "bool"}` entry.

This makes the key recognized and type-checked, so a typo or wrong type surfaces
at startup instead of silently defaulting.

### D3 — `draft` in subject fetch (`github_command_handler.py`)
Change the `jq` in `_fetch_subject_info` from
`{state: .state, merged: .merged, head_sha: .head.sha}` to
`{state: .state, merged: .merged, head_sha: .head.sha, draft: .draft}` and
document the new key. `.draft` is `true`/`false` on PRs, absent (`null`) on
issues — harmless for the `assign` path, which the gate never reads.

### D4 — The soft-skip gate (`_try_assignment_notification`)
Insert the gate **immediately after the closed/merged skip block** (groups the
"skip this notification" conditions; `subject_info` is already in scope there;
the dedup/cooldown checks above remain untouched). Pseudocode:

```python
if reason == "review_requested" and subject_info.get("draft"):
    from app.config import get_review_draft_skip_config
    if get_review_draft_skip_config()["enabled"]:
        log.info(...)                      # deferred draft PR
        _notify_draft_pr_skipped(...)      # FR-007, best-effort
        mark_notification_read(notif_id)
        notification[NOTIFICATION_OUTCOME_KEY] = NOTIFICATION_OUTCOME_HANDLED_NOOP
        return True                         # handled (soft skip)
```

Critical: this branch **must not** call `track_thread` or `set_review_cooldown`
(FR-004). It returns `True` (handled/noop), consistent with the closed/merged
and already-tracked early-returns.

Placement rationale: after the thread-tracked dedup check and cooldown check so
those continue to work for non-draft re-polls; for a draft PR we never track, so
those checks are inert and we reach this gate on each fresh notification. The
gate keys off the live `draft` flag fetched this poll, so a PR that later becomes
ready is evaluated fresh.

### D5 — Notify helper (`github_command_handler.py`)
Add `_notify_draft_pr_skipped(owner, repo, subject_title, notification)`
mirroring `_notify_closed_subject_skipped`: builds the web URL, sends an INFO
Telegram message with the remedy, swallows all errors. Emoji `💤` to distinguish
"deferred" from the closed/merged `⏭️`.

### D6 — Tests (`koan/tests/test_github_command_handler.py`)
Extend the autouse `_stub_subject_info` seam with a `subject_draft` fixture
(default `False`), threading `"draft": subject_draft` into all three branches —
additive, preserves every existing test. Then in `TestTryAssignmentNotification`:

- `test_review_requested_draft_pr_deferred_when_gate_enabled` — gate on, draft →
  no mission, read marked, outcome NOOP, no `set_review_cooldown` /
  `track_thread` calls.
- `test_review_requested_draft_pr_reviewed_when_gate_disabled` — draft but gate
  off (default) → mission queued (AC1).
- `test_review_requested_non_draft_pr_reviewed_when_gate_enabled` — gate on,
  non-draft → mission queued (AC3).
- `test_review_requested_mention_path_not_gated` — `@bot /review` mention on a
  draft PR with the gate on still queues (AC4); documents the structural
  separation.

Config unit test (`koan/tests/test_config.py` or adjacent): default disabled,
reads `enabled: true`, coerces non-dict / non-bool to disabled (AC5).

### D7 — Docs (`instance.example/config.yaml`, `docs/`)
Add a commented `review_draft_skip:` block to `instance.example/config.yaml`
beside the other review configs (after `review_inline_comments`). Update the
relevant review-config doc page (e.g. `docs/users/` review config reference) so
the flag is discoverable.

## Integration Points

- **Caller**: `process_single_notification` — unchanged; the gate is internal to
  `_try_assignment_notification`.
- **Shared state**: reads `instance/.review-threads.json` semantics (does NOT
  write on the skip path).
- **Config**: new top-level key, loaded via the existing `_load_config()` cache.
- **GitHub API**: the single subject fetch now also reads `.draft`.

## Risks & Mitigations

- **Risk**: soft-skipping a draft whose author never marks it ready, and GitHub
  not re-firing on ready-for-review → review never auto-runs.
  **Mitigation**: FR-005 / AC4 — explicit `/review` always works; FR-007 notify
  tells the operator the remedy. Acceptable per the task (human override is the
  documented fallback).
- **Risk**: gate fires on the wrong path (mentions).
  **Mitigation**: gate is inside the assignment path only; regression test AC4.
- **Risk**: breaking the default.
  **Mitigation**: default `false`; AC1 regression test.

## Out of Scope (explicit)

- Post-mission autoreview (`mission_runner._maybe_queue_autoreview`).
- Per-project override of this flag (global only for now; can be added later by
  mirroring `_resolve_verdict_config` if needed).
