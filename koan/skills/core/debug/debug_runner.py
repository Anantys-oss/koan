"""
Koan -- Debug runner.

Gathers failure context from journals and the original issue, then invokes
Claude with the structured 4-step debug prompt (reproduce, hypothesize,
minimal fix, verify).

CLI:
    python3 -m skills.core.debug.debug_runner --project-path <path> --issue-url <url>
    python3 -m skills.core.debug.debug_runner --project-path <path> --issue-url <url> --context "backend only"
"""

import logging
import os
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

_MAX_FAILURE_CONTEXT_CHARS = 4000


def _gather_failure_context(instance_dir: str, project_name: str) -> str:
    """Read recent journal entries for failure context."""
    from datetime import date

    journal_dir = os.path.join(instance_dir, "journal")
    if not os.path.isdir(journal_dir):
        return "No journal directory found — no prior failure context available."

    today = date.today().isoformat()
    journal_file = os.path.join(journal_dir, today, f"{project_name}.md")
    if not os.path.isfile(journal_file):
        dated_dirs = sorted(
            (d for d in os.listdir(journal_dir)
             if os.path.isdir(os.path.join(journal_dir, d))),
            reverse=True,
        )
        journal_file = None
        for d in dated_dirs[:3]:
            candidate = os.path.join(journal_dir, d, f"{project_name}.md")
            if os.path.isfile(candidate):
                journal_file = candidate
                break

    if not journal_file:
        return "No recent journal entries found for this project."

    try:
        text = Path(journal_file).read_text()
        if len(text) > _MAX_FAILURE_CONTEXT_CHARS:
            text = text[-_MAX_FAILURE_CONTEXT_CHARS:]
        return text
    except OSError:
        return "Could not read journal file."


def _build_issue_body(body: str, comments: List[dict]) -> str:
    """Build full issue content including relevant comments."""
    parts = [body.strip()] if body else []

    for comment in comments:
        comment_body = comment.get("body", "").strip()
        author = comment.get("author", "")

        if "[bot]" in author or len(comment_body) < 20:
            continue

        parts.append(f"\n---\n**Comment by {author}**:\n{comment_body}")

    return "\n".join(parts)


def run_debug(
    project_path: str,
    issue_url: str,
    context: Optional[str] = None,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
    base_branch: Optional[str] = None,
    project_name: str = "",
    instance_dir: str = "",
) -> Tuple[bool, str]:
    """Execute the structured debug pipeline."""
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    print("[debug] Starting debug runner", flush=True)
    context_label = f" ({context})" if context else ""
    project_name = project_name or project_name_for_path(project_path)
    print(f"[debug] Fetching tracker issue {issue_url}", flush=True)

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

    owner = repo = None
    repo_slug = ref.repo or resolve_code_repository(project_name, project_path)
    if repo_slug and "/" in repo_slug:
        owner, repo = repo_slug.split("/", 1)

    notify_fn(f"🐛 Debugging {provider} issue {label}{context_label}...")

    print("[debug] Issue fetched, building prompt", flush=True)
    if not body and not comments:
        return False, f"Issue {label} has no content."

    full_body = _build_issue_body(body, comments)

    from app.projects_config import resolve_base_branch
    effective_base_branch = base_branch or resolve_base_branch(
        project_name, project_path,
    )

    print("[debug] Invoking Claude for structured debug", flush=True)
    try:
        output = _execute_debug(
            project_path=project_path,
            issue_url=issue_url,
            issue_title=title,
            issue_body=full_body,
            context=context or "Debug the issue using the structured loop.",
            skill_dir=skill_dir,
            issue_number=str(issue_number),
            project_name=project_name,
            instance_dir=instance_dir,
            base_branch=effective_base_branch,
        )
    except Exception as e:
        return False, f"Debug failed: {str(e)[:300]}"

    if not output:
        return False, "Claude returned empty output."

    pr_url = None
    if owner and repo:
        pr_url = _submit_debug_pr(
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

    branch = get_current_branch(project_path)
    on_base_branch = branch in (effective_base_branch, "main", "master")

    if pr_url:
        notify_fn(
            f"✅ Debug complete for issue {label}"
            f"{context_label}\nDraft PR: {pr_url}"
        )
        summary = f"Debug complete for {label}{context_label}\nDraft PR: {pr_url}"
    elif not on_base_branch:
        notify_fn(
            f"✅ Debug complete for issue {label}"
            f"{context_label}\nBranch: {branch}"
        )
        summary = f"Debug complete for {label}{context_label}\nBranch: {branch}"
    else:
        notify_fn(
            f"⚠️ Debug complete for issue {label}"
            f"{context_label} — changes landed on base branch `{branch}`"
        )
        summary = f"Debug complete for {label}{context_label} (on base branch {branch}, no PR)"

    return True, summary


def _execute_debug(
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
    """Execute the debug via Claude CLI."""
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

    failure_context = _gather_failure_context(instance_dir, project_name) if instance_dir else ""
    if not failure_context:
        failure_context = "No prior failure context available."

    branch_section = (
        f"Create a branch named `{branch_prefix}/debug-{issue_number}` "
        f"from `{effective_base}`."
    )

    template_vars = dict(
        ISSUE_URL=issue_url,
        ISSUE_TITLE=issue_title,
        ISSUE_BODY=issue_body,
        FAILURE_CONTEXT=failure_context,
        CONTEXT=context,
        PROJECT_MEMORY=project_memory,
        BRANCH_SECTION=branch_section,
        BASE_BRANCH=effective_base,
    )

    prompt = load_prompt_or_skill(skill_dir, "debug", **template_vars)

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


def _submit_debug_pr(
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
    """Submit a draft PR for the debug fix."""
    from app.pr_submit import build_koan_footer

    branch = get_current_branch(project_path)
    if not branch or branch in ("main", "master"):
        return None

    footer = build_koan_footer()
    pr_body = (
        f"## Debug fix for {issue_url}\n\n"
        f"Structured hypothesis-driven fix for: **{issue_title}**\n\n"
        f"Closes {issue_url}\n\n"
        f"---\n{footer}"
    )

    try:
        return submit_draft_pr(
            project_path=project_path,
            owner=owner,
            repo=repo,
            title=f"fix: debug {issue_title[:60]}",
            body=pr_body,
            base_branch=base_branch,
            project_name=project_name,
            notify_fn=notify_fn,
        )
    except Exception as e:
        logger.warning("PR submission failed: %s", e)
        if notify_fn:
            notify_fn(f"⚠️ Debug fix committed but PR creation failed: {e}")
        return None


def main(argv=None):
    """CLI entry point for debug_runner."""
    import argparse
    from app.url_skill_args import add_url_skill_common_args

    parser = argparse.ArgumentParser(
        description="Debug a failed issue with structured hypothesis loop."
    )
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--issue-url", required=True,
        help="GitHub or Jira issue URL to debug",
    )
    add_url_skill_common_args(parser)
    cli_args = parser.parse_args(argv)

    skill_dir = Path(__file__).resolve().parent

    success, summary = run_debug(
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
