"""Kōan — semantic eval layer for structured review output.

Layered on top of :func:`app.review_schema.validate_review` (structural JSON-schema
validation), this module adds the **cross-field / semantic invariants** the schema
cannot express:

* a blocking finding (``critical``/``warning``) must contradict ``lgtm: true``
* checklist ``finding_refs`` must point at real ``file_comments`` indices
* an empty ``file_comments`` list is suspicious under ``lgtm: false``

The same function scores curated fixtures (Tier 1, CI) and live model output
(Tier 2, API-gated). See ``skills/core/review/eval/PLAN.md`` for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.review_schema import validate_review

# Severities that block a merge and therefore forbid ``lgtm: true``.
_BLOCKING_SEVERITIES = {"critical", "warning"}


@dataclass
class EvalReport:
    """Result of evaluating a single review object.

    ``errors`` are blocking invariant violations (structural or semantic);
    ``passed`` is true iff there are none. ``warnings`` are soft, surfaced-only
    signals that never fail an eval.
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when there are no blocking violations."""
        return not self.errors


def _ref_index(ref: object) -> int:
    """Coerce a validated ``finding_refs`` entry to an int.

    :func:`evaluate_review` runs :func:`validate_review` first and returns early
    on failure, so ``ref`` is guaranteed int-like (JSON ints arrive as ``int``,
    occasionally as int-valued ``float``). We accept both.
    """
    return int(ref) if isinstance(ref, float) else ref  # type: ignore[return-value]


def evaluate_review(data: object) -> EvalReport:
    """Evaluate a review object for structural validity + semantic invariants.

    Runs :func:`validate_review` first. If the structure is broken, the semantic
    rules are skipped (they assume the validated shape) and the schema errors are
    returned as-is. Otherwise the cross-field invariants below are applied.

    Returns an :class:`EvalReport`; ``passed`` is True only when the object is
    structurally valid AND satisfies every invariant.
    """
    report = EvalReport()

    ok, errors = validate_review(data)
    if not ok:
        report.errors.extend(f"schema: {e}" for e in errors)
        return report

    # validate_review guarantees dict shape + field presence from here on.
    assert isinstance(data, dict)
    file_comments = data.get("file_comments") or []
    review_summary = data.get("review_summary") or {}
    lgtm = review_summary.get("lgtm")
    checklist = review_summary.get("checklist") or []

    # Invariant 1 — blocking findings contradict LGTM. The REVIEW_SUMMARY_SCHEMA
    # docstring promises lgtm is False "if there are critical or warning-level
    # findings"; enforce that promise as a cross-field rule.
    blocking = [
        fc
        for fc in file_comments
        if isinstance(fc, dict) and fc.get("severity") in _BLOCKING_SEVERITIES
    ]
    if lgtm is True and blocking:
        report.errors.append(
            f"review_summary.lgtm is true but {len(blocking)} finding(s) are "
            "critical/warning — a blocking issue must contradict LGTM"
        )

    # Invariant 2 — finding_refs in range. validate_review checks only that the
    # refs are integers, not that they point at real findings; the renderer
    # otherwise silently drops dangling references. validate_review guarantees
    # every checklist item is a dict and every ref is int-like.
    n_comments = len(file_comments)
    for i, item in enumerate(checklist):
        for j, ref in enumerate(item.get("finding_refs") or []):
            idx = _ref_index(ref)
            if idx < 0 or idx >= n_comments:
                report.errors.append(
                    f"review_summary.checklist[{i}].finding_refs[{j}] = {idx} "
                    f"is out of range for {n_comments} file_comments"
                )

    # Invariant 3 (soft) — flagging the PR with zero actionable findings is
    # suspicious. Summary-only concerns are legitimate, so this is a warning.
    if not file_comments and lgtm is False:
        report.warnings.append(
            "review_summary.lgtm is false but file_comments is empty — the "
            "review flags the PR without any actionable finding"
        )

    return report
