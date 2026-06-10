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


def _get_review_dispatch_config() -> dict:
    """Load review_dispatch config section from config.yaml."""
    try:
        from app.utils import load_config
        cfg = load_config()
        rd = cfg.get("review_dispatch") or {}
        return {
            "enabled": bool(rd.get("enabled", _DEFAULT_ENABLED)),
            "cooldown_minutes": int(rd.get("cooldown_minutes", _DEFAULT_COOLDOWN_MINUTES)),
        }
    except (ImportError, OSError, ValueError) as e:
        log.warning("Failed to load review_dispatch config, using defaults: %s", e)
        return {"enabled": _DEFAULT_ENABLED, "cooldown_minutes": _DEFAULT_COOLDOWN_MINUTES}


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


def _read_tracker(instance_dir: str) -> dict:
    """Read tracker state under a shared lock (snapshot for cooldown checks)."""
    from app.locked_file import locked_json_read
    return locked_json_read(_tracker_path(instance_dir), default={}) or {}


def _mutate_tracker(instance_dir: str, fn) -> None:
    """Apply fn(data) → data under exclusive lock (atomic read-modify-write)."""
    from app.locked_file import locked_json_modify
    locked_json_modify(_tracker_path(instance_dir), fn)


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

    # Read snapshot for cooldown checks (no lock needed — stale reads are fine here).
    snapshot = _read_tracker(instance_dir)
    bot_username = _get_bot_username()
    cooldown_secs = config["cooldown_minutes"] * 60
    now = time.time()
    dispatched = 0

    for project_name, project_path in projects:
        project_key = f"cooldown:{project_name}"
        last_check = snapshot.get(project_key, 0)
        if now - last_check < cooldown_secs:
            continue

        full_repo = _resolve_full_repo(project_path)
        if not full_repo:
            _mutate_tracker(instance_dir, lambda d: d.update({project_key: now}) or d)
            continue

        prs = fetch_koan_open_prs(project_path)
        if not prs:
            _mutate_tracker(instance_dir, lambda d: d.update({project_key: now}) or d)
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
                # Clear stored fingerprint under lock when comments are resolved.
                def _clear_pr(data, _pr_key=pr_key):
                    data.pop(_pr_key, None)
                _mutate_tracker(instance_dir, _clear_pr)
                snapshot.pop(pr_key, None)
                continue

            fingerprint = compute_comment_fingerprint(all_comments)
            stored = snapshot.get(pr_key)

            # Fast path: skip if snapshot shows no change.
            if stored == fingerprint:
                continue

            summary = _format_comment_summary(all_comments)
            mission = (
                f"[project:{project_name}] Address review comments on "
                f"#{pr_number} ({summary})"
            )

            # Re-check under exclusive lock before inserting to prevent concurrent
            # dispatchers from queuing the same mission twice.
            inserted = False

            def _record_and_insert(
                data,
                _pr_key=pr_key,
                _fingerprint=fingerprint,
                _stored=stored,
                _mission=mission,
                _full_repo=full_repo,
                _pr_number=pr_number,
            ):
                nonlocal inserted
                if data.get(_pr_key) == _fingerprint:
                    return data  # lost the race — already dispatched
                try:
                    from app.utils import insert_pending_mission
                    missions_path = Path(instance_dir) / "missions.md"
                    inserted = insert_pending_mission(missions_path, f"- {_mission}")
                except (ImportError, OSError) as e:
                    log.warning("Failed to insert review dispatch mission: %s", e)
                    return data
                data[_pr_key] = _fingerprint
                return data

            try:
                _mutate_tracker(instance_dir, _record_and_insert)
            except OSError as e:
                log.warning("Failed to update review dispatch tracker: %s", e)
                continue

            if inserted:
                log.info(
                    "Review dispatch: new comments on %s#%d (fingerprint %s → %s)",
                    full_repo, pr_number,
                    (stored or "none")[:8], fingerprint[:8],
                )
                dispatched += 1
                snapshot[pr_key] = fingerprint

        _mutate_tracker(instance_dir, lambda d: d.update({project_key: now}) or d)

    return dispatched
