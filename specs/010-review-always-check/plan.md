# Implementation Plan: Repo-level `.koan/config.yaml` — `review.always_check`

**Branch**: `koan.atoomic/review-config-always-check` | **Date**: 2026-07-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/010-review-always-check/spec.md`

## Summary

Give repo owners a way to **pin** files so a large `/review` never silently skips
them. A new optional, checked-into-the-target-repo `.koan/config.yaml` grows the
existing markdown-only `.koan/` steering tree into a structured config surface. Its
`review.always_check` key is a list of file globs; matching changed files are moved to
the front of the diff-compressor's inclusion order so they survive budget-based
skipping. The same pins apply to the compressor-off character backstop. Absent/empty/
malformed config is a byte-identical no-op. Ships with end-user docs, a committed sample
config, and identifies further repo-level review knobs as documented (unimplemented)
extension points.

### Context (docs/specs consulted)

- `specs/components/skills.md` → **"`review` diff-size & partial-coverage contract"**
  (the single-source-of-truth for diff size: compressor `token_budget` default 80k;
  `_build_coverage_note()` merges fetch/compressor/triage skips into ONE value feeding
  both the `{SKIPPED_FILES}` prompt slot and the posted note — "the two can never
  diverge"). This is the durable contract this feature extends.
- `specs/components/skills.md` → **".koan/skills/<name>/" + "General KOAN.md in skill
  prompts"** (existing `.koan/` reader/injection mechanism via `project_koan.py`,
  precedence chain, per-file caps, stderr context-load logging).
- `specs/components/agent-loop.md` → documents `project_koan` (the repo `.koan/` reader
  home).
- `specs/skills/review.md` → the `/review` skill contract (delegates diff limits to the
  skills-component contract; Evaluation section: schema/prompt changes must reconcile
  golden cases — this feature changes neither schema nor prompt text).
- `docs/users/koan-md.md` → user-facing docs for the `.koan/` directory (markdown-only
  today); the natural home to document `.koan/config.yaml`.
- `wiki/index.md` had **no** page for a `.koan/config.yaml` or a "never skip these files"
  review knob → confirmed missing coverage; this feature adds it.

## Technical Context

**Language/Version**: Python 3.11+ (constitution constraint; no 3.12+ syntax).

**Primary Dependencies**: existing `app.project_koan` (repo `.koan/` readers),
`app.diff_compressor.compress_diff`, `app.utils.truncate_diff_with_skips`,
`app.review_runner.build_review_prompt` / `_build_coverage_note`. `yaml.safe_load`
(PyYAML, already a project dependency used by `config.py`/`projects_config.py`) for
parsing. Standard-library `fnmatch` for glob matching. No new third-party dependency.

**Storage**: N/A — no new persistent state. Config is read per-review from the target
repo root; nothing is written.

**Testing**: pytest with `KOAN_ROOT` set. New unit tests for: the `.koan/config.yaml`
reader (absent/empty/malformed/valid, cap enforcement) in `test_project_koan.py`; the
pin-aware compressor + backstop in `test_diff_compressor.py` / test for
`truncate_diff_with_skips`; and the `build_review_prompt` wiring (pinned file present,
absent from coverage note) in `test_review_runner.py`. Never invokes the Claude
subprocess.

**Target Platform**: Linux/macOS daemon.

**Project Type**: Single Python package (`koan/`), review skill runner + `.koan/`
reader + diff-compression component.

**Performance Goals**: N/A — matching is linear in (changed files × configured
patterns), both small and hard-capped (FR-012).

**Constraints**: Fail-safe on all untrusted repo config (Principle V); no inline prompts
(no new prompts — this feature changes no prompt template text); must pass `ruff`/`make
lint`; functions ≤ ~30 lines, files ≤ ~600 lines (split via helpers). The diff-size
ceiling stays the single source of truth for size — pinning reorders inclusion, it does
NOT raise the budget.

**Scale/Scope**: One new reader function + typed accessor in `project_koan.py`; one new
optional parameter threaded through `compress_diff` and `truncate_diff_with_skips`; a
few lines of wiring in `build_review_prompt`; docs + a sample config file.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Human Authority**: PASS — `/review` still only posts advisory comments; no
  merges, no commits to `main`. The config is read-only steering supplied by the repo
  owner. No change to the authority model.
- **II. Specs Are the Source of Truth**: **ARCHITECTURAL CHANGE — declared.** This
  extends the durable **"`review` diff-size & partial-coverage contract"** in
  `specs/components/skills.md`: the compressor's inclusion order now honors a
  config-supplied pin set, and the "coverage note reflects only genuinely-omitted files"
  invariant is *tightened* (pinned-but-included files must not appear as omitted). It
  also extends the `.koan/` reader contract (a new `read_koan_config()` alongside the
  existing markdown readers) — reflected in `specs/components/agent-loop.md` /
  `skills.md`. Per Principle II the specs are changed **contract-first** (this plan +
  the component-spec edits define the intended contract in the same branch, before/with
  the code) and the PR MUST check the **"Architectural change"** box. The new
  `compress_diff` / `truncate_diff_with_skips` parameter is **optional with a no-pin
  default**, so every existing caller (rebase/squash/recreate/ci_queue) is byte-identical
  — no breaking change. `specs/skills/review.md` gains a note about repo-level pinning;
  the review output **schema and prompt text are unchanged**, so the eval golden
  cases/baseline need no reconciliation.
- **III. Local Files by Default; Mission State in the Store**: PASS — no mission-store
  interaction; config is a plain file read, no new runtime state.
- **IV. Provider Isolation**: PASS — no provider branching; the change is provider-
  agnostic (operates on the unified diff before prompt assembly).
- **V. Untrusted Inputs, Audited Outputs**: PASS (and central to the design) — a target
  repo's `.koan/config.yaml` is semi-untrusted input. It is validated at the edge
  (`yaml.safe_load`; type-checked to a `list[str]`; per-pattern length + count caps);
  every malformed shape fails safe to "no pins" with a single diagnostic and NEVER
  aborts, hangs, or crashes the review. Pinning can only *retain* files already in the
  PR diff — it cannot inject unrelated content, so it adds no exfiltration surface.
- **VI. Single Writer, Single Read Path**: PASS — the coverage note stays built by the
  single `_build_coverage_note()`; pins change only *which files reach the skipped list*,
  so the one-value "prompt and posted body never diverge" invariant is preserved. The
  new config is read through one helper (`project_koan.read_koan_config`), mirroring the
  single-reader pattern of the existing `.koan/` markdown readers.
- **VII. Simplicity and Honest Reporting**: PASS — no new dependency (stdlib `fnmatch` +
  already-present PyYAML); a minimal optional parameter rather than a parallel code path;
  ships only the `always_check` key (YAGNI) while *documenting* future keys instead of
  speculatively building them. The partial-coverage note keeps reporting honestly:
  pinned-and-included files leave the omitted list; genuinely-skipped files stay.

**Result**: PASS with one **declared architectural change** (the `review` diff-size
contract + the `.koan/` reader contract). Recorded in Complexity Tracking below.

## Project Structure

### Documentation (this feature)

```text
specs/010-review-always-check/
├── plan.md              # This file
├── spec.md              # Feature spec
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (config schema + function contracts)
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
koan/app/
├── project_koan.py        # + read_koan_config(project_path) -> dict
│                          # + get_review_always_check(project_path) -> list[str]
├── diff_compressor.py     # compress_diff(..., pinned_patterns=None); pin-aware sort
├── utils.py               # truncate_diff_with_skips(..., pinned_patterns=None)
└── review_runner.py       # build_review_prompt(): read config, thread pins into both
                           # skip paths (compressor + backstop)

koan/tests/
├── test_project_koan.py       # config reader: absent/empty/malformed/valid, caps
├── test_diff_compressor.py    # pin-aware compression
├── test_utils.py (or new)     # pin-aware truncate_diff_with_skips
└── test_review_runner.py      # end-to-end wiring: pinned present, not in coverage note

docs/
├── users/koan-md.md           # document .koan/config.yaml + review.always_check
└── (index frontmatter via /brain sync)

specs/components/skills.md      # contract-first: extend diff-size contract (+ .koan config)
specs/components/agent-loop.md  # note read_koan_config on the .koan reader
specs/skills/review.md          # note repo-level always_check pinning

<sample config location — decided in research.md>  # committed sample .koan/config.yaml
```

**Structure Decision**: Single Python package. The feature is a thin, additive layer:
one reader in `project_koan.py`, one optional pin parameter on the two existing
skip-path functions, and a few lines of wiring in `build_review_prompt`. No new module
or package is warranted (Principle VII). Matching-helper placement (in `diff_compressor`
vs a shared util) is resolved in `research.md`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| Declared architectural change to the `review` diff-size contract + `.koan/` reader contract (`specs/components/skills.md`, `agent-loop.md`; `specs/skills/review.md`) | The reported bug (SKILL.md silently skipped) can only be fixed by letting repo config influence which files survive compression — that *is* a change to the diff-size contract's behavior, and the fix needs a new repo-config read path. | A pure code change with no spec update would leave the durable contract lying about behavior (Principle II). Hardcoding "never skip *.md/SKILL.md" in `koan/app/` was rejected: it removes user control, bakes policy into core (fails the "mechanism, not enumeration" rule), and still changes the contract. |
