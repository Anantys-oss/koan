"""Display-side formatter for ``make logs``.

Reads the raw ``tail -F`` stream on stdin and renders the provider's
``[cli] …`` summary lines (emitted by
``app.provider.__init__._summarize_stream_event``) as a compact,
human-friendly feed on stdout. Every other line — run.py's
``[HH:MM:SS][category]`` logs, awake.py output, pending.md, and
``tail``'s ``==> file <==`` headers — is passed through untouched.

Purely presentational: it never parses for control flow and never
alters the log files. Unknown or garbled lines pass through verbatim so
the filter can never hide or corrupt output. Colors are emitted only
when stdout is a TTY (force with KOAN_FORCE_COLOR, disable with NO_COLOR).
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
from typing import Optional, TextIO, Tuple

_CLI_PREFIX = "[cli] "
_TICK = "•"
# Cap the visible width of an accumulating thinking dot-run.
_MAX_DOTS = 50

# Per-tool glyphs; anything unlisted falls back to the wrench.
_TOOL_ICONS = {
    "Edit": "✏️", "MultiEdit": "✏️", "Write": "📝", "Read": "📖",
    "Bash": "💻", "Grep": "🔎", "Glob": "🔍", "Task": "🤖",
    "WebFetch": "🌐", "WebSearch": "🌐", "TodoWrite": "🗒️",
}
_DEFAULT_TOOL_ICON = "🔧"

# Low-signal whole-line bodies that collapse to a single dim tick.
_TICK_BODIES = frozenset({
    "assistant — thinking", "assistant — streaming",
    "assistant — message start", "assistant — message end",
    "assistant — (empty)", "assistant — (hidden)", "user turn",
})

# system-event subtypes that are pure liveness heartbeats carrying no display
# signal — collapse to a thinking tick instead of surfacing verbatim. The
# provider keeps emitting the raw ``[cli] system: …`` line (run.py's liveness
# watchdog needs the heartbeat); only the display formatter suppresses it.
_TICK_SYSTEM_PREFIXES = ("system: thinking", "system: task_progress")

# Split ", "-joined assistant parts ONLY before a known part keyword, so a
# text preview that itself contains ", " is not broken mid-sentence.
_PART_SEP = re.compile(r", (?=tool_use: |text(?:: |$)|thinking$)")


def _supports_color() -> bool:
    if os.environ.get("KOAN_FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception as e:
        print(f"[log_fmt] isatty probe error: {e}", file=sys.stderr)
        return False


class _Palette:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.enabled else s

    def dim(self, s: str) -> str:
        return self._wrap("2", s)

    def yellow(self, s: str) -> str:
        return self._wrap("33", s)

    def red(self, s: str) -> str:
        return self._wrap("1;31", s)

    def green(self, s: str) -> str:
        return self._wrap("32", s)


def classify_cli(body: str) -> list[dict]:
    """Return structured display rows for one ``[cli]`` line body.

    Each row is a JSON-friendly dict with keys:
    ``kind``, ``label``, ``icon``, ``preview``, ``tool_name``, ``raw``,
    ``is_tick``.

    An empty list means "suppress" (same cases ``render_cli`` returns
    ``None`` for the rendered text). Shared grammar with ``render_cli`` /
    ``make logs`` so the dashboard timeline cannot drift.
    """
    raw = body

    def _row(
        kind: str,
        *,
        label: str = "",
        icon: str = "",
        preview: str = "",
        tool_name: str = "",
        is_tick: bool = False,
    ) -> dict:
        return {
            "kind": kind,
            "label": label,
            "icon": icon,
            "preview": preview,
            "tool_name": tool_name,
            "raw": raw,
            "is_tick": is_tick,
        }

    if body in _TICK_BODIES or body.startswith(_TICK_SYSTEM_PREFIXES):
        return [_row("thinking", label="thinking", is_tick=True)]

    if body.startswith("assistant — "):
        rest = body[len("assistant — "):]
        rows: list[dict] = []
        for part in _PART_SEP.split(rest):
            if part.startswith("tool_use: "):
                spec = part[len("tool_use: "):]
                name, sep, preview = spec.partition(": ")
                rows.append(_row(
                    "tool_use",
                    label=name,
                    icon=_TOOL_ICONS.get(name, _DEFAULT_TOOL_ICON),
                    preview=preview if sep else "",
                    tool_name=name,
                ))
            elif part.startswith("text: "):
                text = part[len("text: "):]
                rows.append(_row(
                    "text", label="assistant", preview=text, icon="🧠",
                ))
            elif part in ("text", "thinking"):
                rows.append(_row("thinking", label=part, is_tick=True))
            else:
                rows.append(_row("raw", preview=part))
        # If every part was a tick, collapse to a single thinking row.
        if rows and all(r["is_tick"] for r in rows):
            return [_row("thinking", label="thinking", is_tick=True)]
        return [r for r in rows if not r["is_tick"]] or [
            _row("thinking", label="thinking", is_tick=True)
        ]

    if body.startswith("tool_result"):
        if "(error)" in body:
            return [_row(
                "tool_error", label="tool error", icon="❌", preview=body,
            )]
        return []

    if body.startswith("tool_end: "):
        rest = body[len("tool_end: "):]
        # Match render_cli: tool_end is always shown (never a thinking tick).
        return [_row(
            "tool_end",
            label=rest,
            icon="↩",
            preview=rest,
            is_tick=False,
        )]

    if body.startswith("result: "):
        return [_row("result", label=body, icon="✅", preview=body)]

    if body.startswith("session init"):
        return [_row("session", label=body, icon="▶", preview=body)]

    if body.startswith(("retry", "context_overflow", "rate_limit_rejected")):
        return [_row("warning", label=body, icon="⚠", preview=body)]

    return [_row("raw", preview=body)]


def render_cli(body: str, pal: "_Palette") -> Tuple[Optional[str], bool]:
    """Render one ``[cli] `` line body.

    Returns ``(rendered_text, is_tick)``. ``rendered_text`` is ``None`` when
    the line carries no signal and should be dropped entirely. ``is_tick``
    lines are low-signal thinking events that accumulate into a dot-run.
    """
    tick = pal.dim(_TICK)

    if body in _TICK_BODIES or body.startswith(_TICK_SYSTEM_PREFIXES):
        return tick, True

    if body.startswith("assistant — "):
        rest = body[len("assistant — "):]
        rendered = []
        for part in _PART_SEP.split(rest):
            if part.startswith("tool_use: "):
                spec = part[len("tool_use: "):]
                name, sep, preview = spec.partition(": ")
                icon = _TOOL_ICONS.get(name, _DEFAULT_TOOL_ICON)
                label = f"{icon} {name}"
                if sep and preview:
                    label += " " + pal.dim(preview)
                rendered.append(label)
            elif part.startswith("text: "):
                rendered.append(f"🧠 {part[len('text: '):]}")
            elif part in ("text", "thinking"):
                rendered.append(tick)
            else:
                rendered.append(part)  # unknown part → verbatim
        real = [x for x in rendered if x != tick]
        if not real:
            return tick, True
        return "  ".join(real), False

    if body.startswith("tool_result"):
        if "(error)" in body:
            # Tool failures are high-signal: never collapse them.
            return pal.red("❌ tool error"), False
        # Success adds no signal on its own — drop the line entirely.
        return None, True

    if body.startswith("tool_end: "):
        rest = body[len("tool_end: "):]
        if "FAILED" in rest:
            return pal.red(f"↩ {rest}"), False
        return pal.dim(f"↩ {rest}"), False

    if body.startswith("result: "):
        return pal.green(f"✅ {body}"), False

    if body.startswith("session init"):
        return pal.dim(f"▶ {body}"), False

    if body.startswith(("retry", "context_overflow", "rate_limit_rejected")):
        return pal.yellow(f"⚠ {body}"), False

    # Unknown [cli] shape (incl. rate_limit_ok, item-fallback types):
    # surface verbatim so nothing is ever hidden.
    return body, False


def _out_is_tty(out: TextIO) -> bool:
    try:
        return bool(out.isatty())
    except (AttributeError, ValueError):
        # No isatty() (non-file stream) or closed file — treat as non-TTY.
        return False


def run(inp: TextIO, out: TextIO) -> None:
    pal = _Palette(_supports_color())
    tty = _out_is_tty(out)
    tick_run = 0

    def _dots(n: int) -> str:
        return pal.dim(_TICK * min(n, _MAX_DOTS))

    def finalize_ticks() -> None:
        # Close an in-progress dot run onto its own line.
        nonlocal tick_run
        if tick_run:
            if tty:
                out.write("\n")          # dots already drawn in place via \r
            else:
                out.write(_dots(tick_run) + "\n")
            tick_run = 0

    for raw in inp:
        line = raw.rstrip("\n")
        try:
            if line.startswith(_CLI_PREFIX):
                rendered, tick = render_cli(line[len(_CLI_PREFIX):], pal)
                if rendered is None:
                    continue              # suppressed: transparent to dot run
                if tick:
                    tick_run += 1
                    if tty:
                        out.write("\r" + _dots(tick_run))
                        out.flush()
                    continue
                finalize_ticks()
                out.write(rendered + "\n")
            else:
                finalize_ticks()
                out.write(line + "\n")
            out.flush()
        except Exception as e:
            # A single malformed line must never kill the stream.
            print(f"[log_fmt] render error: {e}", file=sys.stderr)
            try:
                finalize_ticks()
                out.write(line + "\n")
                out.flush()
            except Exception as e2:
                print(f"[log_fmt] passthrough error: {e2}", file=sys.stderr)
    finalize_ticks()  # flush a trailing dot run at EOF


def main() -> int:
    with contextlib.suppress(KeyboardInterrupt, BrokenPipeError):
        run(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
