# Phase 0 Research: Spec Changes Are Architectural Changes

## Decision 1 — Enforce with a blocking CI guard, not prose alone

**Decision**: Add `scripts/spec_change_guard.py` run as a **blocking**
`pull_request` check.

**Rationale**: Constitution Principle V states prompt-level controls are advisory and
"only code- or git-enforced controls are load-bearing." The customer concern is
precisely that an advisory instruction ("update the spec after") gets routed around by
an agent. The only fix with teeth is a git-level gate.

**Alternatives considered**:
- *Prose-only instruction change* — rejected: it is the status quo that failed.
- *Self-fixing backstop like `wiki-sync.yml`* — rejected: auto-fixing a missing
  declaration would defeat the purpose (the human acknowledgement is the point). This
  guard must **block**, not repair.

## Decision 2 — Declaration = checked checkbox with a fixed phrase in the PR body

**Decision**: A valid declaration is a Markdown task line `- [x] … architectural
change …` (case-insensitive) in the PR body, surfaced by a PR template.

**Rationale**: Deterministic and machine-checkable, human-legible, cheap to satisfy,
and self-documenting in the rendered PR. The checkbox forces a conscious act.

**Alternatives considered**:
- *Commit-message trailer* (`ARCHITECTURE-CHANGE:`) — rejected: less visible to
  reviewers than the PR body; agents amend commits freely.
- *Free-text "mention it"* — rejected: not machine-verifiable, so not load-bearing.
- *PR label* — rejected: labels are set post-open and often by maintainers, not the
  author; harder to require at check time and easy to add reflexively.

## Decision 3 — Durable contracts = `specs/components/**` + `specs/skills/**`, minus index/template

**Decision**: Gate only `specs/components/*.md` and `specs/skills/**.md`, excluding any
`index.md` and `specs/skills/SKILL_SPEC_TEMPLATE.md`.

**Rationale**: `specs/README.md` already defines these two trees as the durable design
contracts and `specs/<NNN-slug>/` as ephemeral speckit proposals. Index files are wiki
bookkeeping (exempt from review under Principle I). The skill template is scaffolding,
not a live contract.

**Alternatives considered**:
- *Gate all of `specs/`* — rejected: would fire on every ephemeral speckit planning
  edit (the intended "spec-first" artifact), inverting the intent.
- *Gate `docs/` too* — rejected: `docs/` is usage guidance, not contracts; behaviour
  docs are meant to change with UX.

## Decision 4 — Declaration required, separate spec-first PR only recommended

**Decision**: Require the declaration for any durable-contract change; **recommend** (do
not enforce) landing the contract change spec-first, ahead of the implementing code.

**Rationale**: The customer's explicit minimum is "rare + explicit PR notification." A
mandatory separate-PR-before-code flow has no reliable git-level enforcement point and
would materially slow legitimate contract evolution. The declaration + rarity guidance
achieves the intent; the contract-first framing (change the intended contract, then make
code conform) prevents the retroactive-mirroring failure directly.

## Decision 5 — Follow the existing script+CI shape

**Decision**: Model the guard on `scripts/wiki_check.py` (git-diff base-ref driven,
stdlib, exit-code contract) and the workflow on `.github/workflows/wiki-sync.yml`
(`pull_request` → `main`, `actions/checkout` full depth, `setup-python` 3.11), but with
`permissions: contents: read` and no push step.

**Rationale**: Consistency with established repo tooling; reviewers already understand the
pattern; no new dependencies (Principle VII simplicity).

## Open questions

None. All clarifications resolved in `spec.md` §Clarifications.
