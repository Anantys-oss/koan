"""GitHub AI-powered reply handler.

When an authorized admin user @mentions the bot with a question or request
(not a recognized command), this module generates a contextual reply using
Claude and posts it as a comment.

Flow:
1. Extract freeform text from the @mention comment
2. Fetch issue/PR context (title, body, recent comments)
3. Build prompt with context + question
4. Call Claude CLI to generate a concise reply
5. Post reply as a GitHub comment
"""

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from app.cli_provider import run_command
from app.github import api, sanitize_github_comment
from app.prompts import load_prompt
from app.utils import truncate_text

log = logging.getLogger(__name__)

# Throttle for the "thread reply budget exceeded" Telegram heads-up: at most
# one warning per thread per window so a tripped breaker doesn't itself spam.
_budget_warn_lock = threading.Lock()
_last_budget_warning: dict = {}
_BUDGET_WARN_THROTTLE_SECONDS = 3600


def _warn_reply_budget_once(owner: str, repo: str, issue_number: str, cap: int) -> None:
    """Send a single Telegram heads-up when a thread's reply budget trips."""
    thread_key = f"{owner}/{repo}#{issue_number}"
    now = time.monotonic()
    with _budget_warn_lock:
        last = _last_budget_warning.get(thread_key, 0.0)
        if now - last < _BUDGET_WARN_THROTTLE_SECONDS:
            return
        _last_budget_warning[thread_key] = now
    try:
        from app.notify import NotificationPriority, send_telegram

        send_telegram(
            f"🛑 Reply circuit breaker tripped on {thread_key}: reached "
            f"{cap} bot replies within the hour — further replies suppressed.",
            priority=NotificationPriority.ACTION,
        )
    except Exception as e:  # noqa: BLE001 — best-effort notification
        log.warning("Failed to send reply-budget warning for %s: %s", thread_key, e)


def _enforce_reply_budget(owner: str, repo: str, issue_number: str) -> bool:
    """Per-thread circuit breaker. Return True if a reply may be posted.

    Records the reply when allowed. When the rolling-hour cap is reached,
    suppresses the post (logs + warns the operator once) and returns False.
    Fails open: if state cannot be read (no KOAN_ROOT, file error), the reply
    is allowed.
    """
    koan_root = os.environ.get("KOAN_ROOT", "")
    if not koan_root:
        return True
    try:
        from app.github_config import get_github_max_replies_per_thread
        from app.utils import load_config

        cap = get_github_max_replies_per_thread(load_config())
    except Exception as e:  # noqa: BLE001 — config failure must not block replies
        log.warning("Reply budget: config load failed, allowing reply: %s", e)
        return True
    if cap <= 0:
        return True  # breaker disabled

    instance_dir = str(Path(koan_root) / "instance")
    try:
        from app.github_notification_tracker import try_consume_reply_budget

        # Atomic check-and-record: the count check and the slot record happen
        # inside one lock, so concurrent callers can't both pass and overshoot
        # the cap (no check-then-act race).
        if not try_consume_reply_budget(
            instance_dir, owner, repo, str(issue_number), cap,
        ):
            log.warning(
                "GitHub: reply circuit breaker tripped for %s/%s#%s "
                "(cap=%d/hour) — suppressing reply",
                owner, repo, issue_number, cap,
            )
            _warn_reply_budget_once(owner, repo, str(issue_number), cap)
            return False
    except Exception as e:  # noqa: BLE001 — tracker failure must not block replies
        log.warning("Reply budget: tracker access failed, allowing reply: %s", e)
        return True
    return True

# Regex for stripping code blocks before mention extraction
_CODE_BLOCK_RE = re.compile(r'```.*?```|`[^`]+`', re.DOTALL)

# Maximum context chars to prevent prompt overflow
_MAX_CONTEXT_CHARS = 8000
_MAX_COMMENTS = 10


def extract_mention_text(comment_body: str, nickname: str) -> Optional[str]:
    """Extract freeform text after an @mention.

    Unlike parse_mention_command which expects a command word, this extracts
    everything after @nickname as a single text block.

    Args:
        comment_body: The full comment text.
        nickname: The bot's GitHub username (without @).

    Returns:
        The text after @nickname, or None if no mention found.
    """
    if not comment_body or not nickname:
        return None

    # Remove code blocks to avoid matching mentions in code
    clean_body = _CODE_BLOCK_RE.sub('', comment_body)

    # Match @nickname followed by any text (greedy, multiline)
    pattern = rf'@{re.escape(nickname)}\s+(.*?)$'
    match = re.search(pattern, clean_body, re.IGNORECASE | re.DOTALL)
    if not match:
        return None

    text = match.group(1).strip()
    return text if text else None


def fetch_thread_context(
    owner: str,
    repo: str,
    issue_number: str,
    bot_username: str = "",
) -> dict:
    """Fetch issue/PR context for reply generation.

    Args:
        owner: Repository owner.
        repo: Repository name.
        issue_number: Issue/PR number.
        bot_username: If provided, comments from this user are excluded
            from the context to prevent self-reply loops.

    Returns:
        Dict with keys: title, body, comments, is_pr, diff_summary.
        Empty/default values on API errors.
    """
    context = {
        "title": "",
        "body": "",
        "comments": [],
        "is_pr": False,
        "diff_summary": "",
    }

    # Fetch issue/PR metadata
    try:
        raw = api(
            f"repos/{owner}/{repo}/issues/{issue_number}",
            jq='{"title": .title, "body": .body, "pull_request": .pull_request}',
        )
        data = json.loads(raw) if raw else {}
        context["title"] = data.get("title", "")
        context["body"] = truncate_text(data.get("body", "") or "", _MAX_CONTEXT_CHARS)
        context["is_pr"] = data.get("pull_request") is not None
    except (RuntimeError, json.JSONDecodeError):
        pass

    # Fetch recent comments
    try:
        raw = api(
            f"repos/{owner}/{repo}/issues/{issue_number}/comments",
            jq=f'[.[-{_MAX_COMMENTS}:] | .[] | {{author: .user.login, body: .body}}]',
        )
        comments = json.loads(raw) if raw else []
        if isinstance(comments, list):
            bot_lower = bot_username.lower() if bot_username else ""
            context["comments"] = [
                {"author": c.get("author", "?"), "body": truncate_text(c.get("body", ""), 500)}
                for c in comments
                if not (bot_lower and c.get("author", "").lower() == bot_lower)
            ]
    except (RuntimeError, json.JSONDecodeError):
        pass

    # For PRs, fetch a diff summary (file list only, not full diff)
    if context["is_pr"]:
        try:
            raw = api(
                f"repos/{owner}/{repo}/pulls/{issue_number}/files",
                jq='[.[] | {filename: .filename, status: .status, additions: .additions, deletions: .deletions}]',
            )
            files = json.loads(raw) if raw else []
            if isinstance(files, list):
                lines = [
                    f"  {f.get('status', '?')} {f.get('filename', '?')} "
                    f"(+{f.get('additions', 0)}/-{f.get('deletions', 0)})"
                    for f in files[:30]
                ]
                context["diff_summary"] = "\n".join(lines)
        except (RuntimeError, json.JSONDecodeError):
            pass

    return context


def build_reply_prompt(
    question: str,
    thread_context: dict,
    owner: str,
    repo: str,
    issue_number: str,
    comment_author: str,
) -> str:
    """Build the prompt for Claude to generate a reply.

    Args:
        question: The user's question/request text.
        thread_context: Dict from fetch_thread_context().
        owner: Repository owner.
        repo: Repository name.
        issue_number: Issue/PR number.
        comment_author: GitHub username of the person asking.

    Returns:
        The complete prompt string.
    """
    kind = "pull request" if thread_context.get("is_pr") else "issue"
    title = thread_context.get("title", "")
    body = thread_context.get("body", "")
    comments = thread_context.get("comments", [])
    diff_summary = thread_context.get("diff_summary", "")

    # Format comments for context
    comments_text = ""
    if comments:
        comment_lines = [f"@{c['author']}: {c['body']}" for c in comments]
        comments_text = "\n\n".join(comment_lines)

    return load_prompt(
        "github-reply",
        REPO=f"{owner}/{repo}",
        ISSUE_NUMBER=issue_number,
        KIND=kind,
        TITLE=title,
        BODY=body,
        COMMENTS=comments_text,
        DIFF_SUMMARY=diff_summary,
        QUESTION=question,
        AUTHOR=comment_author,
    )


def generate_reply(
    question: str,
    thread_context: dict,
    owner: str,
    repo: str,
    issue_number: str,
    comment_author: str,
    project_path: str,
) -> Optional[str]:
    """Generate an AI reply using Claude CLI.

    Args:
        question: The user's question.
        thread_context: Context from fetch_thread_context().
        owner: Repository owner.
        repo: Repository name.
        issue_number: Issue/PR number.
        comment_author: Who asked the question.
        project_path: Local path to the project (for CLI cwd).

    Returns:
        The reply text, or None on failure.
    """
    prompt = build_reply_prompt(
        question, thread_context, owner, repo, issue_number, comment_author,
    )

    try:
        reply = run_command(
            prompt=prompt,
            project_path=project_path,
            allowed_tools=["Read", "Glob", "Grep"],
            model_key="chat",
            max_turns=5,
            timeout=300,
            max_turns_source=None,
        )
        return clean_reply(reply) if reply else None
    except Exception as e:
        log.warning("GitHub reply generation failed: %s", e)
        return None


def post_reply(
    owner: str,
    repo: str,
    issue_number: str,
    body: str,
) -> bool:
    """Post a comment reply to a GitHub issue or PR.

    Args:
        owner: Repository owner.
        repo: Repository name.
        issue_number: Issue/PR number.
        body: Comment body (markdown).

    Returns:
        True if posted successfully.
    """
    if not _enforce_reply_budget(owner, repo, issue_number):
        return False
    try:
        safe_body = sanitize_github_comment(body)
        api(
            f"repos/{owner}/{repo}/issues/{issue_number}/comments",
            method="POST",
            extra_args=["-f", f"body={safe_body}"],
        )
        return True
    except RuntimeError as e:
        log.warning("Failed to post GitHub reply: %s", e)
        return False


def post_threaded_reply(
    owner: str,
    repo: str,
    issue_number: str,
    body: str,
    comment_api_url: str = "",
    comment_id: str = "",
    comment_author: str = "",
    comment_body: str = "",
) -> bool:
    """Post a reply threaded to the original comment when possible.

    For PR review comments (pulls/comments/NNN): uses ``in_reply_to``
    to create a native GitHub review-comment thread.
    For issue/PR comments: posts a new comment with a blockquote of
    the original to provide visual threading.

    Falls back to a plain ``post_reply`` when threading metadata is
    unavailable.
    """
    if not _enforce_reply_budget(owner, repo, issue_number):
        return False
    safe_body = sanitize_github_comment(body)

    # PR review comments support native threading via in_reply_to
    if comment_api_url and "/pulls/comments/" in comment_api_url and comment_id:
        try:
            api(
                f"repos/{owner}/{repo}/pulls/{issue_number}/comments",
                method="POST",
                extra_args=[
                    "-f", f"body={safe_body}",
                    "-F", f"in_reply_to={comment_id}",
                ],
            )
            return True
        except RuntimeError as e:
            log.debug("Threaded PR review reply failed, falling back: %s", e)

    # Issue/PR comments: prefix with a blockquote for visual context
    if comment_author and comment_body:
        quote_line = comment_body.split("\n")[0]
        if len(quote_line) > 120:
            quote_line = quote_line[:120] + "..."
        threaded_body = f"> @{comment_author}: {quote_line}\n\n{body}"
        safe_body = sanitize_github_comment(threaded_body)

    try:
        api(
            f"repos/{owner}/{repo}/issues/{issue_number}/comments",
            method="POST",
            extra_args=["-f", f"body={safe_body}"],
        )
        return True
    except RuntimeError as e:
        log.warning("Failed to post threaded reply: %s", e)
        return False


def clean_reply(text: str) -> str:
    """Clean Claude CLI output artifacts from the reply."""
    lines = text.strip().splitlines()
    # Remove CLI noise lines
    lines = [l for l in lines if not re.match(r"^Error:.*max turns", l, re.IGNORECASE)]
    return "\n".join(lines).strip()
