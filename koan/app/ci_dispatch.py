"""Auto-dispatch fix missions when CI fails on Koan-authored PRs.

Checks open PRs authored by Koan (identified by branch prefix), fetches
check-run status from GitHub, and inserts a fix mission when a CI run
fails.  Dedup state persisted in ``instance/.ci-dispatch-tracker.json``
keyed by ``{repo}#{pr}:{head_sha}:{job_name}`` to prevent re-dispatching
for the same failure.

Config in config.yaml::

    ci_dispatch:
      enabled: false           # opt-in
      cooldown_minutes: 30     # min time between checks per project
      log_snippet_bytes: 4096  # max log snippet size in mission text
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

_DEFAULT_ENABLED = False
_DEFAULT_COOLDOWN_MINUTES = 30
_DEFAULT_LOG_SNIPPET_BYTES = 4096


def _get_ci_dispatch_config() -> dict:
    try:
        from app.utils import load_config
        cfg = load_config()
        cd = cfg.get("ci_dispatch") or {}
        return {
            "enabled": bool(cd.get("enabled", _DEFAULT_ENABLED)),
            "cooldown_minutes": int(cd.get("cooldown_minutes", _DEFAULT_COOLDOWN_MINUTES)),
            "log_snippet_bytes": int(cd.get("log_snippet_bytes", _DEFAULT_LOG_SNIPPET_BYTES)),
        }
    except (ImportError, OSError, ValueError):
        return {
            "enabled": _DEFAULT_ENABLED,
            "cooldown_minutes": _DEFAULT_COOLDOWN_MINUTES,
            "log_snippet_bytes": _DEFAULT_LOG_SNIPPET_BYTES,
        }


def _get_branch_prefix() -> str:
    try:
        from app.config import get_branch_prefix
        return get_branch_prefix()
    except (ImportError, OSError):
        return "koan/"


def _resolve_full_repo(project_path: str) -> Optional[str]:
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


def _tracker_path(instance_dir: str) -> Path:
    return Path(instance_dir) / ".ci-dispatch-tracker.json"


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


def fetch_koan_open_prs(project_path: str) -> List[dict]:
    """Fetch open PRs whose branch starts with the configured prefix.

    Returns list of dicts with number, title, headRefName, headRefOid.
    """
    prefix = _get_branch_prefix()
    try:
        raw = run_gh(
            "pr", "list",
            "--state", "open",
            "--limit", "30",
            "--json", "number,title,headRefName,headRefOid",
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


def fetch_failing_check_runs(
    full_repo: str,
    head_sha: str,
) -> List[dict]:
    """Fetch failed check runs for a given commit SHA.

    Returns list of dicts: {id, name, conclusion, html_url}.
    Only returns runs with conclusion == "failure".
    """
    try:
        raw = run_gh(
            "api", f"repos/{full_repo}/commits/{head_sha}/check-runs",
            "--jq", '.check_runs[] | {id: .id, name: .name, conclusion: .conclusion, html_url: .html_url}',
            timeout=15,
        )
    except RuntimeError as e:
        log.debug("Failed to fetch check runs for %s: %s", head_sha[:8], e)
        return []

    if not raw.strip():
        return []

    results = []
    for line in raw.strip().split("\n"):
        try:
            item = json.loads(line)
            if item.get("conclusion") == "failure":
                results.append(item)
        except (json.JSONDecodeError, KeyError):
            continue

    return results


def fetch_check_run_log_snippet(
    full_repo: str,
    check_run_id: int,
    max_bytes: int = _DEFAULT_LOG_SNIPPET_BYTES,
) -> str:
    """Fetch the annotation/output for a failing check run.

    Uses the check-run output summary + annotations as a compact failure
    signal.  Falls back to empty string if unavailable.
    """
    try:
        raw = run_gh(
            "api", f"repos/{full_repo}/check-runs/{check_run_id}",
            "--jq", '{summary: .output.summary, text: .output.text, annotations: [.output.annotations[]? | {message: .message, path: .path, line: .start_line}]}',
            timeout=15,
        )
    except RuntimeError:
        return ""

    if not raw.strip():
        return ""

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""

    parts = []
    summary = (data.get("summary") or "").strip()
    if summary:
        parts.append(summary)

    text = (data.get("text") or "").strip()
    if text:
        parts.append(text)

    annotations = data.get("annotations") or []
    for ann in annotations[:10]:
        msg = ann.get("message", "")
        path = ann.get("path", "")
        line = ann.get("line", "")
        if msg:
            loc = f"{path}:{line}" if path else ""
            parts.append(f"  {loc}: {msg}" if loc else f"  {msg}")

    result = "\n".join(parts)
    if len(result) > max_bytes:
        result = result[:max_bytes - 20] + "\n...(truncated)"
    return result


def compute_ci_fingerprint(
    pr_number: int,
    head_sha: str,
    job_name: str,
) -> str:
    """Deterministic dedup key for a CI failure."""
    key = f"{pr_number}:{head_sha}:{job_name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def check_and_dispatch_ci_fixes(
    instance_dir: str,
    koan_root: str,
) -> int:
    """Check Koan's open PRs for CI failures and dispatch fix missions.

    For each known project, fetches open Koan PRs, checks their CI status,
    and dispatches a fix mission for each new failure.

    Returns:
        Number of missions dispatched.
    """
    config = _get_ci_dispatch_config()
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
    cooldown_secs = config["cooldown_minutes"] * 60
    max_log_bytes = config["log_snippet_bytes"]
    now = time.time()
    dispatched = 0
    tracker_changed = False

    for project_name, project_path in projects:
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
            head_sha = pr.get("headRefOid", "")
            if not head_sha:
                continue

            failures = fetch_failing_check_runs(full_repo, head_sha)
            if not failures:
                continue

            for fail in failures:
                job_name = fail.get("name", "unknown")
                fingerprint = compute_ci_fingerprint(pr_number, head_sha, job_name)
                fp_key = f"{full_repo}#{fingerprint}"

                if fp_key in tracker:
                    continue

                log_snippet = fetch_check_run_log_snippet(
                    full_repo, fail["id"], max_log_bytes,
                )

                context = f"Job: {job_name}"
                if log_snippet:
                    context += f"\n\nCI output:\n```\n{log_snippet}\n```"

                mission = (
                    f"[project:{project_name}] Fix CI failure: "
                    f"{job_name} on PR #{pr_number} — {context}"
                )

                try:
                    from app.utils import insert_pending_mission
                    missions_path = Path(instance_dir) / "missions.md"
                    inserted = insert_pending_mission(missions_path, f"- {mission}")
                except (ImportError, OSError) as e:
                    log.warning("Failed to insert CI fix mission: %s", e)
                    continue

                if inserted:
                    log.info(
                        "CI dispatch: failure %s on %s#%d (sha %s)",
                        job_name, full_repo, pr_number, head_sha[:8],
                    )
                    dispatched += 1

                tracker[fp_key] = fingerprint
                tracker_changed = True

        tracker[project_key] = now
        tracker_changed = True

    if tracker_changed:
        _save_tracker(instance_dir, tracker)

    return dispatched
