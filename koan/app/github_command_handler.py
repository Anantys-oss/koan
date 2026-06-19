"""GitHub command handler — bridges notifications to missions and replies.

Orchestrates the full flow from a GitHub @mention notification to either:
- A queued mission in missions.md (for recognized commands)
- A direct AI-generated reply (for questions/requests from authorized users)

Command flow:
1. Parse comment → extract command
2. Validate command → check skill has github_enabled
3. Check permissions → verify user is authorized
4. Add reaction → mark as processed (👍)
5. Build mission → format with project tag
6. Insert mission → write to missions.md

Reply flow (when reply_enabled=true and command not recognized):
1. Verify user is authorized
2. Fetch issue/PR thread context
3. Generate AI reply via Claude CLI
4. Post reply as GitHub comment
"""

import json
import logging
import os
import re
import subprocess
import time
from typing import Dict, List, Optional, Tuple

from app.bounded_set import BoundedSet
from app.github_config import (
    get_github_ack_enabled,
    get_github_authorized_users,
    get_github_natural_language,
    get_github_nickname,
    get_github_reply_authorized_users,
    get_github_reply_enabled,
    get_github_reply_rate_limit,
    get_github_subscribe_enabled,
    get_github_subscribe_max_per_cycle,
)
from app.github_notifications import (
    add_reaction,
    api_url_to_web_url,
    check_already_processed,
    check_user_permission,
    find_all_mentions_in_thread,
    find_mention_in_thread,
    get_comment_from_notification,
    is_notification_stale,
    is_self_mention,
    mark_notification_read,
    parse_mention_command,
)
from app.skills import SkillRegistry

log = logging.getLogger(__name__)

# Track error replies to avoid duplicate error messages per comment.
# Bounded: FIFO eviction when limit is reached (oldest entries removed first).
_MAX_TRACKED_ENTRIES = 10000
_error_replies: BoundedSet = BoundedSet(maxlen=_MAX_TRACKED_ENTRIES)

# Per-user rate tracking for AI replies — persisted to survive restarts.
_REPLY_RATE_FILE = ".reply-rate-limits.json"

# Notification outcome annotation key set on the notification dict.
# loop_manager uses this to decide whether to count/log as mission creation.
NOTIFICATION_OUTCOME_KEY = "_koan_notification_outcome"
NOTIFICATION_OUTCOME_QUEUED = "queued"
NOTIFICATION_OUTCOME_HANDLED_NOOP = "handled_noop"


def _load_reply_timestamps(instance_dir: str) -> Dict[str, List[float]]:
    """Load reply timestamps from disk, discarding entries older than 1 hour."""
    path = os.path.join(instance_dir, _REPLY_RATE_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    one_hour_ago = time.time() - 3600
    result: Dict[str, List[float]] = {}
    for user, timestamps in data.items():
        if not isinstance(timestamps, list):
            continue
        fresh = [t for t in timestamps if isinstance(t, (int, float)) and t > one_hour_ago]
        if fresh:
            result[user] = fresh
    return result


def _save_reply_timestamps(instance_dir: str, data: Dict[str, List[float]]) -> None:
    """Persist reply timestamps to disk atomically."""
    from pathlib import Path

    from app.utils import atomic_write_json

    atomic_write_json(Path(instance_dir) / _REPLY_RATE_FILE, data)


def _quarantine_github_mission(text: str, reason: str, author: str):
    """Write a flagged GitHub mission to the quarantine file."""
    import os
    from pathlib import Path

    from app.missions import quarantine_mission

    koan_root = os.environ.get("KOAN_ROOT", "")
    if not koan_root:
        return
    quarantine_path = Path(koan_root) / "instance" / "missions-quarantine.md"
    ok = quarantine_mission(quarantine_path, text, reason, source=f"github/@{author}")
    if not ok:
        log.warning("GitHub: failed to write quarantine entry: %s", reason)


def _expand_combo_mission(
    command_name: str,
    mission_entry: str,
    project_name: str,
) -> list:
    """Expand a combo skill mission into its constituent sub-missions.

    Combo skills (e.g. /rr) are bridge-side handlers that queue multiple
    sub-commands.  When triggered via GitHub @mentions, the mission goes
    through the agent loop, which needs a dedicated expansion step.
    Expanding here — at the notification handler level — is more reliable
    because it mirrors what the Telegram bridge handler does: insert the
    sub-missions directly.

    Args:
        command_name: The parsed command (e.g. "rr").
        mission_entry: The full mission line (e.g. "- [project:X] /rr URL 📬").
        project_name: The resolved project name.

    Returns:
        A list of mission entries.  For non-combo commands this is
        ``[mission_entry]`` (passthrough).  For combo commands it's the
        expanded sub-missions.
    """
    from app.skill_dispatch import get_combo_sub_commands

    sub_commands = get_combo_sub_commands(command_name)
    if not sub_commands:
        return [mission_entry]

    # Extract the URL + context portion from the original mission.
    # mission_entry looks like: "- [project:X] /rr <url> [context] 📬"
    # We need to replace "/rr" with "/review", "/rebase" etc.
    import re
    pattern = rf"(/){re.escape(command_name)}(\s)"
    entries = []
    for sub_cmd in sub_commands:
        expanded = re.sub(pattern, rf"\g<1>{sub_cmd}\g<2>", mission_entry, count=1)
        entries.append(expanded)

    log.info(
        "GitHub: expanded combo /%s into %d sub-missions for %s",
        command_name, len(entries), project_name,
    )
    return entries


def validate_command(command_name: str, registry: SkillRegistry) -> Optional[object]:
    """Check if a command maps to a skill with github_enabled.

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


def get_github_enabled_commands(registry: SkillRegistry) -> List[str]:
    """Get list of command names that are github_enabled.

    Returns sorted, deduplicated list of primary command names.
    """
    commands = set()
    for skill in registry.list_all():
        if skill.github_enabled:
            for cmd in skill.commands:
                commands.add(cmd.name)
    return sorted(commands)


def get_github_enabled_commands_with_descriptions(
    registry: SkillRegistry,
) -> List[Tuple[str, str]]:
    """Get github-enabled commands with their descriptions.

    Returns sorted list of (command_name, description) tuples.
    Only includes primary command names (not aliases).
    """
    commands: dict = {}
    for skill in registry.list_all():
        if skill.github_enabled:
            for cmd in skill.commands:
                if cmd.name not in commands:
                    commands[cmd.name] = cmd.description or skill.description
    return sorted(commands.items())


# Group labels for the help message, keyed by SKILL.md ``group`` field.
#
# Order here controls section order in the rendered help. Core groups come
# first; ``integrations`` is last so custom third-party skills (e.g. the
# cPanel integration under ``instance/skills/cp/``) show up in a dedicated
# trailing block.
_GROUP_LABELS: Dict[str, str] = {
    "code": "Code & Development",
    "pr": "Pull Requests",
    "status": "Status & Info",
    "missions": "Missions",
    "config": "Configuration",
    "ideas": "Ideas & Planning",
    "system": "System",
    "integrations": "Integrations",
}


def _get_github_enabled_skills(registry: SkillRegistry) -> List[Tuple[str, "Skill"]]:
    """Collect github-enabled skills, deduplicated by primary command name.

    Returns a list of (primary_command_name, Skill) sorted by name.
    """
    from app.skills import Skill as _Skill  # noqa: F811 — local alias for type hint

    seen: Dict[str, object] = {}
    for skill in registry.list_all():
        if not skill.github_enabled:
            continue
        for cmd in skill.commands:
            if cmd.name not in seen:
                seen[cmd.name] = skill
    return sorted(seen.items(), key=lambda t: t[0])


def _format_command_line(
    cmd_name: str,
    skill,
    bot_username: str,
) -> str:
    """Format a single command entry for help output.

    Includes emoji, command, aliases, and description.
    """
    # Find the matching SkillCommand for alias info
    cmd_obj = None
    for c in skill.commands:
        if c.name == cmd_name:
            cmd_obj = c
            break

    emoji = skill.emoji or ""
    description = (cmd_obj.description if cmd_obj and cmd_obj.description else skill.description) or ""

    # Build alias hint
    aliases = ""
    if cmd_obj and cmd_obj.aliases:
        alias_str = ", ".join(f"`{a}`" for a in cmd_obj.aliases)
        aliases = f" (alias: {alias_str})"

    prefix = f"{emoji} " if emoji else ""
    return f"- {prefix}`@{bot_username} {cmd_name}`{aliases} — {description}"


def format_help_message(
    invalid_command: str,
    registry: SkillRegistry,
    bot_username: str,
) -> str:
    """Build a help message listing available GitHub commands.

    Args:
        invalid_command: The command that was not recognized.
        registry: Skills registry.
        bot_username: The bot's GitHub username (for usage examples).

    Returns:
        A formatted markdown help message for GitHub comments.
    """
    suggestion = registry.suggest_command(invalid_command)
    hint = f" Did you mean `{suggestion}`?" if suggestion else ""
    lines = [f"Unknown command `{invalid_command}`.{hint}\n"]
    lines.append(_build_grouped_command_list(registry, bot_username))
    lines.append(f"\nUsage: `@{bot_username} <command>` in any PR or issue comment.")
    return "\n".join(lines)


def _build_grouped_command_list(
    registry: SkillRegistry,
    bot_username: str,
) -> str:
    """Build a grouped command list for help output.

    Groups commands by their SKILL.md ``group`` field with section headers.
    Commands without a recognized group go under "Other".
    """
    entries = _get_github_enabled_skills(registry)

    # Bucket by group
    groups: Dict[str, List[str]] = {}
    for cmd_name, skill in entries:
        group = skill.group or "other"
        line = _format_command_line(cmd_name, skill, bot_username)
        groups.setdefault(group, []).append(line)

    # Render in a stable order: known groups first, then unknowns
    lines: List[str] = []
    for group_key, label in _GROUP_LABELS.items():
        if group_key not in groups:
            continue
        lines.append(f"### {label}")
        lines.extend(groups.pop(group_key))
        lines.append("")

    # Any remaining (unknown) groups
    for group_key in sorted(groups):
        label = group_key.replace("_", " ").title()
        lines.append(f"### {label}")
        lines.extend(groups[group_key])
        lines.append("")

    return "\n".join(lines).rstrip()


def format_help_list_message(
    registry: SkillRegistry,
    bot_username: str,
) -> str:
    """Build a clean help message listing available GitHub commands.

    Unlike format_help_message, this does NOT prefix with "Unknown command".
    Used when the user explicitly asks for help via ``@bot help``.

    Args:
        registry: Skills registry.
        bot_username: The bot's GitHub username (for usage examples).

    Returns:
        A formatted markdown help message for GitHub comments.
    """
    lines = ["Here are the commands I support:\n"]
    lines.append(_build_grouped_command_list(registry, bot_username))
    lines.append(f"\nℹ️ `@{bot_username} help` — Show this help message")
    lines.append(f"\nUsage: `@{bot_username} <command>` in any PR or issue comment.")
    return "\n".join(lines)


def _post_help_reply(
    owner: str,
    repo: str,
    issue_number: str,
    help_message: str,
) -> bool:
    """Post a help reply to a GitHub issue/PR comment thread.

    Args:
        owner: Repository owner.
        repo: Repository name.
        issue_number: Issue or PR number.
        help_message: The help message body.

    Returns:
        True if posted successfully.
    """
    from app.github import api, sanitize_github_comment

    try:
        api(
            f"repos/{owner}/{repo}/issues/{issue_number}/comments",
            method="POST",
            extra_args=["-f", f"body={sanitize_github_comment(help_message)}"],
        )
        return True
    except RuntimeError:
        log.warning("GitHub: failed to post help reply on %s/%s#%s", owner, repo, issue_number)
        return False


def _handle_help_command(
    notification: dict,
    comment: dict,
    registry: SkillRegistry,
    bot_username: str,
    owner: str,
    repo: str,
) -> bool:
    """Handle the built-in 'help' command — reply with available commands list.

    Posts a help comment, reacts with 👍, and marks notification as read.

    Args:
        notification: Notification dict.
        comment: Comment dict.
        registry: Skills registry.
        bot_username: Bot's GitHub username.
        owner: Repository owner.
        repo: Repository name.

    Returns:
        True if help was posted successfully.
    """
    issue_number = extract_issue_number_from_notification(notification)
    if not issue_number:
        log.debug("GitHub help: could not extract issue number")
        mark_notification_read(str(notification.get("id", "")))
        return False

    help_msg = format_help_list_message(registry, bot_username)
    if not _post_help_reply(owner, repo, issue_number, help_msg):
        mark_notification_read(str(notification.get("id", "")))
        return False

    # React and mark as read
    comment_id = str(comment.get("id", ""))
    comment_api_url = comment.get("url", "")
    add_reaction(owner, repo, comment_id, emoji="eyes",
                 comment_api_url=comment_api_url)
    mark_notification_read(str(notification.get("id", "")))

    log.info("GitHub: posted help reply on %s/%s#%s", owner, repo, issue_number)
    return True


def _resolve_project_from_url(url: str) -> Optional[str]:
    """Resolve project name from a GitHub URL's owner/repo.

    Parses the URL to extract owner and repo, then looks up the
    corresponding project. Returns the project name or None if the
    URL cannot be parsed or the repo is not a known project.
    """
    match = re.search(
        r'https?://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)',
        url,
    )
    if not match:
        return None

    owner, repo = match.group(1), match.group(2)

    from app.utils import project_name_for_path, resolve_project_path

    project_path = resolve_project_path(repo, owner=owner)
    if not project_path:
        return None

    return project_name_for_path(project_path)


def _extract_url_from_context(context: str) -> Optional[Tuple[str, str]]:
    """Extract URL from context text if present.
    
    Args:
        context: Context text that may contain a URL
        
    Returns:
        Tuple of (url, remaining_context) or None if no URL found
    """
    # Require /pull/N or /issues/N path — bare repo URLs must not match
    url_match = re.search(
        r'https?://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/(?:pull|issues)/\d+',
        context,
    )
    if not url_match:
        return None
    
    url = url_match.group(0)
    # Remove URL from context
    remaining = context[:url_match.start()].strip() + " " + context[url_match.end():].strip()
    remaining = remaining.strip()
    return url, remaining


def build_mission_from_command(
    skill,
    command_name: str,
    context: str,
    notification: dict,
    project_name: str,
    comment_url: Optional[str] = None,
) -> str:
    """Construct a mission string from a GitHub notification command.

    Args:
        skill: The Skill object.
        command_name: The command name (e.g., "rebase").
        context: Additional context text from the @mention.
        notification: The notification dict.
        project_name: The resolved project name.
        comment_url: Optional comment web URL. When set, overrides the
            subject URL and skips context (used by /ask to store only the
            comment URL, keeping missions.md free of raw question text).

    Returns:
        A mission entry string like "- [project:X] /command url context"
    """
    # When a comment URL is explicitly provided (e.g., for /ask), use it
    # directly and skip context — the question text lives on GitHub.
    if comment_url:
        mission_text = f"/{command_name} {comment_url}"
        return f"- [project:{project_name}] {mission_text} 📬"

    # Extract URL from notification subject
    subject_url = notification.get("subject", {}).get("url", "")
    web_url = api_url_to_web_url(subject_url) if subject_url else ""

    # Check if context contains a URL — if so, use that instead
    url_in_context = _extract_url_from_context(context)
    if url_in_context:
        web_url, context = url_in_context

        # Re-resolve project when context URL points to a different repo.
        # Without this, a command like "@bot plan <other-repo-url>" posted
        # on repo A would tag the mission with project A but the URL targets
        # repo B — causing the plan to run in the wrong project directory.
        resolved = _resolve_project_from_url(web_url)
        if resolved:
            project_name = resolved

    # Build mission text
    parts = [f"/{command_name}"]
    if web_url:
        parts.append(web_url)
    if context and skill.github_context_aware:
        parts.append(context)

    mission_text = " ".join(parts)
    # Trailing 📬 marks missions originating from GitHub @mentions.
    # The /list handler repositions it as a leading visual hint.
    return f"- [project:{project_name}] {mission_text} 📬"


def resolve_project_from_notification(notification: dict) -> Optional[Tuple[str, str, str]]:
    """Resolve project name from notification repository.

    Args:
        notification: A notification dict.

    Returns:
        Tuple of (project_name, owner, repo) or None if unknown.
    """
    repo_data = notification.get("repository", {})
    full_name = repo_data.get("full_name", "")
    if not full_name or "/" not in full_name:
        return None

    owner, repo = full_name.split("/", 1)

    from app.utils import project_name_for_path, resolve_project_path

    project_path = resolve_project_path(repo, owner=owner)
    if not project_path:
        return None

    project_name = project_name_for_path(project_path)
    return project_name, owner, repo


def _skip_if_foreign_repo(
    notification: dict, log_prefix: str,
) -> Optional[Tuple[str, str, str]]:
    """Resolve the project for ``notification`` or log a foreign-repo skip.

    Centralizes the resolve-or-log boilerplate that previously lived in
    ``process_single_notification``, ``_try_assignment_notification`` and
    ``_try_subscription_notification``. Callers decide what to return on
    a miss (``False``, ``(False, None)``, etc.) — this helper only does
    the resolution and the debug log.

    Args:
        notification: A notification dict.
        log_prefix: Short label included in the debug log so the source of
            the skip is visible in ``/logs`` (e.g. ``"GitHub"`` for the
            command path, ``"GitHub assign"`` for the assignment path).

    Returns:
        ``(project_name, owner, repo)`` when the repo is registered to
        this instance, ``None`` otherwise.
    """
    project_info = resolve_project_from_notification(notification)
    if project_info:
        return project_info
    repo_data = notification.get("repository", {})
    full_name = repo_data.get("full_name", "?")
    reason = notification.get("reason", "?")
    log.debug(
        "%s: repo %s (reason=%s) not in projects.yaml — ignoring notification",
        log_prefix, full_name, reason,
    )
    return None


def _fetch_and_filter_comment(notification: dict, bot_username: str, max_age_hours: int) -> Optional[dict]:
    """Fetch the triggering comment and check if notification should be skipped.

    Uses latest_comment_url as the fast path, but falls back to searching the
    full thread when the fast path fails (API error, self-mention, or stale URL
    pointing to a comment that doesn't mention the bot).

    Args:
        notification: Notification dict
        bot_username: Bot's GitHub username
        max_age_hours: Maximum age threshold

    Returns:
        The comment dict if notification should be processed, or None to skip.
    """
    thread_id = notification.get("id", "?")
    repo_name = notification.get("repository", {}).get("full_name", "?")

    # Check staleness
    if is_notification_stale(notification, max_age_hours):
        log.debug("GitHub: skipping notification %s from %s — stale (>%dh)", thread_id, repo_name, max_age_hours)
        mark_notification_read(str(notification.get("id", "")))
        return None

    # Fast path: fetch comment from latest_comment_url
    comment = get_comment_from_notification(notification)
    need_thread_search = False

    if not comment:
        # API failure or missing URL — don't give up yet, search the thread
        log.debug("GitHub: notification %s from %s — latest_comment_url failed, will search thread", thread_id, repo_name)
        need_thread_search = True
    elif is_self_mention(comment, bot_username):
        # latest_comment_url points to bot's own comment (race condition)
        log.debug(
            "GitHub: latest comment on %s is self-authored — searching thread for @mention",
            repo_name,
        )
        need_thread_search = True
    elif f"@{bot_username}".lower() not in (comment.get("body") or "").lower():
        # latest_comment_url shifted to a comment that doesn't mention the bot
        # (e.g., CI bot commented after the @mention, or PR body was returned)
        comment_author = comment.get("user", {}).get("login", "?")
        log.debug(
            "GitHub: latest comment on %s by @%s doesn't mention @%s — searching thread",
            repo_name, comment_author, bot_username,
        )
        need_thread_search = True
    else:
        comment_author = comment.get("user", {}).get("login", "?")
        log.debug("GitHub: notification %s from %s — comment by @%s", thread_id, repo_name, comment_author)

    if need_thread_search:
        mention_comment = find_mention_in_thread(notification, bot_username)
        if mention_comment:
            mention_author = mention_comment.get("user", {}).get("login", "?")
            log.debug(
                "GitHub: found unprocessed @mention by @%s in thread (latest_comment_url was stale)",
                mention_author,
            )
            return mention_comment

        log.debug("GitHub: no unprocessed @mention in thread — skipping notification %s", thread_id)
        mark_notification_read(str(notification.get("id", "")))
        return None

    return comment


def _validate_and_parse_command(
    notification: dict,
    comment: dict,
    config: dict,
    registry: SkillRegistry,
    bot_username: str,
    owner: str,
    repo: str,
) -> Tuple[Optional[object], Optional[str], str]:
    """Validate command and parse from comment.

    Args:
        notification: Notification dict
        comment: Comment dict
        config: Config dict
        registry: Skills registry
        bot_username: Bot's GitHub username
        owner: Repository owner
        repo: Repository name

    Returns:
        Tuple of (skill, command_name, context).
        skill is None if command is invalid or already processed.
        command_name is None if already processed/no valid mention.
    """
    comment_id = str(comment.get("id", ""))
    comment_api_url = comment.get("url", "")

    # Check if already processed
    if check_already_processed(comment_id, bot_username, owner, repo,
                                comment_api_url=comment_api_url):
        log.debug("GitHub: comment %s already processed", comment_id)
        mark_notification_read(str(notification.get("id", "")))
        return None, None, ""

    # Parse command from comment
    nickname = get_github_nickname(config)
    command_result = parse_mention_command(comment.get("body", ""), nickname)
    if not command_result:
        log.debug("GitHub: no valid @mention command in comment %s", comment_id)
        mark_notification_read(str(notification.get("id", "")))
        return None, None, ""

    command_name, context = command_result
    log.debug("GitHub: parsed command=%s context=%s from comment %s", command_name, context, comment_id)

    # Validate command
    skill = validate_command(command_name, registry)
    if not skill:
        log.debug("GitHub: command '%s' is not github-enabled", command_name)
        return None, command_name, context  # Invalid command, but we have the name for error message

    return skill, command_name, context


def _try_nlp_classification(
    comment: dict,
    config: dict,
    projects_config: Optional[dict],
    registry: SkillRegistry,
    bot_username: str,
    project_name: str,
    owner: str,
    repo: str,
) -> Optional[Tuple[object, str, str]]:
    """Attempt NLP intent classification for an unrecognized command.

    Only runs when natural_language is enabled in config. Calls Claude
    to classify the comment text into a known github-enabled command.

    Args:
        comment: Comment dict.
        config: Global config.
        projects_config: Projects config.
        registry: Skills registry.
        bot_username: Bot's GitHub username.
        project_name: Resolved project name.
        owner: Repository owner.
        repo: Repository name.

    Returns:
        Tuple of (skill, command_name, context) if classification succeeded,
        or None if NLP is disabled, failed, or returned no match.
    """
    if not get_github_natural_language(config, project_name, projects_config):
        return None

    # Resolve project path for Claude CLI
    from app.utils import resolve_project_path
    project_path = resolve_project_path(repo, owner=owner)
    if not project_path:
        log.debug("GitHub NLP: could not resolve project path for %s/%s", owner, repo)
        return None

    # Get available commands for the classifier
    commands = get_github_enabled_commands_with_descriptions(registry)
    if not commands:
        return None

    # Extract the full comment text (after @mention, code blocks stripped)
    nickname = get_github_nickname(config)
    from app.github_reply import extract_mention_text
    message = extract_mention_text(comment.get("body", ""), nickname)
    if not message:
        return None

    from app.github_intent import classify_intent

    log.debug("GitHub NLP: classifying intent for: %s", message[:100])
    result = classify_intent(message, commands, project_path)

    if not result or not result.get("command"):
        log.debug("GitHub NLP: no command classified")
        return None

    classified_command = result["command"]
    classified_context = result.get("context", "")

    # Validate the classified command is actually github_enabled
    skill = validate_command(classified_command, registry)
    if not skill:
        log.debug(
            "GitHub NLP: classified command '%s' is not github-enabled",
            classified_command,
        )
        return None

    log.info(
        "GitHub NLP: classified '%s' as /%s for %s/%s",
        message[:80], classified_command, owner, repo,
    )
    return skill, classified_command, classified_context


def _try_reply(
    notification: dict,
    comment: dict,
    config: dict,
    projects_config: Optional[dict],
    bot_username: str,
    owner: str,
    repo: str,
    project_name: str,
    question_text: str,
) -> bool:
    """Attempt to generate and post an AI reply for a non-command @mention.

    Checks reply_enabled config and user permissions before generating.

    Args:
        notification: Notification dict.
        comment: Comment dict.
        config: Global config.
        projects_config: Projects config.
        bot_username: Bot's GitHub username.
        owner: Repository owner.
        repo: Repository name.
        project_name: Resolved project name.
        question_text: The user's question/request text.

    Returns:
        True if reply was generated and posted successfully.
    """
    if not get_github_reply_enabled(config):
        return False

    comment_author = comment.get("user", {}).get("login", "")
    comment_id = str(comment.get("id", ""))

    # Check permissions — use reply_authorized_users if configured, else authorized_users
    reply_users = get_github_reply_authorized_users(config, project_name, projects_config)
    if reply_users is None:
        reply_users = get_github_authorized_users(config, project_name, projects_config)

    if not check_user_permission(owner, repo, comment_author, reply_users):
        log.debug(
            "GitHub reply: permission denied for @%s on %s/%s",
            comment_author, owner, repo,
        )
        return False

    # Rate limit: prevent API quota abuse from broad reply permissions.
    # State persisted to disk so limits survive process restarts.
    koan_root = os.environ.get("KOAN_ROOT", "")
    instance_dir = os.path.join(koan_root, "instance") if koan_root else ""

    rate_limit = get_github_reply_rate_limit(config)
    if instance_dir:
        all_timestamps = _load_reply_timestamps(instance_dir)
    else:
        all_timestamps = {}
    user_timestamps = all_timestamps.get(comment_author, [])
    if len(user_timestamps) >= rate_limit:
        log.warning(
            "GitHub reply: rate limit (%d/h) exceeded for @%s on %s/%s",
            rate_limit, comment_author, owner, repo,
        )
        return False

    # Extract issue number for the thread
    issue_number = extract_issue_number_from_notification(notification)
    if not issue_number:
        log.debug("GitHub reply: could not extract issue number from notification")
        return False

    # Resolve project path for Claude CLI
    from app.utils import resolve_project_path
    project_path = resolve_project_path(repo, owner=owner)
    if not project_path:
        log.debug("GitHub reply: could not resolve project path for %s/%s", owner, repo)
        return False

    log.info(
        "GitHub reply: generating reply for @%s on %s/%s#%s",
        comment_author, owner, repo, issue_number,
    )

    # Notify on Telegram: question received from GitHub
    _notify_github_question(
        comment_author, owner, repo, issue_number, question_text,
    )

    from app.github_reply import (
        fetch_thread_context,
        generate_reply,
        post_threaded_reply,
    )

    # Fetch context and generate reply (exclude bot's own comments to avoid self-reply)
    thread_context = fetch_thread_context(owner, repo, issue_number, bot_username=bot_username)
    reply_text = generate_reply(
        question=question_text,
        thread_context=thread_context,
        owner=owner,
        repo=repo,
        issue_number=issue_number,
        comment_author=comment_author,
        project_path=project_path,
    )

    if not reply_text:
        log.warning("GitHub reply: failed to generate reply for comment %s", comment_id)
        return False

    # Post reply threaded to the original comment
    comment_api_url = comment.get("url", "")
    comment_body = comment.get("body", "")
    if not post_threaded_reply(
        owner, repo, issue_number, reply_text,
        comment_api_url=comment_api_url,
        comment_id=comment_id,
        comment_author=comment_author,
        comment_body=comment_body,
    ):
        log.warning("GitHub reply: failed to post reply for comment %s", comment_id)
        return False

    # Mark as processed (comment_api_url already set above)
    add_reaction(owner, repo, comment_id, emoji="eyes",
                 comment_api_url=comment_api_url)
    mark_notification_read(str(notification.get("id", "")))

    # Notify on Telegram: reply posted to GitHub
    _notify_github_reply(
        owner, repo, issue_number, reply_text,
    )

    # Record successful reply for rate limiting (persist to disk)
    if instance_dir:
        all_timestamps = _load_reply_timestamps(instance_dir)
        all_timestamps.setdefault(comment_author, []).append(time.time())
        _save_reply_timestamps(instance_dir, all_timestamps)

    log.info("GitHub reply: posted reply to @%s on %s/%s#%s", comment_author, owner, repo, issue_number)
    return True


# Mapping from notification reason to the command to queue.
# These are "implicit command" notifications — no @mention comment needed.
_ASSIGNMENT_REASON_TO_COMMAND = {
    "review_requested": "review",
    "assign": "implement",
}


def _is_bot_still_requested(owner: str, repo: str, pr_number: str, bot_username: str) -> bool:
    """Check if the bot is still in the PR's requested reviewers list.

    Returns False on API errors (fail-closed: keep cooldown active).
    """
    if not bot_username:
        return False
    try:
        from app.github import api as gh_api

        raw = gh_api(
            f"repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers",
            jq="[.users[].login, .teams[].slug] | .[]",
            timeout=10,
        )
        reviewers = [r.strip().lower() for r in raw.strip().splitlines() if r.strip()]
        return bot_username.lower() in reviewers
    except Exception as exc:
        log.debug("requested_reviewers check failed for %s/%s#%s: %s", owner, repo, pr_number, exc)
        return False


def _try_assignment_notification(
    notification: dict,
    registry: SkillRegistry,
    config: dict,
) -> bool:
    """Handle assignment-based notifications (review_requested, assign).

    When the bot is assigned as a PR reviewer or assigned to an issue,
    queue the appropriate mission without requiring an @mention comment.

    - review_requested → /review <PR URL>
    - assign → /implement <issue URL>

    Returns True if the notification was handled (queued or deduplicated/no-op).
    """
    import os
    from pathlib import Path

    reason = notification.get("reason", "")
    command_name = _ASSIGNMENT_REASON_TO_COMMAND.get(reason)
    if not command_name:
        return False

    notif_id = str(notification.get("id", ""))
    koan_root = os.environ.get("KOAN_ROOT", "")
    instance_dir = str(Path(koan_root) / "instance") if koan_root else ""

    from app.github_notification_tracker import (
        is_review_on_cooldown,
        is_thread_tracked,
        set_review_cooldown,
        track_thread,
    )

    # Fast path for `assign` (issues have no head SHA): dedup on notif_id
    # alone, which needs no API call, so short-circuit before any fetch.
    # updated_at is deliberately excluded — comments on the issue bump it,
    # and we must not re-trigger /implement on every comment.
    if reason == "assign" and instance_dir and notif_id and is_thread_tracked(
        instance_dir, notif_id,
    ):
        log.debug("GitHub assign: notification %s already tracked, skipping", notif_id)
        mark_notification_read(notif_id)
        notification[NOTIFICATION_OUTCOME_KEY] = NOTIFICATION_OUTCOME_HANDLED_NOOP
        return True

    # Validate the command is registered and github_enabled
    skill = validate_command(command_name, registry)
    if not skill:
        log.debug(
            "GitHub assign: command '%s' not github_enabled, skipping %s notification",
            command_name, reason,
        )
        return False

    # Check staleness
    if is_notification_stale(notification):
        log.debug("GitHub assign: skipping stale %s notification", reason)
        mark_notification_read(notif_id)
        return False

    # Foreign-repo skip: never write to shared GitHub state for a repo this
    # instance doesn't own (would clear the notification from a sibling
    # Kōan instance's inbox). The outer ownership gate already filters most
    # of these out — this is defense in depth.
    project_info = _skip_if_foreign_repo(notification, "GitHub assign")
    if not project_info:
        return False

    project_name, owner, repo = project_info

    # One API call: subject state/merged (closed check) + head SHA (dedup key).
    #
    # Performance trade-off: for `review_requested`, this fetch runs on every
    # poll of an already-tracked PR (unlike `assign`, which short-circuits on
    # notif_id before any fetch). The cost was evaluated and accepted because
    # the head SHA is required for the dedup key — without it, we'd re-queue
    # /review on every comment that bumps `updated_at`. If GitHub API rate
    # pressure becomes an issue, a local LRU keyed on (notif_id, updated_at)
    # could fast-path the unchanged-since-last-poll case.
    subject_info = _fetch_subject_info(notification)

    # Persistent dedup key — survives restart, unlike the in-memory loop cache.
    #
    # review_requested → key on the PR head SHA so a re-review fires only when
    #   new commits land. The previous key embedded updated_at, but ANY thread
    #   activity bumps updated_at — including the bot's own posted review and
    #   CI-bot comments — yielding a fresh key every poll and re-queuing
    #   /review in an infinite loop. The head SHA changes only with new code.
    # assign / unknown SHA → notif_id alone. Falling back to notif_id when the
    #   head SHA is unavailable loses new-commit re-review for that poll but
    #   never produces a duplicate. An empty notif_id makes the key useless, so
    #   tracking is skipped entirely in that case.
    head_sha = str(subject_info.get("head_sha") or "")
    if not notif_id:
        thread_key = ""
    elif reason == "review_requested" and head_sha:
        thread_key = f"{notif_id}:{head_sha}"
    else:
        thread_key = notif_id

    if instance_dir and thread_key and is_thread_tracked(instance_dir, thread_key):
        log.debug(
            "GitHub assign: %s notification %s already tracked, skipping",
            reason, thread_key,
        )
        mark_notification_read(notif_id)
        notification[NOTIFICATION_OUTCOME_KEY] = NOTIFICATION_OUTCOME_HANDLED_NOOP
        return True

    # Review cooldown — belt-and-suspenders guard against re-review loops
    # when the thread tracker misses a renewed request (same PR, new notif).
    if reason == "review_requested" and instance_dir:
        pr_number = extract_issue_number_from_notification(notification)
        if pr_number and is_review_on_cooldown(instance_dir, owner, repo, pr_number):
            # Cooldown active — check if a human explicitly re-requested.
            # GitHub removes the bot from requested_reviewers after it
            # submits its review; presence means a human clicked Refresh.
            from app.github_config import get_github_nickname

            bot_username = get_github_nickname(config)
            if bot_username and _is_bot_still_requested(owner, repo, pr_number, bot_username):
                log.info(
                    "GitHub assign: review cooldown bypassed for %s/%s#%s "
                    "(bot still in requested_reviewers — human re-request)",
                    owner, repo, pr_number,
                )
                from app.github_notification_tracker import clear_review_cooldown

                clear_review_cooldown(instance_dir, owner, repo, pr_number)
            else:
                log.debug(
                    "GitHub assign: review for %s/%s#%s is on cooldown, skipping",
                    owner, repo, pr_number,
                )
                mark_notification_read(notif_id)
                notification[NOTIFICATION_OUTCOME_KEY] = NOTIFICATION_OUTCOME_HANDLED_NOOP
                return True

    # Skip closed/merged subjects (reuse the already-fetched subject_info)
    subject_state = _closed_reason_from_subject_info(subject_info)
    if subject_state:
        subject_title = notification.get("subject", {}).get("title", "?")
        log.info(
            "GitHub assign: skipping %s notification on %s subject: %s/%s — %s",
            reason, subject_state, owner, repo, subject_title,
        )
        _notify_closed_subject_skipped(
            owner, repo, subject_title, subject_state, notification,
        )
        mark_notification_read(notif_id)
        return False

    # Build web URL from subject
    subject_url = notification.get("subject", {}).get("url", "")
    web_url = api_url_to_web_url(subject_url) if subject_url else ""
    if not web_url:
        log.debug("GitHub assign: no subject URL in %s notification", reason)
        mark_notification_read(notif_id)
        return False

    if not koan_root:
        log.error("GitHub assign: KOAN_ROOT not set")
        return False

    from app.utils import insert_pending_mission

    assign_instance_dir = Path(koan_root) / "instance"

    # Deduplicate: skip if a mission for the same URL is already pending
    # or in progress.  The in-progress check prevents re-queuing while a
    # review is still running (e.g., a rebase pushes new commits mid-review).
    try:
        from app.mission_store import MissionStore

        store = MissionStore.load()
        active = store.get_by_status("pending") + store.get_by_status("in_progress")
        url_lower = web_url.lower()
        for record in active:
            if url_lower in record.text.lower():
                log.debug(
                    "GitHub assign: mission for %s already active, skipping",
                    web_url,
                )
                mark_notification_read(notif_id)
                if instance_dir and thread_key:
                    track_thread(instance_dir, thread_key)
                notification[NOTIFICATION_OUTCOME_KEY] = NOTIFICATION_OUTCOME_HANDLED_NOOP
                return True  # Already handled — not an error
    except OSError:
        pass  # If we can't read, proceed with insertion (worst case: a dup)

    # Build and insert mission
    mission_text = f"/{command_name} {web_url} 📬"
    log.info(
        "GitHub assign: queuing /%s from %s notification on %s/%s",
        command_name, reason, owner, repo,
    )

    try:
        inserted = insert_pending_mission(mission_text, project_name)
    except OSError as e:
        log.warning("GitHub assign: failed to insert mission: %s", e)
        mark_notification_read(notif_id)
        return False

    mark_notification_read(notif_id)
    if instance_dir and thread_key:
        track_thread(instance_dir, thread_key)
    if reason == "review_requested" and instance_dir:
        pr_number = extract_issue_number_from_notification(notification)
        if pr_number:
            set_review_cooldown(instance_dir, owner, repo, pr_number)
    notification[NOTIFICATION_OUTCOME_KEY] = (
        NOTIFICATION_OUTCOME_QUEUED if inserted else NOTIFICATION_OUTCOME_HANDLED_NOOP
    )
    return True


def _find_all_thread_mentions(
    notification: dict,
    bot_username: str,
    max_age_hours: int = 24,
) -> List[dict]:
    """Find all unprocessed @mention comments for a notification's thread.

    Searches the full thread (issue comments + PR review comments) for
    every unprocessed @mention of the bot, sorted by created_at ascending
    (oldest first).  This ensures that when a user posts multiple commands
    (e.g. ``@bot review`` then ``@bot rebase``), all of them are queued in
    the order they were posted.

    Falls back to the direct ``latest_comment_url`` when the full thread
    search returns nothing (e.g. unparseable subject URL).

    Returns an empty list when the notification is stale or no unprocessed
    mentions are found.
    """
    thread_id = notification.get("id", "?")
    repo_name = notification.get("repository", {}).get("full_name", "?")

    if is_notification_stale(notification, max_age_hours):
        log.debug(
            "GitHub: skipping notification %s from %s — stale (>%dh)",
            thread_id, repo_name, max_age_hours,
        )
        mark_notification_read(str(notification.get("id", "")))
        return []

    mentions = find_all_mentions_in_thread(notification, bot_username)
    if mentions:
        if len(mentions) > 1:
            log.info(
                "GitHub: found %d unprocessed @mentions in thread %s from %s",
                len(mentions), thread_id, repo_name,
            )
        return mentions

    # Fallback: try direct comment URL for edge cases where the subject
    # URL doesn't parse as a standard issues/pulls URL.
    comment = get_comment_from_notification(notification)
    if comment and not is_self_mention(comment, bot_username):
        body = (comment.get("body") or "").lower()
        if f"@{bot_username}".lower() in body:
            comment_id = str(comment.get("id", ""))
            comment_api_url = comment.get("url", "")
            # Extract owner/repo for already-processed check
            repo_data = notification.get("repository", {})
            full_name = repo_data.get("full_name", "")
            if "/" not in full_name:
                log.warning(
                    "GitHub: malformed repository.full_name %r in "
                    "notification %s — skipping fallback comment check",
                    full_name, thread_id,
                )
            else:
                c_owner, c_repo = full_name.split("/", 1)
                if not check_already_processed(
                    comment_id, bot_username, c_owner, c_repo,
                    comment_api_url=comment_api_url,
                ):
                    return [comment]

    log.debug(
        "GitHub: no unprocessed @mentions in thread — "
        "skipping notification %s from %s",
        thread_id, repo_name,
    )
    return []


def _process_mention_comment(
    notification: dict,
    comment: dict,
    registry: SkillRegistry,
    config: dict,
    projects_config: Optional[dict],
    bot_username: str,
    project_name: str,
    owner: str,
    repo: str,
) -> Tuple[bool, Optional[str]]:
    """Process a single @mention comment from a notification thread.

    Per-comment logic extracted from process_single_notification.
    Handles command validation, NLP classification, permission checks,
    mission building, insertion, reactions, and acknowledgments.

    Returns:
        Tuple of (queued, error_message).  queued is True when a mission
        was successfully inserted into missions.md.
    """
    comment_author = comment.get("user", {}).get("login", "")

    # Validate and parse command
    skill, command_name, context = _validate_and_parse_command(
        notification, comment, config, registry, bot_username, owner, repo,
    )

    # If command_name is None, already processed or no valid mention
    if command_name is None:
        return False, None

    # Built-in "help" command — reply with available commands list
    if skill is None and command_name == "help":
        _handle_help_command(
            notification, comment, registry, bot_username, owner, repo,
        )
        return False, None

    # If skill is None but we have a command_name, it's an invalid command
    if skill is None:
        nlp_enabled = get_github_natural_language(
            config, project_name, projects_config,
        )

        if nlp_enabled:
            gh_request_skill = validate_command("gh_request", registry)
            if gh_request_skill:
                nickname = get_github_nickname(config)
                from app.github_reply import extract_mention_text
                full_text = extract_mention_text(comment.get("body", ""), nickname)
                if full_text:
                    skill = gh_request_skill
                    command_name = "gh_request"
                    context = full_text
                    log.info(
                        "GitHub NLP: routing to /gh_request for %s/%s: %s",
                        owner, repo, full_text[:80],
                    )
        else:
            nlp_result = _try_nlp_classification(
                comment, config, projects_config, registry,
                bot_username, project_name, owner, repo,
            )
            if nlp_result:
                nlp_skill, nlp_command, nlp_context = nlp_result
                skill = nlp_skill
                command_name = nlp_command
                context = nlp_context

    # If still no skill after NLP, fall through to reply/error
    if skill is None and command_name is not None and command_name != "help":
        full_question = f"{command_name} {context}".strip()
        if _try_reply(
            notification, comment, config, projects_config,
            bot_username, owner, repo, project_name, full_question,
        ):
            return False, None
        help_msg = format_help_message(command_name, registry, bot_username)
        return False, help_msg

    # Check permissions
    allowed_users = get_github_authorized_users(config, project_name, projects_config)
    if not check_user_permission(owner, repo, comment_author, allowed_users):
        log.debug(
            "GitHub: permission denied for @%s on %s/%s (allowed: %s)",
            comment_author, owner, repo,
            ", ".join(allowed_users) if allowed_users else "none",
        )
        return False, "Permission denied. Only users with write access can trigger bot commands."

    # Scan context text for prompt injection
    if context and context.strip():
        from app.prompt_guard import scan_mission_text
        from app.config import get_prompt_guard_config

        guard_config = get_prompt_guard_config()
        if guard_config["enabled"]:
            guard_result = scan_mission_text(context)
            if guard_result.blocked:
                log.warning(
                    "GitHub: prompt guard flagged @%s context: %s | %s",
                    comment_author, guard_result.reason, context[:100],
                )
                _quarantine_github_mission(
                    context, guard_result.reason, comment_author,
                )
                if guard_config["block_mode"]:
                    return False, f"Mission blocked by prompt guard: {guard_result.reason}"

    # Custom in-process dispatch
    from app.external_skill_dispatch import try_dispatch_custom_handler

    subject = notification.get("subject", {}) or {}
    subject_title = subject.get("title", "") or ""

    inline_reply = try_dispatch_custom_handler(
        skill,
        command_name,
        context,
        source="github",
        github_title=subject_title,
        github_body=comment.get("body", "") or "",
    )

    if inline_reply is not None:
        comment_id = str(comment.get("id", ""))
        comment_api_url = comment.get("url", "")
        add_reaction(owner, repo, comment_id, comment_api_url=comment_api_url)

        from app.github_notification_tracker import track_comment
        from pathlib import Path as _Path
        import os as _os

        koan_root = _os.environ.get("KOAN_ROOT", "")
        if koan_root:
            instance_dir = str(_Path(koan_root) / "instance")
            track_comment(instance_dir, comment_id)

        notification.setdefault("_koan_commands", []).append(
            {"command": command_name, "author": comment_author},
        )
        notification["_koan_command"] = command_name
        notification["_koan_author"] = comment_author

        log.info(
            "GitHub: dispatched custom handler %s from @%s (reply=%r)",
            skill.qualified_name, comment_author, (inline_reply or "")[:80],
        )
        if inline_reply and get_github_ack_enabled(config):
            inline_issue_number = extract_issue_number_from_notification(notification)
            if inline_issue_number:
                from app.github_reply import post_threaded_reply
                posted = post_threaded_reply(
                    owner, repo, inline_issue_number,
                    f"🤖 {inline_reply}",
                    comment_api_url=comment_api_url,
                    comment_id=comment_id,
                    comment_author=comment_author,
                    comment_body=comment.get("body", ""),
                )
                if not posted:
                    log.info("GitHub: failed to post inline handler ack for %s", skill.qualified_name)
        return True, None

    # Build and insert mission
    ask_comment_url = None
    if command_name == "ask":
        ask_comment_url = comment.get("html_url") or None
    from app.missions import extract_now_flag
    urgent = False
    if context:
        urgent, context = extract_now_flag(context)

    mission_entry = build_mission_from_command(
        skill, command_name, context, notification, project_name,
        comment_url=ask_comment_url,
    )
    if urgent:
        log.info("GitHub: priority insertion (--now) from @%s: %s", comment_author, mission_entry)
    else:
        log.info("GitHub: inserting mission from @%s: %s", comment_author, mission_entry)

    from app.utils import insert_pending_mission
    from pathlib import Path
    import os

    koan_root = os.environ.get("KOAN_ROOT", "")
    if not koan_root:
        log.error("GitHub: KOAN_ROOT not set — cannot insert mission")
        return False, "KOAN_ROOT not configured"
    notif_instance_dir = Path(koan_root) / "instance"

    mission_entries = _expand_combo_mission(
        command_name, mission_entry, project_name,
    )

    inserted_any = False
    try:
        from app.utils import parse_project
        for entry in mission_entries:
            proj, text = parse_project(entry)
            text = text.removeprefix("- ")
            inserted_any = insert_pending_mission(
                text, proj or project_name or "", urgent=urgent,
            ) or inserted_any
    except OSError as e:
        log.warning("GitHub: failed to insert mission: %s", e)
        return False, f"Failed to queue mission: {e}"

    # React AFTER mission is persisted (marks as processed)
    comment_id = str(comment.get("id", ""))
    comment_api_url = comment.get("url", "")
    add_reaction(owner, repo, comment_id, comment_api_url=comment_api_url)

    from app.github_notification_tracker import set_review_cooldown, track_comment
    instance_dir = str(Path(koan_root) / "instance")
    track_comment(instance_dir, comment_id)

    if command_name == "review" and inserted_any and instance_dir:
        pr_number = extract_issue_number_from_notification(notification)
        if pr_number:
            set_review_cooldown(instance_dir, owner, repo, pr_number)

    notification.setdefault("_koan_commands", []).append(
        {"command": command_name, "author": comment_author},
    )
    notification["_koan_command"] = command_name
    notification["_koan_author"] = comment_author

    if inserted_any and command_name != "ask" and get_github_ack_enabled(config):
        ack_issue_number = extract_issue_number_from_notification(notification)
        if ack_issue_number:
            _post_command_acknowledgment(
                owner, repo, ack_issue_number,
                command_name, comment, bot_username,
            )

    if inserted_any:
        log.info("GitHub: created mission from @%s: %s", comment_author, command_name)
    else:
        log.debug("GitHub: mission already pending for @%s: %s", comment_author, command_name)
    return inserted_any, None


def process_single_notification(
    notification: dict,
    registry: SkillRegistry,
    config: dict,
    projects_config: Optional[dict],
    bot_username: str,
    max_age_hours: int = 24,
) -> Tuple[bool, Optional[str]]:
    """Process a single GitHub notification.

    Finds ALL unprocessed @mention comments in the notification's thread
    and processes them in chronological order (oldest first).  This ensures
    that when a user posts multiple commands (e.g. ``@bot review`` then
    ``@bot rebase``), every command is queued — not just the last one.

    Falls back to assignment (review_requested, assign) and subscription
    paths when no @mention comments are found.

    Args:
        notification: A notification dict from GitHub API.
        registry: Skills registry.
        config: Global config (from config.yaml).
        projects_config: Projects config (from projects.yaml), or None.
        bot_username: The bot's GitHub username.
        max_age_hours: Max notification age in hours.

    Returns:
        Tuple of (success, error_message). error_message is None on success.
    """
    notification[NOTIFICATION_OUTCOME_KEY] = NOTIFICATION_OUTCOME_HANDLED_NOOP

    # Find ALL unprocessed @mentions in the thread (sorted oldest first).
    # Staleness check is handled inside _find_all_thread_mentions.
    comments = _find_all_thread_mentions(notification, bot_username, max_age_hours)

    if not comments:
        # No @mention found — try assignment path (review_requested, assign)
        if _try_assignment_notification(
            notification, registry, config,
        ):
            return True, None
        # Try subscription path for subscribed/author notifications
        if _try_subscription_notification(
            notification, config, projects_config, bot_username,
        ):
            mark_notification_read(str(notification.get("id", "")))
            return True, None
        return False, None

    # Shared per-notification checks
    project_info = _skip_if_foreign_repo(notification, "GitHub")
    if not project_info:
        return False, None
    project_name, owner, repo = project_info
    log.debug("GitHub: resolved project=%s from %s/%s", project_name, owner, repo)

    # Skip closed/merged subjects
    subject_state = _is_subject_closed(notification)
    if subject_state:
        subject_title = notification.get("subject", {}).get("title", "?")
        log.info(
            "GitHub: skipping notification on %s subject: %s/%s — %s",
            subject_state, owner, repo, subject_title,
        )
        _notify_closed_subject_skipped(
            owner, repo, subject_title, subject_state, notification,
        )
        closed_koan_root = os.environ.get("KOAN_ROOT", "")
        closed_instance_dir = (
            os.path.join(closed_koan_root, "instance") if closed_koan_root else ""
        )
        for c in comments:
            c_id = str(c.get("id", ""))
            add_reaction(
                owner, repo, c_id, emoji="eyes",
                comment_api_url=c.get("url", ""),
            )
            # Durable backstop so a failed reaction doesn't re-loop the
            # closed-subject notification on the next poll.
            if closed_instance_dir:
                from app.github_notification_tracker import track_comment
                track_comment(closed_instance_dir, c_id)
        mark_notification_read(str(notification.get("id", "")))
        return False, None

    # Persistent dedup directory (best-effort; no-op without KOAN_ROOT).
    koan_root = os.environ.get("KOAN_ROOT", "")
    instance_dir = os.path.join(koan_root, "instance") if koan_root else ""

    from app.github_reply import _enforce_reply_budget

    # Process each comment in chronological order
    any_queued = False
    last_error = None
    for comment in comments:
        queued, error = _process_mention_comment(
            notification, comment, registry, config, projects_config,
            bot_username, project_name, owner, repo,
        )
        if queued:
            any_queued = True
        if error:
            last_error = error
            # Post error reply to the specific comment that caused it,
            # subject to the per-thread reply circuit breaker.
            #
            # Single-comment notifications are delegated to the caller
            # (loop_manager._post_error_for_notification) via the returned
            # ``last_error`` below — that path posts the same error with
            # retry-on-failure. Posting inline here too would double-post
            # the reply (and double-spend the breaker budget). So only post
            # inline for the *extra* comments of a multi-mention thread,
            # which the single-error return path does not cover.
            issue_number = extract_issue_number_from_notification(notification)
            comment_id = str(comment.get("id", ""))
            if len(comments) > 1 and issue_number and comment_id:
                if _enforce_reply_budget(owner, repo, issue_number):
                    post_error_reply(
                        owner, repo, issue_number, comment_id, error,
                        comment_api_url=comment.get("url", ""),
                    )
                else:
                    log.info(
                        "GitHub: error reply suppressed by circuit breaker for "
                        "%s/%s#%s comment %s: %s",
                        owner, repo, issue_number, comment_id, error,
                    )

        # Persistently mark every handled comment processed — regardless of
        # outcome (queued, error, help, permission-denied, no-op). The
        # per-path reaction is volatile (depends on the reactions API and a
        # correctly-configured bot_username); this local tracker is the
        # durable backstop that stops the full-thread rescan from
        # re-discovering and re-replying to the same comment every poll.
        if instance_dir:
            from app.github_notification_tracker import track_comment
            track_comment(instance_dir, str(comment.get("id", "")))

    mark_notification_read(str(notification.get("id", "")))

    if any_queued:
        notification[NOTIFICATION_OUTCOME_KEY] = NOTIFICATION_OUTCOME_QUEUED

    # Return success=True if any comment was queued, or if no errors occurred
    # (e.g. all comments were help requests or already processed).
    # Only return an error when there was exactly one comment with an error
    # and no queued missions — this preserves backward-compatible error
    # posting for the single-comment case via the caller in loop_manager.
    if any_queued:
        return True, None
    if last_error and len(comments) == 1:
        return False, last_error
    return False, None


def _post_command_acknowledgment(
    owner: str,
    repo: str,
    issue_number: str,
    command_name: str,
    comment: dict,
    bot_username: str,
) -> None:
    """Post a brief acknowledgment reply when a command is queued.

    Threads the reply to the original comment when possible (PR review
    comments get native threading, issue comments get a blockquote).
    """
    from app.github_reply import post_threaded_reply

    if command_name == "gh_request":
        ack_body = "🤖 Got it — I'll look into this shortly."
    else:
        ack_body = f"🤖 `/{command_name}` queued — I'll get to it shortly."

    comment_id = str(comment.get("id", ""))
    comment_api_url = comment.get("url", "")
    comment_author = comment.get("user", {}).get("login", "")
    comment_body = comment.get("body", "")

    posted = post_threaded_reply(
        owner, repo, issue_number, ack_body,
        comment_api_url=comment_api_url,
        comment_id=comment_id,
        comment_author=comment_author,
        comment_body=comment_body,
    )
    if not posted:
        log.info("GitHub: failed to post command ack for /%s", command_name)


def post_error_reply(
    owner: str,
    repo: str,
    issue_number: str,
    comment_id: str,
    error_message: str,
    comment_api_url: str = "",
) -> bool:
    """Post an error reply to a GitHub comment.

    Includes deduplication — won't post the same error twice for the same comment.

    Args:
        owner: Repository owner.
        repo: Repository name.
        issue_number: Issue or PR number.
        comment_id: The triggering comment ID.
        error_message: The error message to post.
        comment_api_url: The comment's canonical API URL for correct
            reactions endpoint (handles PR review comments, etc.).

    Returns:
        True if posted successfully.
    """
    # Deduplication key
    error_key = f"{comment_id}:{error_message}"
    if error_key in _error_replies:
        return False

    from app.github import api, sanitize_github_comment

    body = sanitize_github_comment(f"❌ {error_message}")
    try:
        api(
            f"repos/{owner}/{repo}/issues/{issue_number}/comments",
            method="POST",
            extra_args=["-f", f"body={body}"],
        )

        # Add reaction to mark as processed — only suppress future
        # retries if the reaction was actually placed.
        reacted = add_reaction(owner, repo, comment_id,
                               comment_api_url=comment_api_url)
        if reacted:
            _error_replies.add(error_key)
        return True
    except RuntimeError:
        return False


def _fetch_new_comments_since(
    owner: str,
    repo: str,
    issue_number: str,
    since_comment_id: Optional[int],
    bot_username: str,
) -> List[dict]:
    """Fetch comments on a thread that are newer than since_comment_id.

    Filters out comments from the bot itself to avoid self-reply loops.

    Returns:
        List of comment dicts from other users, newest last.
    """
    import json as json

    from app.github import api as gh_api

    try:
        raw = gh_api(
            f"repos/{owner}/{repo}/issues/{issue_number}/comments",
            jq='[.[] | {id: .id, body: .body, user_login: .user.login}]',
        )
        comments = json.loads(raw) if raw else []
    except (RuntimeError, ValueError):
        return []

    if not isinstance(comments, list):
        return []

    # Filter: only comments after since_comment_id, not from the bot
    result = []
    for c in comments:
        cid = c.get("id", 0)
        author = c.get("user_login", "")
        if author.lower() == bot_username.lower():
            continue
        if since_comment_id is not None and cid <= since_comment_id:
            continue
        result.append(c)

    return result


def _try_subscription_notification(
    notification: dict,
    config: dict,
    projects_config: Optional[dict],
    bot_username: str,
) -> bool:
    """Handle a subscription/author notification by queuing a /reply mission.

    Called when:
    - subscribe_enabled is True
    - notification reason is 'subscribed' or 'author'
    - no @mention was found (standard command path returned None)

    Returns True if the notification was handled and /reply is already pending
    or newly queued.
    """
    import os
    from pathlib import Path

    reason = notification.get("reason", "")
    if reason not in ("subscribed", "author"):
        return False

    if not get_github_subscribe_enabled(config):
        return False

    # Foreign-repo skip (defense in depth — outer gate filters most of these).
    project_info = _skip_if_foreign_repo(notification, "GitHub subscribe")
    if not project_info:
        return False

    project_name, owner, repo = project_info
    issue_number = extract_issue_number_from_notification(notification)
    if not issue_number:
        return False

    koan_root = os.environ.get("KOAN_ROOT", "")
    if not koan_root:
        return False
    instance_dir = Path(koan_root) / "instance"

    from app.thread_subscriptions import (
        get_last_replied_comment_id,
        has_pending_mission,
        make_thread_key,
        set_pending_mission,
    )

    thread_key = make_thread_key(owner, repo, issue_number)

    # Already have a pending mission for this thread
    if has_pending_mission(instance_dir, thread_key):
        log.debug("GitHub subscribe: pending mission exists for %s", thread_key)
        return False

    # Check for new comments since our last reply
    last_id = get_last_replied_comment_id(instance_dir, thread_key)
    new_comments = _fetch_new_comments_since(
        owner, repo, issue_number, last_id, bot_username,
    )
    if not new_comments:
        log.debug("GitHub subscribe: no new comments on %s", thread_key)
        return False

    # Build web URL for the thread
    subject_url = notification.get("subject", {}).get("url", "")
    web_url = api_url_to_web_url(subject_url) if subject_url else ""
    if not web_url:
        web_url = f"https://github.com/{owner}/{repo}/issues/{issue_number}"

    # Queue /reply mission
    mission_text = f"/reply {web_url}"
    log.info("GitHub subscribe: queuing reply mission for %s", thread_key)

    from app.utils import insert_pending_mission

    subscribe_instance_dir = Path(koan_root) / "instance"
    try:
        inserted = insert_pending_mission(mission_text, project_name)
    except OSError as e:
        log.warning("GitHub subscribe: failed to insert mission: %s", e)
        return False

    # Mark as pending to prevent duplicate missions.
    if inserted:
        set_pending_mission(instance_dir, thread_key, True)
        notification[NOTIFICATION_OUTCOME_KEY] = NOTIFICATION_OUTCOME_QUEUED
    else:
        notification[NOTIFICATION_OUTCOME_KEY] = NOTIFICATION_OUTCOME_HANDLED_NOOP
    return True


def _fetch_subject_info(notification: dict) -> dict:
    """Fetch state, merged status, and head SHA for a notification's subject.

    One API call returns everything the assignment path needs: the
    ``state``/``merged`` fields for the closed/merged check and ``head_sha``
    for the review-request dedup key. Issues have no ``head`` — ``head_sha``
    comes back null in that case.

    Returns:
        A dict with keys ``state``, ``merged``, ``head_sha`` (values may be
        empty/None/False). Returns an empty dict when the subject cannot be
        fetched, so callers must treat a missing ``head_sha`` as "unknown".
    """
    from app.github import SSOAuthRequired, api as gh_api

    subject_url = notification.get("subject", {}).get("url", "")
    if not subject_url:
        return {}

    # Convert full URL to API endpoint
    api_prefix = "https://api.github.com/"
    if not subject_url.startswith(api_prefix):
        return {}
    endpoint = subject_url[len(api_prefix):]
    if not endpoint:
        return {}

    try:
        raw = gh_api(
            endpoint,
            jq="{state: .state, merged: .merged, head_sha: .head.sha}",
            timeout=15,
        )
        data = json.loads(raw) if raw else {}
    except (SSOAuthRequired, RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired):
        # Can't determine state — don't block the notification
        return {}

    return data if isinstance(data, dict) else {}


def _closed_reason_from_subject_info(subject_info: dict) -> Optional[str]:
    """Derive a closed/merged reason string from fetched subject info."""
    if subject_info.get("merged"):
        return "merged"
    if subject_info.get("state") == "closed":
        return "closed"
    return None


def _is_subject_closed(notification: dict) -> Optional[str]:
    """Check if the notification's subject (PR or issue) is closed or merged.

    Fetches the subject state from the GitHub API.

    Args:
        notification: A notification dict from GitHub API.

    Returns:
        A human-readable reason string if the subject is closed/merged,
        or None if it's still open (or state cannot be determined).
    """
    return _closed_reason_from_subject_info(_fetch_subject_info(notification))


def _notify_closed_subject_skipped(
    owner: str,
    repo: str,
    subject_title: str,
    subject_state: str,
    notification: dict,
) -> None:
    """Send Telegram notification when skipping a closed/merged PR or issue."""
    try:
        from app.github_notifications import api_url_to_web_url
        from app.notify import NotificationPriority, send_telegram

        subject_url = notification.get("subject", {}).get("url", "")
        web_url = api_url_to_web_url(subject_url) if subject_url else ""
        subject_type = notification.get("subject", {}).get("type", "item")

        url_part = f"\n{web_url}" if web_url else ""
        send_telegram(
            f"⏭️ Skipped GitHub notification on {subject_state} {subject_type.lower()}: "
            f"{owner}/{repo} — {subject_title}{url_part}",
            priority=NotificationPriority.INFO,
        )
    except Exception as e:
        log.warning("Failed to send closed-subject skip notification: %s", e)


def _notify_github_question(
    author: str, owner: str, repo: str, issue_number: str, question: str,
) -> None:
    """Send ❓ Telegram notification when a question is received from GitHub."""
    try:
        from app.notify import send_telegram, NotificationPriority
        # Truncate question for Telegram readability
        short = question[:200] + "…" if len(question) > 200 else question
        send_telegram(
            f"❓ GitHub question from @{author}\n"
            f"{owner}/{repo}#{issue_number}: {short}",
            priority=NotificationPriority.ACTION,
        )
    except Exception as e:
        log.warning("Failed to send GitHub question notification: %s", e)


def _notify_github_reply(
    owner: str, repo: str, issue_number: str, reply_text: str,
) -> None:
    """Send 💬 Telegram notification when Kōan posts a reply on GitHub."""
    try:
        from app.notify import send_telegram, NotificationPriority
        short = reply_text[:200] + "…" if len(reply_text) > 200 else reply_text
        send_telegram(
            f"💬 Replied on GitHub\n"
            f"{owner}/{repo}#{issue_number}: {short}",
            priority=NotificationPriority.ACTION,
        )
    except Exception as e:
        log.warning("Failed to send GitHub reply notification: %s", e)


def extract_issue_number_from_notification(notification: dict) -> Optional[str]:
    """Extract issue/PR number from a notification.

    Works for both issues and pull requests.
    """
    subject_url = notification.get("subject", {}).get("url", "")
    if not subject_url:
        return None

    # API URL: .../issues/42 or .../pulls/42
    match = re.search(r'/(?:issues|pulls)/(\d+)', subject_url)
    return match.group(1) if match else None
