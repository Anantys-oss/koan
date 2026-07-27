"""Reuse decision for repeated reviews (spec 010, US1, FR-001).

When a PR is reviewed again with an identical head AND base (merge-base) SHA and
an equivalent review request, the prior review is *reproduced* instead of
re-derived — eliminating run-to-run drift on unchanged code (the "keeps finding
different issues" frustration).

Pure, deterministic helpers only: the runner supplies the current SHAs, the
request signature, and the persisted prior-review record; this module just
decides whether reuse is warranted. Any missing/mismatched piece -> no reuse
(fall back to a fresh review, FR-007), so the decision is fail-safe by
construction.
"""

from __future__ import annotations

from typing import Optional


def request_signature(focus_flags, discovery_enabled: object) -> dict:
    """Normalized signature that decides review-request equivalence (FR-001/FR-016).

    Two requests are equivalent iff they target the same PR with the same focus
    passes and the same comprehensive-discovery setting. Normalizing (sorted,
    de-duplicated, stripped flags; coerced bool) makes equality order- and
    format-insensitive.
    """
    flags = sorted({
        str(flag).strip() for flag in (focus_flags or []) if str(flag).strip()
    })
    return {"focus_flags": flags, "discovery_enabled": bool(discovery_enabled)}


def should_reuse(
    prior_record: Optional[dict],
    head_sha: str,
    base_sha: str,
    signature: dict,
) -> bool:
    """True iff the prior review can be reproduced verbatim (FR-001).

    Requires a prior record whose head SHA, base SHA, and (normalized) request
    signature all match the current review. Any missing piece — no prior record,
    unknown SHA, mismatched signature — yields ``False`` so the caller falls back
    to a fresh review (FR-007). Base movement alone (FR-001, D2) defeats reuse
    because the effective diff changed even if the head is identical.
    """
    if not isinstance(prior_record, dict):
        return False
    if not head_sha or not base_sha:
        return False
    if str(prior_record.get("head_sha") or "") != str(head_sha):
        return False
    if str(prior_record.get("base_sha") or "") != str(base_sha):
        return False
    prior_sig = prior_record.get("request_signature")
    if not isinstance(prior_sig, dict):
        return False
    current = request_signature(
        signature.get("focus_flags"), signature.get("discovery_enabled"),
    )
    stored = request_signature(
        prior_sig.get("focus_flags"), prior_sig.get("discovery_enabled"),
    )
    return current == stored
