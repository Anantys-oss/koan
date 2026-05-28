"""
Kōan -- Shared helpers for the CI/CD pipeline.

Git operations, Claude Code CLI invocation, and text utilities
used by pr_review.py, rebase_pr.py, recreate_pr.py, and other
pipeline modules.
"""

import json
import logging
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.cli_exec import popen_cli, stream_with_timeout
from app.cli_provider import build_full_command, run_command
from app.config import get_model_config
from app.git_utils import get_current_branch as _git_utils_get_current_branch
from app.git_utils import ordered_remotes, run_git_strict
from app.github import pr_create, run_gh, sanitize_github_comment
from app.prompts import load_prompt_or_skill


class StepResult:
    """Result of a :func:`run_claude_step` invocation.

    Behaves as a bool (truthy when a commit was created) for backward
    compatibility, while also carrying the Claude CLI output text for
    callers that need it (e.g. extracting change summaries).  Failed steps
    also expose quota classification so CI loops can stop as transient quota
    exhaustion instead of treating the result as "no changes".
    """

    __slots__ = ("committed", "error", "output", "quota_exhausted")

    def __init__(
        self,
        committed: bool,
        output: str = "",
        *,
        quota_exhausted: bool = False,
        error: str = "",
    ):
        self.committed = committed
        self.output = output
        self.quota_exhausted = quota_exhausted
        self.error = error

    def __bool__(self) -> bool:
        return self.committed

    def __repr__(self) -> str:
        return (
            "StepResult("
            f"committed={self.committed!r}, "
            f"quota_exhausted={self.quota_exhausted!r}, "
            f"output={self.output[:60]!r}...)"
        )


# Backward-compatible alias — callers should import from app.cli_provider
run_claude_command = run_command


def _run_git(cmd: list, cwd: str = None, timeout: int = 60) -> str:
    """Run a git command, raise on failure.

    Thin wrapper around git_utils.run_git_strict() preserving the
    original interface where callers pass ["git", ...] as cmd.
    """
    # Strip leading "git" if present — run_git_strict prepends it
    args = cmd[1:] if cmd and cmd[0] == "git" else cmd
    return run_git_strict(*args, cwd=cwd, timeout=timeout)


_REBASE_EXCEPTIONS = (RuntimeError, subprocess.TimeoutExpired, OSError)
CI_QUOTA_STOP_ACTION = "CI fix stopped: API quota exhausted"


def _fetch_branch(remote: str, branch: str, cwd: str = None, timeout: int = 60) -> str:
    """Fetch a branch using an explicit refspec to guarantee tracking ref update.

    ``git fetch <remote> <branch>`` fetches objects but does NOT update
    ``refs/remotes/<remote>/<branch>`` — it only writes to FETCH_HEAD.
    A subsequent ``git checkout -B branch remote/branch`` then uses the
    **stale** tracking ref instead of the freshly fetched state.

    Using an explicit refspec ``+refs/heads/X:refs/remotes/R/X`` ensures
    the remote tracking ref is always up-to-date after fetch.
    """
    refspec = f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
    return _run_git(["git", "fetch", remote, refspec], cwd=cwd, timeout=timeout)


def _abort_rebase_safely(project_path: str) -> None:
    """Abort a rebase in progress, ignoring errors."""
    try:
        subprocess.run(
            ["git", "rebase", "--abort"],
            stdin=subprocess.DEVNULL,
            capture_output=True, cwd=project_path,
            timeout=30,
        )
    except Exception as e:
        print(f"[claude_step] rebase --abort failed (non-fatal): {e}", file=sys.stderr)


def has_rebase_in_progress(project_path: str) -> bool:
    """Check if a git rebase is in progress (typically due to conflicts)."""
    git_dir = Path(project_path) / ".git"
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


# Re-export for backward compatibility — canonical source is git_utils.ordered_remotes
_ordered_remotes = ordered_remotes


def _is_ancestor(maybe_ancestor: str, descendant: str, cwd: str) -> bool:
    """Return True if *maybe_ancestor* is an ancestor of (or equal to) *descendant*."""
    try:
        _run_git(
            ["git", "merge-base", "--is-ancestor", maybe_ancestor, descendant],
            cwd=cwd, timeout=10,
        )
        return True
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return False


def _prefetch_all_remotes(
    base: str,
    project_path: str,
    preferred_remote: Optional[str] = None,
    head_remote: Optional[str] = None,
) -> None:
    """Eagerly fetch the base branch from all relevant remotes.

    Ensures every remote tracking ref is current before the rebase loop
    starts, so that ancestry checks and --onto calculations use fresh data.
    Failures are logged but never prevent the rebase attempt.
    """
    remotes_to_fetch: List[str] = list(_ordered_remotes(preferred_remote))
    if head_remote and head_remote not in remotes_to_fetch:
        remotes_to_fetch.append(head_remote)
    for remote in remotes_to_fetch:
        try:
            _fetch_branch(remote, base, cwd=project_path)
        except _REBASE_EXCEPTIONS as e:
            print(f"[claude_step] Pre-fetch {remote}/{base} failed (non-fatal): {e}",
                  file=sys.stderr)



def _rebase_onto_target(
    base: str,
    project_path: str,
    preferred_remote: Optional[str] = None,
    head_remote: Optional[str] = None,
    on_conflict: Optional[Callable[[str], bool]] = None,
) -> Optional[str]:
    """Rebase onto target branch, trying *preferred_remote* first.

    When *preferred_remote* is given (e.g. the remote matching the PR's
    target repository), it is tried before the default ``origin`` /
    ``upstream`` fallbacks.  When *head_remote* is known and differs from
    the target remote, uses ``--onto`` to replay only the PR's commits.

    All relevant remotes are pre-fetched before the rebase loop so that
    tracking refs are guaranteed fresh for ancestry checks and --onto.

    Args:
        on_conflict: Optional callback invoked when a rebase fails and a
            rebase-in-progress is detected (i.e. conflicts exist).
            Receives ``project_path`` and should return True if the
            conflicts were resolved and the rebase completed, False
            otherwise.  When None (default), conflicts cause an immediate
            abort.

    Returns:
        Remote name used (e.g. "origin" or "upstream") on success, None on failure.
    """
    _prefetch_all_remotes(base, project_path, preferred_remote, head_remote)

    for remote in _ordered_remotes(preferred_remote):
        if head_remote and head_remote != remote:
            # Only use --onto when the fork has genuinely diverged from
            # upstream (i.e. has commits that upstream doesn't).  When the
            # fork is simply behind, --onto replays upstream commits that
            # already exist on the target, causing spurious conflicts in
            # files the PR never touched.
            use_onto = not _is_ancestor(
                f"{head_remote}/{base}", f"{remote}/{base}", project_path,
            )
            if use_onto:
                try:
                    _run_git(
                        ["git", "rebase", "--onto", f"{remote}/{base}",
                         f"{head_remote}/{base}", "--autostash"],
                        cwd=project_path,
                    )
                    return remote
                except _REBASE_EXCEPTIONS as e:
                    print(f"[claude_step] --onto rebase failed: {e}", file=sys.stderr)
                    if on_conflict and has_rebase_in_progress(project_path):
                        if on_conflict(project_path):
                            return remote
                    _abort_rebase_safely(project_path)
                    # Fall through to plain rebase

        # Fallback: plain rebase
        try:
            _run_git(
                ["git", "rebase", "--autostash", f"{remote}/{base}"],
                cwd=project_path,
            )
            return remote
        except _REBASE_EXCEPTIONS as e:
            print(f"[claude_step] Rebase onto {remote}/{base} failed: {e}", file=sys.stderr)
            if on_conflict and has_rebase_in_progress(project_path):
                if on_conflict(project_path):
                    return remote
            _abort_rebase_safely(project_path)
    return None


def strip_cli_noise(text: str) -> str:
    """Strip Claude CLI error artifacts from output.

    The CLI appends lines like 'Error: Reached max turns (N)' to stdout
    even on successful runs. These pollute journal entries and reflections
    when the output is stored verbatim.

    Returns:
        Cleaned text with CLI noise removed.
    """
    lines = text.splitlines()
    lines = [l for l in lines if not re.match(r"^Error:.*max turns", l, re.IGNORECASE)]
    return "\n".join(lines).strip()


def run_claude(cmd: list, cwd: str, timeout: int = 600) -> dict:
    """Run a Claude Code CLI command, streaming stdout in real time.

    Thin wrapper around :func:`app.cli_exec.stream_with_timeout`. Each
    Claude stdout line is forwarded to ``sys.stdout`` while also being
    captured. Streaming serves two purposes:

    1. Each emitted line resets the parent process's liveness watchdog
       in ``run.py`` (default 600s), so long but still-progressing
       Claude calls no longer get killed for "no output".
    2. ``/live`` and the bridge see Claude's progress in real time
       instead of a silent wait.

    The subprocess is started with a new POSIX session
    (``start_new_session=True``) so that on timeout the entire process
    group can be killed — preventing grandchildren (e.g. tool-call
    subprocesses) from holding the stdout pipe open and turning a
    ``TimeoutExpired`` into an indefinite hang during pipe drain.

    Returns:
        Dict with keys: success (bool), output (str), error (str).
    """
    from app.security_audit import SUBPROCESS_EXEC, _redact_list, log_event

    try:
        proc, cleanup = popen_cli(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            start_new_session=True,
        )
    except Exception as e:
        log_event(SUBPROCESS_EXEC, details={
            "cmd": _redact_list(cmd),
            "cwd": cwd,
        }, result="failure")
        return {
            "success": False,
            "output": "",
            "error": f"Failed to spawn CLI: {e}",
        }

    try:
        stream_result = stream_with_timeout(
            proc,
            timeout=timeout,
            on_line=lambda line: print(line, flush=True),
        )
    finally:
        cleanup()

    stdout_text = stream_result.stdout
    stderr_text = stream_result.stderr

    if stream_result.timed_out:
        log_event(SUBPROCESS_EXEC, details={
            "cmd": _redact_list(cmd),
            "cwd": cwd,
        }, result="timeout")
        return {
            "success": False,
            "output": stdout_text,
            "error": f"Timeout ({timeout}s)",
        }

    returncode = proc.returncode
    if returncode != 0:
        stderr_snippet = stderr_text[-500:] if stderr_text else "no stderr"
        # When stderr is empty, stdout often contains the actual error
        # (e.g. "Error: context window exceeded").  Include it so callers
        # get actionable diagnostics instead of just "no stderr".
        if not stderr_text and stdout_text:
            stderr_snippet = f"no stderr | stdout: {stdout_text[-500:]}"
        log_event(SUBPROCESS_EXEC, details={
            "cmd": _redact_list(cmd),
            "cwd": cwd,
            "exit_code": returncode,
        }, result="failure")
        return {
            "success": False,
            "output": stdout_text,
            "error": f"Exit code {returncode}: {stderr_snippet}",
            "exit_code": returncode,
        }

    log_event(SUBPROCESS_EXEC, details={
        "cmd": _redact_list(cmd),
        "cwd": cwd,
        "exit_code": 0,
    })
    return {
        "success": True,
        "output": stdout_text,
        "error": "",
        "exit_code": returncode,
    }


def commit_if_changes(project_path: str, message: str) -> bool:
    """Stage all changes and commit if there are any.

    Returns True if a commit was created.
    """
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=project_path,
        timeout=30,
    )
    if not status.stdout.strip():
        return False

    _run_git(["git", "add", "-A"], cwd=project_path)
    _run_git(["git", "commit", "-m", message], cwd=project_path)
    return True


def run_claude_step(
    prompt: str,
    project_path: str,
    commit_msg: str,
    success_label: str,
    failure_label: str,
    actions_log: List[str],
    max_turns: int = 20,
    timeout: int = 600,
    use_skill: bool = False,
    use_convention_subject: bool = False,
) -> StepResult:
    """Run a Claude Code step: invoke CLI, commit changes, log result.

    Args:
        use_skill: If True, include the Skill tool in allowed tools
                   so Claude can invoke registered skills (e.g. /refactor).
        use_convention_subject: If True, parse COMMIT_SUBJECT from Claude's
                   output and use it instead of *commit_msg*. Falls back to
                   *commit_msg* if no valid subject is found.

    Returns:
        A :class:`StepResult` — truthy when a commit was created (backward
        compatible with ``bool``), with ``.output`` carrying the cleaned
        Claude CLI output text.
    """
    models = get_model_config()

    tools = ["Bash", "Read", "Write", "Glob", "Grep", "Edit"]
    if use_skill:
        tools.append("Skill")

    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=tools,
        model=models["mission"],
        fallback=models["fallback"],
        max_turns=max_turns,
    )

    from app.commit_conventions import parse_commit_subject

    result = run_claude(cmd, project_path, timeout=timeout)
    cleaned_output = strip_cli_noise(result.get("output", ""))
    if result["success"]:
        effective_msg = commit_msg
        if use_convention_subject:
            parsed = parse_commit_subject(cleaned_output)
            if parsed:
                effective_msg = _sanitize_commit_subject(parsed)
        committed = commit_if_changes(project_path, effective_msg)
        if committed and success_label:
            actions_log.append(success_label)
        return StepResult(committed=committed, output=cleaned_output)
    elif failure_label:
        error_detail = result['error'][:200]
        # Claude CLI often reports errors via stdout, not stderr.
        # Include stdout snippet when stderr is empty to aid debugging.
        if "no stderr" in error_detail and result.get("output"):
            stdout_snippet = result["output"][-300:]
            error_detail = f"{error_detail} | stdout: {stdout_snippet}"
        actions_log.append(f"{failure_label}: {error_detail}")

    quota_exhausted = False
    try:
        from app.cli_errors import ErrorCategory, classify_cli_error
        from app.provider import get_provider_name

        quota_exhausted = (
            classify_cli_error(
                int(result.get("exit_code") or 1),
                stdout=result.get("output", ""),
                stderr=result.get("error", ""),
                provider_name=get_provider_name(),
            )
            == ErrorCategory.QUOTA
        )
    except Exception as exc:
        logging.warning("Failed to classify Claude step error: %s", exc)
        quota_exhausted = False

    return StepResult(
        committed=False,
        output=cleaned_output,
        quota_exhausted=quota_exhausted,
        error=result.get("error", ""),
    )


def run_project_tests(project_path: str, test_cmd: str = "make test",
                      timeout: int = 300) -> dict:
    """Run a project's test suite and return structured results.

    Args:
        project_path: Path to the project root.
        test_cmd: Shell command to run tests (default: "make test").
        timeout: Maximum seconds to wait.

    Returns:
        Dict with keys: passed (bool), output (str), details (str).
    """
    try:
        result = subprocess.run(
            shlex.split(test_cmd),
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True,
            timeout=timeout, cwd=project_path,
        )
        output = result.stdout + result.stderr
        passed = result.returncode == 0

        details = "OK" if passed else "FAILED"
        count_match = re.search(
            r'(\d+)\s+(?:tests?|passed)', output, re.IGNORECASE
        )
        if count_match:
            if passed:
                details = count_match.group(0)
            else:
                # Keep FAILED prefix with count info for context
                failed_match = re.search(r'(\d+)\s+failed', output, re.IGNORECASE)
                if failed_match:
                    details = f"{failed_match.group(0)}, {count_match.group(0)}"

        return {"passed": passed, "output": output[-3000:], "details": details}
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "", "details": f"timeout ({timeout}s)"}
    except FileNotFoundError:
        return {"passed": False, "output": "", "details": "command not found"}
    except Exception as e:
        return {"passed": False, "output": str(e), "details": str(e)[:100]}


# ---------------------------------------------------------------------------
# Shared PR pipeline helpers
# ---------------------------------------------------------------------------

def _get_current_branch(project_path: str) -> str:
    """Get the current branch name.

    Delegates to :func:`app.git_utils.get_current_branch`.
    Kept as a re-export so ``rebase_pr`` and ``recreate_pr`` continue to work.
    """
    return _git_utils_get_current_branch(cwd=project_path)


def _get_diffstat(base_ref: str, project_path: str) -> str:
    """Get a compact diffstat between base_ref and HEAD.

    Returns a summary like "5 files changed, 42 insertions(+), 10 deletions(-)"
    or empty string on failure.
    """
    try:
        stat = _run_git(
            ["git", "diff", "--stat", f"{base_ref}..HEAD"],
            cwd=project_path,
            timeout=30,
        )
        # The last line of --stat output is the summary
        lines = stat.strip().splitlines()
        if lines:
            return lines[-1].strip()
    except Exception as e:
        print(f"[claude_step] diffstat failed: {e}", file=sys.stderr)
    return ""


def _safe_checkout(branch: str, project_path: str) -> None:
    """Checkout a branch without raising on failure."""
    try:
        _run_git(["git", "checkout", branch], cwd=project_path)
    except Exception as e:
        print(f"[claude_step] Safe checkout failed for {branch}: {e}", file=sys.stderr)


# Conclusions that don't signal a real CI outcome. The classic case is
# "Dependabot auto-merge", which runs on every PR but only acts on
# Dependabot-authored PRs — on every other PR it completes with
# conclusion="skipped". Treating that as a CI failure sends Kōan into a
# fix loop against a workflow that isn't actually broken.
_IGNORED_CI_CONCLUSIONS = frozenset(
    {"skipped", "cancelled", "neutral", "action_required"}
)

# Workflow run statuses that mean "blocked, awaiting manual action".
# GitHub sets `status="action_required"` on fork PRs from first-time
# contributors until a maintainer approves the run, and `status="waiting"`
# when a job is gated on environment approval. In both cases, polling
# forever — or, worse, pushing new commits to "fix" CI — never unsticks
# the run. Kōan must treat these as terminal so the PR drops out of the
# ## CI queue with a human-readable note.
_APPROVAL_BLOCKED_STATUSES = frozenset({"action_required", "waiting"})

# Canonical CI status string returned by aggregate_ci_runs() and
# wait_for_ci() when a workflow run is blocked on maintainer or
# environment approval.  Use the constant instead of the raw string
# to avoid typos across modules.
CI_STATUS_BLOCKED_APPROVAL = "blocked_approval"

# Upper bound on runs fetched per branch — enough to cover all workflows
# triggered by a single push (typically <10), small enough to keep the
# `gh run list` call cheap.
_CI_RUN_LIMIT = 20


def _filter_runs_to_latest_sha(runs: list) -> list:
    """Return only the runs whose ``headSha`` matches the latest SHA.

    The latest SHA is the ``headSha`` of the run with the greatest
    ``createdAt`` value. When ``createdAt`` is missing for the candidate,
    the run's position in the input list (later = newer, matching
    ``gh run list`` ordering) breaks the tie.

    Runs without a ``headSha`` field are left untouched (treated as a
    single anonymous group) — this preserves behaviour for legacy callers
    and the bulk of existing tests.
    """
    has_sha = [r for r in runs if r.get("headSha")]
    if not has_sha:
        return runs

    def _sort_key(r):
        # createdAt is ISO-8601 and lexicographically sortable; fallback
        # to the run's index in the original list so the most-recently
        # returned entry still wins when timestamps are missing.
        return (r.get("createdAt") or "", runs.index(r))

    latest_sha = max(has_sha, key=_sort_key).get("headSha")
    return [r for r in runs if r.get("headSha") == latest_sha]


def aggregate_ci_runs(runs: list) -> Tuple[str, Optional[int]]:
    """Reduce a list of workflow runs to a single (status, run_id) tuple.

    Restricts aggregation to runs on the **latest** commit SHA seen in
    *runs* (by ``createdAt``), so a failed run from a prior commit on the
    same branch doesn't masquerade as a current failure. Runs whose entry
    omits ``headSha`` are treated as a single anonymous group — preserving
    backward compatibility with callers that don't supply the field.

    Then filters out runs whose conclusion is in
    :data:`_IGNORED_CI_CONCLUSIONS` (notably the "Dependabot auto-merge"
    skip case) so a benign skipped workflow doesn't masquerade as a CI
    failure.

    Aggregation rules over the remaining runs:
    - any failed completed run → ("failure", failed_run_id)
    - else any run blocked on maintainer/environment approval →
      ("blocked_approval", blocked_run_id) — Kōan can't unstick it, so
      callers should stop retrying and surface a notification.
    - else any non-completed run → ("pending", pending_run_id)
    - else all completed + success → ("success", first_run_id)
    - empty input or every run filtered out → ("none", None)

    Failure takes precedence over blocked_approval so a genuinely broken
    workflow on the same push still gets surfaced for a fix attempt.
    """
    if not runs:
        return ("none", None)

    runs = _filter_runs_to_latest_sha(runs)

    relevant = [
        r for r in runs
        if (r.get("conclusion") or "").lower() not in _IGNORED_CI_CONCLUSIONS
    ]
    if not relevant:
        return ("none", None)

    failed_run = None
    blocked_run = None
    pending_run = None
    for run in relevant:
        status = (run.get("status") or "").lower()
        conclusion = (run.get("conclusion") or "").lower()
        if status == "completed":
            if conclusion != "success" and failed_run is None:
                failed_run = run
        elif status in _APPROVAL_BLOCKED_STATUSES:
            if blocked_run is None:
                blocked_run = run
        elif pending_run is None:
            pending_run = run

    if failed_run is not None:
        return ("failure", failed_run.get("databaseId"))
    if blocked_run is not None:
        return (CI_STATUS_BLOCKED_APPROVAL, blocked_run.get("databaseId"))
    if pending_run is not None:
        return ("pending", pending_run.get("databaseId"))
    return ("success", relevant[0].get("databaseId"))


def fetch_branch_ci_runs(branch: str, full_repo: str) -> list:
    """Return raw `gh run list` entries for a branch.

    Raises on `gh` failure so callers can decide between fall-back
    behaviours (e.g. "treat as pending" vs "treat as none").
    """
    raw = run_gh(
        "run", "list",
        "--branch", branch,
        "--repo", full_repo,
        "--json", "databaseId,status,conclusion,name,workflowName,headSha,createdAt",
        "--limit", str(_CI_RUN_LIMIT),
    )
    return json.loads(raw) if raw.strip() else []


def wait_for_ci(
    branch: str,
    full_repo: str,
    *,
    timeout: int = 600,
    poll_interval: int = 30,
) -> Tuple[str, Optional[int], str]:
    """Poll GitHub Actions CI for a branch until completion or timeout.

    Args:
        branch: Branch name to check CI for.
        full_repo: "owner/repo" string.
        timeout: Max seconds to wait (default 10 min).
        poll_interval: Seconds between polls (default 30s).

    Returns:
        (status, run_id, logs) where:
        - status: "success", "failure", "blocked_approval", "timeout", or "none"
        - run_id: GitHub Actions run ID (None if no runs found)
        - logs: Failed job logs (empty unless status is "failure")
    """
    deadline = time.time() + timeout

    # Wait a few seconds for GitHub to register the push
    time.sleep(min(10, poll_interval))

    while time.time() < deadline:
        try:
            runs = fetch_branch_ci_runs(branch, full_repo)
        except Exception as e:
            print(f"[claude_step] CI poll error: {e}", file=sys.stderr)
            time.sleep(poll_interval)
            continue

        status, run_id = aggregate_ci_runs(runs)

        if status == "none":
            # No CI signal — either no runs, or every run was filtered as
            # non-CI (e.g. a Dependabot auto-merge skip with nothing else
            # registered yet). Mirror the original "no runs" exit.
            return ("none", None, "")

        if status == "success":
            return ("success", run_id, "")

        if status == "failure":
            logs = _fetch_failed_logs(run_id, full_repo) if run_id else ""
            return ("failure", run_id, logs)

        if status == CI_STATUS_BLOCKED_APPROVAL:
            # A maintainer (or environment reviewer) must click Approve in
            # the GitHub UI; polling won't change that. Exit so the caller
            # can surface a notification instead of burning quota.
            return (CI_STATUS_BLOCKED_APPROVAL, run_id, "")

        # status == "pending" — keep polling
        time.sleep(poll_interval)

    return ("timeout", None, "")


def _fetch_failed_logs(run_id: int, full_repo: str, max_chars: int = 8000) -> str:
    """Fetch logs for failed jobs in a GitHub Actions run.

    Returns truncated log output for context.  Retries once after a
    short delay when the first attempt returns empty — GitHub sometimes
    needs a few seconds to make logs available after a run completes.
    """
    import time

    for attempt in range(2):
        try:
            raw = run_gh(
                "run", "view", str(run_id),
                "--repo", full_repo,
                "--log-failed",
            )
            if raw:
                if len(raw) > max_chars:
                    return "... (truncated)\n" + raw[-max_chars:]
                return raw
            # Empty response — retry after a brief pause
            if attempt == 0:
                time.sleep(5)
        except Exception as e:
            return f"(Could not fetch logs: {e})"
    return ""


def check_existing_ci(
    branch: str,
    full_repo: str,
) -> Tuple[str, Optional[int], str]:
    """Check the most recent CI run on a branch without polling.

    Unlike ``wait_for_ci`` which polls until completion, this does a single
    check to see the current CI state.  Useful for inspecting pre-existing
    failures before pushing a new version.

    Returns:
        (status, run_id, logs) where:
        - status: "success", "failure", "pending", "blocked_approval", or "none"
        - run_id: GitHub Actions run ID (None if no runs found)
        - logs: Failed job logs (empty unless status is "failure")
    """
    try:
        runs = fetch_branch_ci_runs(branch, full_repo)
    except Exception as e:
        print(f"[claude_step] CI check error: {e}", file=sys.stderr)
        return ("none", None, "")

    status, run_id = aggregate_ci_runs(runs)

    if status == "failure":
        logs = _fetch_failed_logs(run_id, full_repo) if run_id else ""
        return ("failure", run_id, logs)

    return (status, run_id, "")


def _force_push(remote: str, branch: str, project_path: str) -> None:
    """Force-push branch, trying --force-with-lease first then --force.

    Raises on total failure.
    """
    try:
        _run_git(
            ["git", "push", remote, branch, "--force-with-lease"],
            cwd=project_path,
        )
    except Exception as e:
        print(f"[claude_step] --force-with-lease failed, falling back to --force: {e}", file=sys.stderr)
        _run_git(
            ["git", "push", remote, branch, "--force"],
            cwd=project_path,
        )


def run_ci_fix_loop(
    branch: str,
    base: str,
    full_repo: str,
    project_path: str,
    ci_logs: str,
    actions_log: List[str],
    *,
    max_attempts: int = 2,
    commit_conventions: str = "",
    use_polling: bool = False,
    prompt_builder: Callable[[str, str], str],
    commit_msg_template: str = "fix: resolve CI failures (attempt {attempt})",
    base_remote: str = "origin",
) -> Tuple[bool, str]:
    """Core CI fix loop: diff-fetch -> prompt -> Claude step -> push -> recheck.

    Extracts the repeated pattern shared by ``_attempt_ci_fixes`` (ci_queue_runner)
    and ``_run_ci_check_and_fix`` (rebase_pr) into a single function.

    Args:
        branch: Git branch to fix.
        base: Base branch for diff context.
        full_repo: ``"owner/repo"`` string.
        project_path: Local path to the project repository.
        ci_logs: Initial CI failure logs.
        actions_log: Mutable list for logging actions.
        max_attempts: Maximum fix attempts.
        commit_conventions: Project commit convention guidance.
        use_polling: If True, use ``wait_for_ci`` (blocking poll); else use
            ``check_existing_ci`` after a brief sleep (non-blocking).
        prompt_builder: ``(ci_logs, diff) -> prompt`` callable. Keeps
            caller-specific prompt logic out of this module.
        commit_msg_template: Template with ``{attempt}`` placeholder.
        base_remote: Remote name for diff base (default ``"origin"``).

    Returns:
        ``(success, last_ci_logs)`` — *success* is True if CI passes or a fix
        was pushed and CI is pending/running. Callers decide what to do with
        the pending state (e.g. re-enqueue for monitoring).
    """
    from app.config import get_skill_max_turns, get_skill_timeout
    from app.utils import truncate_diff

    for attempt in range(1, max_attempts + 1):
        print(f"[claude_step] CI fix attempt {attempt}/{max_attempts}", file=sys.stderr)
        actions_log.append(f"CI fix attempt {attempt}/{max_attempts}")

        # Fetch diff for context
        diff = ""
        try:
            diff = _run_git(
                ["git", "diff", f"{base_remote}/{base}..HEAD"],
                cwd=project_path, timeout=30,
            )
        except Exception as e:
            print(f"[claude_step] diff fetch failed: {e}", file=sys.stderr)
        diff = truncate_diff(diff, 32000)

        # Build prompt and run Claude
        prompt = prompt_builder(ci_logs, diff)

        fixed = run_claude_step(
            prompt=prompt,
            project_path=project_path,
            commit_msg=commit_msg_template.format(attempt=attempt),
            success_label=f"Applied CI fix (attempt {attempt})",
            failure_label=f"CI fix step failed (attempt {attempt})",
            actions_log=actions_log,
            max_turns=get_skill_max_turns(),
            timeout=get_skill_timeout(),
            use_convention_subject=bool(commit_conventions),
        )

        if getattr(fixed, "quota_exhausted", False):
            actions_log.append(CI_QUOTA_STOP_ACTION)
            return False, ci_logs

        if not fixed:
            actions_log.append("Claude produced no changes — giving up")
            break

        # Force-push the fix
        try:
            _force_push("origin", branch, project_path)
        except Exception as e:
            actions_log.append(f"Push failed: {str(e)[:100]}")
            break

        actions_log.append(f"Pushed CI fix (attempt {attempt})")

        # Recheck CI
        if use_polling:
            status, _run_id, new_logs = wait_for_ci(branch, full_repo)
        else:
            time.sleep(15)
            status, _run_id, new_logs = check_existing_ci(branch, full_repo)

        if status == "success":
            actions_log.append(f"CI passed after fix attempt {attempt}")
            return True, new_logs

        if status == CI_STATUS_BLOCKED_APPROVAL:
            actions_log.append(
                f"CI waiting for approval after fix attempt {attempt} — stopping"
            )
            return False, new_logs

        # Polling path: timeout/none are terminal — fix was pushed, can't confirm
        if use_polling and status in ("timeout", "none"):
            actions_log.append(f"CI {status} after fix attempt {attempt}")
            return True, new_logs

        # Non-polling path: pending means CI is running with our fix
        if not use_polling and status == "pending":
            actions_log.append(
                f"CI running after fix push (attempt {attempt})"
            )
            return True, new_logs

        # Failure — update logs for next attempt
        if new_logs:
            ci_logs = new_logs

    actions_log.append(f"CI still failing after {max_attempts} fix attempts")
    return False, ci_logs


def _is_permission_error(error_msg: str) -> bool:
    """Check if an error message indicates a permission/access problem."""
    indicators = [
        "permission", "denied", "forbidden", "403",
        "protected branch", "not allowed",
        "unable to access", "authentication failed",
    ]
    lower = error_msg.lower()
    return any(ind in lower for ind in indicators)


def resolve_pr_location(
    owner: str,
    repo: str,
    pr_number: str,
    project_path: str,
) -> Tuple[str, str]:
    """Resolve the actual GitHub owner/repo where a PR lives.

    When a user provides a PR URL from a different fork (e.g.,
    ``sukria/koan/pull/171`` instead of ``Anantys-oss/koan/pull/171``),
    the PR may not exist at the given owner/repo.  This helper verifies
    the PR exists, and if not, tries all git remotes of the local project
    to find the repository that actually hosts the PR.

    Args:
        owner: Owner from the URL
        repo: Repo name from the URL
        pr_number: PR number as string
        project_path: Local path to the project (for git remote discovery)

    Returns:
        Tuple of (resolved_owner, resolved_repo) where the PR exists.

    Raises:
        RuntimeError: If the PR cannot be found at any known remote.
    """
    # Fast path: check if PR exists at the given owner/repo
    try:
        run_gh(
            "pr", "view", str(pr_number),
            "--repo", f"{owner}/{repo}",
            "--json", "number",
        )
        return owner, repo
    except RuntimeError:
        pass

    # Fallback: try all git remotes from the local project
    from app.utils import get_all_github_remotes

    remotes = get_all_github_remotes(project_path)
    tried = {f"{owner}/{repo}".lower()}

    for remote_slug in remotes:
        slug_lower = remote_slug.lower()
        if slug_lower in tried:
            continue
        tried.add(slug_lower)
        try:
            run_gh(
                "pr", "view", str(pr_number),
                "--repo", remote_slug,
                "--json", "number",
            )
            parts = remote_slug.split("/", 1)
            logging.info(
                "PR #%s not found at %s/%s, resolved to %s",
                pr_number, owner, repo, remote_slug,
            )
            return parts[0], parts[1]
        except RuntimeError:
            continue

    raise RuntimeError(
        f"PR #{pr_number} not found at {owner}/{repo} "
        f"or any known remote ({', '.join(sorted(tried))})"
    )


def _build_pr_prompt(
    prompt_name: str,
    context: dict,
    skill_dir: Optional[Path] = None,
    max_diff_chars: int = 80_000,
    commit_conventions: str = "",
) -> str:
    """Build a prompt for Claude to process PR feedback.

    Shared by rebase and recreate pipelines — the only difference is the
    prompt template name.

    Args:
        prompt_name: Prompt template name (e.g. "rebase", "recreate").
        context: PR context dict from fetch_pr_context().
        skill_dir: Optional skill directory for prompt resolution.
        max_diff_chars: Maximum characters for the diff section to prevent
            context window overflow on large PRs.
        commit_conventions: Project commit convention guidance to include
            in the prompt. When non-empty, also loads the commit subject
            instruction fragment.
    """
    diff = context.get("diff", "")
    if len(diff) > max_diff_chars:
        diff = diff[:max_diff_chars] + "\n\n... (diff truncated — too large for context window)"
        print(
            f"[claude_step] Diff truncated from {len(context.get('diff', ''))} "
            f"to {max_diff_chars} chars",
            file=sys.stderr,
        )

    commit_subject_instruction = ""
    if commit_conventions:
        commit_subject_instruction = _load_commit_subject_instruction(skill_dir)

    from app.prompt_guard import fence_external_data

    kwargs = dict(
        TITLE=fence_external_data(context["title"], "PR title"),
        BODY=fence_external_data(context.get("body", ""), "PR body"),
        BRANCH=context["branch"],
        BASE=context["base"],
        DIFF=fence_external_data(diff, "PR diff", scan=False),
        REVIEW_COMMENTS=fence_external_data(
            context.get("review_comments", ""), "review comments"
        ),
        REVIEWS=fence_external_data(
            context.get("reviews", ""), "reviews"
        ),
        ISSUE_COMMENTS=fence_external_data(
            context.get("issue_comments", ""), "issue comments"
        ),
        COMMIT_CONVENTIONS=commit_conventions,
        COMMIT_SUBJECT_INSTRUCTION=commit_subject_instruction,
    )
    return load_prompt_or_skill(skill_dir, prompt_name, **kwargs)


def _sanitize_commit_subject(subject: str) -> str:
    """Sanitize a parsed commit subject for safe use in git commit messages.

    Strips control characters and collapses whitespace to prevent
    malformed or adversarial subjects from breaking git log output.
    """
    import unicodedata

    # Strip control characters (keep printable + spaces)
    cleaned = "".join(
        ch for ch in subject
        if not unicodedata.category(ch).startswith("C") or ch == "\t"
    )
    # Collapse whitespace and strip
    cleaned = " ".join(cleaned.split()).strip()
    return cleaned


def _load_commit_subject_instruction(skill_dir: Optional[Path] = None) -> str:
    """Load the commit subject instruction prompt fragment.

    Tries the skill directory first, then falls back to system prompts.
    Returns empty string if the fragment is not found.
    """
    if skill_dir is not None:
        path = skill_dir / "prompts" / "commit_subject_instruction.md"
        try:
            return path.read_text()
        except (FileNotFoundError, OSError):
            pass

    # Fall back to system-prompts directory
    from app.prompts import PROMPT_DIR
    path = PROMPT_DIR / "commit_subject_instruction.md"
    try:
        return path.read_text()
    except (FileNotFoundError, OSError):
        return ""


# -- Push with PR fallback (shared config) ----------------------------------

_PR_TYPE_CONFIG = {
    "rebase": {
        "force_label": "Force-pushed `{branch}`",
        "branch_suffix": "rebase-",
        "title_prefix": "[Rebase]",
        "pr_body": (
            "Supersedes #{pr_number}.\n\n"
            "This PR contains the rebased version of `{branch}` onto `{base}`.\n"
            "Original PR: {url}\n\n"
            "---\n_Automated by Kōan_"
        ),
        "crosslink": (
            "This PR has been rebased and superseded by {ref}.\n\n"
            "The new PR contains the same changes rebased onto `{base}`.\n\n"
            "---\n_Automated by Kōan_"
        ),
    },
    "recreate": {
        "force_label": "Force-pushed `{branch}` (recreated from scratch)",
        "branch_suffix": "recreate-",
        "title_prefix": "[Recreate]",
        "pr_body": (
            "Supersedes #{pr_number}.\n\n"
            "This PR contains a fresh reimplementation of the original feature, "
            "built on top of current `{base}`.\n\n"
            "The original branch had diverged too far for a clean rebase, so the "
            "feature was recreated from scratch based on the original PR's intent.\n\n"
            "Original PR: {url}\n\n"
            "---\n_Automated by Kōan_"
        ),
        "crosslink": (
            "This PR has been recreated from scratch and superseded by {ref}.\n\n"
            "The original branch had diverged too far for a clean rebase. "
            "The new PR contains a fresh reimplementation on current `{base}`.\n\n"
            "---\n_Automated by Kōan_"
        ),
    },
}


def _push_with_pr_fallback(
    branch: str,
    base: str,
    full_repo: str,
    pr_number: str,
    context: dict,
    project_path: str,
    *,
    pr_type: str = "rebase",
) -> dict:
    """Push branch, falling back to new draft PR if permission denied.

    Shared by rebase and recreate pipelines.

    Args:
        pr_type: "rebase" or "recreate" — controls labels, prefix, and body text.

    Returns:
        dict with keys: success, actions, error, new_pr_url (optional).
    """
    actions: List[str] = []
    cfg = _PR_TYPE_CONFIG.get(pr_type, _PR_TYPE_CONFIG["rebase"])

    # Option 1: Try force-pushing to the existing branch
    try:
        _run_git(
            ["git", "push", "origin", branch, "--force-with-lease"],
            cwd=project_path,
        )
        actions.append(cfg["force_label"].format(branch=branch))
        return {"success": True, "actions": actions, "error": ""}
    except Exception as push_error:
        error_msg = str(push_error)

    # Option 2: Permission denied — create a new draft PR
    if not _is_permission_error(error_msg):
        return {"success": False, "actions": actions, "error": error_msg}

    from app.config import get_branch_prefix
    prefix = get_branch_prefix()
    new_branch = f"{prefix}{cfg['branch_suffix']}{branch.replace('/', '-')}"
    try:
        _run_git(["git", "checkout", "-b", new_branch], cwd=project_path)
        _run_git(["git", "push", "-u", "origin", new_branch], cwd=project_path)
        actions.append(
            f"Created new branch `{new_branch}` (no push permission on `{branch}`)"
        )

        title = context.get("title", f"{cfg['title_prefix'].strip('[]')} of #{pr_number}")
        boilerplate = cfg["pr_body"].format(
            pr_number=pr_number, branch=branch, base=base,
            url=context.get("url", f"#{pr_number}"),
        )
        pr_body = boilerplate
        try:
            from app.describe_pr import describe_pr, format_description
            desc = describe_pr(project_path, base)
            if desc:
                pr_body = f"{format_description(desc)}\n\n{boilerplate}"
        except Exception as _desc_err:
            logging.warning("[%s_pr] describe_pr failed, using boilerplate: %s", pr_type, _desc_err)
        new_pr_url = pr_create(
            title=f"{cfg['title_prefix']} {title}",
            body=pr_body,
            draft=True,
            base=base,
            repo=full_repo,
            head=new_branch,
        )
        actions.append(f"Created draft PR: {new_pr_url.strip()}")

        # Cross-link on original PR
        new_pr_match = re.search(r'/pull/(\d+)', new_pr_url)
        new_pr_ref = new_pr_match.group(0) if new_pr_match else new_pr_url.strip()

        try:
            run_gh(
                "pr", "comment", pr_number,
                "--repo", full_repo,
                "--body", sanitize_github_comment(cfg["crosslink"].format(ref=new_pr_ref, base=base)),
            )
            actions.append("Cross-linked original PR")
        except Exception as e:
            print(f"[{pr_type}_pr] Cross-link comment failed: {e}", file=sys.stderr)

        return {
            "success": True,
            "actions": actions,
            "error": "",
            "new_pr_url": new_pr_url.strip(),
        }

    except Exception as e:
        return {
            "success": False,
            "actions": actions,
            "error": f"Failed to create fallback PR: {e}",
        }
