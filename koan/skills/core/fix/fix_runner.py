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
import re
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
from app.github_url_parser import parse_pr_url

logger = logging.getLogger(__name__)

_SKIP_DIAGNOSE_RE = re.compile(r'\s*--skip-diagnose\s*', re.IGNORECASE)


def _extract_skip_diagnose(context):
    """Extract --skip-diagnose flag from context string.

    Returns (skip_diagnose: bool, cleaned_context: str).
    """
    if not context:
        return False, context or ""
    if _SKIP_DIAGNOSE_RE.search(context):
        return True, _SKIP_DIAGNOSE_RE.sub(" ", context).strip()
    return False, context


def _build_footer() -> str:
    from app.pr_footer import build_koan_footer
    return build_koan_footer()


def _get_existing_koan_branch(issue_url: str) -> Optional[str]:
    """Return the head branch if issue_url is a koan-owned PR, else None.

    Parses the URL to detect a PR (not an issue). If it's a PR whose head
    branch starts with this instance's branch_prefix, the human is asking
    koan to fix its own PR in-place — return the branch so the runner can
    skip creating a new branch and a new PR.
    """
    try:
        owner, repo, pr_number = parse_pr_url(issue_url)
    except ValueError:
        return None  # Not a PR URL — nothing to do

    from app.github_skill_helpers import is_own_pr
    try:
        is_owned, head_branch = is_own_pr(owner, repo, pr_number)
    except Exception:
        return None

    return head_branch if is_owned else None


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

    # Check if issue_url is a koan-owned PR — fix in-place on the existing branch.
    # When true, we skip branch creation and PR submission: the PR already exists.
    existing_branch = _get_existing_koan_branch(issue_url)
    if existing_branch:
        print(f"[fix] PR branch '{existing_branch}' is koan-owned — fixing in-place", flush=True)

    notify_fn(f"\U0001f527 Fixing {provider} issue {label}{context_label}...")

    print("[fix] Issue fetched, building prompt", flush=True)
    if not body and not comments:
        return False, f"Issue {label} has no content."

    # Build full issue body (include relevant comments)
    full_body = _build_issue_body(body, comments)

    # Parse --skip-diagnose from context
    skip_diagnose, context = _extract_skip_diagnose(context or "")

    # Run diagnostic step (lightweight, read-only)
    diagnostic_context = ""
    if not skip_diagnose:
        from skills.core.fix.fix_diagnose import (
            run_diagnostic,
            format_diagnostic_context,
        )
        print("[fix] Running pre-fix diagnostic...", flush=True)
        diagnostic = run_diagnostic(
            project_path=project_path,
            issue_url=issue_url,
            issue_title=title,
            issue_body=full_body,
            context=context or "",
            skill_dir=skill_dir,
        )
        diagnostic_context = format_diagnostic_context(diagnostic)
        confidence = diagnostic.get("confidence", "LOW")
        diag_error = diagnostic.get("error")
        print(f"[fix] Diagnostic confidence: {confidence}", flush=True)
        if diag_error and notify_fn:
            notify_fn(
                f"⚠️ Diagnostic step failed for {label}: {diag_error} — "
                f"fix will proceed without diagnostic context"
            )
        elif confidence == "LOW" and notify_fn:
            notify_fn(
                f"⚠️ Low-confidence diagnostic for {label} — "
                f"fix will proceed but may need human review"
            )
    else:
        print("[fix] Diagnostic skipped (--skip-diagnose)", flush=True)

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
            existing_branch=existing_branch,
            diagnostic=diagnostic_context,
        )
    except Exception as e:
        return False, f"Fix failed: {str(e)[:300]}"

    if not output:
        return False, "Claude returned empty output."

    # Post-fix: submit draft PR — skipped when fixing an existing koan PR in-place
    pr_url = None
    if owner and repo and not existing_branch:
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

    # In-place fix: the PR already exists, just report the branch.
    if existing_branch:
        notify_fn(
            f"✅ Fix applied to existing PR branch `{branch}`{context_label}"
        )
        return True, f"Fix applied to existing PR branch {branch}{context_label}"

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
    existing_branch: Optional[str] = None,
    diagnostic: str = "",
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
        existing_branch=existing_branch,
        diagnostic=diagnostic,
    )

    from app.claude_step import run_skill_loop
    from app.cli_provider import CLAUDE_TOOLS, run_command_streaming
    from app.config import get_skill_max_turns, get_skill_timeout

    def _step_fn(_evidence):
        return run_command_streaming(
            prompt, project_path,
            allowed_tools=sorted(CLAUDE_TOOLS),
            model_key="mission",
            max_turns=get_skill_max_turns(), timeout=get_skill_timeout(),
        )

    loop_outcome = run_skill_loop(
        step_fn=_step_fn,
        evidence_fn=lambda _a, _r: "",
        should_continue_fn=lambda _a, _r: (False, "done"),
        max_attempts=1,
    )

    attempts = loop_outcome.get("attempts", [])
    if attempts and attempts[0].get("error"):
        raise attempts[0]["error"]
    return attempts[0]["result"] if attempts else ""


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
    existing_branch: Optional[str] = None,
    diagnostic: str = "",
) -> str:
    """Build the fix prompt from the issue content."""
    branch_section = _build_branch_section(
        branch_prefix=branch_prefix,
        issue_number=issue_number,
        base_branch=base_branch,
        existing_branch=existing_branch,
    )
    template_vars = dict(
        ISSUE_URL=issue_url,
        ISSUE_TITLE=issue_title,
        ISSUE_BODY=issue_body,
        CONTEXT=context,
        BRANCH_PREFIX=branch_prefix,
        ISSUE_NUMBER=issue_number,
        PROJECT_MEMORY=project_memory,
        BASE_BRANCH=base_branch,
        BRANCH_SECTION=branch_section,
        DIAGNOSTIC=diagnostic,
    )

    return load_prompt_or_skill(skill_dir, "fix", **template_vars)


def _build_branch_section(
    branch_prefix: str,
    issue_number: str,
    base_branch: str,
    existing_branch: Optional[str] = None,
) -> str:
    """Build the branch-setup section for the fix prompt.

    For a fresh issue fix: instruct Claude to create a new branch.
    For an in-place PR fix: instruct Claude to check out the existing branch
    and skip PR creation (the PR already exists).
    """
    if existing_branch:
        return (
            f"You are applying a fix to an **existing PR on branch "
            f"`{existing_branch}`**. A PR already exists — do not create a new one.\n\n"
            f"**Branch setup**: Check out `{existing_branch}` before making any changes:\n"
            f"```bash\n"
            f"git fetch origin {existing_branch}\n"
            f"git checkout {existing_branch}\n"
            f"```\n\n"
            f"After implementing the fix, push to the existing branch:\n"
            f"```bash\n"
            f"git push origin {existing_branch}\n"
            f"```\n\n"
            f"**Skip Phase 7** (Submit Pull Request) — the PR already exists for "
            f"this branch. The fix is complete once you push."
        )

    new_branch = f"{branch_prefix}fix-issue-{issue_number}"
    return (
        f"Branch naming: `{new_branch}`\n\n"
        f"**Mandatory before any commit**: the repository's base branch for this "
        f"project is `{base_branch}`. If you are currently on `{base_branch}`, on "
        f"`main`, or on `master`, create and switch to the branch named above before "
        f"making any changes. **Never commit on `{base_branch}`, `main`, or `master` "
        f"directly** — that leaves the work on a base branch where no PR can be opened "
        f"and is treated as a failed mission. If you are already on a feature branch "
        f"(anything other than `{base_branch}`, `main`, or `master`), stay on it."
    )


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
        f"---\n{_build_footer()}"
    )

    try:
        from app.describe_pr import describe_pr, format_description
        desc = describe_pr(project_path, effective_base)
        if desc:
            pr_body = (
                f"{format_description(desc)}\n\n"
                f"Fixes {issue_url}\n\n"
                f"---\n{_build_footer()}"
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
