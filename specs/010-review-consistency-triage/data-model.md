# Phase 1 — Data Model

The feature adds no database and no review-output-schema fields. Its "data" is (1) an extended
per-PR **review sidecar** (the durable prior-review record) and (2) in-memory value objects the new
helper modules pass around. Severity vocabulary is unchanged: `critical` | `warning` | `suggestion`.

## Entity: Finding (existing — annotated)

A single review observation (a `file_comments[]` element in the review output).

| Field | Type | Notes |
|---|---|---|
| `file` | string | repo-relative path |
| `line_start`, `line_end` | int | anchor range |
| `severity` | enum | `critical`/`warning`/`suggestion` (unchanged) |
| `title` | string | may carry a `[Pre-Existing Issue]` / `[Deferred]` prefix (FR-027/FR-034) |
| `comment` | string | explanation |
| `code_snippet` | string | validated/resynced (existing snippet-validation) |
| **`identity_key`** (derived) | string | `file` + tolerant region + semantic topic (FR-002, D1) — computed, not model-authored |
| **`pre_existing`** (derived) | bool | reviewer assessment the issue predates the changeset (FR-027, D4) |

**Identity rule (FR-002)**: `identity_key = f(file, snapped_region, topic)`; independent of exact
line numbers and title wording. Two findings are "the same" iff equal keys.

## Entity: Review sidecar / Prior review record (existing — extended)

File: `instance/.review-findings/{owner}_{repo}_{pr}.json` (atomic write; fail-open read).

| Field | Type | New? | Notes |
|---|---|---|---|
| `file_comments` | Finding[] | existing | the surfaced findings |
| `review_summary` | object | existing | summary + `lgtm` + checklist |
| `head_sha` | string | existing (`prior_head_sha`) | reviewed PR head |
| **`base_sha`** | string | NEW | base/merge-base at review time (FR-001 reuse key, D2) |
| **`request_signature`** | object | NEW | `{focus_flags[], discovery_enabled}` — equivalence (FR-001/FR-016) |
| **`identity_index`** | map | NEW | `identity_key → finding` for fast reconciliation (D1/D3) |
| **`dispositions`** | Disposition[] | NEW | sticky human dispositions (FR-037, D7) |
| `schema_version` | int | NEW | migration guard; absent → treat as legacy (fail-open) |

**Lifecycle**: written after each posted review; read at the start of the next review to drive
reuse (D2), freeze/reconcile (D3), and disposition stickiness (D7). Missing/corrupt/legacy →
behaves as "no prior review" (FR-007, fail-open).

## Entity: Request signature

Value object deciding reuse equivalence (FR-001).

| Field | Type | Notes |
|---|---|---|
| `focus_flags` | sorted string[] | `--architecture`/`--errors`/`--comments`/`--plan-url` present |
| `discovery_enabled` | bool | comprehensive-discovery on/off (FR-016) |

Reuse fires iff `head_sha`, `base_sha`, **and** `request_signature` all equal the prior record's.

## Entity: Triage decision (in-memory)

Per finding, the outcome of the yellow-tier bar + pre-existing rule (FR-008–011, FR-027–030).

| Field | Type | Values |
|---|---|---|
| `action` | enum | `keep` (Important) / `demote` (→ recommendation) / `drop` / `keep_critical` |
| `pre_existing` | bool | drives `[Pre-Existing Issue]` prefix + forced `suggestion` if non-critical |
| `reason` | string | deterministic rationale (auditable) |

Determinism (FR-011): ties on the Important/Recommendation boundary resolve the same way every run
(stable tiebreak in `review_triage.py`).

## Entity: Human disposition (in-memory + persisted)

A non-bot PR comment that dispositions a finding (FR-031–038, D7).

| Field | Type | Notes |
|---|---|---|
| `kind` | enum | `dismiss` / `defer` / `retract` / `none` |
| `commenter` | string | non-bot author handle (bot comments excluded) |
| `rationale_quote` | string | quoted/linked text — mandatory for attribution (FR-036) |
| `target_key` | string | `identity_key` of the finding it addresses (inline location or reply thread) |
| `at` | string | timestamp (for retract ordering / stickiness) |

**Effect**: `dismiss` → finding not re-raised as blocking (suppressed, attributed);
`defer` → forced `suggestion` + `[Deferred]`, non-blocking; `retract` → lifts a prior disposition.
Applies to all severities incl. `critical` (FR-035), always attributed (FR-036). A comment cannot
alter the rubric/verdict (FR-038).

## Entity: Discovery perspective + Merged finding set (in-memory, US3 only)

| Entity | Notes |
|---|---|
| Discovery perspective | one lens from the fixed set {correctness, security, architecture, silent-failure, test-coverage}; produces candidate findings (FR-015) |
| Merged finding set | union deduped by `identity_key`, highest-justified severity + clearest explanation retained (FR-017); feeds triage/consistency unchanged (FR-018) |

## Relationships & precedence

```
PR comments ──parse──▶ Disposition[] ─┐
prior sidecar ────────────────────────┤
current model findings ──identity──────┼─▶ reconcile (freeze) ─▶ triage (yellow bar + pre-existing)
discovery perspectives (opt-in) ──merge┘                              │
                                                                      ▼
                                                         verdict re-derived from post-triage
                                                         severities ─▶ posted review + updated sidecar
```

**Precedence when rules interact**:
1. **Reuse** (D2) short-circuits everything when head+base+signature match.
2. **Disposition** (D7) overrides surfacing for its target finding (dismiss/defer), all severities,
   attributed — takes precedence over normal triage for that finding.
3. **Freeze** (D3) suppresses first-time non-critical findings on unchanged code (before triage),
   `critical` excepted.
4. **Triage** (D4) applies the yellow bar + `[Pre-Existing Issue]` to whatever survives.
5. **Verdict** re-derived from post-triage severities (existing invariant).
