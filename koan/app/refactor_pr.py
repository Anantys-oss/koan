"""
Kōan -- Pull Request refactor workflow.

Refactors the code on a PR's branch for simplicity, reuse, and clarity while
preserving behavior, commits the result, pushes to the existing PR branch, and
posts a bullet-list summary comment on the PR.

Pipeline:
1. Resolve the PR location (cross-owner support) and fetch its metadata
2. Checkout the PR branch locally
3. Run the reusable refactor pass (refactor → commit → tests → push)
4. Comment on the PR with a summary of what changed
5. Restore the original branch

The actual refactoring lives in :mod:`app.refactor_step` so the same pass can
run internally inside /implement, /rebase and /fix before their review gate.
"""

import sys
from pathlib import Path
from typing import Optional, Tuple

from app.claude_step import _get_current_branch, _safe_checkout, resolve_pr_location
from app.github import run_gh, sanitize_github_comment
from app.rebase_pr import _find_remote_for_repo, fetch_pr_context
from app.refactor_step import RefactorResult, run_refactor_pass
from app.squash_pr import _checkout_pr_branch

_REFACTOR_SKILL_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "core" / "refactor"
)


def run_refactor(
    owner: str,
    repo: str,
    pr_number: str,
    project_path: str,
    context: str = "",
    notify_fn=None,
    skill_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Execute the refactor pipeline for a pull request.

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram
    skill_dir = skill_dir or _REFACTOR_SKILL_DIR

    # -- Step 0: Resolve actual PR location (cross-owner support) --
    print(f"[refactor] Starting refactor for PR #{pr_number}", flush=True)
    try:
        owner, repo = resolve_pr_location(owner, repo, pr_number, project_path)
    except RuntimeError as e:
        return False, str(e)
    full_repo = f"{owner}/{repo}"

    # -- Step 1: Fetch PR context --
    notify_fn(f"Reading PR #{pr_number}...")
    try:
        pr = fetch_pr_context(owner, repo, pr_number, project_path)
    except Exception as e:
        return False, f"Failed to fetch PR context: {e}"

    pr_state = pr.get("state", "").upper()
    if pr_state in ("MERGED", "CLOSED"):
        msg = f"PR #{pr_number} is already {pr_state.lower()} — skipping refactor."
        notify_fn(msg)
        return True, msg

    branch = pr.get("branch")
    if not branch:
        return False, "Could not determine PR branch name."
    base = pr.get("base", "main")

    head_owner = pr.get("head_owner", "")
    head_remote = (
        _find_remote_for_repo(head_owner, repo, project_path) if head_owner else None
    )

    # -- Step 2: Checkout PR branch --
    notify_fn(f"Checking out `{branch}`...")
    original_branch = _get_current_branch(project_path)
    try:
        _checkout_pr_branch(
            branch, project_path,
            head_remote=head_remote, head_owner=head_owner, repo=repo,
        )
    except Exception as e:
        return False, f"Failed to checkout branch `{branch}`: {e}"

    # -- Step 3: Refactor pass --
    focus = f" (focus: {context})" if context else ""
    notify_fn(f"Refactoring `{branch}`{focus}...")
    try:
        result = run_refactor_pass(
            project_path,
            context=context,
            skill_dir=skill_dir,
            base_branch=base,
            branch=branch,
            notify_fn=notify_fn,
            run_tests=True,
            push=True,
        )
    except Exception as e:
        _safe_checkout(original_branch, project_path)
        return False, f"Refactor failed: {e}"

    # No changes needed — leave a short note and stop.
    if not result.committed:
        _safe_checkout(original_branch, project_path)
        _comment_no_changes(pr_number, full_repo, notify_fn)
        msg = f"PR #{pr_number}: no refactoring changes were needed."
        notify_fn(msg)
        return True, msg

    if not result.pushed:
        _safe_checkout(original_branch, project_path)
        return False, (
            f"Refactored `{branch}` but the push was rejected — the branch may "
            "not be pushable from this instance."
        )

    # -- Step 4: Comment on the PR --
    comment_body = _build_refactor_comment(branch, result)
    commented = False
    try:
        run_gh(
            "pr", "comment", pr_number,
            "--repo", full_repo,
            "--body", sanitize_github_comment(comment_body),
        )
        commented = True
    except Exception as e:
        notify_fn(f"Changes pushed but failed to comment on PR: {e}")

    # -- Step 5: Restore original branch --
    _safe_checkout(original_branch, project_path)

    parts = [f"PR #{pr_number} refactored and pushed."]
    parts.extend(f"- {b}" for b in result.bullets)
    if result.tests:
        parts.append(f"- Tests: {result.tests}")
    if commented:
        parts.append("- Commented on PR")
    return True, "\n".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_refactor_comment(branch: str, result: RefactorResult) -> str:
    """Build a markdown comment summarizing the refactor."""
    bullets = result.bullets or ["Simplified and cleaned up recent changes."]
    changes_md = "\n".join(f"- {b}" for b in bullets)

    parts = [
        "## Refactor\n",
        f"Branch `{branch}` was refactored for clarity and simplicity — "
        "preserving behavior — then pushed.\n",
        f"### What changed\n\n{changes_md}\n",
    ]
    if result.tests:
        parts.append(f"### Tests\n\n{result.tests}\n")
    parts.append("---\n_Automated by Kōan_")
    return "\n".join(parts)


def _comment_no_changes(pr_number: str, full_repo: str, notify_fn) -> None:
    """Post a short best-effort note when no refactoring was needed."""
    body = (
        "## Refactor\n\nNo refactoring changes were needed — the code already "
        "reads cleanly. ✅\n\n---\n_Automated by Kōan_"
    )
    try:
        run_gh(
            "pr", "comment", pr_number,
            "--repo", full_repo,
            "--body", sanitize_github_comment(body),
        )
    except Exception as e:
        notify_fn(f"Could not comment on PR: {e}")


# ---------------------------------------------------------------------------
# CLI entry point -- python3 -m app.refactor_pr <url> --project-path <path>
# ---------------------------------------------------------------------------


def main(argv=None):
    """CLI entry point for refactor_pr.

    Returns exit code (0 = success, 1 = failure).
    """
    import argparse

    from app.github_url_parser import parse_pr_url as _parse_url

    parser = argparse.ArgumentParser(
        description="Refactor a GitHub PR's code for clarity, then push & comment."
    )
    parser.add_argument("url", help="GitHub PR URL")
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--context", default="",
        help="Optional extra focus for the refactor (e.g. 'focus on the tests')",
    )
    cli_args = parser.parse_args(argv)

    try:
        owner, repo, pr_number = _parse_url(cli_args.url)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    success, summary = run_refactor(
        owner, repo, pr_number, cli_args.project_path,
        context=cli_args.context,
        skill_dir=_REFACTOR_SKILL_DIR,
    )

    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
