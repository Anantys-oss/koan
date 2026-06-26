"""Per-mission file-touch provenance.

After each successful mission, ``record_provenance`` appends a JSONL line to
``instance/.mission-provenance.jsonl`` describing which files the mission
changed. ``read_provenance`` answers the reverse query: given a file, which
missions touched it? Foundational for churn detection (#2127) and
prompt-time "this file is hot" warnings (#2128).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List

# Reuse the already-implemented diff + git helpers from the security pass.
from app.security_review import get_changed_files, _run_git
from app.locked_file import locked_jsonl_append_capped, locked_jsonl_read

_PROVENANCE_FILE = ".mission-provenance.jsonl"
_MAX_PROVENANCE_ENTRIES = 2000


def _head_sha(project_path: str) -> str:
    """Return the current HEAD sha, or '' on any git failure (no raise)."""
    return _run_git(project_path, "rev-parse", "HEAD")


def _resolve_base_branch(instance_dir: str, project_name: str) -> str:
    """Resolve the project's base branch, defaulting to 'main' on any error."""
    try:
        import os
        from app.projects_config import load_projects_config, get_project_auto_merge

        koan_root = os.environ.get("KOAN_ROOT", str(Path(instance_dir).parent))
        config = load_projects_config(koan_root)
        if not config:
            return "main"
        return get_project_auto_merge(config, project_name).get("base_branch", "main")
    except Exception:
        return "main"


def record_provenance(
    instance_dir: str,
    project_name: str,
    project_path: str,
    mission_title: str,
) -> None:
    """Append a provenance record for the just-completed mission.

    Best-effort: tolerates empty file lists (writes ``"files": []``) and a
    missing/failed git repo (``commit_sha`` becomes ``""``). Capped at
    ``_MAX_PROVENANCE_ENTRIES`` lines via oldest-first rotation.

    NOTE: deliberately has NO ``pipeline_expired`` parameter — the pipeline
    passes that kwarg to ``_PipelineTracker.run_step``, which consumes it
    itself and never forwards it here.
    """
    base_branch = _resolve_base_branch(instance_dir, project_name)
    files = get_changed_files(project_path, base_branch)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mission": mission_title or "",
        "project": project_name,
        "commit_sha": _head_sha(project_path),
        "files": files,
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
    for raw in locked_jsonl_read(path):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if rec.get("project") != project:
            continue
        if file_path in (rec.get("files") or []):
            matches.append(rec)
    return matches[-limit:] if limit > 0 else matches
