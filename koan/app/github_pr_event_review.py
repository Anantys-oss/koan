"""Review on PR events — queue /review when a PR is opened or pushed.

Opt-in per project via ``github.pr_event_review.enabled`` (resolved by
``github_config.get_pr_event_review_config``). Runs inside the GitHub poll
(``loop_manager.process_github_notifications``) right after the
requested-review scan. "Webhook for latency, poll for reliability": the
webhook receiver only forces this scan sooner; it never queues missions.

Per enabled repo, one ``gh pr list`` per scan; ``/review <url>`` is queued
for each open PR that:

- was updated after the repo's first scan (no backlog burst on enable) and
  within ``github.max_age_hours``;
- is not a draft (unless ``include_drafts``), has no ``review_pause_label``,
  was not authored by the bot nor opened from a branch-prefix branch
  (Kōan PRs belong to ``autoreview``), and its author passes
  ``github.authorized_users``;
- has a head SHA this scan never handled (``on_push: false`` → only the
  first eligible head), no active mission for its URL, and no review
  cooldown (a cooldown defers rather than consumes the SHA).
"""

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from app.github_command_handler import (
    _active_mission_exists_for_url,
    _project_review_scan_repos,
    validate_command,
)
from app.github_config import (
    get_github_authorized_users,
    get_github_max_age_hours,
    get_github_nickname,
    get_github_parallel_workers,
    get_pr_event_review_config,
)
from app.github_notifications import check_user_permission
from app.skills import SkillRegistry

log = logging.getLogger(__name__)

_PR_FIELDS = "number,url,headRefOid,headRefName,isDraft,author,labels,updatedAt"


@dataclass(frozen=True)
class _ScanContext:
    config: dict
    projects_config: Optional[dict]
    tracker_dir: str
    missions_path: Path
    max_age_hours: int
    bot_logins: frozenset
    branch_prefix: str
    pause_label: str


def _throttle_key(repo_slug: str) -> str:
    # Own prefix in the shared scan-throttle file: never shares state with
    # the requested-review scan's bare ``owner/repo`` keys.
    return f"pr_event:{repo_slug}"


def _dedup_key(repo_slug: str, pr: dict, on_push: bool) -> str:
    head = str(pr.get("headRefOid") or "") if on_push else "first"
    return f"pr_event_review:{repo_slug}#{pr.get('number')}:{head}"


def _fetch_open_prs(repo_slug: str) -> Optional[List[dict]]:
    """List open PRs of ``repo_slug``; ``None`` on any fetch failure.

    ``None`` (not ``[]``) leaves the repo un-throttled so a transient
    failure retries on the next poll.
    """
    from app.github import SSOAuthRequired, run_gh
    from app.github_notifications import _record_sso_failure

    try:
        raw = run_gh(
            "pr", "list", "--repo", repo_slug, "--state", "open",
            "--limit", "100", "--json", _PR_FIELDS, timeout=30,
        )
    except SSOAuthRequired:
        _record_sso_failure(f"pr_event_review_scan {repo_slug}")
        return None
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        log.debug("PR-event review: failed to list PRs for %s: %s", repo_slug, exc)
        return None
    try:
        prs = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        log.debug("PR-event review: invalid PR list JSON for %s", repo_slug)
        return None
    if not isinstance(prs, list):
        return None
    return [pr for pr in prs if isinstance(pr, dict)]


def _updated_ts(pr: dict) -> Optional[float]:
    raw = pr.get("updatedAt")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _held_reason(pr: dict, review_cfg: dict, ctx: _ScanContext) -> str:
    """Draft / pause-label hold that a later event can lift, or ""."""
    if pr.get("isDraft") and not review_cfg["include_drafts"]:
        return "draft"
    labels = pr.get("labels") if isinstance(pr.get("labels"), list) else []
    names = {str(lbl.get("name", "")) for lbl in labels if isinstance(lbl, dict)}
    if ctx.pause_label and ctx.pause_label in names:
        return "pause label"
    return ""


def _skip_reason(pr: dict, review_cfg: dict, ctx: _ScanContext,
                 not_before: float) -> str:
    """Why ``pr`` is not a candidate, or "" when it is."""
    updated = _updated_ts(pr)
    if updated is None or updated < not_before:
        return "not updated since seed / outside max_age_hours"
    held = _held_reason(pr, review_cfg, ctx)
    if held:
        return held
    author = pr.get("author") if isinstance(pr.get("author"), dict) else {}
    login = str(author.get("login", "")).lower()
    if not login or author.get("is_bot") or login in ctx.bot_logins:
        return "bot-authored"
    if str(pr.get("headRefName", "")).startswith(ctx.branch_prefix):
        return "Kōan branch"
    return ""


def _not_before(ctx: _ScanContext, repo_slug: str) -> Optional[float]:
    """Earliest ``updatedAt`` that counts; ``None`` on the seeding pass."""
    from app.github_notification_tracker import (
        get_pr_event_seeded_at,
        mark_pr_event_seeded,
    )

    seeded_at = get_pr_event_seeded_at(ctx.tracker_dir, repo_slug)
    if seeded_at is None:
        mark_pr_event_seeded(ctx.tracker_dir, repo_slug)
        return None
    return max(seeded_at, time.time() - ctx.max_age_hours * 3600)


def _track_seed_heads(repo_slug: str, prs: List[dict], review_cfg: dict,
                      ctx: _ScanContext) -> None:
    """Mark every reviewable head open at seed time as handled.

    A later comment or label bumps ``updatedAt`` past the seed without any
    PR event; without this baseline that pre-seed head would be reviewed.
    Drafts and paused PRs stay untracked: their "ready for review" or
    unpause is a real event that must still queue a review.
    """
    from app.github_notification_tracker import track_thread

    for pr in prs:
        if not (pr.get("number") and pr.get("headRefOid")):
            continue
        if _held_reason(pr, review_cfg, ctx):
            continue
        track_thread(ctx.tracker_dir,
                     _dedup_key(repo_slug, pr, review_cfg["on_push"]))


def _deferred_or_handled(pr: dict, repo_slug: str, key: str,
                         ctx: _ScanContext) -> bool:
    """True when this head needs no review from this scan right now.

    A head the requested-review scan already queued is consumed (tracked)
    so it is not reviewed twice once the shared cooldown lapses. A cooldown
    hit alone stays untracked on purpose: retried once the cooldown lapses.
    """
    from app.github_notification_tracker import (
        is_review_on_cooldown,
        is_thread_tracked,
        track_thread,
    )

    # Same key format as github_command_handler.scan_requested_review_missions.
    request_key = f"review_scan:{repo_slug}#{pr['number']}:{pr['headRefOid']}"
    if is_thread_tracked(ctx.tracker_dir, request_key):
        track_thread(ctx.tracker_dir, key)
        return True
    owner, repo = repo_slug.split("/", 1)
    return is_review_on_cooldown(ctx.tracker_dir, owner, repo, str(pr["number"]))


def _queue_pr_review(pr: dict, project_name: str, repo_slug: str,
                     key: str, ctx: _ScanContext) -> bool:
    """Queue ``/review`` for one authorized candidate. True when inserted."""
    from app.github_notification_tracker import set_review_cooldown, track_thread
    from app.utils import insert_pending_mission

    owner, repo = repo_slug.split("/", 1)
    number, web_url = str(pr["number"]), str(pr["url"])
    if _active_mission_exists_for_url(ctx.missions_path, web_url):
        track_thread(ctx.tracker_dir, key)
        return False
    try:
        inserted = insert_pending_mission(
            ctx.missions_path, f"- [project:{project_name}] /review {web_url} 📬")
    except OSError as exc:
        log.warning("PR-event review: failed to insert mission for %s: %s",
                    web_url, exc)
        return False
    track_thread(ctx.tracker_dir, key)
    if inserted:
        set_review_cooldown(ctx.tracker_dir, owner, repo, number)
        log.info("PR-event review: queued /review for %s (head %s)",
                 web_url, str(pr["headRefOid"])[:12])
    return inserted


def _process_repo(project_name: str, repo_slug: str, prs: List[dict],
                  ctx: _ScanContext) -> int:
    from app.github_notification_tracker import is_thread_tracked

    review_cfg = get_pr_event_review_config(
        ctx.config, project_name, ctx.projects_config)
    not_before = _not_before(ctx, repo_slug)
    if not_before is None:
        _track_seed_heads(repo_slug, prs, review_cfg, ctx)
        log.info("PR-event review: seeded %s — only PRs updated from now on count",
                 repo_slug)
        return 0
    owner, repo = repo_slug.split("/", 1)
    allowed = get_github_authorized_users(ctx.config, project_name, ctx.projects_config)
    queued = 0
    for pr in prs:
        if queued >= review_cfg["max_per_cycle"]:
            break
        if not (pr.get("number") and pr.get("url") and pr.get("headRefOid")):
            continue
        reason = _skip_reason(pr, review_cfg, ctx, not_before)
        if reason:
            log.debug("PR-event review: skip %s#%s (%s)",
                      repo_slug, pr.get("number"), reason)
            continue
        key = _dedup_key(repo_slug, pr, review_cfg["on_push"])
        if is_thread_tracked(ctx.tracker_dir, key):
            continue
        if _deferred_or_handled(pr, repo_slug, key, ctx):
            continue
        if not check_user_permission(owner, repo, pr["author"]["login"], allowed):
            continue
        if _queue_pr_review(pr, project_name, repo_slug, key, ctx):
            queued += 1
    return queued


def _build_context(projects_config: Optional[dict], config: dict,
                   registry: SkillRegistry,
                   instance_dir: str) -> Optional[_ScanContext]:
    nickname = get_github_nickname(config)
    koan_root = os.environ.get("KOAN_ROOT", "")
    if not validate_command("review", registry) or not nickname or not koan_root:
        return None
    from app.config import get_branch_prefix, get_review_pause_label

    logins = {nickname.lower(), os.environ.get("GITHUB_USER", "").lower()} - {""}
    return _ScanContext(
        config=config,
        projects_config=projects_config,
        tracker_dir=instance_dir or str(Path(koan_root) / "instance"),
        missions_path=Path(koan_root) / "instance" / "missions.md",
        max_age_hours=get_github_max_age_hours(config),
        bot_logins=frozenset(logins),
        branch_prefix=get_branch_prefix(),
        pause_label=get_review_pause_label(),
    )


def _enabled_repos(ctx: _ScanContext) -> List[Tuple[str, str, dict]]:
    """``(project, repo, resolved config)`` for every enabled project repo."""
    enabled = []
    for project_name, repo_slug in _project_review_scan_repos(ctx.projects_config):
        review_cfg = get_pr_event_review_config(
            ctx.config, project_name, ctx.projects_config)
        if review_cfg["enabled"]:
            enabled.append((project_name, repo_slug, review_cfg))
    return enabled


def _due_repos(ctx: _ScanContext, force: bool) -> List[Tuple[str, str]]:
    from app.github_notification_tracker import (
        is_repo_scan_due,
        prune_pr_event_seeds,
    )

    enabled = _enabled_repos(ctx)
    # Forget seeds of repos no longer enabled, so re-enabling re-seeds.
    prune_pr_event_seeds(ctx.tracker_dir, {slug for _, slug, _ in enabled})
    due = []
    for project_name, repo_slug, review_cfg in enabled:
        interval = review_cfg["scan_interval_minutes"] * 60
        throttle_key = _throttle_key(repo_slug)
        if force or is_repo_scan_due(ctx.tracker_dir, throttle_key, interval):
            due.append((project_name, repo_slug))
    return due


def _fetch_all(due: List[Tuple[str, str]], config: dict):
    def _fetch(entry):
        project_name, repo_slug = entry
        return project_name, repo_slug, _fetch_open_prs(repo_slug)

    workers = min(get_github_parallel_workers(config), len(due))
    if workers <= 1:
        return [_fetch(entry) for entry in due]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="gh-pr-event-scan") as pool:
        return list(pool.map(_fetch, due))


def scan_pr_event_review_missions(
    projects_config: Optional[dict],
    config: dict,
    registry: SkillRegistry,
    instance_dir: str,
    force: bool = False,
) -> int:
    """Queue /review missions for PRs opened or pushed in enabled projects.

    ``force`` (a webhook-triggered or /check_notifications poll) bypasses
    the per-repo scan throttle. Returns the number of missions queued.
    """
    ctx = _build_context(projects_config, config, registry, instance_dir)
    if ctx is None:
        return 0
    due = _due_repos(ctx, force)
    if not due:
        return 0
    from app.github_notification_tracker import mark_repo_scanned

    queued = 0
    for project_name, repo_slug, prs in _fetch_all(due, config):
        if prs is None:
            continue  # fetch failed — leave un-marked so it retries next poll
        mark_repo_scanned(ctx.tracker_dir, _throttle_key(repo_slug))
        queued += _process_repo(project_name, repo_slug, prs, ctx)
    return queued
