"""Per-mission file-touch provenance.

After each successful mission, ``record_provenance`` appends a JSONL line to
``instance/.mission-provenance.jsonl`` describing which files the mission
changed. ``read_provenance`` answers the reverse query: given a file, which
missions touched it? Foundational for churn detection (#2127) and
prompt-time "this file is hot" warnings (#2128).
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

# Reuse the already-implemented diff + git helpers from the security pass.
from app.security_review import get_changed_files, _run_git
from app.locked_file import locked_jsonl_append_capped, locked_jsonl_read
from app.projects_config import resolve_base_branch
from app.run_log import log_safe

_PROVENANCE_FILE = ".mission-provenance.jsonl"
_MAX_PROVENANCE_ENTRIES = 2000


def _head_sha(project_path: str) -> str:
    """Return the current HEAD sha, or '' on any git failure (no raise)."""
    return _run_git(project_path, "rev-parse", "HEAD")


def _run_git_rc(project_path: str, *args: str, timeout: int = 30) -> Tuple:
    """Run git; return ``(returncode, stdout)``. ``returncode`` is ``None`` on
    an exec failure (timeout, missing binary, bad cwd). Unlike
    ``security_review._run_git`` this exposes the exit code so callers can tell
    a successful empty result apart from a failed command.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True, text=True,
            cwd=project_path, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        return result.returncode, result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None, ""


def _empty_diff_is_genuine(project_path: str, base_branch: str) -> bool:
    """True only when an empty change set is a real no-op, not a failed diff.

    Mirrors the ref-fallback order in ``get_changed_files``. The first base ref
    that resolves to a commit decides: its ``git diff`` must exit cleanly
    (``rc == 0``) for the empty file list to count as a genuine no-op. If no
    candidate ref resolves, or the diff command for the resolved ref fails
    (lock contention, OOM, timeout), the empty list is the product of a broken
    read and the record is flagged incomplete.
    """
    for ref in (f"upstream/{base_branch}", f"origin/{base_branch}", base_branch):
        if not _run_git(project_path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
            continue
        rc, _out = _run_git_rc(project_path, "diff", "--name-only", f"{ref}...HEAD")
        return rc == 0
    return False


def record_provenance(
    instance_dir: str,
    project_name: str,
    project_path: str,
    mission_title: str,
) -> None:
    """Append a provenance record for the just-completed mission.

    Best-effort: tolerates empty file lists (writes ``"files": []``) and a
    missing/failed git repo (``commit_sha`` becomes ``""``). The
    ``incomplete`` flag is set when either HEAD or the changed-file diff
    could not be reliably obtained, so a failed read is distinguishable from
    a genuine no-op. Capped at ``_MAX_PROVENANCE_ENTRIES`` lines via
    oldest-first rotation.

    NOTE: deliberately has NO ``pipeline_expired`` parameter — the pipeline
    passes that kwarg to ``_PipelineTracker.run_step``, which consumes it
    itself and never forwards it here.
    """
    base_branch = resolve_base_branch(project_name, project_path)
    files = get_changed_files(project_path, base_branch)
    commit_sha = _head_sha(project_path)
    # Mark the record incomplete when git could not be read reliably, so
    # downstream churn/hot-file analysis can tell a broken-git read from a
    # genuine no-op. Two failure modes: (1) HEAD unresolvable -> empty sha;
    # (2) an empty file list that came from a failed diff rather than a real
    # no-op -- caught by re-diffing against the resolved base ref and checking
    # the command's exit code, so even a transient diff failure (lock
    # contention, OOM) against an existing ref flags the record incomplete.
    files_unreliable = not files and not _empty_diff_is_genuine(project_path, base_branch)
    incomplete = (not commit_sha) or files_unreliable
    if incomplete:
        log_safe(
            "error",
            f"provenance: git read failed for {project_name} "
            f"({project_path}); recording incomplete entry",
        )
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mission": mission_title or "",
        "project": project_name,
        "commit_sha": commit_sha,
        "files": files,
        "incomplete": incomplete,
    }
    path = Path(instance_dir) / _PROVENANCE_FILE
    locked_jsonl_append_capped(path, record, _MAX_PROVENANCE_ENTRIES)


def read_provenance(
    instance_dir: str,
    project: str,
    file_path: str,
    limit: int = 20,
) -> List[dict]:
    """Return up to *limit* most-recent missions that touched *file_path*.

    Filters by both project and file membership. Most-recent-last in the
    file; the returned slice is the newest *limit*, oldest-first.
    """
    path = Path(instance_dir) / _PROVENANCE_FILE
    matches: List[dict] = []
    for idx, raw in enumerate(locked_jsonl_read(path)):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            log_safe("error", f"provenance: skipping malformed line {idx} in {path}")
            continue
        if rec.get("project") != project:
            continue
        if file_path in (rec.get("files") or []):
            matches.append(rec)
    return matches[-limit:] if limit > 0 else matches
