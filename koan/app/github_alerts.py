"""Shared builder for GitHub-flavored markdown alert callouts.

GitHub renders ``> [!NOTE]`` / ``[!TIP]`` / ``[!IMPORTANT]`` / ``[!WARNING]`` /
``[!CAUTION]`` blocks as distinct colored icon callouts in the PR/issue UI, in
email notifications, and on mobile. This module centralizes their construction
so every call site emits correctly ``> ``-prefixed syntax instead of
hand-typing it — and degrades to a plain-text prefix on non-GitHub providers
(e.g. Jira) so callers never special-case them.

Keep this deliberately tiny: one public function. This is infrastructure, not a
framework — no templating DSL, no per-finding alert factories. GitHub has no
native type for the project's QUESTION/TODO/BUG/SUCCESS/ERROR conventions;
those are emoji + bold in prose (see specs/components/comment-formatting.md),
not this helper.
"""

from __future__ import annotations

# The five native GitHub alert types.
_NATIVE_KINDS = frozenset({"NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"})


def build_alert(kind: str, text: str, provider: str = "github") -> str:
    """Build a provider-appropriate alert block.

    Args:
        kind: One of the five native GitHub alert types (case-insensitive):
            NOTE, TIP, IMPORTANT, WARNING, CAUTION.
        text: The alert body. May span multiple lines; every line (including
            blank paragraph separators) is prefixed so the whole block renders
            as one callout.
        provider: ``"github"`` (default) emits the ``> [!KIND]`` block; any
            other value degrades to a plain-text ``KIND: text`` prefix.

    Returns:
        The formatted alert string with **no** leading or trailing blank
        lines — the caller controls spacing around the block.

    Raises:
        ValueError: if ``kind`` is not one of the five native types.
    """
    normalized = kind.strip().upper()
    if normalized not in _NATIVE_KINDS:
        raise ValueError(
            f"Unknown alert kind {kind!r}; expected one of "
            f"{', '.join(sorted(_NATIVE_KINDS))}"
        )

    if provider != "github":
        return f"{normalized}: {text}"

    lines = [f"> [!{normalized}]"]
    lines.extend(f"> {line}" if line else ">" for line in text.split("\n"))
    return "\n".join(lines)
