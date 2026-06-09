"""Gogs PR review workflow.

Mirrors pr_review.run_pr_review but uses the GogsForge API (urllib-based)
instead of the ``gh`` CLI so it works with self-hosted Gogs instances.
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from app.claude_step import (
    _rebase_onto_target,
    _run_git,
    run_claude_step as _run_claude_step,
    run_project_tests,
)
from app.config import get_skill_max_turns
from app.pr_review import (
    _build_pr_comment,
    build_pr_prompt,
    build_quality_review_prompt,
    build_refactor_prompt,
    detect_skills,
    detect_test_command,
)
from app.utils import truncate_diff, truncate_text

log = logging.getLogger(__name__)


def fetch_pr_context_gogs(
    owner: str,
    repo: str,
    pr_number: str,
    project_path: Optional[str] = None,
) -> dict:
    """Fetch PR details, diff, and comments from a Gogs instance.

    Returns a dict with the same keys as ``rebase_pr.fetch_pr_context``
    so the rest of the pipeline can treat both forges uniformly.
    """
    from app.forge.gogs import GogsForge
    from app.rebase_pr import _truncate_recent

    forge = GogsForge()
    full_repo = f"{owner}/{repo}"

    # PR metadata
    pr_data = forge.pr_view(full_repo, int(pr_number))

    # Diff (served via web endpoint; may be empty on error)
    diff = ""
    try:
        diff = forge.pr_diff(full_repo, int(pr_number))
    except Exception as exc:
        log.warning("Gogs diff fetch failed for #%s: %s", pr_number, exc)

    # Issue-level comments (Gogs treats PR discussion as issue comments)
    issue_comments = ""
    try:
        comments = forge._api(
            "GET",
            f"{owner}/{repo}/issues/{pr_number}/comments",
        )
        if isinstance(comments, list):
            lines = [
                f"@{c.get('user', {}).get('login', 'unknown')}: {c.get('body', '')}"
                for c in comments
                if isinstance(c, dict)
            ]
            issue_comments = "\n".join(lines)
    except Exception as exc:
        log.warning("Gogs issue comments fetch failed for #%s: %s", pr_number, exc)

    return {
        "title": pr_data.get("title", ""),
        "body": pr_data.get("body", ""),
        "branch": pr_data.get("headRefName", ""),
        "base": pr_data.get("baseRefName", "main"),
        "state": pr_data.get("state", ""),
        "author": "",
        "head_owner": owner,
        "url": pr_data.get("url", ""),
        "diff": truncate_diff(diff, 32000),
        "diff_error": "",
        "review_comments": "",   # Gogs has no review-comment API at this scope
        "reviews": "",
        "issue_comments": _truncate_recent(issue_comments, 4000),
        "has_pending_reviews": False,
    }


def _find_remote_for_gogs_repo(
    owner: str,
    repo: str,
    project_path: str,
) -> Optional[str]:
    """Find the local git remote whose URL matches a Gogs owner/repo.

    Checks both SSH (``git@host:owner/repo``) and HTTPS
    (``https://host/owner/repo``) remote URL shapes.

    Returns the remote name (e.g. ``"origin"``) or ``None`` on no match.
    """
    from app.gogs_auth import get_gogs_host

    gogs_host = get_gogs_host().rstrip("/")
    if not gogs_host:
        return None

    host_frag = gogs_host.replace("https://", "").replace("http://", "").lower()
    target = f"{owner}/{repo}".lower()
    slug_re = re.compile(r"[:/]([^/:]+)/([^/\s.]+?)(?:\.git)?$")

    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            cwd=project_path,
            timeout=5,
        )
        if result.returncode != 0:
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        remote_name, url = parts[0], parts[1]
        if host_frag not in url.lower():
            continue
        m = slug_re.search(url)
        if m and f"{m.group(1)}/{m.group(2)}".lower() == target:
            return remote_name

    return None


def _post_gogs_pr_comment(
    owner: str,
    repo: str,
    pr_number: str,
    body: str,
) -> None:
    """Post a comment on a Gogs PR via the issues comment API."""
    from app.forge.gogs import GogsForge

    forge = GogsForge()
    forge._api(
        "POST",
        f"{owner}/{repo}/issues/{pr_number}/comments",
        {"body": body},
    )


def run_pr_review_gogs(
    owner: str,
    repo: str,
    pr_number: str,
    project_path: str,
    notify_fn=None,
    skill_dir: Path = None,
) -> Tuple[bool, str]:
    """Execute the full PR review pipeline for a Gogs-hosted PR.

    Steps mirror pr_review.run_pr_review:
        1. Fetch PR context from Gogs API
        2. Checkout the PR branch and rebase onto target branch
        3. Run Claude Code to address review feedback
        4. Run refactor skill if available
        5. Run review skill if available
        6. Run tests — fix if broken
        7. Force-push the branch
        8. Comment on PR

    Args:
        owner: Gogs repo owner.
        repo: Gogs repo name.
        pr_number: PR number as string.
        project_path: Local path to the project checkout.
        notify_fn: Optional callback for progress notifications.
        skill_dir: Path to the calling skill directory (for prompt loading).

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    actions_log: List[str] = []

    # ── Step 1: Fetch PR context ──────────────────────────────────────
    notify_fn(f"Reading Gogs PR #{pr_number}...")
    try:
        context = fetch_pr_context_gogs(owner, repo, pr_number, project_path)
    except Exception as exc:
        return False, f"Failed to fetch Gogs PR context: {exc}"

    if not context["branch"]:
        return False, "Could not determine PR branch name."

    branch = context["branch"]
    base = context["base"]

    base_remote = _find_remote_for_gogs_repo(owner, repo, project_path)

    # ── Step 2: Checkout and rebase onto target branch ────────────────
    notify_fn(f"Rebasing `{branch}` onto `{base}`...")
    try:
        _run_git(["git", "fetch", "origin", branch], cwd=project_path)
        _run_git(["git", "checkout", branch], cwd=project_path)
        _run_git(["git", "pull", "origin", branch, "--rebase"], cwd=project_path)
    except Exception as exc:
        return False, f"Failed to checkout branch {branch}: {exc}"

    rebase_remote = _rebase_onto_target(
        base,
        project_path,
        preferred_remote=base_remote,
    )
    if rebase_remote:
        actions_log.append(f"Rebased `{branch}` onto `{rebase_remote}/{base}`")
    else:
        return False, f"Rebase conflict on {base} (tried origin and upstream)"

    # ── Step 3: Address review feedback via Claude Code ───────────────
    has_review_feedback = bool(
        context["review_comments"].strip()
        or context["reviews"].strip()
        or context["issue_comments"].strip()
    )

    if has_review_feedback:
        notify_fn(f"Addressing review comments on `{branch}`...")
        _run_claude_step(
            prompt=build_pr_prompt(context, skill_dir=skill_dir),
            project_path=project_path,
            commit_msg=f"pr-review: address feedback on #{pr_number}",
            success_label="Addressed reviewer feedback",
            failure_label="Review feedback step failed",
            actions_log=actions_log,
            max_turns=get_skill_max_turns(),
        )

    # ── Step 4: Refactor pass ─────────────────────────────────────────
    refactor_skill, review_skill = detect_skills(project_path)

    if refactor_skill:
        notify_fn(f"Running refactor pass ({refactor_skill})...")
        _run_claude_step(
            prompt=build_refactor_prompt(project_path, refactor_skill, skill_dir=skill_dir),
            project_path=project_path,
            commit_msg=f"refactor: apply refactoring pass on #{pr_number}",
            success_label=f"Applied refactoring via `{refactor_skill}`",
            failure_label="Refactor step skipped",
            actions_log=actions_log,
            use_skill=True,
        )

    # ── Step 5: Quality review pass ───────────────────────────────────
    if review_skill:
        notify_fn(f"Running quality review pass ({review_skill})...")
        _run_claude_step(
            prompt=build_quality_review_prompt(project_path, review_skill, skill_dir=skill_dir),
            project_path=project_path,
            commit_msg=f"review: apply quality improvements on #{pr_number}",
            success_label=f"Applied quality review via `{review_skill}`",
            failure_label="Quality review step skipped",
            actions_log=actions_log,
            use_skill=True,
        )

    # ── Step 6: Run tests ─────────────────────────────────────────────
    test_cmd = detect_test_command(project_path)
    if test_cmd:
        notify_fn("Running tests...")
        test_result = run_project_tests(project_path, test_cmd=test_cmd)
        if test_result["passed"]:
            actions_log.append(
                f"Tests passing ({test_result.get('details', 'OK')})"
            )
        else:
            notify_fn("Tests failing — attempting fix...")
            fix_prompt = (
                f"The test suite is failing after PR changes. "
                f"Test command: `{test_cmd}`\n\n"
                f"Test output:\n```\n{test_result.get('output', '')[:3000]}\n```\n\n"
                f"Fix the failing tests. Only modify what's necessary."
            )
            _run_claude_step(
                prompt=fix_prompt,
                project_path=project_path,
                commit_msg=f"fix: repair tests after PR #{pr_number} changes",
                success_label="",
                failure_label="",
                actions_log=[],
                max_turns=get_skill_max_turns(),
                timeout=600,
            )
            retest = run_project_tests(project_path, test_cmd=test_cmd)
            if retest["passed"]:
                actions_log.append("Tests fixed and passing")
            else:
                actions_log.append(
                    f"Tests still failing: {retest.get('details', 'unknown')}"
                )

    # ── Step 7: Force-push ────────────────────────────────────────────
    notify_fn(f"Pushing `{branch}`...")
    try:
        _run_git(
            ["git", "push", "origin", branch, "--force-with-lease"],
            cwd=project_path,
        )
        actions_log.append(f"Force-pushed `{branch}`")
    except Exception as exc:
        return (
            False,
            f"Push failed: {exc}\n\nActions completed before failure:\n"
            + "\n".join(f"- {a}" for a in actions_log),
        )

    # ── Step 8: Comment on PR ─────────────────────────────────────────
    comment_body = _build_pr_comment(pr_number, branch, base, actions_log, context)
    try:
        _post_gogs_pr_comment(owner, repo, pr_number, comment_body)
        actions_log.append("Commented on PR")
    except Exception as exc:
        notify_fn(f"Changes pushed but failed to comment on Gogs PR: {exc}")

    summary = f"Gogs PR #{pr_number} updated.\n" + "\n".join(
        f"- {a}" for a in actions_log
    )
    return True, summary
