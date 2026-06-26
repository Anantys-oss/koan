"""Auto-dispatch missions when human reviewers leave comments on Koan's PRs.

Checks open PRs authored by Koan (identified by branch prefix), computes a
fingerprint of current unresolved review comments, and inserts a mission when
the fingerprint changes.  Dedup state persisted in
``instance/.review-dispatch-tracker.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import List, Optional

from app.github import run_gh

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEFAULT_COOLDOWN_MINUTES = 30
_DEFAULT_ENABLED = False
_DEFAULT_TRACKER_MAX_AGE_DAYS = 30


def _get_review_dispatch_config() -> dict:
    """Load review_dispatch config section from config.yaml."""
    try:
        from app.utils import load_config
        cfg = load_config()
        rd = cfg.get("review_dispatch") or {}
        return {
            "enabled": bool(rd.get("enabled", _DEFAULT_ENABLED)),
            "cooldown_minutes": int(rd.get("cooldown_minutes", _DEFAULT_COOLDOWN_MINUTES)),
            "tracker_max_age_days": int(rd.get("tracker_max_age_days", _DEFAULT_TRACKER_MAX_AGE_DAYS)),
        }
    except (ImportError, OSError, ValueError) as e:
        log.warning("Failed to load review_dispatch config, using defaults: %s", e)
        return {
            "enabled": _DEFAULT_ENABLED,
            "cooldown_minutes": _DEFAULT_COOLDOWN_MINUTES,
            "tracker_max_age_days": _DEFAULT_TRACKER_MAX_AGE_DAYS,
        }


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def _get_branch_prefix() -> str:
    try:
        from app.config import get_branch_prefix
        return get_branch_prefix()
    except (ImportError, OSError):
        return "koan/"


def _get_bot_username() -> str:
    try:
        from app.utils import load_config
        cfg = load_config()
        gh = cfg.get("github") or {}
        return str(gh.get("nickname", "")).strip()
    except (ImportError, OSError) as e:
        log.warning("Failed to load bot username, bot-comment filtering disabled: %s", e)
        return ""


def fetch_koan_open_prs(
    project_path: str,
) -> List[dict]:
    """Fetch open PRs whose branch starts with the configured prefix.

    Returns list of dicts: {number, title, headRefName, updatedAt}.
    """
    prefix = _get_branch_prefix()
    try:
        raw = run_gh(
            "pr", "list",
            "--state", "open",
            "--limit", "30",
            "--json", "number,title,headRefName,updatedAt",
            cwd=project_path,
            timeout=15,
        )
    except RuntimeError as e:
        log.debug("Failed to list open PRs: %s", e)
        return []

    try:
        prs = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []

    return [
        pr for pr in prs
        if pr.get("headRefName", "").startswith(prefix)
    ]


def fetch_unresolved_review_comments(
    full_repo: str,
    pr_number: int,
    bot_username: str = "",
) -> List[dict]:
    """Fetch non-bot review comments for a PR.

    Returns list of dicts: {id, user, body, path}.  Excludes bot-authored
    comments to prevent self-reply loops.
    """
    results: List[dict] = []
    try:
        raw = run_gh(
            "api", f"repos/{full_repo}/pulls/{pr_number}/comments",
            "--limit", "100", "--jq",
            r'.[] | {id: .id, user: .user.login, body: .body, path: .path, user_type: .user.type}',
            timeout=15,
        )
    except RuntimeError:
        return results

    if not raw.strip():
        return results

    bot_lower = bot_username.lower() if bot_username else ""
    for line in raw.strip().split("\n"):
        try:
            item = json.loads(line)
            if item.get("user_type") == "Bot":
                continue
            if bot_lower and item.get("user", "").lower() == bot_lower:
                continue
            results.append({
                "id": item["id"],
                "user": item.get("user", ""),
                "body": item.get("body", ""),
                "path": item.get("path", ""),
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return results


def fetch_review_body_comments(
    full_repo: str,
    pr_number: int,
    bot_username: str = "",
) -> List[dict]:
    """Fetch review-body comments (top-level review submissions).

    Only includes reviews with body text and state CHANGES_REQUESTED or
    COMMENTED.
    """
    results: List[dict] = []
    try:
        raw = run_gh(
            "api", f"repos/{full_repo}/pulls/{pr_number}/reviews",
            "--limit", "100", "--jq",
            r'.[] | {id: .id, user: .user.login, body: .body, state: .state, user_type: .user.type}',
            timeout=15,
        )
    except RuntimeError:
        return results

    if not raw.strip():
        return results

    bot_lower = bot_username.lower() if bot_username else ""
    for line in raw.strip().split("\n"):
        try:
            item = json.loads(line)
            if item.get("user_type") == "Bot":
                continue
            if bot_lower and item.get("user", "").lower() == bot_lower:
                continue
            body = (item.get("body") or "").strip()
            if not body:
                continue
            if item.get("state") not in ("CHANGES_REQUESTED", "COMMENTED"):
                continue
            results.append({
                "id": item["id"],
                "user": item.get("user", ""),
                "body": body,
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return results


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------

def compute_comment_fingerprint(comments: List[dict]) -> str:
    """SHA-256 of sorted comment ID+body pairs — detects additions, removals, and edits."""
    parts = sorted(
        f"{c.get('id', '')}:{c.get('body', '')[:200]}" for c in comments
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Tracker persistence
# ---------------------------------------------------------------------------

def _tracker_path(instance_dir: str) -> Path:
    return Path(instance_dir) / ".review-dispatch-tracker.json"


def _load_tracker(instance_dir: str) -> dict:
    path = _tracker_path(instance_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_tracker(instance_dir: str, data: dict) -> None:
    from app.utils import atomic_write_json
    atomic_write_json(_tracker_path(instance_dir), data)


def _prune_tracker(data: dict, max_age_days: int = _DEFAULT_TRACKER_MAX_AGE_DAYS) -> int:
    """Remove tracker entries older than *max_age_days*. Returns count removed."""
    cutoff = time.time() - max_age_days * 86400
    stale = [
        k for k, v in data.items()
        if not k.startswith("cooldown:") and _entry_ts(v) < cutoff
    ]
    for k in stale:
        del data[k]
    return len(stale)


def _entry_ts(value) -> float:
    if isinstance(value, dict):
        return value.get("ts", 0)
    return 0


# ---------------------------------------------------------------------------
# Main dispatch logic
# ---------------------------------------------------------------------------

def _resolve_full_repo(project_path: str) -> Optional[str]:
    """Get 'owner/repo' for a project from gh."""
    try:
        raw = run_gh(
            "repo", "view",
            "--json", "nameWithOwner",
            "--jq", ".nameWithOwner",
            cwd=project_path,
            timeout=10,
        )
        return raw.strip() or None
    except RuntimeError:
        # gh non-zero exit (no repo, auth, etc.) — expected, stay quiet.
        return None
    except FileNotFoundError:
        # project_path no longer exists on disk (stale config / unmounted volume).
        return None
    except OSError as e:
        # Permission denied, ENOTDIR, I/O fault — unexpected; surface it.
        log.warning("Failed to resolve repo for %s: %s", project_path, e)
        return None


def _format_comment_summary(comments: List[dict], max_len: int = 200) -> str:
    """Build a short summary of review comments for the mission description."""
    if not comments:
        return ""
    users = sorted({c.get("user", "?") for c in comments})
    paths = sorted({c.get("path", "") for c in comments if c.get("path")})

    parts = [f"from {', '.join(users)}"]
    if paths:
        shown = paths[:3]
        if len(paths) > 3:
            shown.append(f"+{len(paths) - 3} more")
        parts.append(f"on {', '.join(shown)}")

    summary = "; ".join(parts)
    if len(summary) > max_len:
        summary = summary[:max_len - 3] + "..."
    return summary


def check_and_dispatch_review_comments(
    instance_dir: str,
    koan_root: str,
) -> int:
    """Check Koan's open PRs for new review comments and dispatch missions.

    For each known project, fetches open Koan PRs, computes a comment
    fingerprint, and dispatches a mission if the fingerprint changed since
    last check.

    Returns:
        Number of missions dispatched.
    """
    config = _get_review_dispatch_config()
    if not config["enabled"]:
        return 0

    try:
        from app.projects_config import load_projects_config, get_projects_from_config
        projects_config = load_projects_config(koan_root)
        projects = get_projects_from_config(projects_config)
    except (ImportError, OSError) as e:
        log.debug("Failed to load projects config: %s", e)
        return 0

    if not projects:
        return 0

    tracker = _load_tracker(instance_dir)
    pruned = _prune_tracker(tracker, config.get("tracker_max_age_days", _DEFAULT_TRACKER_MAX_AGE_DAYS))
    bot_username = _get_bot_username()
    cooldown_secs = config["cooldown_minutes"] * 60
    now = time.time()
    dispatched = 0
    tracker_changed = pruned > 0

    for project_name, project_path in projects:
        # Match projects_merged's expanduser()-based check so a '~'-style
        # path is resolved identically in both layers (no false skip).
        if not Path(project_path).expanduser().is_dir():
            log.debug(
                "Skipping project '%s': path does not exist: %s",
                project_name, project_path,
            )
            continue

        project_key = f"cooldown:{project_name}"
        last_check = tracker.get(project_key, 0)
        if now - last_check < cooldown_secs:
            continue

        full_repo = _resolve_full_repo(project_path)
        if not full_repo:
            continue

        prs = fetch_koan_open_prs(project_path)
        if not prs:
            tracker[project_key] = now
            tracker_changed = True
            continue

        for pr in prs:
            pr_number = pr["number"]
            pr_key = f"{full_repo}#{pr_number}"

            inline = fetch_unresolved_review_comments(
                full_repo, pr_number, bot_username,
            )
            reviews = fetch_review_body_comments(
                full_repo, pr_number, bot_username,
            )
            all_comments = inline + reviews

            if not all_comments:
                if pr_key in tracker:
                    del tracker[pr_key]
                    tracker_changed = True
                continue

            fingerprint = compute_comment_fingerprint(all_comments)
            stored_raw = tracker.get(pr_key)
            stored_fp = stored_raw.get("fingerprint", "") if isinstance(stored_raw, dict) else stored_raw

            if stored_fp == fingerprint:
                continue

            summary = _format_comment_summary(all_comments)
            mission = (
                f"[project:{project_name}] Address review comments on "
                f"#{pr_number} ({summary})"
            )

            try:
                from app.utils import insert_pending_mission
                missions_path = Path(instance_dir) / "missions.md"
                inserted = insert_pending_mission(missions_path, f"- {mission}")
            except (ImportError, OSError) as e:
                log.warning("Failed to insert review dispatch mission: %s", e)
                continue

            if inserted:
                log.info(
                    "Review dispatch: new comments on %s#%d (fingerprint %s → %s)",
                    full_repo, pr_number,
                    (stored_fp or "none")[:8], fingerprint[:8],
                )
                dispatched += 1
                tracker[pr_key] = {"fingerprint": fingerprint, "ts": now}
                tracker_changed = True

        tracker[project_key] = now
        tracker_changed = True

    if tracker_changed:
        _save_tracker(instance_dir, tracker)

    return dispatched
