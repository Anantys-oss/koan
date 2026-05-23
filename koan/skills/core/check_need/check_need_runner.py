"""Runner for /check_need skill — analyzes PR/issue relevance and posts a GitHub comment.

When triggered via the agent loop or GitHub @mention, this runner:
1. Fetches PR/issue context from GitHub (title, body, diff, comments)
2. Sends it to Claude with the check_need prompt for analysis
3. Posts the analysis as a GitHub comment
"""

import argparse
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger(__name__)

_GITHUB_URL_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)/"
    r"(?P<type>pull|issues)/(?P<number>\d+)"
)


def run_check_need(
    url: str,
    project_path: str,
    project_name: str,
    instance_dir: str,
) -> Tuple[bool, str]:
    """Execute the /check_need flow.

    Args:
        url: GitHub PR or issue URL.
        project_path: Local path to the project repository.
        project_name: Name of the project.
        instance_dir: Path to the instance directory.

    Returns:
        (success, summary) tuple.
    """
    from app import github_reply
    from app.cli_provider import run_command
    from app.prompts import load_skill_prompt

    parsed = _parse_url(url)
    if not parsed:
        return False, f"Could not parse GitHub URL: {url}"

    owner, repo, url_type, number = parsed
    is_pr = url_type == "pull"
    kind = "pull request" if is_pr else "issue"

    print(f"\u2192 Fetching {kind} #{number} context from {owner}/{repo}")

    # Fetch thread context
    bot_username = _resolve_bot_username()
    thread_context = github_reply.fetch_thread_context(
        owner, repo, number, bot_username=bot_username,
    )

    title = thread_context.get("title", "")
    body = thread_context.get("body", "") or ""
    diff_summary = thread_context.get("diff_summary", "")
    comments = thread_context.get("comments", [])

    # For PRs, fetch the full diff for deeper analysis
    full_diff = ""
    if is_pr:
        full_diff = _fetch_pr_diff(owner, repo, number)

    comments_text = ""
    if comments:
        comments_text = "\n\n".join(
            f"@{c['author']}: {c['body']}" for c in comments
        )

    # Build prompt
    skill_dir = Path(__file__).parent
    prompt = load_skill_prompt(
        skill_dir,
        "check_need",
        REPO=f"{owner}/{repo}",
        NUMBER=number,
        KIND=kind,
        TITLE=title,
        BODY=body,
        DIFF_SUMMARY=diff_summary,
        FULL_DIFF=full_diff,
        COMMENTS=comments_text,
    )

    # Run Claude analysis
    print(f"\u2192 Analyzing relevance of {kind} #{number}...")
    try:
        raw = run_command(
            prompt=prompt,
            project_path=project_path,
            allowed_tools=["Read", "Glob", "Grep", "Bash"],
            model_key="default",
            max_turns=15,
            timeout=600,
            max_turns_source=None,
        )
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        log.warning("check_need: analysis failed: %s", e)
        return False, f"Analysis failed: {e}"

    if not raw:
        return False, "Analysis returned empty output."

    reply_text = github_reply.clean_reply(raw)
    if not reply_text:
        return False, "Analysis produced no usable output."

    # Post comment to GitHub
    print(f"\u2192 Posting analysis to {owner}/{repo}#{number}")
    if not github_reply.post_reply(owner, repo, number, reply_text):
        return False, "Failed to post comment to GitHub."

    issue_url = f"https://github.com/{owner}/{repo}/{url_type}/{number}"
    summary = (
        f"Relevance analysis posted to {owner}/{repo}#{number}\n"
        f"Title: {title}\n"
        f"Reply preview: {reply_text[:200]}...\n"
        f"{issue_url}"
    )
    return True, summary


def _parse_url(url: str) -> Optional[Tuple[str, str, str, str]]:
    """Parse owner, repo, type, number from GitHub URL."""
    match = _GITHUB_URL_RE.search(url)
    if not match:
        return None
    return (
        match.group(1),
        match.group(2),
        match.group("type"),
        match.group("number"),
    )


def _resolve_bot_username() -> str:
    """Read the bot's GitHub nickname from config.yaml."""
    try:
        from app.utils import load_config
        config = load_config()
        github = config.get("github") or {}
        return str(github.get("nickname", "")).strip()
    except Exception as e:
        print(f"[check_need_runner] could not resolve bot username: {e}",
              file=sys.stderr)
    return ""


def _fetch_pr_diff(owner: str, repo: str, number: str) -> str:
    """Fetch the PR diff, truncated to avoid prompt overflow."""
    from app.github import api
    from app.utils import truncate_text

    try:
        raw = api(
            f"repos/{owner}/{repo}/pulls/{number}",
            extra_args=["-H", "Accept: application/vnd.github.v3.diff"],
        )
        if raw:
            return truncate_text(raw, 12000)
    except RuntimeError:
        pass
    return ""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run /check_need skill")
    parser.add_argument("--project-path", required=True, help="Path to the project")
    parser.add_argument("--project-name", required=True, help="Project name")
    parser.add_argument("--instance-dir", required=True, help="Path to instance dir")
    parser.add_argument(
        "--context-file",
        help="File containing the GitHub URL",
    )
    args = parser.parse_args(argv)

    # Read URL from context file
    url = ""
    if args.context_file:
        try:
            url = Path(args.context_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"Error reading context file: {e}", file=sys.stderr)
            sys.exit(1)

    if not url:
        print("No GitHub URL provided. Use --context-file.", file=sys.stderr)
        sys.exit(1)

    success, summary = run_check_need(
        url=url,
        project_path=args.project_path,
        project_name=args.project_name,
        instance_dir=args.instance_dir,
    )

    print(summary)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
