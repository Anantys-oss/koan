"""Jira notification fetching and parsing.

Handles polling Jira for @mention comments, parsing commands, and
tracking processed comments to avoid duplicate mission creation.

Authentication uses Atlassian Basic auth (email + API token).
Jira Cloud comment bodies are ADF (Atlassian Document Format) JSON —
this module extracts plain text from ADF before regex matching.
"""

import json
import logging
import os
import re
import time
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.bounded_set import BoundedSet

log = logging.getLogger(__name__)

# In-memory set of processed Jira comment IDs (resets on restart).
_MAX_PROCESSED_COMMENTS = 10000
_processed_comments: BoundedSet = BoundedSet(maxlen=_MAX_PROCESSED_COMMENTS)

# Regex for stripping code blocks before @mention search (same as GitHub module)
_CODE_BLOCK_RE = re.compile(r'\{\{.*?\}\}|{{noformat.*?noformat}}|\{code.*?\{code\}', re.DOTALL)


class JiraFetchResult:
    """Result from fetch_jira_mentions."""

    __slots__ = ("mentions",)

    def __init__(self, mentions: List[dict]):
        self.mentions = mentions


def _make_auth_header(email: str, api_token: str) -> str:
    """Build Basic auth header value for Atlassian API."""
    creds = f"{email}:{api_token}"
    encoded = b64encode(creds.encode()).decode()
    return f"Basic {encoded}"


def _jira_get(base_url: str, auth_header: str, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """Make a GET request to the Jira REST API.

    Args:
        base_url: Jira instance base URL (e.g. https://myorg.atlassian.net).
        auth_header: Basic auth header value.
        path: API path (e.g. /rest/api/3/issue/{key}/comment).
        params: Optional query parameters.

    Returns:
        Parsed JSON dict/list, or None on error.
    """
    try:
        import urllib.request
        import urllib.parse

        url = base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(url)
        req.add_header("Authorization", auth_header)
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except Exception as e:
        log.warning("Jira API GET %s failed: %s", path, e)
        return None


def _jira_post(base_url: str, auth_header: str, path: str, body: Dict[str, Any]) -> Optional[dict]:
    """Make a POST request to the Jira REST API.

    Args:
        base_url: Jira instance base URL (e.g. https://myorg.atlassian.net).
        auth_header: Basic auth header value.
        path: API path (e.g. /rest/api/3/search/jql).
        body: JSON request body.

    Returns:
        Parsed JSON dict/list, or None on error.
    """
    try:
        import urllib.request

        url = base_url + path
        data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", auth_header)
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except Exception as e:
        log.warning("Jira API POST %s failed: %s", path, e)
        return None


def _adf_to_text(node: Any) -> str:
    """Recursively extract plain text from an Atlassian Document Format (ADF) node.

    ADF is a JSON tree format used by Jira Cloud comment bodies.
    This extracts text nodes while ignoring formatting and code blocks.

    Args:
        node: An ADF node (dict) or list of nodes.

    Returns:
        Plain text string.
    """
    if not node:
        return ""

    if isinstance(node, list):
        return " ".join(_adf_to_text(item) for item in node)

    if not isinstance(node, dict):
        return str(node)

    node_type = node.get("type", "")

    # Skip code blocks — don't want to match @mentions inside code
    if node_type in ("codeBlock", "code", "inlineCard"):
        return ""

    # Text nodes carry the actual content
    if node_type == "text":
        return node.get("text", "")

    # Mention nodes (Jira @mentions different from text @mentions)
    if node_type == "mention":
        attrs = node.get("attrs", {})
        text = attrs.get("text", "")
        return text

    # Hard break → space
    if node_type in ("hardBreak", "rule"):
        return " "

    # Recurse into content children
    children = node.get("content", [])
    parts = []
    for child in children:
        text = _adf_to_text(child)
        if text:
            parts.append(text)
    return " ".join(parts)


def _extract_comment_text(comment_body: Any) -> str:
    """Extract plain text from a Jira comment body.

    Handles both:
    - ADF JSON (Jira Cloud): dict with "type": "doc"
    - Plain text (Jira Server/older): string

    Args:
        comment_body: The comment body field from Jira API.

    Returns:
        Plain text string.
    """
    if isinstance(comment_body, str):
        return comment_body
    if isinstance(comment_body, dict):
        return _adf_to_text(comment_body)
    return ""


def parse_jira_mention_command(text: str, nickname: str) -> Optional[Tuple[str, str]]:
    """Extract command and args from a @mention in a Jira comment body.

    Mirrors parse_mention_command() from github_notifications.py.
    Ignores mentions inside Jira code blocks ({code} ... {code}).
    Only processes the first @mention found.

    Args:
        text: The comment plain text.
        nickname: The bot's Jira mention name (without @).

    Returns:
        Tuple of (command, context) or None if no valid mention found.
        Command is lowercase. Context is remaining text after command.
    """
    if not text or not nickname:
        return None

    # Remove Jira code blocks to avoid matching mentions in code
    clean_text = _CODE_BLOCK_RE.sub("", text)

    # Match @nickname followed by a command word (optional leading / is stripped)
    pattern = rf'@{re.escape(nickname)}\s+/?(\w+)(.*?)(?:\n|$)'
    match = re.search(pattern, clean_text, re.IGNORECASE)
    if not match:
        return None

    command = match.group(1).strip().lower()
    context = match.group(2).strip()

    if not command:
        return None

    return command, context


def _get_comment_age_hours(updated_str: str) -> Optional[float]:
    """Compute hours since a Jira comment's updated timestamp.

    Args:
        updated_str: ISO 8601 timestamp string from Jira API.

    Returns:
        Age in hours, or None if unparseable.
    """
    try:
        # Jira returns timestamps like "2024-01-15T10:30:00.000+0000"
        updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
        return age
    except (ValueError, TypeError):
        return None


def _load_processed_tracker(tracker_path: Path) -> Set[str]:
    """Load the set of processed comment IDs from the persistent tracker file.

    Args:
        tracker_path: Path to .jira-processed.json in instance dir.

    Returns:
        Set of processed comment IDs.
    """
    try:
        if tracker_path.exists():
            data = json.loads(tracker_path.read_text())
            if isinstance(data, list):
                return set(str(x) for x in data)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return set()


def _save_processed_tracker(tracker_path: Path, processed: Set[str]) -> None:
    """Persist the processed comment IDs to disk.

    Keeps only the most recent 5000 IDs to prevent unbounded growth.
    Uses atomic write via temp file + rename.

    Args:
        tracker_path: Path to .jira-processed.json in instance dir.
        processed: Set of processed comment IDs.
    """
    try:
        from app.utils import atomic_write

        # Trim to most recent 5000 entries (arbitrary stable order)
        ids = sorted(processed, key=lambda x: int(x) if x.isdigit() else 0)[-5000:]
        atomic_write(tracker_path, json.dumps(ids, indent=2))
    except Exception as e:
        log.debug("Failed to save Jira processed tracker: %s", e)


def check_jira_already_processed(
    comment_id: str,
    processed_set: Set[str],
) -> bool:
    """Check if a Jira comment has already been processed.

    Checks both the in-memory BoundedSet and the caller-supplied
    persistent set (loaded from .jira-processed.json).

    Args:
        comment_id: The Jira comment ID.
        processed_set: Persistent processed IDs from tracker file.

    Returns:
        True if already processed.
    """
    str_id = str(comment_id)
    if str_id in _processed_comments:
        return True
    if str_id in processed_set:
        _processed_comments.add(str_id)
        return True
    return False


def mark_jira_comment_processed(comment_id: str, processed_set: Set[str]) -> None:
    """Mark a Jira comment as processed in both in-memory and persistent sets.

    Args:
        comment_id: The Jira comment ID.
        processed_set: The persistent processed set (mutated in-place).
    """
    str_id = str(comment_id)
    _processed_comments.add(str_id)
    processed_set.add(str_id)


def acknowledge_jira_comment(issue_key: str, command_name: str, base_url: str, auth_header: str) -> bool:
    """Post a brief acknowledgment reply on a Jira issue comment.

    Mirrors GitHub's 👍 reaction by posting a short reply comment.

    Note: posting this comment updates the issue's ``updated`` timestamp,
    which will cause ``_search_issues_with_comments`` to re-fetch the issue
    on the next polling cycle.  This is harmless (the bot won't self-trigger
    because the ack comment lacks an @mention), but does add extra API calls
    for the remainder of the ``max_age_hours`` window.

    Args:
        issue_key: Jira issue key (e.g. "CPANEL-52372").
        command_name: The command being executed (e.g. "fix").
        base_url: Jira instance base URL (e.g. https://myorg.atlassian.net).
        auth_header: Basic auth header value.

    Returns:
        True if the comment was posted, False on error.
    """
    try:
        # ADF body with thumbs-up emoji + command acknowledgment
        body = {
            "body": {
                "version": 1,
                "type": "doc",
                "content": [{
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "emoji",
                            "attrs": {
                                "shortName": ":thumbsup:",
                                "id": "1f44d",
                                "text": "\U0001f44d",
                            },
                        },
                        {
                            "type": "text",
                            "text": f" Mission queued: /{command_name}",
                        },
                    ],
                }],
            },
        }

        result = _jira_post(
            base_url, auth_header,
            f"/rest/api/3/issue/{issue_key}/comment",
            body,
        )
        return result is not None
    except Exception as e:
        log.debug("Failed to acknowledge Jira comment on %s: %s", issue_key, e)
        return False


def resolve_project_from_jira_key(issue_key: str, project_map: Dict[str, str]) -> Optional[str]:
    """Map a Jira issue key (e.g. FOO-123) to a Kōan project name.

    Args:
        issue_key: Full Jira issue key like "FOO-123".
        project_map: Dict from jira_config.get_jira_project_map().

    Returns:
        Kōan project name or None if not mapped.
    """
    if not issue_key or "-" not in issue_key:
        return None
    jira_project_key = issue_key.split("-")[0].upper()
    return project_map.get(jira_project_key)


def resolve_branch_from_jira_key(issue_key: str, branch_map: Dict[str, str]) -> Optional[str]:
    """Map a Jira issue key to a configured target branch.

    Args:
        issue_key: Full Jira issue key like "FOO-123".
        branch_map: Dict from jira_config.get_jira_branch_map().

    Returns:
        Branch name or None if no branch is configured for this project key.
    """
    if not issue_key or "-" not in issue_key:
        return None
    jira_project_key = issue_key.split("-")[0].upper()
    return branch_map.get(jira_project_key)


def _search_issues_with_comments(
    base_url: str,
    auth_header: str,
    project_keys: List[str],
    since: datetime,
) -> List[dict]:
    """Search for Jira issues updated since a given time using JQL.

    Uses JQL to find recently-updated issues in the mapped projects.
    Paginates to handle large result sets.

    Args:
        base_url: Jira instance base URL.
        auth_header: Basic auth header value.
        project_keys: List of Jira project keys to search.
        since: Minimum updated timestamp.

    Returns:
        List of issue dicts from Jira API.
    """
    if not project_keys:
        return []

    # Build JQL: project in (FOO, BAR) AND updated >= "YYYY-MM-DD HH:MM"
    # Jira JQL uses "YYYY-MM-DD HH:MM" format for datetime comparisons
    since_str = since.strftime("%Y-%m-%d %H:%M")
    # Validate project keys to prevent JQL injection (keys must be alphanumeric)
    _PROJECT_KEY_RE = re.compile(r'^[A-Z0-9]+$')
    safe_keys = [k for k in project_keys if _PROJECT_KEY_RE.match(k)]
    if not safe_keys:
        log.warning("Jira: no valid project keys after sanitization (got %s)", project_keys)
        return []
    project_in = ", ".join(f'"{k}"' for k in safe_keys)
    jql = f'project in ({project_in}) AND updated >= "{since_str}" ORDER BY updated DESC'

    issues: List[dict] = []
    max_results = 50
    next_page_token: Optional[str] = None

    while True:
        body: Dict[str, Any] = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ["summary", "updated"],
        }
        if next_page_token is not None:
            body["nextPageToken"] = next_page_token

        data = _jira_post(base_url, auth_header, "/rest/api/3/search/jql", body)
        if not data or not isinstance(data, dict):
            break

        batch = data.get("issues", [])
        if not batch:
            break

        issues.extend(batch)

        if data.get("isLast", True):
            break
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    return issues


def _get_issue_comments(
    base_url: str,
    auth_header: str,
    issue_key: str,
    since: datetime,
) -> List[dict]:
    """Fetch comments on a Jira issue updated since the given time.

    Paginates through all comments on the issue.

    Args:
        base_url: Jira instance base URL.
        auth_header: Basic auth header value.
        issue_key: Jira issue key (e.g. "FOO-123").
        since: Minimum updated timestamp.

    Returns:
        List of comment dicts from Jira API.
    """
    comments = []
    start_at = 0
    max_results = 100

    while True:
        params = {
            "startAt": start_at,
            "maxResults": max_results,
            "orderBy": "created",
        }
        data = _jira_get(
            base_url, auth_header,
            f"/rest/api/3/issue/{issue_key}/comment",
            params,
        )
        if not data or not isinstance(data, dict):
            break

        batch = data.get("comments", [])
        if not batch:
            break

        for comment in batch:
            # Filter by updated time
            updated_str = comment.get("updated", "")
            if updated_str:
                try:
                    updated = datetime.fromisoformat(
                        updated_str.replace("Z", "+00:00")
                    )
                    if updated >= since:
                        comments.append(comment)
                except (ValueError, TypeError):
                    comments.append(comment)  # Include on parse error

        total = data.get("total", 0)
        start_at += len(batch)

        if start_at >= total or len(batch) < max_results:
            break

    return comments


def fetch_jira_issue(
    issue_key: str,
) -> Tuple[str, str, List[dict]]:
    """Fetch a Jira issue's title, description, and comments.

    Uses the Jira config from config.yaml to authenticate.

    Args:
        issue_key: Jira issue key (e.g. "CPANEL-52372").

    Returns:
        Tuple of (title, body, comments) where comments is a list of
        dicts with "author" and "body" keys.

    Raises:
        RuntimeError: If Jira is not configured or the API call fails.
    """
    from app.jira_config import (
        get_jira_api_token,
        get_jira_base_url,
        get_jira_email,
        get_jira_enabled,
        validate_jira_config,
    )
    from app.utils import load_config

    config = load_config()
    if not get_jira_enabled(config):
        raise RuntimeError("Jira integration is not enabled in config.yaml")

    error = validate_jira_config(config)
    if error:
        raise RuntimeError(f"Jira config error: {error}")

    base_url = get_jira_base_url(config)
    email = get_jira_email(config)
    api_token = get_jira_api_token(config)
    auth_header = _make_auth_header(email, api_token)

    # Fetch the issue itself
    data = _jira_get(base_url, auth_header, f"/rest/api/3/issue/{issue_key}")
    if not data or not isinstance(data, dict):
        raise RuntimeError(f"Failed to fetch Jira issue {issue_key}")

    fields = data.get("fields", {})
    title = fields.get("summary", "")

    # Description is ADF (Atlassian Document Format) on Jira Cloud
    desc_node = fields.get("description")
    body = _adf_to_text(desc_node) if desc_node else ""

    # Fetch all comments (no time filter — we want full context)
    all_comments = []
    start_at = 0
    max_results = 100

    while True:
        params = {
            "startAt": start_at,
            "maxResults": max_results,
            "orderBy": "created",
        }
        cdata = _jira_get(
            base_url, auth_header,
            f"/rest/api/3/issue/{issue_key}/comment",
            params,
        )
        if not cdata or not isinstance(cdata, dict):
            break

        batch = cdata.get("comments", [])
        if not batch:
            break

        for comment in batch:
            author_data = comment.get("author", {})
            author_name = (
                author_data.get("displayName")
                or author_data.get("emailAddress")
                or "unknown"
            )
            comment_body_node = comment.get("body")
            comment_text = _adf_to_text(comment_body_node) if comment_body_node else ""
            if comment_text.strip():
                all_comments.append({
                    "author": author_name,
                    "body": comment_text,
                })

        total = cdata.get("total", 0)
        start_at += len(batch)
        if start_at >= total or len(batch) < max_results:
            break

    return title, body, all_comments


def fetch_jira_mentions(
    config: dict,
    project_map: Dict[str, str],
    since_iso: Optional[str] = None,
) -> JiraFetchResult:
    """Fetch Jira comments that @mention the bot.

    Searches recently-updated issues in mapped projects, fetches their
    comments, and returns those containing @bot mentions.

    Args:
        config: Global config dict (from config.yaml).
        project_map: Jira project key → Kōan project name mapping.
        since_iso: ISO 8601 timestamp to search from. If None, uses max_age_hours.

    Returns:
        JiraFetchResult with list of mention dicts.
    """
    from app.jira_config import (
        get_jira_api_token,
        get_jira_base_url,
        get_jira_email,
        get_jira_max_age_hours,
        get_jira_nickname,
    )

    base_url = get_jira_base_url(config)
    email = get_jira_email(config)
    api_token = get_jira_api_token(config)
    nickname = get_jira_nickname(config)
    max_age_hours = get_jira_max_age_hours(config)

    if not all([base_url, email, api_token, nickname]):
        log.debug("Jira: missing config (base_url/email/api_token/nickname), skipping")
        return JiraFetchResult([])

    auth_header = _make_auth_header(email, api_token)
    project_keys = list(project_map.keys())

    if not project_keys:
        log.debug("Jira: no project keys configured in jira.projects, skipping")
        return JiraFetchResult([])

    # Determine time window
    if since_iso:
        try:
            since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            since = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    # Search for recently-updated issues (cap at 20 to limit API calls)
    _MAX_ISSUES_PER_CYCLE = 20
    issues = _search_issues_with_comments(base_url, auth_header, project_keys, since)
    if not issues:
        log.debug("Jira: no recently-updated issues found")
        return JiraFetchResult([])

    if len(issues) > _MAX_ISSUES_PER_CYCLE:
        log.debug(
            "Jira: found %d issues, capping at %d to limit API calls",
            len(issues), _MAX_ISSUES_PER_CYCLE,
        )
        issues = issues[:_MAX_ISSUES_PER_CYCLE]
    else:
        log.debug("Jira: found %d recently-updated issues", len(issues))

    # Collect @mention comments from all issues
    mentions = []
    bot_mention_lower = f"@{nickname}".lower()

    for issue in issues:
        issue_key = issue.get("key", "")
        if not issue_key:
            continue

        # Determine Kōan project for this issue
        project_name = resolve_project_from_jira_key(issue_key, project_map)
        if not project_name:
            log.debug("Jira: issue %s has no project mapping, skipping", issue_key)
            continue

        comments = _get_issue_comments(base_url, auth_header, issue_key, since)
        for comment in comments:
            body = comment.get("body", "")
            text = _extract_comment_text(body)
            if bot_mention_lower not in text.lower():
                continue

            # Build a normalized mention dict for the command handler
            mentions.append({
                "comment_id": str(comment.get("id", "")),
                "issue_key": issue_key,
                "project_name": project_name,
                "author_email": comment.get("author", {}).get("emailAddress", ""),
                "author_name": comment.get("author", {}).get("displayName", ""),
                "body_text": text,
                "updated": comment.get("updated", ""),
                "issue_url": f"{base_url}/browse/{issue_key}",
                "comment_url": (
                    f"{base_url}/browse/{issue_key}"
                    f"?focusedCommentId={comment.get('id', '')}"
                ),
            })

    if mentions:
        log.debug("Jira: found %d @%s mention(s)", len(mentions), nickname)
    else:
        log.debug("Jira: no @%s mentions found", nickname)

    return JiraFetchResult(mentions)
