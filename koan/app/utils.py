#!/usr/bin/env python3
"""
Kōan -- Shared utilities

Core shared utilities used across modules:
- load_dotenv: .env file loading
- load_config: config.yaml loading
- parse_project: [project:name] / [projet:name] tag extraction
- atomic_write: crash-safe file writes
- insert_pending_mission: append mission to missions.md pending section
- modify_missions_file: locked read-modify-write on missions.md
- get_known_projects / resolve_project_path: project registry
- append_to_outbox: outbox file appending

Configuration, journal, and telegram history functions have been
extracted to dedicated modules (config.py, journal.py, conversation_history.py).
Backward-compatible re-exports are provided below.
"""

import contextlib
import fcntl
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import yaml
from pathlib import Path
from typing import List, Optional, Tuple


if "KOAN_ROOT" not in os.environ:
    raise SystemExit("KOAN_ROOT environment variable is not set. Run via 'make run' or 'make awake'.")
KOAN_ROOT = Path(os.environ["KOAN_ROOT"])

# Single source of truth for the project-name character class.
# Dots are allowed because project names may be domain-like, e.g. developers.esphome.io.
# Extend here (not in scattered call sites) when the allowed character set changes.
PROJECT_NAME_CHARS = r"a-zA-Z0-9_.-"

# Bracketed inline tag, capturing form: [project:X] / [projet:X]
PROJECT_TAG_RE = re.compile(rf'\[projec?t:([{PROJECT_NAME_CHARS}]+)\]', re.IGNORECASE)
# Bracketed inline tag, strip form (with trailing whitespace consumed).
PROJECT_TAG_STRIP_RE = re.compile(rf'\[projec?t:[{PROJECT_NAME_CHARS}]+\]\s*', re.IGNORECASE)
# Anchored prefix form (used to peel a leading tag off a mission line).
PROJECT_TAG_PREFIX_RE = re.compile(rf'^\[projec?t:([{PROJECT_NAME_CHARS}]+)\]\s*', re.IGNORECASE)
# Full alternation form with surrounding whitespace (dashboard / template-side parity).
PROJECT_TAG_FULL_RE = re.compile(rf'\s*\[(?:project|projet):([{PROJECT_NAME_CHARS}]+)\]\s*', re.IGNORECASE)
# Markdown sub-header form: "### project:name" / "### projet:name"
PROJECT_SUBHEADER_RE = re.compile(rf'###\s+projec?t\s*:\s*([{PROJECT_NAME_CHARS}]+)', re.IGNORECASE)
# Natural-text hint form: "(projet: name)" / "projet:name" (no brackets)
PROJECT_HINT_RE = re.compile(rf'\(?\s*projec?t\s*:\s*([{PROJECT_NAME_CHARS}]+)\s*\)?', re.IGNORECASE)
# Unbracketed *trailing* hint: "... project:name" at the very end of the text.
# Anchored to end (and requires leading whitespace) so a "project:" mid-sentence
# is never misread as a tag — used as a lenient fallback for command input.
PROJECT_TRAILING_HINT_RE = re.compile(rf'\s+projec?t:([{PROJECT_NAME_CHARS}]+)\s*$', re.IGNORECASE)

_MISSIONS_DEFAULT = "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
_MISSIONS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Core utilities (stay here)
# ---------------------------------------------------------------------------

def read_timestamp_file(path) -> Optional[float]:
    """Read a file containing a single epoch timestamp.

    Returns the timestamp as float, or None if the file is missing
    or its content cannot be parsed.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        return float(path.read_text().strip())
    except (ValueError, OSError):
        return None


def get_file_age_seconds(path) -> Optional[float]:
    """Return how many seconds ago a timestamp stored in *path* was written.

    Combines :func:`read_timestamp_file` with the current wall-clock time.
    Returns None when the file is missing or unparseable.
    """
    ts = read_timestamp_file(path)
    if ts is None:
        return None
    return time.time() - ts


def load_dotenv():
    """Load .env file from the project root, stripping quotes from values.

    Uses os.environ.setdefault so existing env vars are not overwritten.
    """
    env_path = KOAN_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_config() -> dict:
    """Load configuration from instance/config.yaml.

    Returns the full config dict, or empty dict if file doesn't exist.
    """
    config_path = KOAN_ROOT / "instance" / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as e:
        print(f"[utils] Error loading config: {e}")
        return {}


# Track whether we've already logged the deprecation warning
_cli_provider_warned = False


def get_cli_provider_env() -> str:
    """Get CLI provider from environment variables.

    Reads KOAN_CLI_PROVIDER (primary) with fallback to CLI_PROVIDER (deprecated).
    Logs a deprecation warning once per process if the fallback is used.

    Returns:
        The environment variable value (lowercase, stripped), or empty string if neither is set.
    """
    global _cli_provider_warned

    # Primary: KOAN_CLI_PROVIDER
    value = os.environ.get("KOAN_CLI_PROVIDER", "").strip().lower()
    if value:
        return value

    # Fallback: CLI_PROVIDER (deprecated)
    fallback = os.environ.get("CLI_PROVIDER", "").strip().lower()
    if fallback:
        if not _cli_provider_warned:
            print("[utils] Warning: CLI_PROVIDER is deprecated. Use KOAN_CLI_PROVIDER instead.")
            _cli_provider_warned = True
        return fallback

    return ""


def parse_project(text: str) -> Tuple[Optional[str], str]:
    """Extract [project:name] or [projet:name] from text.

    Returns (project_name, cleaned_text) where cleaned_text has the tag removed.
    Returns (None, text) if no tag found.
    """
    match = PROJECT_TAG_RE.search(text)
    if match:
        project = match.group(1)
        cleaned = PROJECT_TAG_STRIP_RE.sub('', text).strip()
        return project, cleaned
    return None, text


def parse_project_lenient(text: str) -> Tuple[Optional[str], str]:
    """Extract a project, accepting both bracketed and trailing-inline forms.

    Tries the canonical ``[project:name]`` tag first (via :func:`parse_project`);
    if absent, falls back to an unbracketed **trailing** ``project:name`` hint
    (e.g. ``run the audit project:yarn``). The fallback is anchored to the end
    of the string, so a ``project:`` appearing mid-sentence is never mistaken
    for a tag.

    Returns ``(project_name, cleaned_text)`` with the tag/hint removed, or
    ``(None, text)`` when neither form is present. Use this for human command
    input (e.g. ``/daily``) where forgetting the brackets should not silently
    drop the project.
    """
    project, cleaned = parse_project(text)
    if project is not None:
        return project, cleaned
    match = PROJECT_TRAILING_HINT_RE.search(text)
    if match:
        project = match.group(1)
        cleaned = PROJECT_TRAILING_HINT_RE.sub('', text).strip()
        return project, cleaned
    return None, text


def load_project_aliases() -> dict:
    """Load project aliases from instance/.project-aliases.json.

    Returns a dict mapping shortcut (lowercase) -> canonical project name.
    """
    import json
    aliases_path = KOAN_ROOT / "instance" / ".project-aliases.json"
    if not aliases_path.exists():
        return {}
    try:
        return json.loads(aliases_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def resolve_project_alias(name: str) -> Optional[str]:
    """Resolve a project alias to its canonical project name.

    Returns the canonical project name if *name* is a known alias,
    or None if it isn't.
    """
    aliases = load_project_aliases()
    return aliases.get(name.lower())


def detect_project_from_text(text: str) -> Tuple[Optional[str], str]:
    """Detect project name or alias from the first word of text.

    If the first word matches a known project name (case-insensitive)
    or a project alias, returns (project_name, remaining_text).
    Otherwise returns (None, text).
    """
    parts = text.strip().split(None, 1)
    if not parts:
        return None, text

    first_word = parts[0].lower()
    known = get_known_projects()
    project_names = {name.lower(): name for name, _path in known}

    if first_word in project_names:
        remaining = parts[1].strip() if len(parts) > 1 else ""
        return project_names[first_word], remaining

    # Alias fallback
    alias_project = resolve_project_alias(first_word)
    if alias_project:
        remaining = parts[1].strip() if len(parts) > 1 else ""
        return alias_project, remaining

    return None, text


# Pre-compiled regex for GitHub remote URL parsing (SSH and HTTPS)
_GITHUB_REMOTE_RE = re.compile(r'github\.com[:/]([^/]+)/([^/\s.]+?)(?:\.git)?$')


def get_github_remote(project_path: str) -> Optional[str]:
    """Extract owner/repo from a project's git remote.

    Tries 'origin' first, falls back to 'upstream'.
    Returns "owner/repo" as a normalized lowercase string, or None on failure.
    """
    for remote in ("origin", "upstream"):
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", remote],
                stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=5,
                cwd=project_path,
            )
            if result.returncode != 0:
                continue
            url = result.stdout.strip()
            match = _GITHUB_REMOTE_RE.search(url)
            if match:
                owner = match.group(1).lower()
                repo = match.group(2).lower()
                return f"{owner}/{repo}"
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
    return None


def get_all_github_remotes(project_path: str) -> List[str]:
    """Extract owner/repo from ALL git remotes in a project.

    Returns a list of "owner/repo" strings (normalized lowercase) for every
    remote that points to GitHub.  Useful for matching a GitHub URL against
    a local project that may have both an origin (fork) and an upstream.
    """
    remotes: List[str] = []
    try:
        result = subprocess.run(
            ["git", "remote"],
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=5,
            cwd=project_path,
        )
        if result.returncode != 0:
            return remotes
        remote_names = result.stdout.strip().splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return remotes

    for remote in remote_names:
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", remote.strip()],
                stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=5,
                cwd=project_path,
            )
            if result.returncode != 0:
                continue
            url = result.stdout.strip()
            match = _GITHUB_REMOTE_RE.search(url)
            if match:
                owner = match.group(1).lower()
                repo = match.group(2).lower()
                slug = f"{owner}/{repo}"
                if slug not in remotes:
                    remotes.append(slug)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
    return remotes


def atomic_write(path: Path, content: str):
    """Write content to a file atomically using write-to-temp + rename.

    Prevents data loss if the process crashes mid-write. Uses an exclusive
    lock on the temp file to serialize concurrent writers.
    """
    dir_path = path.parent
    fd, tmp = tempfile.mkstemp(dir=str(dir_path), prefix=".koan-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_write_json(path: Path, data, indent=None):
    """Serialize ``data`` to JSON and write atomically via :func:`atomic_write`.

    Convenience wrapper used by modules that persist dicts/lists as JSON.
    """
    import json
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=indent))


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate text with indicator."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...(truncated)"


def truncate_diff(diff: str, max_chars: int) -> str:
    """Truncate a unified diff intelligently, preserving whole file blocks.

    Instead of cutting at an arbitrary character offset (which leaves the
    reviewer guessing what was cut), this splits the diff into per-file
    blocks and keeps as many complete blocks as fit within *max_chars*.
    Files that don't fit are listed as a summary at the end so the
    reviewer knows they exist.
    """
    if not diff or len(diff) <= max_chars:
        return diff

    # Split into per-file blocks at 'diff --git' boundaries.
    raw_blocks = re.split(r'(?=^diff --git )', diff, flags=re.MULTILINE)
    blocks = [b for b in raw_blocks if b.strip()]

    if not blocks:
        # Can't parse structure — fall back to character truncation.
        return truncate_text(diff, max_chars)

    # Pre-scan filenames so we can estimate the worst-case footer size
    # and reserve budget for it, ensuring output stays within max_chars.
    filenames: list[str] = []
    for block in blocks:
        m = re.match(r'diff --git a/\S+ b/(\S+)', block)
        filenames.append(m.group(1) if m else "(unknown file)")

    # Greedy first pass: keep blocks that fit without any footer.
    kept: list[str] = []
    skipped: list[str] = []
    used = 0

    for block, name in zip(blocks, filenames, strict=True):
        if used + len(block) <= max_chars:
            kept.append((block, name))
            used += len(block)
        else:
            skipped.append(name)

    # If we skipped files, we need a footer — trim kept blocks until
    # the footer fits too.
    while skipped and kept:
        footer = _build_footer(skipped, len(kept))
        if used + len(footer) <= max_chars:
            break
        # Drop the last kept block to make room for the footer.
        dropped_block, dropped_name = kept.pop()
        used -= len(dropped_block)
        skipped.insert(0, dropped_name)

    result = "".join(b for b, _ in kept)
    if skipped:
        result += _build_footer(skipped, len(kept))
    return result


def _build_footer(skipped: list[str], kept_count: int) -> str:
    """Build the omitted-files footer string."""
    listing = "\n".join(f"  - {f}" for f in skipped)
    return (
        f"\n\n...(diff truncated — {len(skipped)} file(s) omitted, "
        f"{kept_count} file(s) shown)\n"
        f"Omitted files:\n{listing}\n"
    )



def _locked_missions_rw(missions_path: Path, transform):
    """Read-modify-write missions.md with crash-safe atomic writes.

    Uses a separate lock file for cross-process synchronization so that
    the data file can be replaced atomically via temp + rename. A process
    crash between truncate() and write() previously risked leaving
    missions.md empty; this pattern eliminates that window entirely.

    Args:
        missions_path: Path to missions.md
        transform: Callable(content: str) -> str that returns modified content.

    Returns the transformed content.
    """
    lock_path = missions_path.with_suffix(".lock")
    missions_path = Path(missions_path)

    with _MISSIONS_LOCK:
        # Ensure parent directory exists (for first-run or test scenarios)
        missions_path.parent.mkdir(parents=True, exist_ok=True)

        with open(lock_path, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            try:
                # Read current content (or default if missing/empty)
                if missions_path.exists():
                    content = missions_path.read_text(encoding="utf-8")
                else:
                    content = ""
                if not content.strip():
                    content = _MISSIONS_DEFAULT

                new_content = transform(content)

                # Atomic write: temp file + rename (same dir = same filesystem)
                fd, tmp = tempfile.mkstemp(
                    dir=str(missions_path.parent), prefix=".missions-",
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(new_content)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, str(missions_path))
                except BaseException:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp)
                    raise
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)

    return new_content


def insert_pending_mission(
    missions_path: Path, entry: str, *, urgent: bool = False,
) -> bool:
    """Insert a mission entry into the pending section of missions.md.

    By default, inserts at the bottom of the pending section (FIFO queue).
    When urgent=True, inserts at the top (next to be picked up).

    Uses file locking for the entire read-modify-write cycle to prevent
    TOCTOU race conditions between awake.py and dashboard.py.
    Creates the file with default structure if it doesn't exist.

    Returns:
        True if the mission was inserted, False if it was a duplicate
        (same command + URL already pending or in progress).
    """
    from app.missions import insert_mission, is_duplicate_mission

    inserted = True

    def _transform(content: str) -> str:
        nonlocal inserted
        if is_duplicate_mission(content, entry):
            inserted = False
            return content
        return insert_mission(content, entry, urgent=urgent)

    _locked_missions_rw(missions_path, _transform)
    return inserted


def modify_missions_file(missions_path: Path, transform):
    """Apply a transform function to missions.md content with file locking.

    Args:
        missions_path: Path to missions.md
        transform: Callable(content: str) -> str that returns modified content.

    Returns the transformed content.
    """
    return _locked_missions_rw(missions_path, transform)


def _get_known_projects_for_root(koan_root: Path) -> list:
    """Return sorted list of (name, path) tuples for a specific Koan root."""
    # 1. Try merged registry (projects.yaml + workspace/)
    try:
        from app.projects_merged import get_all_projects
        result = get_all_projects(str(koan_root))
        if result:
            return result
    except Exception as e:
        print(f"[utils] Merged project registry failed: {e}", file=sys.stderr)

    # 2. Try projects.yaml alone (fallback if merged module fails)
    try:
        from app.projects_config import load_projects_config, get_projects_from_config
        config = load_projects_config(str(koan_root))
        if config is not None:
            return get_projects_from_config(config)
    except Exception as e:
        print(f"[utils] projects.yaml loader failed: {e}", file=sys.stderr)

    # 3. KOAN_PROJECTS env var
    projects_str = os.environ.get("KOAN_PROJECTS", "")
    if projects_str:
        result = []
        for pair in projects_str.split(";"):
            pair = pair.strip()
            if ":" in pair:
                name, path = pair.split(":", 1)
                result.append((name.strip(), path.strip()))
        return sorted(result, key=lambda x: x[0].lower())

    return []


def get_known_projects() -> list:
    """Return sorted list of (name, path) tuples.

    Resolution order:
    1. Merged registry: projects.yaml + workspace/ (if either exists)
    2. KOAN_PROJECTS env var (fallback)

    Returns empty list if none is configured.
    """
    return _get_known_projects_for_root(KOAN_ROOT)


def is_known_project(name: str) -> bool:
    """Check if a name matches a known project (case-insensitive)."""
    try:
        return name.lower() in {n.lower() for n, _ in get_known_projects()}
    except Exception as e:
        print(f"[utils] is_known_project error: {e}", file=sys.stderr)
        return False


def _normalise_project_path_for_match(project_path: str) -> Optional[Path]:
    """Normalize a project path for identity comparisons."""
    if not project_path:
        return None
    try:
        return Path(project_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def find_known_project_name_for_path(
    project_path: str,
    koan_root: Optional[str] = None,
) -> Optional[str]:
    """Return the configured or workspace project name for a local path.

    Unlike ``project_name_for_path``, returns ``None`` when there is no merged
    registry match so callers can decide whether to warn before falling back.
    """
    target = _normalise_project_path_for_match(project_path)
    if target is None:
        return None

    root = Path(koan_root) if koan_root else KOAN_ROOT
    for name, path in _get_known_projects_for_root(root):
        candidate = _normalise_project_path_for_match(path)
        if candidate == target:
            return name

    workspace = _normalise_project_path_for_match(str(root / "workspace"))
    if workspace is not None:
        try:
            relative = target.relative_to(workspace)
        except ValueError:
            relative = None
        if relative is not None and len(relative.parts) == 1:
            name = relative.parts[0]
            if name and not name.startswith("."):
                return name
    return None


def project_name_for_path(project_path: str) -> str:
    """Get the project name for a given local path.

    Checks known projects first; falls back to the directory basename.
    """
    known = find_known_project_name_for_path(project_path)
    if known:
        return known
    return Path(project_path).name if project_path else ""


def _find_partial_name_candidates(
    repo_lower: str, projects: list
) -> list:
    """Find projects whose name/basename partially matches the repo name.

    Catches aliased clones: e.g. repo "perl-convert-asn1" with local dir
    "convert-asn1".  Matches when one name is a dash-separated suffix of
    the other.

    Returns a list of (name, path) tuples — candidates to validate via remote.
    """
    candidates = []
    for name, path in projects:
        name_lower = name.lower()
        basename_lower = Path(path).name.lower()
        for local in (name_lower, basename_lower):
            if local == repo_lower:
                continue  # Already handled by exact-match steps
            # repo name ends with -<local> (e.g., "perl-convert-asn1" ends with "-convert-asn1")
            if repo_lower.endswith(f"-{local}") or repo_lower.endswith(f"_{local}"):
                candidates.append((name, path))
                break
            # local name ends with -<repo> (e.g., local "perl-convert-asn1" for repo "convert-asn1")
            if local.endswith(f"-{repo_lower}") or local.endswith(f"_{repo_lower}"):
                candidates.append((name, path))
                break
    return candidates


def _persist_and_cache_remotes(
    name: str, path: str, all_remotes: list, projects: list
) -> None:
    """Persist discovered github remotes to yaml and in-memory cache."""
    primary = get_github_remote(path)
    try:
        from app.projects_config import load_projects_config, save_projects_config
        config = load_projects_config(str(KOAN_ROOT))
        if config and name in config.get("projects", {}):
            proj = config["projects"][name]
            if isinstance(proj, dict) and proj.get("path"):
                if primary and not proj.get("github_url"):
                    proj["github_url"] = primary
                proj["github_urls"] = all_remotes
                save_projects_config(str(KOAN_ROOT), config)
    except Exception as e:
        print(f"[utils] Failed to persist github_urls for {name}: {e}", file=sys.stderr)
    if primary:
        try:
            from app.projects_merged import set_github_url
            set_github_url(name, primary)
        except Exception as e:
            print(f"[utils] Failed to cache github_url for {name}: {e}", file=sys.stderr)


def _resolve_via_fork_parent(
    target: str, projects: list, config: Optional[dict] = None
) -> Optional[str]:
    """Resolve a GitHub repo via its fork parent.

    When ``target`` (owner/repo) isn't found in any local remote, ask GitHub
    whether it's a fork and try matching the parent repo instead. Handles the
    common case where a user provides a PR URL from their personal fork but
    the local project is cloned from the upstream org repo.

    Returns the project path if the fork's parent matches a known project,
    None otherwise.
    """
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{target}", "--jq", ".parent.full_name"],
            capture_output=True, text=True, timeout=10,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        parent_slug = result.stdout.strip().lower()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    if config is None:
        from app.projects_config import load_projects_config
        try:
            config = load_projects_config(str(KOAN_ROOT))
        except (OSError, ValueError):
            return None
    if not config:
        return None

    for project in config.get("projects", {}).values():
        if not isinstance(project, dict):
            continue
        gh_url = (project.get("github_url") or "").lower()
        if gh_url == parent_slug:
            path = project.get("path")
            if path:
                return path
        for u in project.get("github_urls", []):
            if u.lower() == parent_slug:
                path = project.get("path")
                if path:
                    return path

    # Also check in-memory cache (workspace projects not in projects.yaml)
    try:
        from app.projects_merged import get_github_url_cache
        for proj_name, gh_url in get_github_url_cache().items():
            if gh_url.lower() == parent_slug:
                for name, path in projects:
                    if name == proj_name:
                        return path
    except (ImportError, OSError):
        pass

    return None


def resolve_project_path(repo_name: str, owner: Optional[str] = None) -> Optional[str]:
    """Find local project path matching a repository name.

    Tries in order:
    1. GitHub URL match (if owner provided): check github_url and github_urls
       in projects.yaml — github_urls includes ALL remotes (origin, upstream, etc.)
       so cross-owner matches work on the fast path
    2. Exact match on project name (case-insensitive)
    3. Match on directory basename (case-insensitive)
    3b. Partial name match + remote validation: when the repo was cloned with
        a different local name (e.g., perl-Convert-ASN1 → Convert-ASN1), check
        if a project name/basename is a suffix of the repo name (or vice versa)
        and validate via git remotes.
    4. Auto-discover from ALL git remotes (if owner provided): subprocess
       fallback for projects not yet populated by ensure_github_urls()
    5. Fallback to single project if only one configured
    6. Cross-owner repo-name match (if owner provided): match the repo name
       against the repo component of configured github_url/github_urls.
       E.g. "contributor/repo" matches a project with github_url "org/repo".
       Only used when exactly one project matches (avoids ambiguity).
    7. Fork resolution via GitHub API (if owner provided): ask GitHub whether
       the target repo is a fork and match its parent against known projects.
       Handles the common case where a PR URL points to a contributor's fork.
    """
    projects = get_known_projects()
    target = f"{owner}/{repo_name}".lower() if owner else None

    # Config loaded once at step 1 and reused at steps 6 and 7 to avoid
    # triple-parsing projects.yaml on the same call.
    _projects_config: Optional[dict] = None

    # 1. GitHub URL match via projects.yaml and in-memory cache
    if target:
        try:
            from app.projects_config import load_projects_config
            _projects_config = load_projects_config(str(KOAN_ROOT))
            if _projects_config:
                for project in _projects_config.get("projects", {}).values():
                    if isinstance(project, dict):
                        # Check primary github_url
                        gh_url = project.get("github_url", "")
                        if gh_url and gh_url.lower() == target:
                            path = project.get("path")
                            if path:
                                return path
                        # Check all remotes (cross-owner: fork origin + upstream)
                        gh_urls = project.get("github_urls", [])
                        if target in (u.lower() for u in gh_urls):
                            path = project.get("path")
                            if path:
                                return path
        except Exception as e:
            print(f"[utils] GitHub URL match via projects.yaml failed: {e}", file=sys.stderr)
        # Also check in-memory github_url caches (workspace projects)
        try:
            from app.projects_merged import get_all_github_urls_cache, get_github_url_cache
            # Check primary URL cache
            for proj_name, gh_url in get_github_url_cache().items():
                if gh_url.lower() == target:
                    for name, path in projects:
                        if name == proj_name:
                            return path
            # Check all-URLs cache (covers forks with upstream remotes)
            for proj_name, urls in get_all_github_urls_cache().items():
                if target in (u.lower() for u in urls):
                    for name, path in projects:
                        if name == proj_name:
                            return path
        except Exception as e:
            print(f"[utils] GitHub URL cache lookup failed: {e}", file=sys.stderr)

    # 1b. Alias resolution — translate alias to canonical name before matching
    if not owner:
        canonical = resolve_project_alias(repo_name)
        if canonical:
            repo_name = canonical

    # 2. Exact match on project name
    for name, path in projects:
        if name.lower() == repo_name.lower():
            return path

    # 3. Match on directory basename
    for _name, path in projects:
        if Path(path).name.lower() == repo_name.lower():
            return path

    # 3b. Partial name match + remote validation
    #     Handles aliased clones: repo "perl-Convert-ASN1" cloned as "Convert-ASN1".
    #     Checks if a project name/basename is a suffix of the repo name (or vice
    #     versa) separated by a dash, then validates via git remote.
    if target:
        repo_lower = repo_name.lower()
        candidates = _find_partial_name_candidates(repo_lower, projects)
        for _cname, cpath in candidates:
            all_remotes = get_all_github_remotes(cpath)
            if target in all_remotes:
                _persist_and_cache_remotes(_cname, cpath, all_remotes, projects)
                return cpath

    # 4. Auto-discover from ALL git remotes (origin, upstream, etc.)
    #    This catches cross-owner matches: e.g. local origin is org/repo
    #    but the PR URL points to contributor/repo (the upstream remote).
    if target:
        for name, path in projects:
            all_remotes = get_all_github_remotes(path)
            if target in all_remotes:
                _persist_and_cache_remotes(name, path, all_remotes, projects)
                return path

    # 5. Fallback to single project (skip when owner-specific lookup found nothing)
    if not owner and len(projects) == 1:
        return projects[0][1]

    # 6. Cross-owner repo-name match: e.g. "contributor/repo" matches a project
    #    whose github_url is "org/repo" — same repo, different owner.
    #    Only used when exactly one project matches to avoid ambiguity.
    if target:
        repo_lower = repo_name.lower()
        try:
            config = _projects_config
            if config:
                candidates = []
                for project in config.get("projects", {}).values():
                    if not isinstance(project, dict):
                        continue
                    all_urls = []
                    gh_url = project.get("github_url")
                    if gh_url:
                        all_urls.append(gh_url)
                    all_urls.extend(project.get("github_urls", []))
                    for u in all_urls:
                        if "/" in u and u.rsplit("/", 1)[1].lower() == repo_lower:
                            path = project.get("path")
                            if path and path not in candidates:
                                candidates.append(path)
                            break
                if len(candidates) == 1:
                    return candidates[0]
        except Exception as e:
            print(f"[utils] Cross-owner repo-name match failed: {e}", file=sys.stderr)

    # 7. Fork resolution via GitHub API: when the URL points to a fork not
    #    in any local remote, ask GitHub for the parent repo and try matching.
    if target:
        resolved = _resolve_via_fork_parent(target, projects, config=_projects_config)
        if resolved:
            return resolved

    return None


def resolve_project_name_and_path(
    name: str,
) -> Tuple[str, Optional[str]]:
    """Resolve alias and find project path in one call.

    Returns (canonical_name, path_or_none).
    """
    canonical = resolve_project_alias(name) or name
    return canonical, resolve_project_path(canonical)


def append_to_outbox(outbox_path: Path, content: str, priority=None):
    """Append content to outbox.md with file locking.

    Safe to call from run.py via: python3 -c "from app.utils import append_to_outbox; ..."
    or from Python directly.

    Args:
        outbox_path: Path to outbox.md
        content: Message content to append
        priority: Optional NotificationPriority — when provided, prepends a
                  [priority:name] header so flush_outbox() can parse and apply
                  priority-based filtering. Legacy callers omitting priority
                  default to ACTION in flush_outbox().
    """
    if priority is not None:
        # Import here to avoid circular imports (utils is imported at module level
        # by many modules including notify.py which defines NotificationPriority)
        try:
            from app.notify import NotificationPriority
            if isinstance(priority, NotificationPriority):
                content = f"[priority:{priority.name.lower()}]\n{content}"
        except ImportError:
            pass  # If import fails, write without header (treated as action)

    with open(outbox_path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(content)
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Diff filtering utilities
# ---------------------------------------------------------------------------


def filter_diff_by_ignore(
    diff: str,
    glob_patterns: list,
    regex_patterns: list,
) -> "tuple[str, list[str]]":
    """Remove file hunks from a unified diff based on ignore patterns.

    Splits the unified diff at 'diff --git' boundaries and removes any
    file block whose path matches a glob or regex pattern.

    Args:
        diff: Unified diff string (as returned by GitHub).
        glob_patterns: List of glob patterns. Patterns without '/' are matched
            against the basename only (so '*.lock' matches at any depth).
            Patterns with '/' are matched against the full path.
        regex_patterns: List of regex patterns matched against the full path.
            Malformed patterns are skipped with a warning.

    Returns:
        (filtered_diff, skipped_files) tuple. filtered_diff is the diff with
        ignored file blocks removed. skipped_files is the list of file paths
        that were removed (for logging). Returns original diff unchanged if
        the diff cannot be split into file blocks (safety net).
    """
    import fnmatch
    import os
    import re as _re

    if not diff:
        return diff, []

    if not glob_patterns and not regex_patterns:
        return diff, []

    # Compile regex patterns once; log and skip malformed ones
    compiled_regexes = []
    for pat in regex_patterns:
        try:
            compiled_regexes.append(_re.compile(pat))
        except _re.error as e:
            print(
                f"[utils] filter_diff_by_ignore: skipping malformed regex {pat!r}: {e}",
                file=sys.stderr,
            )

    # Split diff into file blocks. Each block starts with 'diff --git'.
    # Re-join the delimiter with the block that follows it.
    raw_blocks = _re.split(r'(?=^diff --git )', diff, flags=_re.MULTILINE)

    # If splitting yields <=1 block, the format is unexpected — return unchanged
    if len(raw_blocks) <= 1:
        return diff, []

    def _should_ignore(path: str) -> bool:
        # Glob matching
        for pat in glob_patterns:
            if "/" in pat:
                if fnmatch.fnmatch(path, pat):
                    return True
            else:
                # Match against basename for patterns without slash
                if fnmatch.fnmatch(os.path.basename(path), pat):
                    return True
                # Also try full path for patterns like '*.generated'
                if fnmatch.fnmatch(path, pat):
                    return True
        # Regex matching against full path
        for rx in compiled_regexes:
            if rx.search(path):
                return True
        return False

    kept_blocks = []
    skipped_files = []
    _diff_git_re = _re.compile(r'^diff --git a/(.+) b/(.+)$', _re.MULTILINE)

    for block in raw_blocks:
        if not block.strip():
            # Preserve any leading whitespace/preamble before the first block
            kept_blocks.append(block)
            continue

        match = _diff_git_re.search(block)
        if not match:
            kept_blocks.append(block)
            continue

        # Use the b/ path as canonical (post-rename / current name)
        file_path = match.group(2)
        if _should_ignore(file_path):
            skipped_files.append(file_path)
        else:
            kept_blocks.append(block)

    return "".join(kept_blocks), skipped_files


# ---------------------------------------------------------------------------
# Backward-compatible re-exports
# ---------------------------------------------------------------------------
# These preserve existing `from app.utils import X` patterns.
# New code should import from the dedicated modules directly.

from app.config import (  # noqa: E402, F401
    get_chat_tools,
    get_mission_tools,
    get_allowed_tools,
    get_tools_description,
    get_model_config,
    get_start_on_pause,
    get_start_passive,
    get_max_runs,
    get_interval_seconds,
    get_fast_reply_model,
    get_branch_prefix,
    get_contemplative_chance,
    build_claude_flags,
    get_claude_flags_for_role,
    get_cli_binary_for_shell,
    get_cli_provider_name,
    get_auto_merge_config,
)

from app.journal import (  # noqa: E402, F401
    get_journal_file,
    read_all_journals,
    get_latest_journal,
    append_to_journal,
)

from app.conversation_history import (  # noqa: E402, F401
    save_conversation_message as save_telegram_message,
    load_recent_history as load_recent_telegram_history,
    format_conversation_history,
    compact_history as compact_telegram_history,
)
