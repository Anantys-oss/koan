# Specification Quality Checklist: Review Consistency, Yellow-Tier Triage & Comprehensive Discovery

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-21
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Three scope-defining decisions were resolved with the user before writing (recorded in
  Assumptions): the consistency strategy ("stabilize + reuse when unchanged"); the
  yellow-tier policy ("impact bar: keep clear blockers, demote borderline, drop noise"); and
  the comprehensive-discovery delivery ("opt-in prompt partial gated by a config option,
  disabled by default"). No [NEEDS CLARIFICATION] markers remain.
- **Scope was extended in a follow-up turn** (Story 3 / FR-015–FR-021, SC-008–SC-010):
  opt-in comprehensive multi-perspective discovery + reconciliation to reduce missed
  findings and therefore run-to-run delta. It is off by default and does not alter the
  default single-pass review path.
- **Scope was extended again** (Stories 5 & 6 / FR-025–FR-030, SC-012–SC-013): a smarter
  default `review.md` prompt — (5) exhaustive single-pass discovery (always-on, find all
  issues in one parse), and (6) pre-existing non-critical issues demoted to 🟢 recommendation
  with a `[Pre-Existing Issue]` prefix (`critical` keeps severity but is still labeled). The
  freeze-vs-label interaction was resolved "coexist — freeze wins on re-review novelty" and
  recorded in Clarifications + FR-030.
- **The `[Pre-Existing Issue]` change is schema-free** (FR-029): label in the finding title,
  demotion reuses the existing `suggestion` severity — no review output-schema change, so it
  stays a prompt-level change per the user's request.
- **Scope extended once more** (Story 7 / FR-031–FR-038, SC-014–SC-016): on a re-review, honor
  human dispositions left as PR comments — dismiss (stop re-raising) and defer (downgrade to
  non-blocking) — reusing existing comment ingestion + reply mechanism (schema-free). Two
  clarifications resolved the posture: **any non-bot commenter** may dispose, with **full
  authority including `critical`** ("the human decides").
- **Security posture is a deliberate, documented tradeoff** (FR-032/FR-035): the open posture
  widens a social-engineering / prompt-injection surface. Compensating controls are specified
  (FR-036 mandatory attribution/audit — no silent suppression; FR-038 a comment disposes of a
  specific finding but cannot rewrite the rubric/verdict). Planning should weigh an optional
  operator config guard (authorized-users-only / protect-criticals) without changing the
  chosen default. Flagged here so the security implication is reviewed, not buried.
- **Architectural flag for planning**: FR-023 requires a contract-first update to
  `specs/skills/review.md` (the consistency guarantee, the yellow-tier bar, and the opt-in
  discovery mode change the skill's observable contract). The eventual PR MUST declare the
  architectural change per the repo's Specs discipline.
- Success criteria reference a "pre-feature baseline" (SC-005), a "labeled dataset"
  (SC-004, SC-009), and recall/dedup measurement for discovery (SC-009); planning should
  identify how those are captured (the existing eval harness / `learnings.md` calibration
  are the likely sources).
- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.
  All items currently pass.
