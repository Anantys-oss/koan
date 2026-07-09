# Feature Specification: Spec Changes Are Reviewed Architectural Changes

**Feature Branch**: `koan.atoomic/specs-architecture-gate` (speckit dir `005-spec-change-governance`)

**Created**: 2026-07-09

**Status**: Draft

**Input**: Customer concern on PR #2052
([comment](https://github.com/Anantys-oss/koan/pull/2052#issuecomment-4826440629)):

> good idea with the specs, but I would change the instructions so that the specs
> are _never_ updated alongside code — changes to the spec should come first on
> their own as approved architecture changes, and the code changes to implement
> the spec after. Otherwise AI agents will just modify the specs to match whatever
> sloppy code they wrote and the problem isn't solved. At a minimum there should be
> an instruction that changes to the spec should be rare, and require an explicit
> notification in the PR that "this is an architectural change — the new
> architecture needs to be reviewed before approval" or something similar.

## Problem Statement

Kōan's specs discipline (Constitution Principle II, `CLAUDE.md` *Specs discipline*,
`specs/README.md` *Spec discipline*) currently instructs: **"After implementing,
update the spec in the same branch to reflect the new design."** For an autonomous
agent, this instruction is self-defeating: the spec is supposed to be the source of
truth the code must conform to, but "update the spec to match what you just built"
lets the agent silently rewrite the contract to rubber-stamp whatever code it wrote.
The spec stops being a constraint and becomes a mirror of the implementation — the
exact failure the specs discipline was created to prevent.

The concern distinguishes two populations already recognised in `specs/README.md`:

- **Ephemeral speckit `specs/<NNN-slug>/`** planning folders (`spec.md`, `plan.md`,
  `tasks.md`, …) are *proposals written before code*. Editing these in-branch is
  correct — they are the "spec-first" artifact.
- **Durable design contracts** — `specs/components/<group>.md` and
  `specs/skills/<name>.md` — are the source of truth. These are what must **not** be
  bent retroactively to match code.

This feature changes the discipline so that a change to a **durable design contract**
is governed as an **architectural change**: deliberate, contract-first, rare, and
explicitly surfaced for human review before approval — enforced by a load-bearing,
git-level check (Constitution Principle V: "only code- or git-enforced controls are
load-bearing"), not by prose alone.

## Clarifications

### Session 2026-07-09

- Q: Should the guard hard-block a PR that changes a durable contract without a
  declaration, or only warn? → A: Hard-block in CI (non-zero exit / failed check);
  the declaration is a checkbox the author consciously checks, so the block is
  trivially resolvable and forces the "this is architectural" acknowledgement.
- Q: Which spec paths count as "durable contracts" for the gate? → A:
  `specs/components/**.md` and `specs/skills/**.md`, **excluding** any `index.md`
  (wiki bookkeeping, already exempt under Principle I) and
  `specs/skills/SKILL_SPEC_TEMPLATE.md` (a template, not a live contract). All
  `specs/<NNN-slug>/**` speckit folders are out of scope — they are ephemeral
  proposals, not contracts.
- Q: Is "spec change must land in a *separate* PR before code" mandatory or
  recommended? → A: **Recommended, not mandatory.** The customer's stated minimum
  bar is "rare + explicit PR notification." Mandating a two-PR split for every
  contract touch would be heavy and unenforceable at the git level; the declaration
  + rarity guidance achieves the intent. The discipline text recommends the
  spec-first split and requires the declaration.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent cannot silently rewrite a contract to match its code (Priority: P1)

An autonomous Kōan agent implements a change that alters a component's contract and,
following the old instruction, edits `specs/components/<group>.md` to match. The PR
opens. CI runs the spec-change guard, detects a durable-contract modification with no
architectural-change declaration in the PR body, and **fails the check**. The human
reviewer is alerted that the architecture changed and must review it deliberately
before approving — the spec edit cannot slip through as an incidental "kept the docs
in sync" change.

**Why this priority**: This is the entire point of the customer concern. Without an
enforced gate, the instruction change is advisory and an agent will route around it.

**Independent Test**: Craft a diff touching `specs/components/core.md` with a PR body
lacking the declaration; run the guard; assert it exits non-zero with a message
naming the changed contract. Add the declaration; assert it exits zero.

**Acceptance Scenarios**:

1. **Given** a diff that modifies `specs/components/core.md` **and** a PR body with no
   architectural-change declaration, **When** the guard runs, **Then** it exits
   non-zero and lists `specs/components/core.md` as an undeclared contract change.
2. **Given** the same diff **and** a PR body containing a checked architectural-change
   declaration, **When** the guard runs, **Then** it exits zero.
3. **Given** a diff that touches only `specs/004-mission-store/plan.md` (an ephemeral
   speckit folder) and no durable contract, **When** the guard runs, **Then** it exits
   zero regardless of the declaration.

### User Story 2 - Contributor is told, at author time, that a contract change needs a declaration (Priority: P2)

A contributor (human or agent) opening a PR sees a pull-request template with an
**"Architectural change"** section: an unchecked checkbox plus a one-line explanation
that checking it is required when the PR modifies a durable design contract. When they
touch a contract, they check the box and write the rationale, satisfying the guard and
signalling the reviewer in the same motion.

**Why this priority**: Makes the requirement discoverable and cheap to satisfy so it
does not become a surprise CI failure. Secondary to the enforcement itself.

**Independent Test**: Confirm `.github/PULL_REQUEST_TEMPLATE.md` exists and contains the
declaration marker string the guard recognises.

**Acceptance Scenarios**:

1. **Given** a new PR, **When** the author views the description field, **Then** it is
   pre-filled with the architectural-change declaration section.
2. **Given** the template's checked declaration line, **When** the guard parses it,
   **Then** it recognises it as a valid declaration.

### User Story 3 - The written discipline reflects "contract-first, rare, declared" (Priority: P1)

A developer reading the Constitution, `CLAUDE.md`, or `specs/README.md` finds a
consistent, unambiguous rule: durable-contract specs are changed **deliberately and
contract-first** (change the intended contract, then make code conform — never the
reverse), such changes are **rare**, and every one is **declared** in the PR as an
architectural change needing review before approval. The prior "update the spec in the
same branch to match the new design" phrasing — which invited retroactive rubber-
stamping — is gone.

**Why this priority**: The instruction change is the substance of the customer request;
the enforcement (US1) gives it teeth, but the words must be right and consistent across
all four sources of governance text.

**Independent Test**: Grep the four governance files for the removed phrasing (absent)
and the new declaration/rarity/contract-first language (present and consistent).

**Acceptance Scenarios**:

1. **Given** the Constitution Principle II, **When** read, **Then** it states that a
   durable-contract change is an architectural change: contract-first, rare, and
   declared for review — and no longer says "update the spec to match the new design."
2. **Given** `CLAUDE.md` (root and `workspace/koan/`) and `specs/README.md`, **When**
   read, **Then** their specs-discipline sections carry the same rule and cross-link to
   the guard and the decision doc.

### Edge Cases

- **Adding a brand-new spec** for a component/skill that had none (the discipline's
  "no spec yet? write one" path): a net-new `specs/components/<group>.md` file is a
  *new* contract, not a rewrite of an existing one. The guard SHOULD still require a
  declaration (a new contract is an architectural decision), but the discipline text
  clarifies that authoring a first spec is expected and encouraged, not "rare."
  → Resolved: guard treats added contract files the same as modified ones (declaration
  required); the *rarity* guidance applies to churn on existing contracts, and the
  text says so.
- **Index / bookkeeping files** (`specs/components/index.md`, frontmatter-only bumps):
  excluded from the guard — already exempt from Principle I review under the wiki
  bookkeeping carve-out.
- **Fork PRs**: the guard runs on `pull_request` (read-only token) and reports via the
  check status; it never pushes. A failing check on a fork PR is still visible to
  maintainers.
- **Guard cannot read the PR body locally**: the guard accepts the PR body via an
  argument/env/stdin so it works both in CI (from the event payload) and locally
  (developer passes `--pr-body-file` or the check is CI-only). If no body is provided
  and durable specs changed, it fails closed with an explanatory message.
- **Reverting a bad contract change**: a revert still modifies a durable contract, so it
  still needs a declaration — acceptable; a revert of an architectural change is itself
  an architectural decision.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Constitution's Principle II MUST be amended so that updating a durable
  design contract (`specs/components/**`, `specs/skills/**`) is defined as an
  architectural change that is (a) contract-first — the spec expresses the *intended*
  design and code is made to conform, never edited afterward to match code; (b) rare
  for existing contracts; and (c) explicitly declared in the PR as needing architecture
  review before approval. The prior "after implementing, update the spec in the same
  branch to reflect the new design" compliance statement MUST be replaced.
- **FR-002**: The Workflow & Quality Gates section of the Constitution MUST be updated so
  the "docs-and-specs-in-branch" gate no longer implies retroactive contract editing,
  and MUST reference the declaration requirement.
- **FR-003**: `CLAUDE.md` (repo root `/CLAUDE.md`) and `workspace/koan/CLAUDE.md`
  *Specs discipline (mandatory)* sections MUST be updated to the new rule, consistent
  with the Constitution, and MUST point to the enforcing guard and the decision doc.
- **FR-004**: `specs/README.md` *Spec discipline* section MUST be updated to the same
  rule, preserving the existing durable-vs-ephemeral distinction it already documents.
- **FR-005**: A load-bearing check (`scripts/spec_change_guard.py`) MUST detect, from a
  git diff range, any added or modified durable-contract spec file and MUST fail (non-
  zero exit) unless a recognised architectural-change declaration is present in the
  supplied PR body. It MUST print the offending file(s) and remediation instructions.
- **FR-006**: The guard MUST exclude `index.md` files, `SKILL_SPEC_TEMPLATE.md`, and all
  `specs/<NNN-slug>/**` speckit folders from the "durable contract" set.
- **FR-007**: A GitHub Actions workflow MUST run the guard on `pull_request` events
  targeting `main`, passing the diff base and the PR body, and surface pass/fail as a
  check. It MUST NOT push commits or require secrets beyond the default token, and MUST
  degrade gracefully on fork PRs (report via check status).
- **FR-008**: A `.github/PULL_REQUEST_TEMPLATE.md` MUST exist containing the
  architectural-change declaration section with the exact marker the guard recognises,
  plus a short instruction for when to check it.
- **FR-009**: A decision doc under `docs/design/` MUST record the rationale ("specs are
  contract-first, not code-mirrors"), the customer origin, the chosen enforcement, and
  the rejected heavier alternative (mandatory two-PR split).
- **FR-010**: The guard's declaration detection MUST be resilient to reasonable
  formatting variation (case-insensitive; checkbox `- [x]` with the marker phrase) and
  MUST treat an unchecked box or absent marker as "not declared."
- **FR-011**: Unit tests MUST cover: durable-contract detection (positive/negative
  paths), exclusions (index/template/ephemeral), declaration parsing (checked /
  unchecked / absent), and the end-to-end pass/fail decision.

### Key Entities

- **Durable design contract**: a spec file under `specs/components/` or `specs/skills/`
  (excluding `index.md` and `SKILL_SPEC_TEMPLATE.md`) that defines a component/skill
  contract. Changing one is an architectural change.
- **Architectural-change declaration**: an explicit, recognisable marker in a PR body
  (a checked checkbox with a fixed phrase) asserting "this PR changes a design contract;
  the new architecture must be reviewed before approval," with a short rationale.
- **Spec-change guard**: the script + CI check that ties a durable-contract diff to the
  presence of a declaration and fails closed otherwise.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A PR diff that modifies any `specs/components/**.md` or `specs/skills/**.md`
  (non-index, non-template) file without a declaration in its body causes the guard to
  exit non-zero; adding the declaration flips it to zero — verified by unit tests.
- **SC-002**: A PR diff that touches only ephemeral speckit folders, index files, or the
  skill template never causes the guard to fail — verified by unit tests.
- **SC-003**: The phrase "update the spec in the same branch to reflect the new design"
  (and equivalent retroactive-editing phrasing) no longer appears as an instruction in
  the Constitution, either `CLAUDE.md`, or `specs/README.md`; the declaration/rarity/
  contract-first rule appears in all four.
- **SC-004**: The new/opened PR shows the architectural-change declaration section by
  default (template present), and the guard recognises the template's checked form.
- **SC-005**: `make lint` and the full test suite pass with the new guard and tests.
- **SC-006**: The Constitution version is bumped and its Sync Impact Report enumerates
  every reconciled artifact.

## Assumptions

- The customer's minimum bar ("rare + explicit PR notification of an architectural
  change") is the binding requirement; a mandatory separate-PR-before-code workflow is
  **out of scope** (recommended in prose, not enforced) because it cannot be reliably
  gated at the git level and would materially slow ordinary contract evolution.
- CI runs on GitHub Actions with `pull_request` events and access to the PR body via the
  event payload (`github.event.pull_request.body`), consistent with existing workflows.
- The guard is Python (matches the repo's tooling and test harness) and self-contained
  enough to unit-test without network or the Claude subprocess.
- Existing durable specs on `main` are unaffected; the gate applies only to diffs that
  change them going forward.
