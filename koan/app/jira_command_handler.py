"""Jira command handler — bridges @mention notifications to missions.

Orchestrates the full flow from a Jira @mention comment to a queued
mission in missions.md.

Command flow:
1. Parse comment text → extract command
2. Validate command → check skill has github_enabled (reused for Jira)
3. Check permissions → verify user is in authorized_users
4. Build mission → format with project tag and Jira URL
5. Insert mission → write to missions.md
6. Mark comment as processed → write to .jira-processed.json

Mission format:
    - [project:NAME] /command https://jira.../FOO-123 context 🎫

The 🎫 emoji marks Jira-origin missions (vs 📬 for GitHub).
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from app.jira_config import get_jira_authorized_users, get_jira_nickname
from app.jira_notifications import (
    acknowledge_jira_comment,
    check_jira_already_processed,
    mark_jira_comment_processed,
    parse_jira_mention_command,
    resolve_branch_from_jira_key,
)
from app.skills import SkillRegistry

log = logging.getLogger(__name__)

# Maximum characters of context to include in a mission entry.
_MAX_CONTEXT_LENGTH = 500


def _extract_repo_override(context: str) -> Tuple[Optional[str], str]:
    """Parse a 'repo:name' token from comment context.

    When a commenter writes "@bot plan repo:myproject", the repo: token
    overrides the default project mapping from jira.projects.

    Args:
        context: The context string after the command word.

    Returns:
        Tuple of (project_name_or_None, cleaned_context).
        The repo: token is removed from the cleaned context.
    """
    match = re.search(r'\brepo:(\S+)', context, re.IGNORECASE)
    if not match:
        return None, context

    project_name = match.group(1)
    # Remove the repo: token from context
    cleaned = (context[:match.start()] + context[match.end():]).strip()
    return project_name, cleaned


def _extract_branch_override(context: str) -> Tuple[Optional[str], str]:
    """Parse a 'branch:name' token from comment context.

    When a commenter writes "@bot fix branch:11.126", the branch: token
    overrides the default branch mapping from jira.projects.

    Args:
        context: The context string after the command word.

    Returns:
        Tuple of (branch_name_or_None, cleaned_context).
        The branch: token is removed from the cleaned context.
    """
    match = re.search(r'\bbranch:(\S+)', context, re.IGNORECASE)
    if not match:
        return None, context

    branch_name = match.group(1)
    cleaned = (context[:match.start()] + context[match.end():]).strip()
    return branch_name, cleaned


def validate_command(command_name: str, registry: SkillRegistry) -> Optional[object]:
    """Check if a command maps to a skill with github_enabled.

    Jira reuses the github_enabled flag for skill discovery — both channels
    dispatch the same set of commands.

    Args:
        command_name: The command to validate (e.g., "rebase").
        registry: The skills registry.

    Returns:
        The Skill object if valid, or None.
    """
    skill = registry.find_by_command(command_name)
    if skill is None:
        return None
    if not skill.github_enabled:
        return None
    return skill


def _check_user_permission(author_email: str, allowed_users: List[str]) -> bool:
    """Check if a Jira user is authorized to trigger bot commands.

    Args:
        author_email: The commenter's Jira account email.
        allowed_users: List from jira_config.get_jira_authorized_users().
                       ["*"] means all users.

    Returns:
        True if authorized.
    """
    if "*" in allowed_users:
        return True
    email_lower = author_email.lower()
    return any(u.lower() == email_lower for u in allowed_users)


def build_jira_mission(
    skill,
    command_name: str,
    context: str,
    issue_key: str,
    issue_url: str,
    project_name: str,
    target_branch: Optional[str] = None,
) -> str:
    """Construct a mission string from a Jira @mention.

    Args:
        skill: The Skill object.
        command_name: The command name (e.g., "plan").
        context: Additional context text (max _MAX_CONTEXT_LENGTH chars).
        issue_key: Jira issue key (e.g. "FOO-123").
        issue_url: Full URL to the Jira issue (for missions.md).
        project_name: The resolved Kōan project name.
        target_branch: Optional target branch for PRs (from config or override).

    Returns:
        A mission entry string like "- [project:X] /command url context 🎫"
    """
    # Truncate context to avoid corrupting missions.md
    if context and len(context) > _MAX_CONTEXT_LENGTH:
        context = context[:_MAX_CONTEXT_LENGTH].rstrip()

    parts = [f"/{command_name}"]
    if issue_url:
        parts.append(issue_url)
    if target_branch:
        parts.append(f"branch:{target_branch}")
    if context and skill.github_context_aware:
        parts.append(context)

    mission_text = " ".join(parts)
    # 🎫 marks missions originating from Jira @mentions
    return f"- [project:{project_name}] {mission_text} 🎫"


def process_jira_mention(
    mention: dict,
    registry: SkillRegistry,
    config: dict,
    processed_set: Set[str],
    branch_map: Optional[Dict[str, str]] = None,
) -> Tuple[bool, Optional[str]]:
    """Process a single Jira @mention and create a mission if valid.

    Full workflow: parse → validate → check permissions → build mission
    → insert → mark processed.

    Args:
        mention: A mention dict from jira_notifications.fetch_jira_mentions().
                 Keys: comment_id, issue_key, project_name, author_email,
                       author_name, body_text, updated, issue_url, comment_url.
        registry: Skills registry.
        config: Global config dict (from config.yaml).
        processed_set: Set of already-processed comment IDs (mutated in-place
                       when a new comment is processed).
        branch_map: Optional mapping of Jira project keys to target branches
                    (from jira_config.get_jira_branch_map()). When set, the
                    resolved branch is injected into the mission context.

    Returns:
        Tuple of (success, error_message). error_message is None on success.
    """
    comment_id = mention.get("comment_id", "")
    issue_key = mention.get("issue_key", "")
    project_name = mention.get("project_name", "")
    author_email = mention.get("author_email", "")
    author_name = mention.get("author_name", author_email)
    body_text = mention.get("body_text", "")
    issue_url = mention.get("issue_url", "")
    updated = mention.get("updated", "")

    if not comment_id:
        log.debug("Jira: mention missing comment_id, skipping")
        return False, None

    # Check if already processed
    if check_jira_already_processed(comment_id, processed_set):
        log.debug("Jira: comment %s already processed", comment_id)
        return False, None

    # Check staleness
    from app.jira_config import get_jira_max_age_hours

    if updated:
        from app.jira_notifications import _get_comment_age_hours

        age_hours = _get_comment_age_hours(updated)
        max_age = get_jira_max_age_hours(config)
        if age_hours is not None and age_hours > max_age:
            log.debug(
                "Jira: comment %s on %s is stale (%.1fh > %dh), skipping",
                comment_id, issue_key, age_hours, max_age,
            )
            mark_jira_comment_processed(comment_id, processed_set)
            return False, None

    # Parse command from comment text
    nickname = get_jira_nickname(config)
    command_result = parse_jira_mention_command(body_text, nickname)
    if not command_result:
        log.debug("Jira: no valid @%s command in comment %s", nickname, comment_id)
        mark_jira_comment_processed(comment_id, processed_set)
        return False, None

    command_name, context = command_result
    log.debug(
        "Jira: parsed command=%s context=%r from comment %s on %s",
        command_name, context[:80], comment_id, issue_key,
    )

    # Handle repo: override in context
    repo_override, context = _extract_repo_override(context)
    if repo_override:
        log.debug(
            "Jira: repo: override '%s' for comment %s (default: %s)",
            repo_override, comment_id, project_name,
        )
        project_name = repo_override

    # Handle branch: override in context (highest priority)
    branch_override, context = _extract_branch_override(context)
    if branch_override:
        target_branch = branch_override
        log.debug(
            "Jira: branch: override '%s' for comment %s",
            target_branch, comment_id,
        )
    elif branch_map:
        target_branch = resolve_branch_from_jira_key(issue_key, branch_map)
        if target_branch:
            log.debug(
                "Jira: config branch '%s' for %s",
                target_branch, issue_key,
            )
    else:
        target_branch = None

    # Validate command
    skill = validate_command(command_name, registry)
    if not skill:
        log.debug("Jira: command '%s' is not github_enabled, skipping", command_name)
        mark_jira_comment_processed(comment_id, processed_set)
        return False, f"Unknown command '{command_name}'"

    # Check permissions
    allowed_users = get_jira_authorized_users(config)
    if not _check_user_permission(author_email, allowed_users):
        log.debug(
            "Jira: permission denied for %s (allowed: %s)",
            author_email,
            ", ".join(allowed_users) if allowed_users else "none",
        )
        mark_jira_comment_processed(comment_id, processed_set)
        return False, "Permission denied"

    # Build mission entry
    mission_entry = build_jira_mission(
        skill, command_name, context, issue_key, issue_url, project_name,
        target_branch=target_branch,
    )
    log.info(
        "Jira: inserting mission from %s (%s): %s",
        author_name, issue_key, mission_entry,
    )

    # Insert into missions.md
    koan_root = os.environ.get("KOAN_ROOT", "")
    if not koan_root:
        log.error("Jira: KOAN_ROOT not set — cannot insert mission")
        return False, "KOAN_ROOT not configured"

    from app.utils import insert_pending_mission

    missions_path = Path(koan_root) / "instance" / "missions.md"
    try:
        insert_pending_mission(missions_path, mission_entry)
    except OSError as e:
        log.warning("Jira: failed to insert mission: %s", e)
        return False, f"Failed to queue mission: {e}"

    # Mark as processed
    mark_jira_comment_processed(comment_id, processed_set)

    # Acknowledge in Jira (post 👍 reply comment, mirrors GitHub reaction)
    from app.jira_config import (
        get_jira_api_token,
        get_jira_base_url,
        get_jira_email,
    )
    from app.jira_notifications import _make_auth_header

    base_url = get_jira_base_url(config)
    email = get_jira_email(config)
    api_token = get_jira_api_token(config)
    ack_auth = _make_auth_header(email, api_token)
    acknowledge_jira_comment(issue_key, command_name, base_url, ack_auth)

    # Notify Telegram
    _notify_mission_from_jira(mention, command_name)

    log.info("Jira: created mission from %s: %s", author_name, command_name)
    return True, None


def _notify_mission_from_jira(mention: dict, command_name: str) -> None:
    """Send a Telegram notification when a Jira @mention creates a mission."""
    try:
        from app.notify import NotificationPriority, send_telegram

        issue_key = mention.get("issue_key", "?")
        author_name = mention.get("author_name", "unknown")
        issue_url = mention.get("issue_url", "")

        msg = (
            f"🎫 Jira {author_name} → /{command_name} mission queued\n"
            f"{issue_key}"
        )
        if issue_url:
            msg += f"\n{issue_url}"

        send_telegram(msg, priority=NotificationPriority.ACTION)
    except (ImportError, OSError) as e:
        log.debug("Failed to send Jira notification message: %s", e)
