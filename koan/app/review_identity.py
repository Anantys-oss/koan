"""Stable identity for review findings (spec 010, FR-002).

A code-review finding must be recognizable as "the same finding" across repeated
reviews and across the perspectives of a comprehensive-discovery pass, so that a
re-review can reproduce/reconcile prior findings instead of re-rolling a new set
(the "keeps finding different issues" churn). The identity key is deliberately
tolerant and deterministic:

    file + a coarse code region (line bucket) + a semantic issue category

It does NOT depend on exact line numbers (a minor shift keeps the same bucket) or
on the model's title wording (a reworded title maps to the same category). It is
pure Python — no model call — so the consistency-critical paths stay deterministic
and unit-testable.

Two helpers:
- ``finding_key`` — the canonical string key used to index/dedup findings.
- ``same_finding`` — a fuzzy predicate (same file+category, line within a
  tolerance) for matching a finding to a prior one or a human comment when the
  region has drifted more than one bucket.
"""

from __future__ import annotations

import re
from typing import Optional

# Line-region granularity. A finding whose anchor moves by fewer than REGION_WINDOW
# lines usually stays in the same bucket, so a small shift does not break identity.
REGION_WINDOW = 10

# Ordered (category, keyword-pattern) rules. First match wins, so more specific /
# higher-signal categories are listed before generic ones. Patterns are matched
# case-insensitively against the finding's title + comment.
_CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    ("security", re.compile(
        # `auth(?!or)` matches auth/authn/authz/authentication but NOT the common
        # review word "author"/"authored"; `authoriz` re-adds authorization/-ize.
        r"\b(inject|sql|xss|csrf|authoriz|auth(?!or)|secret|credential|token|"
        r"password|vulnerab|exploit|sanitiz|escap|ssrf|rce|traversal|privilege)\w*",
        re.I)),
    ("concurrency", re.compile(
        r"\b(race condition|race|deadlock|thread[- ]?safe|atomic|lock|mutex|"
        r"concurren)\w*", re.I)),
    ("resource-leak", re.compile(
        r"\b(leak|unclosed|not closed|file handle|fd leak|memory leak|"
        r"context manager|with open)\w*", re.I)),
    ("error-handling", re.compile(
        r"\b(error handling|exception|bare except|swallow|silent fail|"
        r"unhandled|try/except|traceback|raise)\w*", re.I)),
    ("correctness", re.compile(
        r"\b(off[- ]?by[- ]?one|null|none check|nil|edge case|incorrect|"
        r"logic (bug|error)|wrong|returns? the wrong|boundary|overflow)\w*", re.I)),
    ("performance", re.compile(
        r"\b(perf|performance|slow|o\(n|quadratic|n\+1|inefficien|hot path|"
        r"unnecessary (loop|allocation)|blocking call)\w*", re.I)),
    ("test", re.compile(
        r"\b(test coverage|missing test|no test|add a test|untested|"
        r"test case)\w*", re.I)),
    ("typing", re.compile(
        r"\b(type hint|type annotation|typing|mypy|typed)\w*", re.I)),
    ("docs", re.compile(
        r"\b(docstring|comment|documentation|readme)\w*", re.I)),
    ("naming", re.compile(
        r"\b(naming|rename|misnamed|unclear name|variable name)\w*", re.I)),
    ("style", re.compile(
        r"\b(style|formatting|whitespace|lint|cosmetic|readability|magic number)\w*", re.I)),
]

_NON_PATH = re.compile(r"^\./+")


def _norm_path(file: object) -> str:
    """Normalize a finding's file path for identity comparison."""
    path = str(file or "").strip()
    path = _NON_PATH.sub("", path)
    return path.strip("/")


def _anchor_line(finding: dict) -> int:
    """Best-effort 1-based anchor line for a finding (0 when unknown)."""
    for key in ("line_start", "line_end", "line"):
        value = finding.get(key)
        try:
            line = int(value)
        except (TypeError, ValueError):
            continue
        if line > 0:
            return line
    return 0


def _category(finding: dict) -> str:
    """Map a finding to a coarse semantic category from its title + comment.

    Deterministic keyword classification — the same underlying issue lands in the
    same category regardless of how the model worded the title. Falls back to
    ``"other"`` when no rule matches.
    """
    haystack = f"{finding.get('title', '')} {finding.get('comment', '')}"
    for category, pattern in _CATEGORY_RULES:
        if pattern.search(haystack):
            return category
    return "other"


def finding_key(finding: dict) -> str:
    """Canonical identity key: ``<file>|<region-bucket>|<category>`` (FR-002).

    A coarse grouping/dedup key: exact line numbers are bucketed into
    ``REGION_WINDOW``-line regions and the title wording is reduced to a semantic
    category. Two findings with the same key are the same finding.

    Tolerance caveat: a shift that stays *within* a bucket keeps the key, but a
    shift that crosses a bucket boundary changes it. For cross-run matching that
    must survive arbitrary line drift, use :func:`same_finding` (tolerant by
    ``line_tolerance``) rather than string-equality on this key.
    """
    file = _norm_path(finding.get("file"))
    bucket = _anchor_line(finding) // REGION_WINDOW
    return f"{file}|{bucket}|{_category(finding)}"


def same_finding(
    a: dict, b: dict, line_tolerance: Optional[int] = None
) -> bool:
    """True when two findings are the same issue, tolerant to region drift.

    Complements :func:`finding_key` for matching across drift larger than one
    bucket (e.g. a prior finding vs. a re-derived one after lines moved): same
    normalized file, same category, and anchor lines within ``line_tolerance``
    (default ``REGION_WINDOW``). Findings with unknown anchors match on
    file+category alone.
    """
    tol = REGION_WINDOW if line_tolerance is None else max(0, int(line_tolerance))
    if _norm_path(a.get("file")) != _norm_path(b.get("file")):
        return False
    if _category(a) != _category(b):
        return False
    la, lb = _anchor_line(a), _anchor_line(b)
    if la == 0 or lb == 0:
        return True
    return abs(la - lb) <= tol
