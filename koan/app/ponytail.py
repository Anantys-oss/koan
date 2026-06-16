"""Kōan — Ponytail code minimalism helpers.

Single source of truth for "should the ponytail directive be appended to this
prompt?".  The directive (six-gate decision ladder for code minimalism) lives
in ``koan/system-prompts/ponytail-mode.md`` and is injected in the agent loop
via ``app.prompt_builder._get_ponytail_section``.

Ponytail targets CODE QUANTITY — how much code Claude generates.
Caveman targets PROSE VERBOSITY — how Claude communicates.
They are complementary, not overlapping.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_ponytail_section() -> str:
    """Return the ponytail directive text, or ``""`` when suppressed.

    Gated only by the global ``optimizations.ponytail.enabled`` flag.
    """
    from app.config import is_ponytail_mode
    if not is_ponytail_mode():
        return ""

    try:
        from app.prompts import load_prompt
        return load_prompt("ponytail-mode")
    except OSError:
        logger.warning("ponytail prompt file missing or unreadable")
        return ""
