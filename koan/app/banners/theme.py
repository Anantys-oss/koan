"""Anantys visual theme for Kōan terminal output.

Midnight-dark background with a mint-green accent, echoing the Anantys
brand. Provides truecolor (24-bit) ANSI helpers with a graceful fallback
to basic 16-color codes on terminals that don't advertise truecolor, and
a pixel/cells gradient art block reused by the banner, the interactive
launcher, and the terminal dashboard.

No emojis — plain glyphs and box-drawing only.
"""

import os
import sys

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# --- Anantys palette (R, G, B) ----------------------------------------------
# Each entry pairs a truecolor RGB tuple with a basic-16 fallback code used
# when the terminal does not advertise 24-bit color support.
MINT = ((62, 207, 142), "32")        # accent — Anantys mint green
MINT_DIM = ((46, 138, 99), "32")     # muted accent / dividers
TEXT = ((220, 226, 230), "97")       # primary foreground
MUTED = ((128, 140, 148), "2")       # secondary / labels
AMBER = ((222, 170, 90), "33")       # caution glyphs


def supports_color(stream=None) -> bool:
    """Return True when ANSI color should be emitted to ``stream``."""
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def supports_truecolor() -> bool:
    """Return True when the terminal advertises 24-bit color support."""
    if os.environ.get("NO_COLOR"):
        return False
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def _seq(color, *, bold: bool = False, dim: bool = False) -> str:
    """Build the opening ANSI sequence for a palette ``color`` entry."""
    rgb, fallback = color
    if supports_truecolor():
        code = f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}"
    else:
        code = fallback
    prefix = ""
    if bold:
        prefix += BOLD
    if dim:
        prefix += DIM
    return f"{prefix}\033[{code}m"


def paint(text: str, color, *, bold: bool = False, dim: bool = False) -> str:
    """Wrap ``text`` in the given palette color, honoring color support.

    Falls back to the plain string when the active stream is not a TTY or
    ``NO_COLOR`` is set, so output stays readable when piped or redirected.
    """
    if not supports_color():
        return text
    return f"{_seq(color, bold=bold, dim=dim)}{text}{RESET}"


# Convenience wrappers -------------------------------------------------------

def mint(text: str, *, bold: bool = False) -> str:
    return paint(text, MINT, bold=bold)


def mint_dim(text: str) -> str:
    return paint(text, MINT_DIM)


def text(value: str, *, bold: bool = False) -> str:
    return paint(value, TEXT, bold=bold)


def muted(value: str) -> str:
    return paint(value, MUTED)


def amber(value: str) -> str:
    return paint(value, AMBER)


# --- pixel / cells gradient -------------------------------------------------
# Glyph ramp from empty to full cell, used to fade the accent block in and
# out the way the Anantys brand corner does.
_RAMP = " ░▒▓█"


def pixel_gradient_lines(width: int = 24, height: int = 4) -> list:
    """Render a deterministic mint pixel-gradient block.

    The cell density fades from solid at the top-left to empty toward the
    bottom-right, mirroring the Anantys brand corner. Deterministic (no RNG)
    so output is stable across runs and testable.
    """
    width = max(1, width)
    height = max(1, height)
    span = (width - 1) + (height - 1) or 1
    lines = []
    for row in range(height):
        cells = []
        for col in range(width):
            # Distance from the dense corner, normalized to the ramp.
            t = (col + row) / span
            idx = int(round((1.0 - t) * (len(_RAMP) - 1)))
            idx = max(0, min(len(_RAMP) - 1, idx))
            glyph = _RAMP[idx]
            # Denser cells use the bright accent, sparser ones the muted one.
            color = MINT if idx >= 3 else MINT_DIM
            cells.append(paint(glyph, color) if glyph != " " else " ")
        lines.append("".join(cells))
    return lines
