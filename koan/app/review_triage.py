"""Deterministic severity triage enforcement (spec 010, US6/US7).

The yellow-tier *bar* (US2) is set by the prompt rubric — the model assigns
severity. This module mechanically **enforces** the rules the prompt cannot
guarantee, deterministically and testably, over the model's output:

- **Pre-existing labeling (US6, FR-027/028):** a finding the reviewer tagged
  ``[Pre-Existing Issue]`` (an issue predating the PR's changeset — FR-029's
  "reviewer's semantic assessment") is forced to a non-blocking ``suggestion``
  when it is not ``critical``; a ``critical`` keeps its severity but retains the
  label. The prefix is normalized to a single leading occurrence.
- **Verdict coherence (FR-012):** after any demotion, ``lgtm`` is re-derived from
  the surviving severities (blocking iff a ``critical``/``warning`` remains).

Pure and fail-open: malformed input is left untouched. The runner calls this in
the accuracy gate, *after* the re-review freeze (so FR-030 "freeze wins" holds —
a frozen finding never reaches here).
"""

from __future__ import annotations

import re

from app.review_reconcile import PRE_EXISTING_PREFIX

DEFERRED_PREFIX = "[Deferred]"

_BLOCKING = {"critical", "warning"}


def derive_lgtm(review_data: dict) -> bool:
    """Blocking iff a ``critical``/``warning`` finding survives (FR-012)."""
    comments = review_data.get("file_comments") if isinstance(review_data, dict) else None
    if not isinstance(comments, list):
        return True
    return not any(
        isinstance(f, dict) and str(f.get("severity") or "").lower() in _BLOCKING
        for f in comments
    )


def _normalize_prefix(title: str, prefix: str) -> str:
    """Return ``title`` with exactly one leading ``prefix`` (idempotent)."""
    cleaned = re.sub(re.escape(prefix), "", title, flags=re.IGNORECASE).strip()
    return f"{prefix} {cleaned}".strip() if cleaned else prefix


def enforce_pre_existing(review_data: dict) -> dict:
    """Enforce the pre-existing rule on findings the reviewer tagged (US6).

    Detection is the reviewer's: a finding whose title carries the
    ``[Pre-Existing Issue]`` prefix. Enforcement is deterministic — a non-critical
    such finding is forced to ``suggestion`` (FR-027); a ``critical`` keeps its
    severity (FR-028); the prefix is normalized to one leading occurrence. Re-derives
    ``lgtm`` when anything was demoted. Returns
    ``{"demoted": n, "critical_labeled": m}``.
    """
    summary = {"demoted": 0, "critical_labeled": 0}
    comments = review_data.get("file_comments") if isinstance(review_data, dict) else None
    if not isinstance(comments, list):
        return summary

    for fc in comments:
        if not isinstance(fc, dict):
            continue
        title = str(fc.get("title") or "")
        if PRE_EXISTING_PREFIX.lower() not in title.lower():
            continue
        fc["title"] = _normalize_prefix(title, PRE_EXISTING_PREFIX)
        severity = str(fc.get("severity") or "").lower()
        if severity == "critical":
            summary["critical_labeled"] += 1
        elif severity == "warning":
            fc["severity"] = "suggestion"
            summary["demoted"] += 1

    if summary["demoted"]:
        rs = review_data.get("review_summary")
        if isinstance(rs, dict):
            rs["lgtm"] = derive_lgtm(review_data)
    return summary


def enforce_deferred(review_data: dict) -> dict:
    """Enforce the deferred rule on findings a human asked to defer (US7, FR-034).

    Detection is the reviewer's: a finding whose title carries the ``[Deferred]``
    prefix (the model applies it when a human said "fix later"). Enforcement is
    deterministic — any such finding is forced to a non-blocking ``suggestion``,
    the prefix is normalized to one leading occurrence, and ``lgtm`` is re-derived.
    Returns ``{"deferred": n}``. Fail-open on malformed input.
    """
    summary = {"deferred": 0}
    comments = review_data.get("file_comments") if isinstance(review_data, dict) else None
    if not isinstance(comments, list):
        return summary

    for fc in comments:
        if not isinstance(fc, dict):
            continue
        title = str(fc.get("title") or "")
        if DEFERRED_PREFIX.lower() not in title.lower():
            continue
        fc["title"] = _normalize_prefix(title, DEFERRED_PREFIX)
        if str(fc.get("severity") or "").lower() != "suggestion":
            fc["severity"] = "suggestion"
        summary["deferred"] += 1

    if summary["deferred"]:
        rs = review_data.get("review_summary")
        if isinstance(rs, dict):
            rs["lgtm"] = derive_lgtm(review_data)
    return summary
