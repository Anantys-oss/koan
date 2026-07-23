# Feature Specification: Repo-level `.koan/config.yaml` — `review.always_check`

**Feature Branch**: `koan.atoomic/review-config-always-check`

**Created**: 2026-07-22

**Status**: Draft

**Input**: User description: "During a /review mission some files are skipped (Diff
compressed — N file(s) skipped). It is a bad idea to skip SKILL.md on a repo that
ships skills. Add a `.koan/config.yaml` file where the user can provide patterns to
never skip some files during review, e.g. `review.always_check: ["*.md", "SKILL.txt"]`,
giving users more control over a review via a configuration file that can be extended
over time. Identify other special review flags that make sense at the repo level.
Document properly and add a sample config.yaml file."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pin critical files so a large review never silently skips them (Priority: P1)

A maintainer of a repository that ships Claude Code skills opens a `/review` on a
large PR. Today the diff compressor drops lower-priority files (Markdown, text) to
fit the token budget, so the review comment carries a `⚠️ Partial review` note listing
dozens of omitted files — including `SKILL.md` files that are the *whole point* of the
PR. The maintainer wants those files reviewed every time, regardless of diff size.

They commit a `.koan/config.yaml` at their repo root:

```yaml
review:
  always_check:
    - "SKILL.md"
    - "*.md"
```

On the next `/review`, every changed `SKILL.md` and `*.md` file is included in the
reviewed diff (pinned ahead of budgeted files) and is no longer listed as omitted.

**Why this priority**: This is the core ask and the only user-visible behavior change
in this feature. Without it, the feature delivers nothing.

**Independent Test**: Configure `review.always_check` in a target repo, run a review
on a diff large enough to trigger compression, and confirm the pinned files appear in
the reviewed diff and are absent from the partial-coverage note. Fully testable on its
own; delivers the complete value slice.

**Acceptance Scenarios**:

1. **Given** a repo with `.koan/config.yaml` containing `review.always_check: ["SKILL.md"]`
   and a PR whose diff exceeds the compressor budget and touches `plugins/x/SKILL.md`
   plus many large source files, **When** the review runs, **Then** `plugins/x/SKILL.md`
   is present in the reviewed diff and does NOT appear in the `⚠️ Partial review` note.
2. **Given** the same repo, **When** the review runs, **Then** files that do not match
   any `always_check` pattern are still subject to normal budget-based skipping.
3. **Given** a pattern `"*.md"`, **When** a changed file is `docs/deep/nested/guide.md`,
   **Then** it matches (glob applies to the full repo-relative path and the basename).

---

### User Story 2 - Discover and adopt the config via documentation and a sample file (Priority: P2)

A user who saw a "Diff compressed — N file(s) skipped" message wants to know how to
stop it. They find the documentation, copy the committed sample `.koan/config.yaml`
into their repo, uncomment the `always_check` block, and adjust the patterns. The
sample also shows — as commented-out, clearly-labelled future keys — what other
repo-level review knobs are planned, so they understand the file is a growing surface.

**Why this priority**: A control the user cannot discover is a control that does not
exist. Documentation + a copyable sample is what turns the mechanism into an adopted
feature. It ships with P1 but is a distinct, independently verifiable deliverable.

**Independent Test**: Follow the docs from a clean repo using only the sample file;
confirm a review honors the configured patterns without reading source code.

**Acceptance Scenarios**:

1. **Given** the published documentation, **When** a user copies the sample
   `.koan/config.yaml` and enables `always_check`, **Then** the behavior in User Story 1
   takes effect with no further steps.
2. **Given** the sample file, **When** a user reads it, **Then** the `always_check` key
   is demonstrated and the identified future keys are present as commented-out,
   non-active examples that do not change behavior.

---

### Edge Cases

- **Absent config**: no `.koan/config.yaml` → byte-identical to today's behavior
  (no pinning, no note change, no log line). This is the overwhelmingly common case.
- **Malformed config**: unparseable YAML, wrong types (e.g. `always_check` is a string
  or a mapping instead of a list, or list items that are not strings) → the file is
  treated as absent for the affected key (fail-safe, no crash), and the review proceeds
  exactly as it would with no config. A single diagnostic is logged; the review is never
  aborted by a bad repo config.
- **Empty patterns**: `always_check: []` or an all-blank list → no-op.
- **Pattern matches nothing in this diff**: a configured pattern that no changed file
  matches has no effect and produces no note or error.
- **Pinned file larger than the entire budget**: an enormous pinned file is still
  included as far as the budget allows (its first hunks), consistent with today's
  "single massive file → partial" behavior; pinning changes *priority/order*, it does
  not raise the token budget. Best-effort inclusion, never a hard failure.
- **Every changed file is pinned on an oversized diff**: pinned files are included in
  configured order until the budget is exhausted; the remainder still fall to the
  coverage note. Pinning cannot make an unbounded diff fit — it reprioritizes what
  survives, it does not remove the size ceiling.
- **Compressor disabled** (`review_compressor.enabled: false`): the same pins apply to
  the character-budget backstop path, so behavior is consistent in every configuration.
- **Config present but names an unknown top-level key** (e.g. a typo like `reviw:`):
  ignored; only recognized keys take effect. Unknown keys never error.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST read an optional `.koan/config.yaml` from the *target
  repository's* root (the project path), distinct from the operator's KOAN_ROOT
  `instance/config.yaml`. Absence is the normal case and MUST be a silent no-op.
- **FR-002**: The system MUST recognize a `review.always_check` key whose value is a
  list of file-glob pattern strings.
- **FR-003**: During a `/review`, any changed file whose repo-relative path OR basename
  matches at least one `always_check` pattern (glob/`fnmatch` semantics) MUST be
  **pinned**: included in the reviewed diff ahead of non-pinned files, and never fully
  dropped to fit the diff-size budget while any budget remains.
- **FR-004**: A pinned file that is actually included MUST NOT appear in the
  `⚠️ Partial review — ... omitted` coverage note. The note MUST continue to reflect
  reality: only files genuinely absent from the reviewed diff are listed (the existing
  "prompt and posted body never diverge" invariant is preserved).
- **FR-005**: Pinning MUST apply on BOTH file-skipping paths: the token-budget diff
  compressor (default) and the compressor-off / character-budget backstop, so the
  guarantee holds in every configuration.
- **FR-006**: Malformed or wrongly-typed config (unparseable YAML, non-list
  `always_check`, non-string items) MUST fail safe: the offending key is ignored, the
  review proceeds as if unconfigured, and the run is never aborted. At most one
  diagnostic message is emitted.
- **FR-007**: Pattern matching MUST be case-sensitive and MUST treat each pattern as a
  glob applied to (a) the full repo-relative path and (b) the file basename, so
  `SKILL.md` matches at any directory depth and `*.md` matches any Markdown file.
- **FR-008**: The `.koan/config.yaml` schema MUST be documented as a generic, extensible
  per-repo configuration surface. The specification MUST identify additional sensible
  repo-level review knobs as **documented future extension points** (e.g. an
  ignore/`never_check` list, a per-repo review pause label, default focus passes) that
  are described but NOT implemented in this feature.
- **FR-009**: The project MUST ship end-user documentation covering the config file's
  purpose, schema, precedence, matching semantics, fail-safe behavior, and the
  relationship to diff compression / partial coverage.
- **FR-010**: The project MUST ship a committed SAMPLE `.koan/config.yaml` (in an
  example/reference location, not a live `instance/` path) demonstrating `always_check`
  and showing the identified future keys as commented-out, inert examples.
- **FR-011**: When at least one file is pinned by config, the system SHOULD emit an
  observability log line (consistent with existing `.koan/` context-load logging) so an
  operator watching `make logs` can confirm the config took effect. Absence of pins
  produces no line.
- **FR-012**: A safety bound MUST cap the number of honored patterns (and pattern
  length) to a reasonable limit so a pathological repo config cannot degrade review
  performance; excess patterns beyond the cap are ignored with a diagnostic.

### Key Entities *(include if feature involves data)*

- **Repo review config**: the parsed representation of `.koan/config.yaml`'s `review`
  section for a given target repo. Attributes: `always_check` (ordered list of glob
  pattern strings). Sourced per-review from the repo root; empty/absent yields the
  default (no pins). Designed to gain sibling keys over time without changing existing
  behavior.
- **Pinned file**: a changed file in the PR diff whose path or basename matches an
  `always_check` pattern; carries top inclusion priority during diff-size reduction.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a review whose diff is large enough that ≥1 file would be skipped by
  default, every file matching a configured `always_check` pattern appears in the
  reviewed diff and is absent from the partial-coverage note — in 100% of runs.
- **SC-002**: With no `.koan/config.yaml` present (or an empty one), review output is
  byte-identical to the pre-feature behavior for the same PR.
- **SC-003**: A malformed `.koan/config.yaml` never aborts, hangs, or errors a review;
  the review completes with the same result it would have produced with no config, in
  100% of malformed-input cases.
- **SC-004**: A new user can enable the behavior end-to-end using only the published
  documentation and the sample file, without inspecting source code.
- **SC-005**: The sample file and documentation enumerate the identified future
  repo-level review knobs, so a reader understands the config surface is extensible.

## Assumptions

- The reported file-skipping originates from the token-budget diff compressor (and its
  compressor-off backstop) invoked while building the review prompt; the very large
  derived fetch-time character cap is out of scope because it does not trigger for the
  reported case (its cap is orders of magnitude larger than the compressor budget).
- The target repo's `.koan/` directory is checked into the repo and NOT gitignored
  (matching the existing `.koan/KOAN.md` / `.koan/skills/` convention), so repo owners
  can commit `.koan/config.yaml`.
- `always_check` is an *allowlist to protect from skipping*, not a filter that would add
  files that are not already part of the PR diff. It can only affect files already
  present in the changed set.
- Pinning reorders/prioritizes inclusion; it does not raise the configured token/char
  budget. The diff-size ceiling remains the single source of truth for size.
- Glob matching uses standard `fnmatch`-style semantics (`*`, `?`, `[seq]`); it does not
  add `**` recursive-glob semantics beyond what `fnmatch` provides (a plain `*` already
  spans path separators in `fnmatch`, which is sufficient for the stated use cases).
- Future keys (`never_check`, per-repo pause label, default focus passes) are identified
  as extension points only; their concrete semantics are deferred to later features.
- This is a public open-source repo; all examples use generic placeholders only.
