"""Shared log-tailing helpers used by the dashboard and the REST API.

Single source of truth for reading the tail of run.log / awake.log so the
dashboard process and the API process produce identical payloads.
"""

import collections
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

LOG_MAX_LINE_LENGTH = 2000
LOG_DEFAULT_LIMIT = 200
LOG_MAX_LIMIT = 2000


def tail_log(log_path: Path, limit: int) -> list[dict]:
    """Return up to *limit* lines from *log_path* as dicts with text and n.

    Uses a deque to avoid loading the full file into memory.
    Returns [] if the file does not exist or cannot be read.
    """
    if not log_path.exists():
        return []
    buf: collections.deque = collections.deque(maxlen=limit)
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, start=1):
                buf.append((n, line.rstrip("\n")))
    except OSError as exc:
        logger.warning("Failed to read log %s: %s", log_path, exc)
    return [{"n": n, "text": text[:LOG_MAX_LINE_LENGTH]} for n, text in buf]


def read_logs(koan_root: Path, source: str = "all", limit: int = LOG_DEFAULT_LIMIT, q: str = "") -> dict:
    """Read recent log lines from run.log and/or awake.log under koan_root/logs.

    Args:
        koan_root: KOAN_ROOT directory (logs live in koan_root/logs).
        source:    "run", "awake", or "all".
        limit:     max lines per source, clamped to [1, LOG_MAX_LIMIT].
        q:         optional case-insensitive substring filter.

    Returns: {"lines": [{"n", "text", "source"}], "total": int}
    """
    limit = max(1, min(int(limit), LOG_MAX_LIMIT))
    q = (q or "").lower()
    logs_dir = Path(koan_root) / "logs"

    source = (source or "all").lower()
    if source == "run":
        sources_to_read = ["run"]
    elif source == "awake":
        sources_to_read = ["awake"]
    else:
        sources_to_read = ["run", "awake"]

    # Tail each source independently so a busy source can never starve the
    # others: each contributes up to `limit` lines (its own tail), and the
    # final result is grouped by source rather than globally truncated.
    lines: list[dict] = []
    for src in sources_to_read:
        src_lines = tail_log(logs_dir / f"{src}.log", limit)
        for entry in src_lines:
            entry["source"] = src
        if q:
            src_lines = [e for e in src_lines if q in e["text"].lower()]
        lines.extend(src_lines[-limit:])

    return {"lines": lines, "total": len(lines)}
