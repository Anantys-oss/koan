"""Reusable locked file operations for JSON and JSONL persistence.

Centralizes the lock-read-modify-write pattern used across many modules.
Reduces duplication and ensures consistent error handling (try/finally
for lock release, atomic writes for JSON modifications).

Two locking strategies are used, matching existing conventions:

- **JSON files** use a *separate* lock file (``<dir>/.<stem>.lock``).
  Writers hold the lock across the full read-modify-write cycle and
  persist changes via :func:`app.utils.atomic_write` (temp + rename).

- **JSONL files** lock the *data file itself*.  Writers append under
  ``LOCK_EX``; readers snapshot under ``LOCK_SH``.
"""

import fcntl
import json
from pathlib import Path
from typing import Any, Callable, List, Optional, TypeVar

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Lock-path derivation
# ---------------------------------------------------------------------------

def _default_lock_path(path: Path) -> Path:
    """Derive a lock file path from a data file path.

    Example: ``/instance/.check-tracker.json`` → ``/instance/.check-tracker.lock``
    """
    stem = path.stem.lstrip(".")
    return path.parent / f".{stem}.lock"


# ---------------------------------------------------------------------------
# JSON: locked modify (read-modify-write under exclusive lock)
# ---------------------------------------------------------------------------

def locked_json_modify(
    path: Path,
    fn: Callable[[Any], T],
    *,
    default_factory: Optional[Callable[[], Any]] = None,
    lock_path: Optional[Path] = None,
    indent: Optional[int] = None,
    validator: Optional[Callable[[Any], Any]] = None,
) -> T:
    """Acquire exclusive lock, load JSON, apply *fn*, save atomically.

    *fn* receives the loaded data and **mutates it in place**.  The
    (mutated) data is then saved back to *path* via :func:`atomic_write`.
    Whatever *fn* returns is forwarded to the caller — this lets callers
    return a status value (e.g. ``True``/``False``) separately from the
    data mutation.

    Args:
        path: Path to the JSON data file.
        fn: Callable that receives the loaded data, mutates it, and
            optionally returns a value for the caller.
        default_factory: Called when the file is missing or contains
            invalid JSON.  Defaults to ``dict``.
        lock_path: Explicit lock file.  If *None*, derived automatically
            via :func:`_default_lock_path`.
        indent: JSON indentation level for pretty-printing.  *None*
            produces compact output.
        validator: Optional callable to validate/transform loaded data
            before passing to *fn*.  If the loaded data has the wrong
            type, return *default_factory()* to reset gracefully.

    Returns:
        Whatever *fn* returns.
    """
    from app.utils import atomic_write

    if default_factory is None:
        default_factory = dict

    lock = lock_path or _default_lock_path(path)
    with open(lock, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            # Load
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    data = default_factory()
            else:
                data = default_factory()

            # Validate/transform if needed
            if validator is not None:
                data = validator(data)

            # Modify
            result = fn(data)

            # Save (atomic temp-file + rename)
            atomic_write(path, json.dumps(data, ensure_ascii=False, indent=indent) + "\n")

            return result
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# JSON: locked read (shared lock)
# ---------------------------------------------------------------------------

def locked_json_read(
    path: Path,
    *,
    default: Any = None,
    lock_path: Optional[Path] = None,
) -> Any:
    """Read and parse a JSON file under a shared (``LOCK_SH``) lock.

    Args:
        path: Path to the JSON data file.
        default: Returned when the file is missing or contains invalid JSON.
        lock_path: Explicit lock file.  If *None*, derived automatically.

    Returns:
        The parsed JSON data, or *default*.
    """
    if not path.exists():
        return default

    lock = lock_path or _default_lock_path(path)
    try:
        with open(lock, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_SH)
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError):
        return default


# ---------------------------------------------------------------------------
# JSONL: locked append (exclusive lock on data file)
# ---------------------------------------------------------------------------

def locked_jsonl_append(path: Path, record: dict) -> None:
    """Append a JSON record as one line to a JSONL file under exclusive lock.

    Locks the data file itself (not a sidecar), matching the existing
    convention in ``conversation_history.py``, ``reaction_store.py``, etc.
    """
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# JSONL: locked read (shared lock on data file)
# ---------------------------------------------------------------------------

def locked_jsonl_read(path: Path) -> List[str]:
    """Read all lines from a JSONL file under a shared lock.

    Returns raw line strings (including trailing newlines).  The caller
    is responsible for parsing — this keeps the utility format-agnostic
    and avoids swallowing parse errors silently.

    Returns an empty list when the file does not exist.
    """
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            return f.readlines()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
