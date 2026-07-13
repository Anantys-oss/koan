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
from typing import TextIO, Tuple

_CLI_PREFIX = "[cli] "
_TICK = "•"

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
    except Exception:
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


def render_cli(body: str, pal: "_Palette") -> Tuple[str, bool]:
    """Render one ``[cli] `` line body.

    Returns ``(rendered_text, is_tick)``. ``is_tick`` lines are low-signal
    and collapse when they repeat consecutively.
    """
    tick = pal.dim(_TICK)

    if body in _TICK_BODIES or body.startswith("system: thinking"):
        return tick, True

    if body.startswith("assistant — "):
        rest = body[len("assistant — "):]
        rendered = []
        for part in _PART_SEP.split(rest):
            if part.startswith("tool_use: "):
                name = part[len("tool_use: "):]
                icon = _TOOL_ICONS.get(name, _DEFAULT_TOOL_ICON)
                rendered.append(f"{icon} {name}")
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
        return pal.dim("↩"), True

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


def run(inp: TextIO, out: TextIO) -> None:
    pal = _Palette(_supports_color())
    last_tick = False
    for raw in inp:
        line = raw.rstrip("\n")
        try:
            if line.startswith(_CLI_PREFIX):
                rendered, tick = render_cli(line[len(_CLI_PREFIX):], pal)
                if tick and last_tick:
                    continue  # collapse consecutive low-signal ticks
                last_tick = tick
                out.write(rendered + "\n")
            else:
                last_tick = False
                out.write(line + "\n")
            out.flush()
        except Exception:
            # A single malformed line must never kill the stream.
            try:
                out.write(line + "\n")
                out.flush()
            except Exception:
                pass


def main() -> int:
    with contextlib.suppress(KeyboardInterrupt, BrokenPipeError):
        run(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
