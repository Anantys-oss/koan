"""Canonical severity → emoji / heading rendering, shared across skills.

Skills keep their own data-contract enums (e.g. review's 3-tier
critical/warning/suggestion); only the *rendering* is centralized here.
Non-canonical vocabularies fold in via SEVERITY_ALIASES.

Not in scope: ordering / threshold comparison (audit_runner._SEVERITY_ORDER)
and audit's effort scheme (_EFFORT_LABELS) — different axes.
"""
from __future__ import annotations

SEVERITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}

CANONICAL_SEVERITIES = tuple(SEVERITY_EMOJI)

# review's warning→medium (🟡) and suggestion→low (🟢) keep its rendering exact.
SEVERITY_ALIASES = {
    "warning": "medium",
    "suggestion": "low",
}

# Consumed by /review only.
SEVERITY_HEADING = {
    "critical": "Blocking",
    "medium": "Important",
    "low": "Suggestions",
}


def canonical(tier: str) -> str:
    key = (tier or "").strip().lower()
    return SEVERITY_ALIASES.get(key, key)


def severity_emoji(tier: str, default: str = "⚪") -> str:
    return SEVERITY_EMOJI.get(canonical(tier), default)


def severity_heading(tier: str, default: str = "") -> str:
    return SEVERITY_HEADING.get(canonical(tier), default)
