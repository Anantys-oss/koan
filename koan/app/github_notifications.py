"""GitHub notification fetching and parsing.

Core module for the notification-driven commands feature. Handles:
- Fetching unread notifications filtered to @mentions
- Parsing @mention commands from comment bodies
- Converting API URLs to web URLs
- Reaction-based deduplication (any bot reaction = processed)
- Permission checks for authorized users
"""

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from app.bounded_set import BoundedSet
from app.github import SSOAuthRequired, api
from app.response_cache import TTLCache

log = logging.getLogger(__name__)

# Regex for extracting @mention commands, skipping code blocks
_CODE_BLOCK_RE = re.compile(r'```.*?```|`[^`]+`', re.DOTALL)

# Reasons that may contain @mention commands in the latest comment.
# "mention" is the primary signal.  "author" and "comment" notifications
# can hide @mentions when a bot-authored thread already has an unread
# notification — GitHub updates the existing notification instead of
# creating a new "mention" one.
# "review_requested" — review requests can include @mentions in the
# associated comment; the notification reason stays review_requested.
# "team_mention" — @team mentions that the bot is part of.
# "subscribed" — when the bot watches a repo, @mentions on threads with
# existing unread notifications may keep the subscribed reason instead
# of updating to mention (GitHub API race condition / caching).
# "assign" — the bot was assigned to an issue; triggers /implement mission.
_ACTIONABLE_REASONS = {
    "mention", "author", "comment",
    "review_requested", "team_mention", "subscribed",
    "assign",
}


# ---------------------------------------------------------------------------
# Constants (kept at module level for backward-compatible imports)
# ---------------------------------------------------------------------------

_FETCH_FAILURE_THRESHOLD = 3
SSO_ESCALATION_THRESHOLD: int = 5
_MAX_PROCESSED_COMMENTS = 10000


# ---------------------------------------------------------------------------
# NotificationTracker — encapsulates all mutable notification state
# ---------------------------------------------------------------------------

class NotificationTracker:
    """Encapsulates all mutable notification-tracking state.

    Holds SSO failure counters, fetch failure counters, and the
    processed-comments dedup set.  Creating a fresh instance gives
    clean state — useful for tests and concurrent use.
    """

    def __init__(self) -> None:
        # SSO failure tracking
        self.sso_failure_count: int = 0
        self.consecutive_sso_failures: int = 0
        self.sso_escalation_sent: bool = False

        # Fetch failure tracking
        self.consecutive_fetch_failures: int = 0
        self.fetch_failure_alerted: bool = False

        # In-memory dedup set (bounded FIFO eviction)
        self.processed_comments: BoundedSet = BoundedSet(
            maxlen=_MAX_PROCESSED_COMMENTS,
        )

        # Permission cache: avoids repeated GitHub API calls for the same
        # user/repo combination during a notification processing cycle.
        self.permission_cache: TTLCache = TTLCache(max_entries=200)

    # -- SSO failure tracking -------------------------------------------------

    def reset_sso_failure_count(self) -> None:
        """Reset the per-cycle SSO failure counter.

        Called at the start of each notification cycle.  Does NOT reset the
        cross-cycle consecutive counter — that is handled by
        ``update_consecutive_sso_failures()``.
        """
        self.sso_failure_count = 0

    def reset_consecutive_sso_state(self) -> None:
        """Reset all consecutive SSO failure state.  For tests only."""
        self.consecutive_sso_failures = 0
        self.sso_escalation_sent = False

    def get_sso_failure_count(self) -> int:
        """Return the number of SSO failures observed in the current cycle."""
        return self.sso_failure_count

    def get_consecutive_sso_failures(self) -> int:
        """Return the number of consecutive SSO failures across cycles."""
        return self.consecutive_sso_failures

    def update_consecutive_sso_failures(self) -> None:
        """Update the cross-cycle consecutive failure counter.

        Call this AFTER a notification cycle completes.  If the cycle had
        SSO failures, they are added to the running total.  If the cycle
        was clean, the running total resets to zero.
        """
        if self.sso_failure_count > 0:
            self.consecutive_sso_failures += self.sso_failure_count
        else:
            self.consecutive_sso_failures = 0
            self.sso_escalation_sent = False

    def check_sso_escalation(self) -> bool:
        """Check if SSO failures should be escalated to outbox.

        Returns True if an outbox alert was written, False otherwise.
        The alert fires once per failure streak (reset when failures stop).
        """
        if self.sso_escalation_sent:
            return False
        if self.consecutive_sso_failures < SSO_ESCALATION_THRESHOLD:
            return False

        koan_root = os.environ.get("KOAN_ROOT", "")
        if not koan_root:
            return False

        outbox_path = Path(koan_root) / "instance" / "outbox.md"
        try:
            from app.utils import append_to_outbox
            append_to_outbox(
                outbox_path,
                f"⚠️ GitHub SSO auth has failed {self.consecutive_sso_failures} times "
                "consecutively — token needs re-authorization.\n"
                "Run: `gh auth refresh -h github.com -s read:org`\n",
            )
            self.sso_escalation_sent = True
            log.warning(
                "SSO escalation: %d consecutive failures, alert written to outbox",
                self.consecutive_sso_failures,
            )
            return True
        except Exception as e:
            log.debug("Failed to write SSO escalation to outbox: %s", e)
            return False

    def record_sso_failure(self, context: str) -> None:
        """Record an SSO failure and log a warning (once per cycle)."""
        self.sso_failure_count += 1
        if self.sso_failure_count == 1:
            log.warning(
                "GitHub SSO auth failure detected (%s). "
                "Token needs re-authorization: gh auth refresh -h github.com -s read:org",
                context,
            )

    # -- Fetch failure tracking -----------------------------------------------

    def reset_fetch_failure_count(self) -> None:
        """Reset the consecutive fetch failure counter."""
        self.consecutive_fetch_failures = 0
        self.fetch_failure_alerted = False

    def get_fetch_failure_count(self) -> int:
        """Return the number of consecutive fetch failures."""
        return self.consecutive_fetch_failures

    def record_fetch_failure(self, reason: str) -> None:
        """Record a fetch failure, escalate logging and notify after threshold."""
        self.consecutive_fetch_failures += 1

        if self.consecutive_fetch_failures < _FETCH_FAILURE_THRESHOLD:
            log.debug("GitHub API: failed to fetch notifications: %s", reason)
            return

        # Threshold reached — escalate to warning
        log.warning(
            "GitHub API: %d consecutive fetch failures (latest: %s). "
            "Notification polling may be broken.",
            self.consecutive_fetch_failures,
            reason,
        )

        # Send a one-time outbox alert so the user gets a Telegram notification
        if not self.fetch_failure_alerted:
            if _send_fetch_failure_alert(self.consecutive_fetch_failures, reason):
                self.fetch_failure_alerted = True

    def clear_fetch_failures(self) -> None:
        """Reset failure counter on a successful fetch."""
        if self.consecutive_fetch_failures > 0:
            if self.fetch_failure_alerted:
                log.info(
                    "GitHub API: notification fetch recovered after %d failures",
                    self.consecutive_fetch_failures,
                )
            self.consecutive_fetch_failures = 0
            self.fetch_failure_alerted = False

    # -- Notification fetching ------------------------------------------------

    def fetch_unread_notifications(
        self,
        known_repos: Optional[Set[str]] = None,
        since: Optional[str] = None,
    ) -> "FetchResult":
        """Fetch GitHub notifications, categorized for processing.

        Returns actionable notifications (may contain @mention commands) and
        drain-only notifications (noise that should be marked as read to
        prevent accumulation that blocks future @mention detection).

        When ``since`` is provided, fetches ALL notifications (read + unread)
        updated after that timestamp.  This catches @mentions that were
        auto-read by the GitHub web UI before the bot could poll them —
        a common race condition when the user posts an @mention while
        viewing the PR page.

        Args:
            known_repos: Optional set of "owner/repo" strings to filter against.
                If None, all notifications from any repo are included.
            since: Optional ISO 8601 timestamp.  When set, uses ``all=true``
                to include already-read notifications updated after this time.

        Returns:
            FetchResult with actionable and drain lists.
        """
        try:
            endpoint = "notifications"
            if since:
                endpoint = f"notifications?since={since}&all=true"
            raw = api(endpoint, extra_args=["--paginate"], timeout=30)
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
            self.record_fetch_failure(str(e))
            return FetchResult([], [])

        if not raw:
            self.record_fetch_failure("empty response")
            return FetchResult([], [])

        try:
            notifications = json.loads(raw)
        except json.JSONDecodeError:
            self.record_fetch_failure("invalid JSON")
            return FetchResult([], [])

        if not isinstance(notifications, list):
            self.record_fetch_failure(
                f"unexpected type: {type(notifications).__name__}",
            )
            return FetchResult([], [])

        # Successful parse — clear any failure streak
        self.clear_fetch_failures()

        log.debug(
            "GitHub API: %d total notifications%s",
            len(notifications),
            " (including read)" if since else "",
        )

        skipped_reasons: Dict[str, int] = {}
        skipped_repos: List[str] = []
        skipped_mention_repos: Dict[str, int] = {}
        skipped_notifications: List[dict] = []
        actionable = []
        drain = []
        for notif in notifications:
            reason = notif.get("reason", "?")
            repo_name = notif.get("repository", {}).get("full_name", "?")

            if known_repos:
                repo_lower = repo_name.lower()
                if repo_lower not in known_repos:
                    skipped_repos.append(repo_name)
                    skipped_notifications.append(notif)
                    if reason in {"mention", "team_mention"}:
                        skipped_mention_repos[repo_name] = (
                            skipped_mention_repos.get(repo_name, 0) + 1
                        )
                    continue

            if reason in _ACTIONABLE_REASONS:
                actionable.append(notif)
            else:
                drain.append(notif)
                skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

        if skipped_reasons:
            log.debug(
                "GitHub: %d drain-only notifications: %s",
                sum(skipped_reasons.values()),
                ", ".join(
                    f"{r}={c}" for r, c in sorted(skipped_reasons.items())
                ),
            )
        if skipped_repos:
            log.debug(
                "GitHub: skipped %d notifications from unknown repos: %s",
                len(skipped_repos), ", ".join(skipped_repos),
            )
        if skipped_mention_repos:
            try:
                from app.config import get_enable_multiple_instances
                _multi = get_enable_multiple_instances()
            except (ImportError, OSError):
                _multi = False
            _log = log.debug if _multi else log.warning
            _log(
                "GitHub: %d @mention(s) dropped from unregistered repo(s): %s",
                sum(skipped_mention_repos.values()),
                ", ".join(
                    f"{r} ({c})"
                    for r, c in sorted(skipped_mention_repos.items())
                ),
            )

        log.debug(
            "GitHub: %d actionable + %d drain notification(s) from known repos",
            len(actionable), len(drain),
        )
        return FetchResult(
            actionable, drain, skipped_repos,
            skipped_mention_repos, skipped_notifications,
        )

    # -- Comment processing ---------------------------------------------------

    def check_already_processed(
        self,
        comment_id: str,
        bot_username: str,
        owner: str,
        repo: str,
        comment_api_url: str = "",
    ) -> bool:
        """Check if a comment has already been processed (has bot reaction).

        Checks for any reaction from the bot — both +1 (command acknowledgment)
        and eyes (AI reply acknowledgment). This prevents duplicate processing
        when mark_notification_read fails.

        Also checks in-memory set for current session deduplication.
        """
        # Check in-memory first
        if comment_id in self.processed_comments:
            return True

        # Check persistent file tracker (survives restarts)
        koan_root = os.environ.get("KOAN_ROOT", "")
        if koan_root:
            from app.github_notification_tracker import is_comment_tracked
            instance_dir = os.path.join(koan_root, "instance")
            if is_comment_tracked(instance_dir, comment_id):
                self.processed_comments.add(comment_id)
                return True

        # Check GitHub reactions — any reaction from the bot means processed
        endpoint = _reactions_endpoint(comment_api_url, owner, repo, comment_id)
        try:
            raw = api(endpoint, timeout=30)
            reactions = json.loads(raw) if raw else []
            if isinstance(reactions, list):
                for reaction in reactions:
                    if reaction.get("user", {}).get("login") == bot_username:
                        self.processed_comments.add(comment_id)
                        return True
        except SSOAuthRequired:
            self.record_sso_failure(
                f"check_already_processed comment={comment_id}",
            )
        except (RuntimeError, json.JSONDecodeError, OSError,
                subprocess.TimeoutExpired) as exc:
            log.warning(
                "GitHub: reactions check failed for comment %s: %s",
                comment_id, exc,
            )

        return False

    def add_reaction(
        self,
        owner: str,
        repo: str,
        comment_id: str,
        emoji: str = "+1",
        comment_api_url: str = "",
    ) -> bool:
        """Add a reaction to a comment.

        Returns True if successful.
        """
        endpoint = _reactions_endpoint(comment_api_url, owner, repo, comment_id)
        try:
            api(
                endpoint,
                method="POST",
                extra_args=["-f", f"content={emoji}"],
                timeout=30,
            )
            return True
        except RuntimeError:
            return False
        finally:
            self.processed_comments.add(comment_id)

    def search_comments_for_mention(
        self,
        comments: list,
        bot_username: str,
        owner: str,
        repo: str,
    ) -> Optional[dict]:
        """Search a list of comments for an unprocessed @mention of the bot.

        Shared helper for find_mention_in_thread — avoids duplicating the
        filter/dedup logic across issue comments and PR review comments.

        Returns:
            The first unprocessed comment containing an @mention, or None.
        """
        results = self.search_all_comments_for_mentions(
            comments, bot_username, owner, repo,
        )
        return results[0] if results else None

    def search_all_comments_for_mentions(
        self,
        comments: list,
        bot_username: str,
        owner: str,
        repo: str,
    ) -> List[dict]:
        """Search a list of comments for ALL unprocessed @mentions of the bot.

        Like search_comments_for_mention but returns every match instead of
        only the first one.  Used by find_all_mentions_in_thread to collect
        all pending commands from a single notification thread.

        Returns:
            List of unprocessed comment dicts (may be empty).
        """
        bot_lower = f"@{bot_username}".lower()
        results = []

        for comment in comments:
            if comment.get("user", {}).get("login") == bot_username:
                continue

            body = comment.get("body", "")
            if bot_lower not in body.lower():
                continue

            comment_id = str(comment.get("id", ""))
            comment_api_url = comment.get("url", "")
            if self.check_already_processed(
                comment_id, bot_username, owner, repo,
                comment_api_url=comment_api_url,
            ):
                continue

            log.debug(
                "GitHub: found unprocessed @mention in comment %s by @%s",
                comment_id,
                comment.get("user", {}).get("login", "?"),
            )
            results.append(comment)

        return results

    def get_comment_from_notification(
        self, notification: dict,
    ) -> Optional[dict]:
        """Fetch the latest comment that triggered the notification.

        Note: subject.latest_comment_url points to the most recent comment on
        the thread, not necessarily the one that triggered the notification.
        When the bot itself posts a comment after the @mention, this URL shifts.
        Use find_mention_in_thread() as a fallback when this returns a
        self-authored comment.
        """
        comment_url = notification.get("subject", {}).get(
            "latest_comment_url", "",
        )
        if not comment_url:
            return None

        # Convert full URL to API endpoint (strict prefix check to prevent SSRF)
        api_prefix = "https://api.github.com/"
        if not comment_url.startswith(api_prefix):
            return None
        endpoint = comment_url[len(api_prefix):]
        if not endpoint:
            return None

        try:
            raw = api(endpoint, timeout=30)
            return json.loads(raw) if raw else None
        except SSOAuthRequired:
            self.record_sso_failure(
                f"get_comment endpoint={endpoint[:80]}",
            )
            return None
        except (
            RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired,
        ) as e:
            log.warning(
                "GitHub API: failed to fetch comment %s: %s",
                endpoint[:80], e,
            )
            return None

    def find_mention_in_thread(
        self,
        notification: dict,
        bot_username: str,
    ) -> Optional[dict]:
        """Search a PR/issue thread for an unprocessed @mention comment.

        Fallback for when latest_comment_url points to a bot comment
        (self-mention race condition). Fetches recent comments on the thread
        and finds the first unprocessed @mention of the bot.

        Searches both issue comments and PR review comments (inline code
        comments), since @mentions can appear in either location.
        """
        subject_url = notification.get("subject", {}).get("url", "")
        if not subject_url:
            return None

        match = re.match(
            r'https://api\.github\.com/repos/([^/]+)/([^/]+)/'
            r'(pulls|issues)/(\d+)',
            subject_url,
        )
        if not match:
            return None

        owner, repo, subject_type, number = match.groups()

        endpoints = [
            (f"repos/{owner}/{repo}/issues/{number}/comments"
             "?per_page=100&sort=created&direction=desc",
             f"find_mention issue_comments {owner}/{repo}#{number}"),
        ]
        if subject_type == "pulls":
            endpoints.append(
                (f"repos/{owner}/{repo}/pulls/{number}/comments"
                 "?per_page=100&sort=created&direction=desc",
                 f"find_mention review_comments {owner}/{repo}#{number}"),
            )

        for endpoint, sso_label in endpoints:
            try:
                raw = api(endpoint, timeout=30)
                comments = json.loads(raw) if raw else []
            except SSOAuthRequired:
                self.record_sso_failure(sso_label)
                continue
            except (
                RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired,
            ) as e:
                log.warning(
                    "GitHub API: failed to fetch %s: %s", endpoint[:80], e,
                )
                continue

            if not isinstance(comments, list):
                continue

            if len(comments) >= 100:
                log.warning(
                    "Truncated comment list for %s/%s#%s (%d items) — "
                    "mention may be missed",
                    owner, repo, number, len(comments),
                )

            result = self.search_comments_for_mention(
                comments, bot_username, owner, repo,
            )
            if result:
                return result

        return None

    def find_all_mentions_in_thread(
        self,
        notification: dict,
        bot_username: str,
    ) -> List[dict]:
        """Search a PR/issue thread for ALL unprocessed @mention comments.

        Like find_mention_in_thread but collects every unprocessed mention
        instead of returning only the first one.  Results are sorted by
        created_at ascending (oldest first) so missions are queued in the
        order the user posted them.

        Searches both issue comments and PR review comments.
        """
        subject_url = notification.get("subject", {}).get("url", "")
        if not subject_url:
            return []

        match = re.match(
            r'https://api\.github\.com/repos/([^/]+)/([^/]+)/'
            r'(pulls|issues)/(\d+)',
            subject_url,
        )
        if not match:
            return []

        owner, repo, subject_type, number = match.groups()

        endpoints = [
            (f"repos/{owner}/{repo}/issues/{number}/comments"
             "?per_page=100&sort=created&direction=desc",
             f"find_all_mentions issue_comments {owner}/{repo}#{number}"),
        ]
        if subject_type == "pulls":
            endpoints.append(
                (f"repos/{owner}/{repo}/pulls/{number}/comments"
                 "?per_page=100&sort=created&direction=desc",
                 f"find_all_mentions review_comments {owner}/{repo}#{number}"),
            )

        all_mentions: List[dict] = []
        seen_ids: set = set()

        for endpoint, sso_label in endpoints:
            try:
                raw = api(endpoint, timeout=30)
                comments = json.loads(raw) if raw else []
            except SSOAuthRequired:
                self.record_sso_failure(sso_label)
                continue
            except (
                RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired,
            ) as e:
                log.warning(
                    "GitHub API: failed to fetch %s: %s", endpoint[:80], e,
                )
                continue

            if not isinstance(comments, list):
                continue

            mentions = self.search_all_comments_for_mentions(
                comments, bot_username, owner, repo,
            )
            for m in mentions:
                mid = str(m.get("id", ""))
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    all_mentions.append(m)

        all_mentions.sort(key=lambda c: c.get("created_at", ""))
        return all_mentions

    def check_user_permission(
        self,
        owner: str,
        repo: str,
        username: str,
        allowed_users: List[str],
    ) -> bool:
        """Check if a user is authorized to trigger bot commands.

        Returns True if authorized.
        """
        # Explicit allowlist: trust the admin's decision, no API call needed
        if "*" not in allowed_users:
            return username in allowed_users

        # Wildcard: verify at least write access via GitHub API
        cache_key = f"{owner}/{repo}/{username}"
        cached = self.permission_cache.get(cache_key)
        if cached is not None:
            return cached == "1"

        try:
            raw = api(
                f"repos/{owner}/{repo}/collaborators/{username}/permission",
                timeout=30,
            )
            data = json.loads(raw) if raw else {}
            permission = data.get("permission", "none")
            result = permission in ("admin", "write")
            self.permission_cache.put(cache_key, "1" if result else "0", ttl=300)
            return result
        except SSOAuthRequired:
            self.record_sso_failure(
                f"check_user_permission {owner}/{repo}",
            )
            return False
        except (RuntimeError, json.JSONDecodeError):
            return False


# ---------------------------------------------------------------------------
# Default module-level tracker instance
# ---------------------------------------------------------------------------

_default_tracker = NotificationTracker()

# Backward-compatible alias — tests import this to call .clear() / .add().
# Points to the same BoundedSet object inside _default_tracker.
_processed_comments: BoundedSet = _default_tracker.processed_comments


# ---------------------------------------------------------------------------
# Stateless helpers (no tracker state needed)
# ---------------------------------------------------------------------------

class FetchResult:
    """Result from fetch_unread_notifications.

    Attributes:
        actionable: Notifications that might contain @mention commands
            (reasons: mention, author, comment).
        drain: Non-actionable notifications from known repos that should
            be marked as read to prevent accumulation.
        skipped_notifications: Full notification dicts from repos not in
            projects.yaml.  In single-instance mode these can be drained
            (marked as read) safely; in multi-instance mode they must be
            left untouched for sibling instances.
    """
    __slots__ = (
        "actionable", "drain", "skipped_repos",
        "skipped_mention_repos", "skipped_notifications",
    )

    def __init__(self, actionable: List[dict], drain: List[dict],
                 skipped_repos: Optional[List[str]] = None,
                 skipped_mention_repos: Optional[Dict[str, int]] = None,
                 skipped_notifications: Optional[List[dict]] = None):
        self.actionable = actionable
        self.drain = drain
        self.skipped_repos = skipped_repos or []
        self.skipped_mention_repos = skipped_mention_repos or {}
        self.skipped_notifications = skipped_notifications or []


def _send_fetch_failure_alert(count: int, reason: str) -> bool:
    """Write a fetch-failure alert to outbox.md.

    Returns True if the alert was written successfully, False otherwise.
    """
    try:
        koan_root = os.environ.get("KOAN_ROOT", "")
        if not koan_root:
            return False
        outbox_path = Path(koan_root) / "instance" / "outbox.md"
        if not outbox_path.parent.is_dir():
            return False
        from app.utils import append_to_outbox
        msg = (
            f"⚠️ GitHub notification polling has failed {count} times in a row "
            f"({reason}). @mentions may be missed until connectivity is restored.\n"
        )
        append_to_outbox(outbox_path, msg)
        return True
    except Exception as exc:
        log.debug("Failed to write fetch-failure alert to outbox: %s", exc)
        return False


def _reactions_endpoint(
    comment_api_url: str = "",
    owner: str = "",
    repo: str = "",
    comment_id: str = "",
) -> str:
    """Build the reactions API endpoint for a comment.

    Uses comment_api_url when available (handles all comment types:
    issue comments, PR review comments, commit comments).
    Falls back to the issues/comments endpoint for backward compatibility.
    """
    if comment_api_url:
        api_prefix = "https://api.github.com/"
        if comment_api_url.startswith(api_prefix):
            return comment_api_url[len(api_prefix):] + "/reactions"
    return f"repos/{owner}/{repo}/issues/comments/{comment_id}/reactions"


def parse_mention_command(comment_body: str, nickname: str) -> Optional[Tuple[str, str]]:
    """Extract command and args from a @mention in a comment body.

    Ignores mentions inside code blocks (``` or `).
    Only processes the first @mention found.

    Context includes text from the rest of the comment — both the
    remainder of the @mention line and any surrounding paragraphs.
    This allows users to write multi-paragraph comments where the
    instructions appear before or after the ``@bot rebase`` line.

    Args:
        comment_body: The full comment text.
        nickname: The bot's GitHub username (without @).

    Returns:
        Tuple of (command, context) or None if no valid mention found.
        Command is lowercase. Context is the surrounding text from the
        same comment.
    """
    if not comment_body or not nickname:
        return None

    # Remove code blocks to avoid matching mentions in code
    clean_body = _CODE_BLOCK_RE.sub('', comment_body)

    # Match @nickname followed by a command word (optional leading / is stripped)
    pattern = rf'@{re.escape(nickname)}\s+/?(\w+)(.*?)(?:\n|$)'
    match = re.search(pattern, clean_body, re.IGNORECASE)
    if not match:
        return None

    command = match.group(1).strip().lower()

    if not command:
        return None

    # Build context from the entire comment, not just the same line.
    # Remove the @mention line itself, keep everything else.
    remaining = (clean_body[:match.start()] + clean_body[match.end():]).strip()
    # Also include any inline args on the same line (e.g. @bot rebase --critical)
    inline_args = match.group(2).strip()
    if inline_args and remaining:
        context = f"{inline_args}\n{remaining}"
    elif inline_args:
        context = inline_args
    else:
        context = remaining

    return command, context


def api_url_to_web_url(api_url: str) -> str:
    """Convert a GitHub API URL to a web URL.

    Examples:
        https://api.github.com/repos/owner/repo/pulls/123
        → https://github.com/owner/repo/pull/123

        https://api.github.com/repos/owner/repo/issues/42
        → https://github.com/owner/repo/issues/42
    """
    url = api_url.replace("https://api.github.com/repos/", "https://github.com/")
    # API uses "pulls" (plural), web uses "pull" (singular)
    url = re.sub(r'/pulls/(\d+)', r'/pull/\1', url)
    return url


def mark_notification_read(thread_id: str) -> bool:
    """Mark a notification thread as read.

    Returns True if successful, False otherwise.
    """
    try:
        api(f"notifications/threads/{thread_id}", method="PATCH", timeout=30)
        return True
    except RuntimeError:
        return False


def is_notification_stale(notification: dict, max_age_hours: int = 24) -> bool:
    """Check if a notification is too old to process.

    Returns True if the notification is stale.
    """
    updated_at = notification.get("updated_at", "")
    if not updated_at:
        return True

    try:
        notif_time = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - notif_time).total_seconds() / 3600
        return age_hours > max_age_hours
    except (ValueError, TypeError):
        return True


def is_self_mention(comment: dict, bot_username: str) -> bool:
    """Check if the comment was posted by the bot itself.

    Returns True if the comment author is the bot.
    """
    author = comment.get("user", {}).get("login", "")
    return author == bot_username


def extract_comment_metadata(comment_url: str) -> Optional[Tuple[str, str, str]]:
    """Extract owner, repo, and comment ID from a comment URL.

    Handles web URLs and API URLs for all GitHub comment types:
        https://github.com/owner/repo/issues/123#issuecomment-456
        https://api.github.com/repos/owner/repo/issues/comments/456
        https://api.github.com/repos/owner/repo/pulls/comments/456

    Returns:
        Tuple of (owner, repo, comment_id) or None.
    """
    # Try API URL format (handles issues/comments and pulls/comments)
    match = re.match(
        r'https?://api\.github\.com/repos/([^/]+)/([^/]+)/(?:issues|pulls)/comments/(\d+)',
        comment_url,
    )
    if match:
        return match.group(1), match.group(2), match.group(3)

    # Try web URL format
    match = re.match(
        r'https?://github\.com/([^/]+)/([^/]+)/(?:issues|pull)/\d+#issuecomment-(\d+)',
        comment_url,
    )
    if match:
        return match.group(1), match.group(2), match.group(3)

    return None


# ---------------------------------------------------------------------------
# Module-level delegate functions — backward-compatible API
#
# All stateful functions delegate to _default_tracker so existing callers
# continue to work unchanged.  For test isolation, create a fresh
# NotificationTracker() instance instead.
# ---------------------------------------------------------------------------

def reset_sso_failure_count() -> None:
    _default_tracker.reset_sso_failure_count()

def reset_consecutive_sso_state() -> None:
    _default_tracker.reset_consecutive_sso_state()

def get_sso_failure_count() -> int:
    return _default_tracker.get_sso_failure_count()

def reset_fetch_failure_count() -> None:
    _default_tracker.reset_fetch_failure_count()

def get_fetch_failure_count() -> int:
    return _default_tracker.get_fetch_failure_count()

def _record_fetch_failure(reason: str) -> None:
    _default_tracker.record_fetch_failure(reason)

def _clear_fetch_failures() -> None:
    _default_tracker.clear_fetch_failures()

def get_consecutive_sso_failures() -> int:
    return _default_tracker.get_consecutive_sso_failures()

def update_consecutive_sso_failures() -> None:
    _default_tracker.update_consecutive_sso_failures()

def check_sso_escalation() -> bool:
    return _default_tracker.check_sso_escalation()

def _record_sso_failure(context: str) -> None:
    _default_tracker.record_sso_failure(context)

def fetch_unread_notifications(
    known_repos: Optional[Set[str]] = None,
    since: Optional[str] = None,
) -> FetchResult:
    return _default_tracker.fetch_unread_notifications(known_repos, since)

def check_already_processed(
    comment_id: str,
    bot_username: str,
    owner: str,
    repo: str,
    comment_api_url: str = "",
) -> bool:
    return _default_tracker.check_already_processed(
        comment_id, bot_username, owner, repo, comment_api_url,
    )

def add_reaction(
    owner: str,
    repo: str,
    comment_id: str,
    emoji: str = "+1",
    comment_api_url: str = "",
) -> bool:
    return _default_tracker.add_reaction(
        owner, repo, comment_id, emoji, comment_api_url,
    )

def _search_comments_for_mention(
    comments: list,
    bot_username: str,
    owner: str,
    repo: str,
) -> Optional[dict]:
    return _default_tracker.search_comments_for_mention(
        comments, bot_username, owner, repo,
    )

def search_all_comments_for_mentions(
    comments: list,
    bot_username: str,
    owner: str,
    repo: str,
) -> List[dict]:
    return _default_tracker.search_all_comments_for_mentions(
        comments, bot_username, owner, repo,
    )

def get_comment_from_notification(notification: dict) -> Optional[dict]:
    return _default_tracker.get_comment_from_notification(notification)

def find_mention_in_thread(
    notification: dict,
    bot_username: str,
) -> Optional[dict]:
    return _default_tracker.find_mention_in_thread(notification, bot_username)

def find_all_mentions_in_thread(
    notification: dict,
    bot_username: str,
) -> List[dict]:
    return _default_tracker.find_all_mentions_in_thread(notification, bot_username)

def check_user_permission(
    owner: str,
    repo: str,
    username: str,
    allowed_users: List[str],
) -> bool:
    return _default_tracker.check_user_permission(
        owner, repo, username, allowed_users,
    )
