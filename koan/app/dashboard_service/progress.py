"""Display-only progress parsing for the dashboard /progress page.

Turns ``instance/journal/pending.md`` text into a structured timeline
payload. Pure presentational logic — never used for control flow,
lifecycle, or quota decisions. Writers of ``pending.md`` are unchanged.
"""

from __future__ import annotations

from typing import Any

from app.log_fmt import classify_cli

_CLI_PREFIX = "[cli] "
_EMPTY_HEADER = {
    "title": "",
    "project": "",
    "started": "",
    "run": "",
    "mode": "",
}


def parse_pending_header(content: str) -> dict[str, str]:
    """Best-effort parse of the pending.md header block."""
    header = dict(_EMPTY_HEADER)
    if not content:
        return header
    for line in content.splitlines():
        if line.startswith("---"):
            break
        if line.startswith("# Mission: "):
            header["title"] = line[len("# Mission: "):].strip()
        elif line.startswith("# Autonomous run"):
            header["title"] = "Autonomous run"
        elif line.startswith("Project: "):
            header["project"] = line[len("Project: "):].strip()
        elif line.startswith("Started: "):
            header["started"] = line[len("Started: "):].strip()
        elif line.startswith("Run: "):
            header["run"] = line[len("Run: "):].strip()
        elif line.startswith("Mode: "):
            header["mode"] = line[len("Mode: "):].strip()
    return header


def build_entries(content: str) -> list[dict[str, Any]]:
    """Turn pending.md body into timeline rows; collapse thinking runs."""
    entries: list[dict[str, Any]] = []
    tick_count = 0

    def flush_ticks() -> None:
        nonlocal tick_count
        if tick_count:
            entries.append({
                "kind": "thinking",
                "label": "thinking",
                "icon": "•",
                "preview": "",
                "tool_name": "",
                "raw": "",
                "is_tick": True,
                "count": tick_count,
            })
            tick_count = 0

    for line in content.splitlines():
        if line.startswith(_CLI_PREFIX):
            for row in classify_cli(line[len(_CLI_PREFIX):]):
                if row.get("is_tick") or row.get("kind") == "thinking":
                    tick_count += 1
                    continue
                flush_ticks()
                entries.append({**row, "count": 1})
        else:
            # Skip blank lines and pure header keys already in header.
            stripped = line.strip()
            if not stripped or stripped == "---":
                continue
            if stripped.startswith("# ") or stripped.startswith((
                "Project: ", "Started: ", "Run: ", "Mode: ",
            )):
                continue
            flush_ticks()
            entries.append({
                "kind": "raw",
                "label": "",
                "icon": "",
                "preview": line,
                "tool_name": "",
                "raw": line,
                "is_tick": False,
                "count": 1,
            })
    flush_ticks()
    return entries


def build_progress_payload(*, active: bool, content: str) -> dict[str, Any]:
    """Assemble the /api/progress snapshot and SSE event payload."""
    content = content or ""
    return {
        "active": bool(active),
        "content": content,
        "header": parse_pending_header(content) if content else dict(_EMPTY_HEADER),
        "entries": build_entries(content) if content else [],
    }
