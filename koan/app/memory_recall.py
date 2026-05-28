"""Task-aware memory recall — score and filter project learnings by mission relevance.

Lightweight Jaccard-similarity scoring (no external dependencies) used by
``prompt_builder`` to keep the injected learnings section under
``memory.max_relevant_learnings`` lines. A small "recency hedge" always keeps
the most recent learnings regardless of score so freshly-captured lessons
are never dropped.

The scoring is deterministic given the same inputs and is intentionally
simple: tokenize → lowercase → drop stopwords → set intersection / union.
For larger semantic recall use #1309 (token-budget-aware trimming) or a
proper vector store; this module just removes the obvious noise.
"""

from __future__ import annotations

import re
from typing import List, Set, Tuple

# Conservative English stopword list. Kept inline (no NLTK / sklearn) to
# preserve the "no extra deps" promise from issue #1306.
_STOPWORDS: Set[str] = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "does",
    "for", "from", "had", "has", "have", "he", "her", "him", "his", "i",
    "if", "in", "into", "is", "it", "its", "just", "me", "my", "no", "not",
    "of", "on", "or", "our", "out", "she", "so", "than", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "those",
    "to", "too", "up", "us", "was", "we", "were", "what", "when", "where",
    "which", "while", "who", "why", "will", "with", "would", "you", "your",
}

# A token is any run of word characters (letters/digits/underscore).
# We lowercase before extracting, so case folding is implicit.
_TOKEN_RE = re.compile(r"\w+")

# Recognises the ``[recall:full]`` escape hatch from a mission title.
_RECALL_FULL_RE = re.compile(r"\[recall:full\]", re.IGNORECASE)


def tokenize(text: str) -> Set[str]:
    """Return the deduplicated, lowercased, stopword-filtered token set.

    Tokens shorter than 3 characters are dropped — they're almost always
    glue words ("a", "is") or false signal (single letters in code blocks).
    """
    if not text:
        return set()
    tokens = {t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 3}
    return tokens - _STOPWORDS


def jaccard_score(a: Set[str], b: Set[str]) -> float:
    """Return ``|a ∩ b| / |a ∪ b|``. Returns 0.0 when both sets are empty."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def has_recall_full_tag(mission_text: str) -> bool:
    """True if ``mission_text`` contains the ``[recall:full]`` escape hatch."""
    if not mission_text:
        return False
    return bool(_RECALL_FULL_RE.search(mission_text))


def _split_learnings(content: str) -> List[str]:
    """Return non-empty, non-header content lines from a learnings file.

    Comments / Markdown headers (lines starting with ``#``) are dropped
    because they carry no project-specific signal.
    """
    out: List[str] = []
    for raw in content.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return out


def score_and_select(
    learnings_content: str,
    mission_text: str,
    max_k: int = 40,
    recent_hedge: int = 5,
) -> Tuple[List[str], int, int]:
    """Filter learnings down to the most relevant lines for ``mission_text``.

    Args:
        learnings_content: Raw text of the ``learnings.md`` file.
        mission_text: Mission title (or focus-area string in autonomous mode).
        max_k: Maximum number of *scored* lines to keep. Capped at the file
            size, never expanded.
        recent_hedge: Number of trailing lines that are *always* kept,
            regardless of score, to preserve freshly-captured lessons.

    Returns:
        ``(selected_lines, total_lines, dropped_count)`` where
        ``selected_lines`` preserves the original file ordering for
        readability. ``total_lines`` is the count of non-header content
        lines in the input. ``dropped_count = total_lines - len(selected_lines)``.

    Behaviour notes:
        * If ``mission_text`` produces no usable tokens, all learnings score
          0.0 and selection falls back to the most recent ``max_k`` lines
          (keeps behaviour stable in autonomous mode with vague focus areas).
        * Selection is deterministic: ties break on later-in-file (recency).
        * The recency hedge is taken *after* selection so duplicates are
          collapsed — asking for ``max_k=40, recent_hedge=5`` may return
          fewer than 45 lines if the last 5 lines were already in the top-K.
    """
    lines = _split_learnings(learnings_content)
    total = len(lines)
    if total == 0:
        return [], 0, 0

    effective_k = min(max_k, total) if max_k > 0 else 0
    effective_hedge = min(recent_hedge, total) if recent_hedge > 0 else 0

    mission_tokens = tokenize(mission_text)

    # Score every line with its original index so we can recover ordering.
    # Tie-break on index (later = higher = more recent) by negating the
    # secondary key in the sort. When ``mission_tokens`` is empty, every
    # line scores 0.0 and the index tie-break alone drives selection — so
    # ``scored[:effective_k]`` ends up picking the most recent K lines.
    # That implicit recency fallback is intentional (autonomous mode with
    # a vague focus area should still get *some* learnings).
    scored: List[Tuple[float, int, str]] = []
    for idx, line in enumerate(lines):
        score = jaccard_score(mission_tokens, tokenize(line)) if mission_tokens else 0.0
        scored.append((score, idx, line))

    # Sort by (score desc, idx desc) — both descending — to prefer high
    # relevance, then prefer recent lines on ties.
    scored.sort(key=lambda t: (-t[0], -t[1]))

    selected_indices: Set[int] = set()
    if effective_k > 0:
        for _score, idx, _ in scored[:effective_k]:
            selected_indices.add(idx)

    # Always include the trailing ``recent_hedge`` lines.
    if effective_hedge > 0:
        for idx in range(total - effective_hedge, total):
            selected_indices.add(idx)

    selected = [lines[i] for i in sorted(selected_indices)]
    return selected, total, total - len(selected)
