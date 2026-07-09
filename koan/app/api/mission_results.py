"""Generic per-command resolver registry for structured mission results.

A skill that emits structured output registers a resolver keyed by the
slash-command(s) it handles. The API calls resolve_mission_result() when a
mission reaches a terminal state and attaches whatever the resolver returns
as the record's typed ``result``. Skills with no resolver leave ``result`` null.
Pull-based by design: the dispatched skill runs as a subprocess with no API
mission id, so the API reads the artifact the skill already persisted rather
than the skill pushing to the sidecar.
"""
import json
import logging
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger("koan.api")

# command (no leading slash) -> (resolver(instance_dir, text) -> Optional[dict], always_inline_keys)
_RESOLVERS: Dict[str, Tuple[Callable[[Path, str], Optional[dict]], List[str]]] = {}

_COMMAND_RE = re.compile(r"/([a-zA-Z0-9_]+)")
_PR_URL_RE = re.compile(r"https?://github\.com/[^\s]+/pull/\d+")


def register_resolver(
    commands: List[str],
    fn: Callable[[Path, str], Optional[dict]],
    *,
    always_inline: Optional[List[str]] = None,
) -> None:
    for cmd in commands:
        _RESOLVERS[cmd.lstrip("/")] = (fn, list(always_inline or []))


def _mission_command(text: str) -> Optional[str]:
    m = _COMMAND_RE.search(text or "")
    return m.group(1) if m else None


def resolve_mission_result(instance_dir: Path, mission_text: str) -> Optional[dict]:
    """Return a structured result for a completed mission, or None."""
    cmd = _mission_command(mission_text)
    entry = _RESOLVERS.get(cmd) if cmd else None
    if entry is None:
        return None
    try:
        return entry[0](instance_dir, mission_text)
    except Exception as e:  # a resolver must never break reconcile
        log.error("mission result resolver for /%s failed: %s", cmd, e)
        return None


def always_inline_keys(mission_text: str) -> List[str]:
    """Keys the store must keep inline even when the result spills."""
    cmd = _mission_command(mission_text)
    entry = _RESOLVERS.get(cmd) if cmd else None
    return list(entry[1]) if entry else []


def _resolve_review_result(instance_dir: Path, mission_text: str) -> Optional[dict]:
    m = _PR_URL_RE.search(mission_text or "")
    if not m:
        return None
    from app.github_url_parser import parse_pr_url
    owner, repo, pr_number = parse_pr_url(m.group(0))
    sidecar = instance_dir / ".review-findings" / f"{owner}_{repo}_{pr_number}.json"
    if not sidecar.exists():
        return None
    data = json.loads(sidecar.read_text())
    return {
        "kind": "review",
        "file_comments": data.get("file_comments", []),
        "review_summary": data.get("review_summary", {}),
    }


register_resolver(["review", "ultrareview"], _resolve_review_result,
                  always_inline=["kind", "review_summary"])
