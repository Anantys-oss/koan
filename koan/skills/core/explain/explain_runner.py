"""Koan -- PR explanation runner.

Fetches PR metadata and diff, builds a pedagogical explanation prompt,
and invokes Claude CLI to produce a plain-language walkthrough of the
changes.  Posts the explanation as a PR comment for the requester.

Usage:
    python3 -m skills.core.explain.explain_runner <pr-url> --project-path /path
"""

import contextlib
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from app.cli_errors import ErrorCategory, classify_cli_error
from app.prompts import load_prompt_or_skill
from app.run_log import log_safe as log

_EXPLAIN_TAG = "<!-- koan:explain -->"

_MAX_RETRIES = 2
_RETRY_DELAY = 10


def _is_transient_error(error: str) -> bool:
    """Check if error suggests a transient failure worth retrying."""
    return classify_cli_error(1, stderr=error) == ErrorCategory.RETRYABLE


def _build_explain_prompt(
    context: dict,
    skill_dir: Optional[Path] = None,
    project_path: Optional[str] = None,
) -> str:
    """Build the explanation prompt from PR context."""
    project_memory = ""
    if project_path:
        from app.skill_memory import build_memory_block_for_skill

        diff = context.get("diff", "") or ""
        task_text = "\n".join(filter(None, (
            context.get("title", ""),
            context.get("body", ""),
            diff[:2000],
        )))
        project_memory = build_memory_block_for_skill(project_path, task_text)

    from app.prompt_guard import fence_external_data

    kwargs = dict(
        TITLE=fence_external_data(context["title"], "PR title"),
        AUTHOR=context["author"],
        BRANCH=context["branch"],
        BASE=context["base"],
        BODY=fence_external_data(context.get("body", ""), "PR body"),
        DIFF=fence_external_data(context.get("diff", ""), "PR diff", scan=False),
        REVIEW_COMMENTS=fence_external_data(
            context.get("review_comments", ""), "review comments"
        ),
        REVIEWS=fence_external_data(
            context.get("reviews", ""), "reviews"
        ),
        ISSUE_COMMENTS=fence_external_data(
            context.get("issue_comments", ""), "issue comments"
        ),
        PROJECT_MEMORY=project_memory,
    )

    return load_prompt_or_skill(skill_dir, "explain", **kwargs)


def _run_claude_explain(
    prompt: str,
    project_path: str,
    timeout: int = 600,
    model: Optional[str] = None,
) -> Tuple[str, str]:
    """Run Claude CLI with read-only tools and return the explanation.

    Returns (output, error) tuple.
    """
    from app.cli_provider import run_command_streaming
    from app.config import get_model_config, get_skill_max_turns

    if model is None:
        models = get_model_config()
        model = models.get("review_mode") or models.get("mission") or None

    cmd_kwargs = dict(
        prompt=prompt,
        project_path=project_path,
        allowed_tools=["Read", "Glob", "Grep"],
        model_key="mission",
        max_turns=get_skill_max_turns(),
        timeout=timeout,
    )
    if model:
        cmd_kwargs["model"] = model

    try:
        output = run_command_streaming(**cmd_kwargs)
        return output, ""
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        error = str(e) or "unknown error"
        log("explain", f"Claude explain failed: {error}")
        return "", error


def _run_claude_explain_with_retry(
    prompt: str,
    project_path: str,
    timeout: int = 600,
    model: Optional[str] = None,
) -> Tuple[str, str]:
    """Run Claude explain with retry on transient failures.

    Retries up to _MAX_RETRIES times when the error looks transient
    (rate limits, timeouts, connection issues).
    """
    output, error = _run_claude_explain(prompt, project_path, timeout, model)
    if not error:
        return output, ""

    for attempt in range(_MAX_RETRIES):
        if not _is_transient_error(error):
            log("explain", f"Non-transient error, not retrying: {error[:200]}")
            break
        log("explain", f"Transient error, retrying ({attempt + 1}/{_MAX_RETRIES}): {error[:100]}")
        time.sleep(_RETRY_DELAY)
        output, error = _run_claude_explain(prompt, project_path, timeout, model)
        if not error:
            return output, ""

    return output, error


def _post_explanation_comment(
    owner: str,
    repo: str,
    pr_number: str,
    explanation: str,
) -> Tuple[bool, str]:
    """Post explanation as a PR comment.  Returns (success, error)."""
    from app.github import run_gh, find_bot_comment, sanitize_github_comment

    full_repo = f"{owner}/{repo}"
    clean_text = sanitize_github_comment(explanation) or ""
    if not clean_text.strip():
        return False, "Explanation empty after sanitization"
    body = f"{_EXPLAIN_TAG}\n## 💡 PR Explanation\n\n{clean_text}"

    existing = find_bot_comment(owner, repo, int(pr_number), _EXPLAIN_TAG)
    if existing:
        comment_id = existing["id"]
        try:
            run_gh(
                "api", f"repos/{full_repo}/issues/comments/{comment_id}",
                "--method", "PATCH", "--field", f"body={body}",
            )
            return True, ""
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            log("explain", f"PATCH failed ({e}), posting new comment")

    try:
        run_gh(
            "pr", "comment", pr_number, "--repo", full_repo,
            "--body", body,
        )
        return True, ""
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def run_explain(
    owner: str,
    repo: str,
    pr_number: str,
    project_path: str,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
    project_name: Optional[str] = None,
) -> Tuple[bool, str]:
    """Explain a PR in plain language.

    Returns (success, explanation_text) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    from app.claude_step import resolve_pr_location
    from app.rebase_pr import fetch_pr_context

    try:
        owner, repo = resolve_pr_location(owner, repo, pr_number, project_path)
    except RuntimeError as e:
        return False, str(e)

    full_repo = f"{owner}/{repo}"
    with contextlib.suppress(Exception):
        notify_fn(f"Explaining PR #{pr_number} ({full_repo})...")

    try:
        context = fetch_pr_context(owner, repo, pr_number, project_path)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
        return False, f"Failed to fetch PR context: {e}"

    diff = context.get("diff", "")
    if not diff:
        return False, f"PR #{pr_number} has no diff — nothing to explain."

    log("explain", f"PR #{pr_number}: {context.get('title', '?')}")

    try:
        prompt = _build_explain_prompt(
            context,
            skill_dir=skill_dir,
            project_path=project_path,
        )
    except Exception as e:
        return False, f"Failed to build explanation prompt: {e}"

    output, error = _run_claude_explain_with_retry(prompt, project_path)
    if error:
        return False, f"Explanation failed: {error}"
    if not output.strip():
        return False, "Claude returned empty output for explanation."

    try:
        posted, post_error = _post_explanation_comment(
            owner, repo, pr_number, output,
        )
    except Exception as e:
        posted, post_error = False, str(e)
    if not posted:
        log("explain", f"Comment post failed: {post_error}")

    post_status = "" if posted else " (comment post failed)"
    summary = (
        f"Explained PR #{pr_number} ({full_repo}): "
        f"{context.get('title', '')}{post_status}\n\n{output}"
    )
    return True, summary


def main(argv=None):
    """CLI entry point for explain_runner."""
    import argparse

    from app.github_url_parser import parse_pr_url

    parser = argparse.ArgumentParser(
        description="Explain a GitHub PR in plain language."
    )
    parser.add_argument("url", help="GitHub PR URL")
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--project-name",
        help="Project name for injecting project-specific memory.",
    )
    cli_args = parser.parse_args(argv)

    try:
        owner, repo, pr_number = parse_pr_url(cli_args.url)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    skill_dir = Path(__file__).resolve().parent

    try:
        success, summary = run_explain(
            owner, repo, pr_number, cli_args.project_path,
            skill_dir=skill_dir,
            project_name=cli_args.project_name,
        )
    except Exception as e:
        print(f"Explanation failed: {e}")
        return 1
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
