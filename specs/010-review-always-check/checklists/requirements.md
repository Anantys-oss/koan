# Specification Quality Checklist: Repo-level `.koan/config.yaml` — `review.always_check`

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-22
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

- Spec references concrete module names (diff_compressor, truncate_diff_with_skips) only
  in the **Input** and **Assumptions** for traceability to the reported bug; the
  Requirements and Success Criteria remain technology-agnostic and user-focused.
- Future extension points (`never_check`, per-repo pause label, default focus passes) are
  scoped OUT of implementation and IN for documentation per FR-008.
