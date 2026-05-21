"""Centralised file-locking helpers for JSON read-modify-write.

This module replaces the fcntl.flock boilerplate that was duplicated across
15+ modules.  Three helpers cover the common access patterns:

* :func:`locked_json_modify` — exclusive-lock read-modify-write for JSON files
* :func:`locked_json_read` — shared-lock read for JSON files
* :func:`locked_jsonl_append` — exclusive-lock append for JSONL files

All helpers use a **separate lock file** (``<data_path>.lock`` by default) so
that :func:`~app.utils.atomic_write` (temp + rename) does not break the lock.
"""

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Callable, TypeVar

from app.utils import atomic_write

T = TypeVar("T")


def _default_lock_path(data_path: Path) -> Path:
    """Derive lock-file path from data-file path."""
    return data_path.with_suffix(data_path.suffix + ".lock")


def _load_json(path: Path, default_factory: Callable[[], T]) -> T:
    """Load JSON from *path*, returning *default_factory()* on any failure."""
    if not path.exists():
        return default_factory()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_factory()
    # Validate type matches factory default (dict vs list)
    expected = default_factory()
    if not isinstance(data, type(expected)):
        return expected
    return data


def locked_json_modify(
    data_path: Path,
    fn: Callable[[T], Any],
    *,
    lock_path: Path | None = None,
    default_factory: Callable[[], T] = dict,
    indent: int | None = None,
) -> Any:
    """Read-modify-write a JSON file under an exclusive flock.

    *fn* receives the current data (loaded from *data_path*, or
    *default_factory()* when the file is missing / corrupt).  *fn* **mutates
    the data in place**.  The data object is saved after *fn* returns, and
    *fn*'s return value is propagated as this function's return value.

    Args:
        data_path: JSON file to operate on.
        fn: Callback that receives the current data.  Mutate in place.
        lock_path: Explicit lock-file path (default: ``<data_path>.lock``).
        default_factory: Called when the file is absent or unreadable.
        indent: JSON indent for pretty-printing (default: compact).

    Returns:
        Whatever *fn* returned.
    """
    data_path = Path(data_path)
    lp = Path(lock_path) if lock_path else _default_lock_path(data_path)

    with open(lp, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            data = _load_json(data_path, default_factory)
            result = fn(data)
            content = json.dumps(data, ensure_ascii=False, indent=indent)
            atomic_write(data_path, content + "\n")
            return result
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def locked_json_read(
    data_path: Path,
    *,
    lock_path: Path | None = None,
    default_factory: Callable[[], T] = dict,
) -> T:
    """Read a JSON file under a shared flock.

    Consistent with :func:`locked_json_modify` so concurrent readers do not
    see a partially-written file.

    Returns:
        Parsed JSON data, or *default_factory()* on failure.
    """
    data_path = Path(data_path)
    lp = Path(lock_path) if lock_path else _default_lock_path(data_path)

    if not data_path.exists():
        return default_factory()

    try:
        with open(lp, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_SH)
            try:
                return _load_json(data_path, default_factory)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except OSError:
        return default_factory()


def locked_jsonl_append(
    data_path: Path,
    record: dict,
    *,
    fsync: bool = False,
) -> None:
    """Append a JSON record to a JSONL file under an exclusive flock.

    Locks the **data file itself** (not a sidecar) since append-only writes
    don't benefit from atomic_write's temp-rename dance.

    Args:
        data_path: JSONL file to append to.
        record: Dict to serialise as one JSON line.
        fsync: Call ``os.fsync()`` after writing for durability.
    """
    data_path = Path(data_path)
    line = json.dumps(record, separators=(",", ":")) + "\n"

    with open(data_path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
