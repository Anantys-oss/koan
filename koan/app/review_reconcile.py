"""Re-review freeze: suppress first-time findings on unchanged code (spec 010, US1, FR-003).

On a re-review (the PR head moved), a *first-time* non-critical finding located in
a file **unchanged since the prior review** is suppressed. This is the exact
"review whiplash" the user reported: new complaints on code they never touched,
which should have been caught in round one (if important) or left alone. A
``critical`` finding is the sole exception — it still surfaces, labelled
``[Pre-Existing Issue]`` (FR-003 critical valve / FR-028). Recurring prior
findings and findings in files the commits changed are unaffected.

Pure and deterministic: the runner supplies the prior finding set and the set of
files changed since the prior review (fail-open — ``None`` means "couldn't
determine", so no freeze). The runner applies the returned drop indices via its
existing ``_remap_findings_after_drop`` so checklist references stay consistent.

Granularity note: freezing is at *file* level (a finding in an unchanged file is
frozen). This is deliberately conservative — a first-time finding in a file the
commits *did* touch still surfaces, so the freeze never suppresses a finding about
genuinely new code. Line-level refinement can tighten this later without changing
the contract.
"""

from __future__ import annotations

from typing import Iterable, Optional

from app.review_identity import norm_path, same_finding

PRE_EXISTING_PREFIX = "[Pre-Existing Issue]"


def _is_recurring(finding: dict, prior_findings: list) -> bool:
    """True if ``finding`` matches a prior surfaced finding by tolerant identity."""
    return any(same_finding(finding, pf) for pf in prior_findings
               if isinstance(pf, dict))


def compute_freeze(
    review_data: dict,
    prior_findings: list,
    changed_files: Optional[Iterable[str]],
    *,
    prior_head: str = "",
) -> tuple[set, dict]:
    """Decide which first-time findings on unchanged files to freeze (FR-003).

    Returns ``(drop_indices, summary)`` where ``drop_indices`` is a set of indices
    into ``review_data["file_comments"]`` to remove, and ``summary`` is
    ``{"suppressed": n, "kept_pre_existing_critical": m}``. Pre-existing
    ``critical`` findings are labelled in place (their title gains the
    ``[Pre-Existing Issue]`` prefix) rather than dropped.

    Fail-open: returns ``(set(), {...0...})`` — i.e. no freeze — when there is no
    prior head (first review has nothing to freeze against) or ``changed_files``
    is ``None`` (the incremental diff couldn't be determined). The caller applies
    the drop via ``_remap_findings_after_drop``.
    """
    summary = {"suppressed": 0, "kept_pre_existing_critical": 0}
    drop_indices: set = set()
    if not isinstance(review_data, dict):
        return drop_indices, summary
    if not prior_head or changed_files is None:
        return drop_indices, summary
    comments = review_data.get("file_comments")
    if not isinstance(comments, list):
        return drop_indices, summary

    prior = [pf for pf in (prior_findings or []) if isinstance(pf, dict)]
    changed = {norm_path(f) for f in changed_files}

    for idx, fc in enumerate(comments):
        if not isinstance(fc, dict):
            continue
        file = norm_path(fc.get("file"))
        # Recurring prior finding OR a finding in a file the commits changed:
        # untouched by the freeze.
        if file in changed or _is_recurring(fc, prior):
            continue
        # First-time finding on a file unchanged since the prior review.
        severity = str(fc.get("severity") or "").strip().lower()
        if severity == "critical":
            title = str(fc.get("title") or "").strip()
            if PRE_EXISTING_PREFIX not in title:
                fc["title"] = f"{PRE_EXISTING_PREFIX} {title}".strip()
            summary["kept_pre_existing_critical"] += 1
        else:
            drop_indices.add(idx)
            summary["suppressed"] += 1

    return drop_indices, summary
