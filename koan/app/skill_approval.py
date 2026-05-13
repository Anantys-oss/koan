"""Kōan -- Skill approval gate.

Newly installed or scaffolded skills are written to ``instance/skills/`` with
a ``.koan-pending`` sidecar containing a SHA-256 fingerprint of the directory
contents. The registry skips any skill whose own directory (or an ancestor up
to ``instance/skills/``) carries the marker, so the bridge will not load and
exec the handler until the operator runs ``/skill approve <ref> <fingerprint>``
and clears the marker.

The fingerprint that gets echoed to Telegram forces an attacker to know the
exact on-disk contents to approve a malicious install — a blind injection
(prompt injection or message-forwarding attack) cannot guess it.
"""

import hashlib
import re
from pathlib import Path
from typing import Optional

from app.utils import atomic_write


MARKER_NAME = ".koan-pending"

# Scope refs accepted by /skill approve: <scope> or <scope>/<name>.
_REF_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_-]*)(?:/([A-Za-z0-9_][A-Za-z0-9_]*))?$")


def compute_fingerprint(skill_dir: Path) -> str:
    """Compute a deterministic SHA-256 fingerprint of a skill directory.

    Hashes the canonical concatenation of ``<rel_path>\\0<sha256(content)>\\n``
    for every regular file under ``skill_dir`` sorted by POSIX relative path.
    The marker file itself is excluded so the fingerprint is stable across
    mark / approve cycles.
    """
    hasher = hashlib.sha256()
    entries = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.name == MARKER_NAME:
            continue
        rel = path.relative_to(skill_dir).as_posix()
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{rel}\0{content_hash}\n")
    for entry in entries:
        hasher.update(entry.encode("utf-8"))
    return hasher.hexdigest()


def mark_pending(skill_dir: Path, fingerprint: str) -> None:
    """Write the pending marker for a freshly installed skill directory."""
    atomic_write(skill_dir / MARKER_NAME, fingerprint + "\n")


def clear_pending(skill_dir: Path) -> None:
    """Remove the pending marker. Idempotent."""
    marker = skill_dir / MARKER_NAME
    try:
        marker.unlink()
    except FileNotFoundError:
        pass


def read_pending_fingerprint(skill_dir: Path) -> Optional[str]:
    """Return the stored fingerprint hex, or None if no marker."""
    marker = skill_dir / MARKER_NAME
    if not marker.is_file():
        return None
    return marker.read_text().strip() or None


def find_pending_ancestor(skill_md: Path, skills_root: Path) -> Optional[Path]:
    """Walk up from ``skill_md.parent`` and return the first directory that
    carries the pending marker, stopping at (but not checking) ``skills_root``.

    Used by ``build_registry`` to filter pending skills at discovery time.
    """
    try:
        skill_md = skill_md.resolve()
        skills_root_r = skills_root.resolve()
    except OSError:
        return None
    current = skill_md.parent
    while True:
        if current == skills_root_r:
            return None
        if (current / MARKER_NAME).is_file():
            return current
        if current.parent == current:
            return None
        try:
            current.relative_to(skills_root_r)
        except ValueError:
            return None
        current = current.parent


def resolve_pending_dir(instance_dir: Path, ref: str) -> Optional[Path]:
    """Resolve a ``<scope>`` or ``<scope>/<name>`` ref to its pending dir.

    Returns the directory if and only if it exists, lives under
    ``instance_dir / skills`` and currently carries the marker. Rejects any
    path-like input (``..``, absolute paths, extra slashes).
    """
    if not ref:
        return None
    match = _REF_RE.match(ref.strip())
    if not match:
        return None
    scope, name = match.group(1), match.group(2)
    skills_root = (instance_dir / "skills").resolve()
    target = skills_root / scope
    if name:
        target = target / name
    if not target.is_dir():
        return None
    try:
        resolved = target.resolve()
        resolved.relative_to(skills_root)
    except (OSError, ValueError):
        return None
    if not (resolved / MARKER_NAME).is_file():
        return None
    return resolved
