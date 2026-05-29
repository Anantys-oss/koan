"""
Koan -- Fix runner.

Reads an issue from the configured tracker (GitHub or Jira), builds a fix
prompt, and invokes Claude to fix it. Unlike implement_runner (which requires
a pre-existing plan), fix_runner takes a raw issue and lets Claude handle the
full pipeline: understand, plan, test, fix, and submit a PR.

CLI:
    python3 -m skills.core.fix.fix_runner --project-path <path> --issue-url <url>
    python3 -m skills.core.fix.fix_runner --project-path <path> --issue-url <url> --context "backend only"
    python3 -m skills.core.fix.fix_runner --project-path <path> --project-name <name> --issue-url <url>
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from app.issue_tracker import (
    UnresolvedJiraProjectError,
    fetch_issue,
    project_name_for_path,
)
from app.issue_tracker.config import resolve_code_repository
from app.pr_submit import (
    get_current_branch,
    guess_project_name,
    submit_draft_pr,
)
from app.prompts import load_prompt_or_skill

logger = logging.getLogger(__name__)


def run_fix(
    project_path: str,
    issue_url: str,
    context: Optional[str] = None,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
    base_branch: Optional[str] = None,
    project_name: str = "",
    instance_dir: str = "",
) -> Tuple[bool, str]:
    """Execute the fix pipeline.

    Fetches the issue through the project's tracker, builds a fix prompt, and
    invokes Claude to understand, plan, test, and fix the issue.

    Args:
        project_path: Local path to the project repository.
        issue_url: GitHub or Jira issue URL.
        context: Optional additional context (e.g. "backend only").
        notify_fn: Notification function (defaults to send_telegram).
        skill_dir: Path to the fix skill directory for prompt loading.

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    print("[fix] Starting fix runner", flush=True)
    context_label = f" ({context})" if context else ""
    project_name = project_name or project_name_for_path(project_path)
    print(f"[fix] Fetching tracker issue {issue_url}", flush=True)

    # The tracker (GitHub or Jira) resolves itself from the URL — the runner
    # never branches on provider.
    try:
        content = fetch_issue(
            issue_url, project_name=project_name, project_path=project_path,
        )
    except UnresolvedJiraProjectError as e:
        msg = str(e)
        notify_fn(f"❌ {msg}")
        return False, msg
    except Exception as e:
        return False, f"Failed to fetch issue: {str(e)[:300]}"

    ref = content.ref
    title = content.title
    body = content.body
    comments = content.comments
    issue_number = ref.key
    label = ref.label
    provider = ref.provider

    if content.state == "closed":
        msg = f"Issue {label} is already closed — skipping."
        logger.info(msg)
        if notify_fn:
            notify_fn(f"⏭ {msg}")
        return True, msg

    # Resolve the GitHub repo that PRs target: the issue's own repo for
    # GitHub, the configured code repo for a Jira-tracked project.
    owner = repo = None
    repo_slug = ref.repo or resolve_code_repository(project_name, project_path)
    if repo_slug and "/" in repo_slug:
        owner, repo = repo_slug.split("/", 1)

    notify_fn(f"\U0001f527 Fixing {provider} issue {label}{context_label}...")

    print("[fix] Issue fetched, building prompt", flush=True)
    if not body and not comments:
        return False, f"Issue {label} has no content."

    # Build full issue body (include relevant comments)
    full_body = _build_issue_body(body, comments)

    # Resolve effective base branch once and feed it through the whole
    # pipeline: the fix prompt needs it so Claude knows which branch counts
    # as "the base" for this project (e.g. `staging`), and the post-fix PR
    # submission + base-branch guard reuse the same resolution.
    from app.projects_config import resolve_base_branch
    effective_base_branch = base_branch or resolve_base_branch(
        project_name, project_path,
    )

    # Invoke Claude with the fix prompt
    print("[fix] Invoking Claude for fix", flush=True)
    try:
        output = _execute_fix(
            project_path=project_path,
            issue_url=issue_url,
            issue_title=title,
            issue_body=full_body,
            context=context or "Fix the issue completely.",
            skill_dir=skill_dir,
            issue_number=str(issue_number),
            project_name=project_name,
            instance_dir=instance_dir,
            base_branch=effective_base_branch,
        )
    except Exception as e:
        return False, f"Fix failed: {str(e)[:300]}"

    if not output:
        return False, "Claude returned empty output."

    # Post-fix: submit draft PR (only when we know the target GitHub repo)
    pr_url = None
    if owner and repo:
        pr_url = _submit_fix_pr(
            project_path=project_path,
            owner=owner,
            repo=repo,
            issue_number=str(issue_number),
            issue_title=title,
            issue_url=issue_url,
            base_branch=base_branch,
            project_name=project_name,
            notify_fn=notify_fn,
        )

    # Build notification and summary
    branch = get_current_branch(project_path)
    on_base_branch = branch in (effective_base_branch, "main", "master")
    if pr_url:
        notify_fn(
            f"✅ Fix complete for issue {label}"
            f"{context_label}\nDraft PR: {pr_url}"
        )
        summary = (
            f"Fix complete for {label}{context_label}"
            f"\nDraft PR: {pr_url}"
        )
    elif not on_base_branch:
        skip_reason = (
            " (PR creation skipped)" if provider != "github"
            else " (PR creation failed — see prior message for details)"
        )
        notify_fn(
            f"✅ Fix complete for issue {label}"
            f"{context_label}\nBranch: {branch}{skip_reason}"
        )
        summary = (
            f"Fix complete for {label}{context_label}"
            f"\nBranch: {branch}"
        )
    else:
        notify_fn(
            f"⚠️ Fix complete for issue {label}"
            f"{context_label} — changes landed on the base branch "
            f"`{branch}`, no PR created. The skill failed to create a "
            "feature branch; move the commits onto a feature branch "
            "manually before pushing."
        )
        summary = (
            f"Fix complete for {label}{context_label}"
            f" (on base branch {branch}, no PR)"
        )

    return True, summary


def _build_issue_body(body: str, comments: List[dict]) -> str:
    """Build full issue content including relevant comments.

    Includes the issue body and any comments that add context
    (e.g. reproduction steps, additional details). Skips bot comments
    and very short comments.
    """
    parts = [body.strip()] if body else []

    for comment in comments:
        comment_body = comment.get("body", "").strip()
        author = comment.get("author", "")

        # Skip bot comments and very short comments
        if "[bot]" in author or len(comment_body) < 20:
            continue

        parts.append(f"\n---\n**Comment by {author}**:\n{comment_body}")

    return "\n".join(parts)


def _execute_fix(
    project_path: str,
    issue_url: str,
    issue_title: str,
    issue_body: str,
    context: str,
    skill_dir: Optional[Path] = None,
    issue_number: str = "",
    project_name: str = "",
    instance_dir: str = "",
    base_branch: Optional[str] = None,
) -> str:
    """Execute the fix via Claude CLI."""
    from app.config import get_branch_prefix
    from app.projects_config import resolve_base_branch
    from app.skill_memory import build_memory_block_for_skill

    branch_prefix = get_branch_prefix()
    effective_base = base_branch or resolve_base_branch(
        project_name or guess_project_name(project_path), project_path,
    )
    project_memory = build_memory_block_for_skill(
        project_path,
        f"{issue_title}\n{issue_body}",
        project_name=project_name,
        instance_dir=instance_dir,
    )

    prompt = _build_prompt(
        issue_url, issue_title, issue_body, context, skill_dir,
        branch_prefix=branch_prefix,
        issue_number=issue_number,
        project_memory=project_memory,
        base_branch=effective_base,
    )

    from app.cli_provider import CLAUDE_TOOLS, run_command_streaming
    from app.config import get_skill_max_turns, get_skill_timeout
    return run_command_streaming(
        prompt, project_path,
        allowed_tools=sorted(CLAUDE_TOOLS),
        max_turns=get_skill_max_turns(), timeout=get_skill_timeout(),
    )


def _build_prompt(
    issue_url: str,
    issue_title: str,
    issue_body: str,
    context: str,
    skill_dir: Optional[Path] = None,
    branch_prefix: str = "koan/",
    issue_number: str = "",
    project_memory: str = "",
    base_branch: str = "main",
) -> str:
    """Build the fix prompt from the issue content."""
    template_vars = dict(
        ISSUE_URL=issue_url,
        ISSUE_TITLE=issue_title,
        ISSUE_BODY=issue_body,
        CONTEXT=context,
        BRANCH_PREFIX=branch_prefix,
        ISSUE_NUMBER=issue_number,
        PROJECT_MEMORY=project_memory,
        BASE_BRANCH=base_branch,
    )

    return load_prompt_or_skill(skill_dir, "fix", **template_vars)


# ---------------------------------------------------------------------------
# Post-fix: draft PR submission (delegates to app.pr_submit)
# ---------------------------------------------------------------------------

def _submit_fix_pr(
    project_path: str,
    owner: str,
    repo: str,
    issue_number: str,
    issue_title: str,
    issue_url: str,
    base_branch: Optional[str] = None,
    project_name: str = "",
    notify_fn=None,
) -> Optional[str]:
    """Build fix-specific PR title/body and delegate to shared submit."""
    from app.pr_submit import get_commit_subjects
    from app.projects_config import resolve_base_branch

    project_name = project_name or guess_project_name(project_path)
    effective_base = base_branch or resolve_base_branch(project_name, project_path)
    commits = get_commit_subjects(project_path, base_branch=effective_base)
    commits_text = "\n".join(f"- {s}" for s in commits)

    pr_title = f"fix: {issue_title}"[:70]
    pr_body = (
        f"## Summary\n\n"
        f"Fixes {issue_url}\n\n"
        f"## Changes\n\n{commits_text}\n\n"
        f"---\n*Generated by Koan /fix*"
    )

    try:
        from app.describe_pr import describe_pr, format_description
        desc = describe_pr(project_path, effective_base)
        if desc:
            pr_body = (
                f"{format_description(desc)}\n\n"
                f"Fixes {issue_url}\n\n"
                f"---\n*Generated by Koan /fix*"
            )
    except Exception as e:
        logger.warning("describe_pr failed, using fallback body: %s", e)

    try:
        return submit_draft_pr(
            project_path=project_path,
            project_name=project_name,
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            pr_title=pr_title,
            pr_body=pr_body,
            issue_url=issue_url,
            base_branch=base_branch,
            notify_fn=notify_fn,
            skill_name="fix",
        )
    except Exception as e:
        logger.warning("PR submission failed: %s", e)
        if notify_fn:
            notify_fn(
                f"❌ PR submission raised "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
        return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for fix_runner."""
    import argparse
    from app.url_skill_args import add_url_skill_common_args

    parser = argparse.ArgumentParser(
        description="Fix a GitHub or Jira issue end-to-end."
    )
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--issue-url", required=True,
        help="GitHub or Jira issue URL to fix",
    )
    add_url_skill_common_args(parser)
    cli_args = parser.parse_args(argv)

    skill_dir = Path(__file__).resolve().parent

    success, summary = run_fix(
        project_path=cli_args.project_path,
        issue_url=cli_args.issue_url,
        context=cli_args.context,
        skill_dir=skill_dir,
        base_branch=cli_args.base_branch,
        project_name=cli_args.project_name,
        instance_dir=cli_args.instance_dir,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
