# Implementation Plan: Spec Changes Are Reviewed Architectural Changes

**Branch**: `koan.atoomic/specs-architecture-gate` | **Date**: 2026-07-09 | **Spec**: [`spec.md`](./spec.md)

**Input**: Feature specification from `specs/005-spec-change-governance/spec.md`

## Context (knowledge base consulted first)

Per `CLAUDE.md`'s "Documentation first" mandate, the knowledge base was consulted
index-first before planning:

- **`wiki/index.md`** — no existing page covers "spec-change governance" or an
  "architectural change" gate; the closest design entry, `docs/design/spec-always-up-railway.md`,
  is an unrelated Railway deployment proposal (matched only on the word "spec"). This
  absence is itself the signal that coverage is missing → this feature adds a new
  `docs/design/` decision page.
- **`.specify/memory/constitution.md`** (v2.0.0) — **Principle II "Specs Are the Source
  of Truth"** contains the exact instruction the customer objects to: *"After
  implementing, update the spec in the same branch to reflect the new design."* The
  **Workflow & Quality Gates** section repeats it as "Docs-and-specs-in-branch." Both
  must change. The amendment procedure (bump version, reconcile every dependent
  artifact, add a Sync Impact Report) is defined in the same file and is followed here.
- **`specs/README.md`** *Spec discipline* — mirrors the same step 2, and already
  documents the durable (`components/`, `skills/`) vs. ephemeral (`<NNN-slug>/`)
  distinction this feature leans on. Update its step 2, keep the distinction.
- **`CLAUDE.md`** (repo root and `workspace/koan/`) *Specs discipline (mandatory)* —
  step 2 identical; both files updated for consistency.
- **`docs/design/decisions.md`** — has a "Documentation First" decision but no spec
  discipline decision; the new decision page cross-links here.
- **Enforcement precedent**: `scripts/wiki_check.py` + `.github/workflows/wiki-sync.yml`
  show the established pattern — a Python script driven by a git diff base-ref, wired
  into a `pull_request` workflow. The new guard follows this shape (script + CI), but
  is a **blocking check**, not a self-fixing backstop (Principle V: prompt-level
  controls are advisory; only git-enforced controls are load-bearing).

## Summary

Redefine the "specs discipline" so that changing a **durable design contract**
(`specs/components/**`, `specs/skills/**`) is an **architectural change** — contract-
first, rare, and explicitly declared in the PR for review before approval — replacing
the self-defeating "update the spec afterward to match the code" instruction. Give the
rule teeth with a load-bearing CI guard (`scripts/spec_change_guard.py`) that fails a PR
which touches a durable contract without an architectural-change declaration in its
body, backed by a PR template that surfaces the declaration at author time. Reconcile
the four governance texts (Constitution, both `CLAUDE.md`, `specs/README.md`), bump the
Constitution version with a Sync Impact Report, and record the rationale in a new
`docs/design/` decision page.

## Technical Context

**Language/Version**: Python 3.11+ (guard + tests); Markdown (governance/docs); YAML
(GitHub Actions).

**Primary Dependencies**: stdlib only for the guard (`argparse`, `re`, `subprocess`,
`pathlib`, `sys`). No new runtime deps. CI uses `actions/checkout`, `actions/setup-python`
(mirroring existing workflows).

**Storage**: N/A — the guard is stateless; it reads a git diff range and a PR-body string.

**Testing**: `pytest` under `koan/tests/`, run with `KOAN_ROOT` set, per the constitution's
testing discipline. Guard functions are pure and unit-tested with synthetic diff/body
inputs — no git, network, or Claude subprocess.

**Target Platform**: GitHub Actions (Ubuntu) for CI; local dev shells for manual runs.

**Project Type**: Single project (governance + tooling change to the existing repo).

**Performance Goals**: N/A (runs once per PR, sub-second).

**Constraints**: Guard must be importable and unit-testable without side effects; must
degrade gracefully when no PR body is available (fail closed with a clear message) and on
fork PRs (report via check status, never push).

**Scale/Scope**: ~1 new script, ~1 test module, ~1 workflow, ~1 PR template, ~1 decision
doc, and edits to 4 governance texts. No changes to `koan/app/` runtime code.

## Constitution Check

*GATE: must pass before design; re-checked after.*

- **I. Human Authority** — ✅ Reinforces it. The guard raises the bar for human review of
  architectural changes; it never merges, pushes, or modifies `main`. The CI workflow is
  read-only (`pull_request`, default token, no `contents: write`).
- **II. Specs Are the Source of Truth** — ✅ This feature *amends* Principle II. Handled
  via the constitution's own amendment procedure (version bump + Sync Impact Report +
  reconcile dependents). The amendment strengthens, not weakens, spec authority.
- **III / IV / VI** — ✅ N/A. No runtime state, provider, or single-writer surface touched.
- **V. Untrusted Inputs, Audited Outputs** — ✅ Directly serves the "only code/git-enforced
  controls are load-bearing" clause: the discipline gains a git-level enforcement point
  instead of prose alone. The guard treats the PR body as *data* (parsed for a marker,
  never executed). No private identifiers introduced.
- **VII. Simplicity & Honest Reporting** — ✅ Extends the existing script+CI mechanism
  (`wiki_check.py` shape) rather than inventing new infrastructure; stdlib-only; the
  rejected heavier alternative (mandatory two-PR split) is recorded in Complexity Tracking
  and the decision doc.

**Verdict**: PASS. The only "violation" is amending a principle, which is explicitly the
constitution's sanctioned path; no unjustified complexity introduced.

## Project Structure

### Documentation (this feature)

```text
specs/005-spec-change-governance/
├── spec.md              # Phase -1 (specify)
├── plan.md              # This file
├── research.md          # Phase 0 — decisions & alternatives
├── data-model.md        # Phase 1 — entities (contract set, declaration, verdict)
├── quickstart.md        # Phase 1 — how to run/verify the guard locally
├── contracts/
│   └── spec_change_guard.md   # Phase 1 — guard CLI/function contract
└── tasks.md             # Phase 2 (tasks)
```

### Source Code (repository root)

```text
scripts/
└── spec_change_guard.py                 # NEW — durable-contract detection + declaration check (CLI + funcs)

koan/tests/
└── test_spec_change_guard.py            # NEW — unit tests for detection, exclusions, declaration parsing, verdict

.github/
├── PULL_REQUEST_TEMPLATE.md             # NEW — architectural-change declaration section
└── workflows/
    └── spec-change-guard.yml            # NEW — blocking check on pull_request → main

docs/design/
└── spec-changes-are-architectural.md    # NEW — decision doc (rationale, origin, enforcement, rejected alt)

# Reconciled governance texts (edits, not new files):
.specify/memory/constitution.md          # Principle II + Workflow gate + version bump + Sync Impact Report
CLAUDE.md                                 # repo-root Specs discipline section
specs/README.md                          # Spec discipline section
docs/design/decisions.md                 # add a short "Spec Changes Are Architectural" decision entry + backlink
```

**Structure Decision**: Single-project layout. The enforcement tooling lives in the
existing `scripts/` dir (alongside `wiki_check.py`) with its test in the existing
`koan/tests/` suite; CI in `.github/workflows/`; the PR template at the conventional
`.github/PULL_REQUEST_TEMPLATE.md`. No `koan/app/` runtime code changes — this is a
governance + CI feature.

## Design Notes

- **Durable-contract predicate** (`is_durable_contract(path)`): true iff the path matches
  `specs/components/*.md` or `specs/skills/**.md`, is not named `index.md`, and is not
  `specs/skills/SKILL_SPEC_TEMPLATE.md`. Everything under `specs/<NNN-slug>/` (a numbered
  speckit folder) is excluded by construction (it is neither `components/` nor `skills/`).
- **Declaration predicate** (`has_architecture_declaration(pr_body)`): true iff the body
  contains a checked task line matching, case-insensitively, a checkbox `- [x]` whose
  text includes the fixed phrase **"architectural change"** (regex
  `^\s*[-*]\s*\[x\]\s*.*architectural change`, `re.I | re.M`). An unchecked `- [ ]` box
  or an absent phrase → not declared.
- **Changed files** come from `git diff --name-only --diff-filter=AM <base>...HEAD`
  (added + modified; deletions of a contract are not gated — removing a contract is
  visible in the diff and does not risk silent code-mirroring). The base ref is a CLI arg
  (`--base-ref`, default `origin/main`), matching `wiki_check.py`.
- **PR body source**: `--pr-body-file <path>` (CI writes the event body to a file) or
  `--pr-body -` (stdin). If durable contracts changed and no body was supplied → exit
  non-zero with the fail-closed message.
- **Exit contract**: `0` = no durable contract changed, or changed-with-declaration; `1` =
  durable contract changed without declaration, or fail-closed. `2` = usage error.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| New CI check + script (vs. prose-only instruction) | Principle V: an autonomous agent routes around advisory prose; the discipline needs a git-enforced gate to be load-bearing | Prose-only was the status quo that produced the customer concern — it is exactly what failed |
| Declaration marker + PR template (vs. free-text PR note) | The guard needs a deterministic, machine-checkable signal; a fixed checkbox is unambiguous and self-documenting | Free-text "mention it in the PR" cannot be verified in CI, so it is not load-bearing |
| Declaration-only (vs. mandatory separate spec-first PR) | Customer's stated minimum bar is "rare + explicit notification"; a forced two-PR split cannot be gated at the git level and would throttle ordinary contract evolution | A mandatory split adds heavy process for every contract touch with no reliable enforcement point — recommended in prose instead |
