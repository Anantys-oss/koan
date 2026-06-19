"""CI queue runner — drains enqueued CI checks without blocking.

Two roles:

1. **drain_one(instance_dir)** — called from the iteration loop.  Reads the
   ## CI section from missions.md and checks each entry non-blocking.
   - Pass → remove from ## CI, write outbox success message.
   - Fail → increment attempt counter, inject ``/ci_check <url>`` mission.
            If max attempts reached, remove from ## CI, write outbox failure.
   - Pending/running → skip (check again next iteration).
   - None → remove from ## CI (no CI configured).

2. **CLI entry point** — ``python -m app.ci_queue_runner <pr-url> --project-path <path>``
   Runs the blocking CI check-and-fix for a single PR (used by the
   ``/ci_check`` fix mission path).

All status/debug output goes to stderr; stdout is reserved for JSON.
"""

import contextlib
import json
import sys
from pathlib import Path
from typing import Optional, Tuple

from app.claude_step import CI_STATUS_BLOCKED_APPROVAL


def check_ci_status(branch: str, full_repo: str) -> Tuple[str, Optional[int], str]:
    """Make a single non-blocking CI status check.

    Delegates to :func:`app.claude_step.check_existing_ci` for consistent
    return type across all CI status functions.

    Returns:
        (status, run_id, logs) where status is one of:
        "success", "failure", "pending", "blocked_approval", "none"
    """
    from app.claude_step import check_existing_ci

    return check_existing_ci(branch, full_repo)


def drain_one(instance_dir: str = "") -> Optional[str]:
    """Check CI entries in ## CI section (non-blocking). Returns a status message or None.

    Called once per iteration from the run loop. Reads the ## CI section,
    picks the first (oldest) entry, and based on CI status:
    - success: remove from ## CI, send outbox notification
    - failure (under max): increment attempt, inject /ci_check mission
    - failure (at max): remove from ## CI, send failure outbox notification
    - pending: leave in ## CI (try again next iteration)
    - none: remove from ## CI (no CI configured)

    Also migrates legacy .ci-queue.json entries and any ## CI section in
    missions.md to .ci-monitor.json on first call.

    The ``instance_dir`` parameter is ignored — kept for call-site compatibility
    while callers are updated.
    """
    from app.ci_queue import (
        monitor_get_items,
        monitor_migrate_from_missions_md,
        monitor_remove_item,
        monitor_update_attempt,
    )

    # One-time migrations: legacy JSON queue and the old ## CI section.
    _maybe_migrate_json_queue()
    monitor_migrate_from_missions_md()

    items = monitor_get_items()
    if not items:
        return None

    # Process first (oldest) entry
    entry = items[0]
    pr_url = entry["pr_url"]
    branch = entry["branch"]
    full_repo = entry["full_repo"]
    pr_number = entry.get("pr_number", "?")
    attempt = entry.get("attempt", 0)
    max_attempts = entry.get("max_attempts", 5)

    # Short-circuit closed/merged PRs — CI fixes can't help a PR that no
    # longer accepts commits. Without this, a closed-but-not-merged PR with
    # past failed CI runs would keep re-queueing /ci_check forever.
    pr_state = _check_pr_state_safe(pr_number, full_repo)
    if pr_state in ("CLOSED", "MERGED"):
        monitor_remove_item(pr_url)
        if pr_state == "CLOSED":
            _write_outbox(
                f"🚫 PR #{pr_number} was closed — removed from CI queue: {pr_url}",
            )
        return f"PR #{pr_number} {pr_state.lower()} — removed from CI monitor"

    status, _run_id, _logs = check_ci_status(branch, full_repo)

    if status == "success":
        monitor_remove_item(pr_url)
        _write_outbox(
            f"✅ CI passed for PR #{pr_number} — ready for review: {pr_url}",
        )
        return f"CI passed for PR #{pr_number} ({branch})"

    if status == "failure":
        if attempt < max_attempts:
            # Only increment attempt counter when a fix mission is actually
            # inserted.  If a /ci_check for this PR is already pending or in
            # progress, skip — avoids rapid-fire duplicate missions and
            # premature attempt exhaustion.
            if _inject_ci_fix_mission(pr_url, entry):
                monitor_update_attempt(pr_url)
                return f"CI failed for PR #{pr_number} — /ci_check mission queued (attempt {attempt + 1}/{max_attempts})"
            return None
        else:
            # Max attempts exhausted
            monitor_remove_item(pr_url)
            _write_outbox(
                f"🚦 CI still failing after {max_attempts} attempts for PR #{pr_number}: {pr_url}",
            )
            return f"CI failed {max_attempts} times for PR #{pr_number} — giving up"

    if status == "none":
        monitor_remove_item(pr_url)
        return f"No CI runs found for PR #{pr_number} — removed from CI monitor"

    if status == CI_STATUS_BLOCKED_APPROVAL:
        # GitHub gates workflow runs on first-time-contributor or
        # environment approval; nothing Kōan does will unstick them.
        # Drop the PR from the monitor so retries stop and notify the human
        # so they can approve in the UI (or politely ping the maintainer).
        monitor_remove_item(pr_url)
        _write_outbox(
            f"⏸ CI workflows on PR #{pr_number} are waiting for maintainer "
            f"approval — Kōan stopped retrying: {pr_url}",
        )
        return (
            f"CI blocked on maintainer approval for PR #{pr_number} — "
            f"removed from CI monitor"
        )

    # status == "pending" — leave in the monitor
    return None


def _check_pr_state_safe(pr_number: str, full_repo: str) -> str:
    """Return the PR's GitHub state, or "UNKNOWN" on any failure.

    Wraps :func:`app.rebase_pr.check_pr_state` so a flaky `gh` call never
    breaks the drain loop — callers fall back to the existing CI-status
    flow when the state can't be determined.
    """
    try:
        from app.rebase_pr import check_pr_state
        state, _mergeable = check_pr_state(pr_number, full_repo)
        return state
    except Exception as e:
        print(f"[ci_queue] PR state check error: {e}", file=sys.stderr)
        return "UNKNOWN"


def _inject_ci_fix_mission(pr_url: str, entry: dict) -> bool:
    """Inject a /ci_check mission into the pending queue.

    Returns True if the mission was inserted, False if a duplicate
    /ci_check for the same PR is already pending or in progress.
    """
    from app.utils import insert_pending_mission

    project_name = entry.get("project_name") or entry.get("project") or _project_name_from_path(
        entry.get("project_path", "")
    )

    mission_text = f"/ci_check {pr_url}"

    return insert_pending_mission(mission_text, project_name, urgent=True)


def _project_name_from_path(project_path: str) -> str:
    """Derive project name from its filesystem path.

    Uses the projects.yaml registry to return the canonical project name
    rather than the directory basename.
    Falls back to basename when the path isn't in the registry.
    """
    if not project_path:
        return ""
    from app.utils import project_name_for_path
    return project_name_for_path(project_path)


def _write_outbox(message: str):
    """Append a message to outbox.md."""
    from app.utils import KOAN_ROOT, append_to_outbox

    outbox_path = KOAN_ROOT / "instance" / "outbox.md"
    try:
        append_to_outbox(outbox_path, message)
    except Exception as e:
        print(f"[ci_queue] Failed to write outbox: {e}", file=sys.stderr)


def _maybe_migrate_json_queue():
    """One-time migration from legacy .ci-queue.json to .ci-monitor.json.

    Reads any entries from the legacy JSON queue and adds them to the CI
    monitor, then removes the JSON file. Migrated entries start at attempt 0.
    """
    import os

    from app.utils import KOAN_ROOT
    instance_dir = KOAN_ROOT / "instance"
    json_path = instance_dir / ".ci-queue.json"
    if not json_path.exists():
        return

    try:
        import json as _json
        data = _json.loads(json_path.read_text())
        entries = data if isinstance(data, list) else []
    except Exception as e:
        print(f"[ci_queue] Failed to read legacy JSON queue: {e}", file=sys.stderr)
        entries = []

    if not entries:
        with contextlib.suppress(OSError):
            os.remove(json_path)
        return

    from app.ci_queue import monitor_add_item
    from app.utils import load_config

    config = load_config()
    max_attempts = config.get("ci_fix_max_attempts", 5)

    for entry in entries:
        pr_url = entry.get("pr_url", "")
        branch = entry.get("branch", "")
        full_repo = entry.get("full_repo", "")
        pr_number = entry.get("pr_number", "")
        project_path = entry.get("project_path", "")
        project_name = _project_name_from_path(project_path)

        if not pr_url or not branch or not full_repo:
            continue

        monitor_add_item(
            project_name, pr_url, pr_number, branch, full_repo, max_attempts,
        )
        print(f"[ci_queue] Migrated {pr_url} from JSON queue to CI monitor", file=sys.stderr)

    try:
        os.remove(json_path)
        lock_path = instance_dir / ".ci-queue.lock"
        if lock_path.exists():
            os.remove(lock_path)
    except OSError:
        pass


def _reenqueue_for_monitoring(
    pr_url: str, branch: str, full_repo: str,
    pr_number: str, project_path: str,
):
    """Re-enqueue a PR for CI monitoring (.ci-monitor.json) after pushing a fix.

    This ensures drain_one() picks up the new CI run result during
    interruptible_sleep, rather than leaving it unmonitored.
    """
    from app.config import is_ci_check_enabled
    if not is_ci_check_enabled():
        print("[ci_check] CI check disabled, skipping re-enqueue", file=sys.stderr)
        return

    project_name = _project_name_from_path(project_path)

    from app.ci_queue import monitor_add_item
    from app.utils import load_config

    config = load_config()
    max_attempts = config.get("ci_fix_max_attempts", 5)

    try:
        monitor_add_item(
            project_name, pr_url, pr_number, branch, full_repo, max_attempts,
        )
        print(f"[ci_check] Re-enqueued {pr_url} for CI monitoring", file=sys.stderr)
    except Exception as e:
        print(f"[ci_check] Failed to re-enqueue: {e}", file=sys.stderr)


# ── CLI entry point ────────────────────────────────────────────────────
# Used by /ci_check skill dispatch: runs the blocking CI check-and-fix
# pipeline for a single PR.


def run_ci_check_and_fix(pr_url: str, project_path: str) -> Tuple[bool, str]:
    """Run the CI check-and-fix pipeline for a single PR.

    Unlike the rebase path (which polls CI for up to 10 minutes), this
    uses a non-blocking status check — drain_one() has already confirmed
    CI failed before injecting this mission, so we skip redundant polling.

    Steps:
    1. Fetch PR context and confirm CI failure (non-blocking)
    2. Checkout the PR branch
    3. Attempt Claude-based fix (up to max_attempts from ## CI entry)
    4. Force-push fixes and re-check CI
    5. Restore original branch
    """
    from app.config import is_ci_check_enabled
    if not is_ci_check_enabled():
        return False, "CI check system is disabled in config.yaml (ci_check.enabled: false)."

    from app.github_url_parser import parse_pr_url

    owner, repo, pr_number = parse_pr_url(pr_url)
    full_repo = f"{owner}/{repo}"

    # Determine max attempts from the CI monitor entry (respects per-enqueue config)
    max_fix_attempts = 2  # fallback if not monitored
    from app.ci_queue import monitor_get_items
    for item in monitor_get_items():
        if item.get("pr_url") == pr_url:
            max_fix_attempts = item.get("max_attempts", max_fix_attempts)
            break

    # Fetch minimal PR context needed for CI fix
    from app.rebase_pr import fetch_pr_context

    try:
        context = fetch_pr_context(owner, repo, pr_number, project_path)
    except Exception as e:
        return False, f"Failed to fetch PR context: {e}"

    branch = context.get("branch", "")
    base = context.get("base", "main")

    if not branch:
        return False, "Could not determine PR branch"

    # Non-blocking CI status check — skip the 10-minute polling loop.
    # drain_one() already confirmed failure, but we need the run_id for logs.
    status, run_id, ci_logs = check_ci_status(branch, full_repo)
    print(f"[ci_check] CI status for {branch}: {status}", file=sys.stderr)

    if status == "success":
        return True, "CI already passing — no fix needed."

    if status == "pending":
        # CI still running — don't attempt fixes against stale logs.
        # drain_one will re-check on the next iteration when CI completes.
        return False, "CI still pending — will retry when CI completes."

    if status == CI_STATUS_BLOCKED_APPROVAL:
        # Pushing more commits won't trigger CI either — the new runs
        # need the same approval. Bail out so the operator can act.
        return False, (
            "CI workflows are waiting for maintainer approval — "
            "cannot fix without an approve click in the GitHub UI."
        )

    if status not in ("failure",):
        return False, f"CI status is '{status}' — nothing to fix."

    if not ci_logs:
        run_info = f" (run_id={run_id})" if run_id else " (no run_id)"
        return False, f"CI failed but no failure logs available{run_info}."

    # Check PR state before attempting fix
    from app.rebase_pr import check_pr_state
    pr_state, mergeable = check_pr_state(pr_number, full_repo)

    if pr_state == "MERGED":
        return True, "PR already merged — CI fix skipped."

    if mergeable == "CONFLICTING":
        return False, "PR has merge conflicts — CI fix skipped (rebase needed first)."

    # Checkout the PR branch using the safe pattern (fetch + checkout -B)
    from app.claude_step import (
        _fetch_branch, _get_current_branch, _run_git, _safe_checkout,
    )
    from app.rebase_pr import _find_remote_for_repo

    original_branch = _get_current_branch(project_path)

    # Resolve remotes: base_remote for the PR target, head_remote for the branch
    base_remote = _find_remote_for_repo(owner, repo, project_path) or "origin"
    head_owner = context.get("head_owner", owner)
    head_remote = _find_remote_for_repo(head_owner, repo, project_path)

    try:
        from app.git_utils import ordered_remotes as _ordered_remotes
        fetch_remote = None
        for remote in _ordered_remotes(head_remote):
            try:
                _fetch_branch(remote, branch, cwd=project_path)
                fetch_remote = remote
                break
            except (RuntimeError, OSError):
                continue
        if not fetch_remote:
            return False, f"Branch `{branch}` not found on any remote"
        # -B resets the local branch to match remote, avoiding stale state
        _run_git(
            ["git", "checkout", "-B", branch, f"{fetch_remote}/{branch}"],
            cwd=project_path,
        )
    except Exception as e:
        return False, f"Failed to checkout {branch}: {e}"

    # Detect project commit conventions for convention-aware commit messages
    from app.commit_conventions import get_project_commit_guidance
    commit_conventions = get_project_commit_guidance(
        project_path, f"{base_remote}/{base}",
    )

    actions_log = []

    try:
        success = _attempt_ci_fixes(
            branch=branch,
            base=base,
            full_repo=full_repo,
            pr_number=pr_number,
            pr_url=pr_url,
            project_path=project_path,
            context=context,
            ci_logs=ci_logs,
            actions_log=actions_log,
            max_attempts=max_fix_attempts,
            base_remote=base_remote,
            commit_conventions=commit_conventions,
        )
    except Exception as e:
        actions_log.append(f"CI check/fix crashed: {e}")
        success = False
    finally:
        _safe_checkout(original_branch, project_path)

    summary = "\n".join(f"- {a}" for a in actions_log)
    return success, f"Actions:\n{summary}"


def _attempt_ci_fixes(
    branch: str,
    base: str,
    full_repo: str,
    pr_number: str,
    pr_url: str,
    project_path: str,
    context: dict,
    ci_logs: str,
    actions_log: list,
    max_attempts: int,
    base_remote: str = "origin",
    commit_conventions: str = "",
) -> bool:
    """Attempt to fix CI failures using Claude. Returns True if CI passes.

    Thin wrapper around :func:`app.claude_step.run_ci_fix_loop` with
    non-blocking CI recheck and re-enqueue on pending.
    """
    from app.claude_step import run_ci_fix_loop
    from app.rebase_pr import _build_ci_fix_prompt

    def _build_prompt(logs: str, diff: str) -> str:
        return _build_ci_fix_prompt(
            context, logs, diff,
            commit_conventions=commit_conventions,
        )

    success, _last_logs = run_ci_fix_loop(
        branch=branch,
        base=base,
        full_repo=full_repo,
        project_path=project_path,
        ci_logs=ci_logs,
        actions_log=actions_log,
        max_attempts=max_attempts,
        commit_conventions=commit_conventions,
        use_polling=False,
        prompt_builder=_build_prompt,
        commit_msg_template=f"fix: resolve CI failures on #{pr_number} (attempt {{attempt}})",
        base_remote=base_remote,
    )

    # Re-enqueue for monitoring when a fix was pushed and CI is pending
    if success and any("CI running after fix push" in a for a in actions_log):
        _reenqueue_for_monitoring(pr_url, branch, full_repo, pr_number, project_path)
        # Amend the last action to note re-enqueue
        for i in range(len(actions_log) - 1, -1, -1):
            if "CI running after fix push" in actions_log[i]:
                actions_log[i] += " — re-enqueued for monitoring"
                break

    return success


def _summary_indicates_quota_exhausted(summary: str) -> bool:
    """Return True when the CI-fix summary represents a provider quota stop."""
    return "API quota exhausted" in (summary or "")


def main(argv=None):
    """CLI entry point for ci_queue_runner."""
    import argparse

    from app.github_url_parser import parse_pr_url as _parse_url

    parser = argparse.ArgumentParser(
        description="Check and fix CI failures for a GitHub PR.",
    )
    parser.add_argument("url", help="GitHub PR URL")
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    cli_args = parser.parse_args(argv)

    try:
        _parse_url(cli_args.url)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        success, summary = run_ci_check_and_fix(cli_args.url, cli_args.project_path)
    except Exception as exc:
        print(f"[ci_check] Unexpected error: {exc}", file=sys.stderr)
        success = False
        summary = f"CI check crashed: {exc}"

    # Output JSON to stdout for mission_runner consumption
    result = {
        "success": success,
        "summary": summary,
        "quota_exhausted": _summary_indicates_quota_exhausted(summary),
    }
    print(json.dumps(result))

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
