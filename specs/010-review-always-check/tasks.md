# Tasks: Repo-level `.koan/config.yaml` — `review.always_check`

**Feature**: `specs/010-review-always-check/` | **Branch**: `koan.atoomic/review-config-always-check`
**Input**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/config-schema.md](./contracts/config-schema.md),
[quickstart.md](./quickstart.md)

Tests are included because koan's convention (`koan/CLAUDE.md`) is **test-first for
behavior changes**. Commit after every task (one commit per task; skip empty commits).

## Phase 1: Setup

- [X] T001 Confirm no new dependencies are needed and the test harness runs: `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_project_koan.py koan/tests/test_diff_compressor.py -q` (baseline green before changes). No file edits.

## Phase 2: Foundational (contract-first specs + shared reader/matcher)

**Per Constitution Principle II, the durable-contract changes are made BEFORE the code
that conforms to them (this whole phase blocks Phase 3).**

- [X] T002 Contract-first: extend the **"`review` diff-size & partial-coverage contract"** in `specs/components/skills.md` — document that the compressor/backstop inclusion order honors a config-supplied pin set (`review.always_check`) and that pinned-and-included files are absent from the `⚠️ Partial review` coverage note. Add a `.koan/config.yaml` subsection describing the reader + fail-safe rules.
- [X] T003 Contract-first: note `project_koan.read_koan_config` / `get_review_always_check` on the `.koan/` reader description in `specs/components/agent-loop.md` (and/or the `.koan/skills/` subsection of `specs/components/skills.md`).
- [X] T004 Contract-first: add an Invariant/Inputs note to `specs/skills/review.md` that a target repo's `.koan/config.yaml` `review.always_check` pins matching files (schema/prompt unchanged ⇒ evals unaffected).
- [X] T005 [P] Write failing tests for the config reader in `koan/tests/test_project_koan.py`: absent file → `{}` / `[]`; valid `review.always_check` list → returned; unparseable YAML / non-mapping top level / `review` not a mapping / `always_check` not a list / non-str items → fail-safe `[]` (no raise); >100 patterns and >200-char patterns → capped.
- [X] T006 Implement `read_koan_config(project_path)` and `get_review_always_check(project_path)` (with `_MAX_ALWAYS_CHECK_PATTERNS=100`, `_MAX_PATTERN_LEN=200`) in `koan/app/project_koan.py` using `yaml.safe_load`; make T005 pass.
- [X] T007 [P] Write failing tests for `path_matches_any` in `koan/tests/test_diff_compressor.py`: basename match (`plugins/x/SKILL.md` vs `SKILL.md`), path match with `*` spanning `/` (`docs/deep/g.md` vs `*.md`), non-match (`main.go` vs `*.md`), empty patterns → `False`, case-sensitivity.
- [X] T008 Implement `path_matches_any(path, patterns)` (using `fnmatch.fnmatchcase` against full path and basename) in `koan/app/diff_compressor.py`; make T007 pass.

## Phase 3: User Story 1 — Pin critical files so a large review never skips them (P1) 🎯 MVP

**Goal**: A changed file matching a configured `always_check` pattern is included in the
reviewed diff and never listed as omitted, on both skip paths.

**Independent test**: Run the tests in [quickstart.md](./quickstart.md) §"Unit /
integration validation" items 2–4; confirm pinned files survive an oversized diff and
are absent from the coverage note, and `pinned_patterns=None` stays byte-identical.

- [X] T009 [P] [US1] Write failing tests in `koan/tests/test_diff_compressor.py`: `compress_diff(big_diff, token_budget=small, pinned_patterns=["SKILL.md"])` includes the SKILL.md block in `diff_text` and excludes it from `skipped_files` while an unpinned large file IS skipped; `pinned_patterns=None`/`[]` → byte-identical to current output (regression guard).
- [X] T010 [US1] Add `pinned_patterns: Optional[list[str]] = None` to `compress_diff` in `koan/app/diff_compressor.py` and extend the sort key with a leading `0 if pinned else 1` term (pinned files first); keep safety/partial logic unchanged; make T009 pass.
- [X] T011 [P] [US1] Write failing tests for the char backstop in `koan/tests/test_utils.py` (create if absent): `truncate_diff_with_skips(diff, max_chars, pinned_patterns=["*.md"])` keeps the `*.md` block and skips others when over budget; `pinned_patterns=None` → byte-identical.
- [X] T012 [US1] Add `pinned_patterns` to `truncate_diff_with_skips` in `koan/app/utils.py`: stably partition `diff --git` blocks into (pinned, rest) before the greedy fit, reusing `diff_compressor.path_matches_any`; make T011 pass.
- [ ] T013 [P] [US1] Write failing test in `koan/tests/test_review_runner.py`: `build_review_prompt` with a `project_path` whose `.koan/config.yaml` pins `SKILL.md` and an oversized diff → returned prompt DIFF contains the SKILL.md block and returned `coverage_note` does NOT list it; with no config → unchanged behavior.
- [ ] T014 [US1] Wire `build_review_prompt` in `koan/app/review_runner.py`: read `get_review_always_check(project_path)`, pass `pinned_patterns=` into both the `compress_diff(...)` and `truncate_diff_with_skips(...)` calls; make T013 pass.
- [ ] T015 [US1] Add the observability log line in `koan/app/review_runner.py` (one `log("review", "Pinned N file(s) via .koan review.always_check: …")` only when ≥1 file is actually pinned; no line when there are no pins). Extend/adjust the T013 test to assert the no-pin path stays silent.

## Phase 4: User Story 2 — Discover & adopt via docs + sample (P2)

**Goal**: A user can enable the behavior end-to-end from the docs and a copyable sample,
and understands the config is an extensible surface.

**Independent test**: [quickstart.md](./quickstart.md) §"Docs / sample validation" —
sample is valid YAML, demonstrates `always_check`, shows future keys commented out; docs
cover schema/semantics/fail-safe/compression relationship.

- [ ] T016 [P] [US2] Create the committed sample `docs/reference/koan-config.sample.yaml`: annotated `review.always_check` with generic placeholder patterns + the identified future keys (`never_check`, `pause_label`, `default_focus`, `compressor_token_budget`) as commented-out inert examples. Must `yaml.safe_load` cleanly.
- [ ] T017 [US2] Document `.koan/config.yaml` in `docs/users/koan-md.md`: purpose, schema, precedence, `fnmatch` path+basename matching semantics, fail-safe behavior, relationship to diff compression / partial coverage, a copy of the annotated sample, and the future-keys list. Cross-link `specs/components/skills.md`.
- [ ] T018 [P] [US2] Add a short pointer in `README.md` (review section) and/or `docs/users/skills.md` `/review` reference to the new repo-level `.koan/config.yaml` `always_check` control, linking to `docs/users/koan-md.md`.

## Phase 5: Polish & cross-cutting

- [ ] T019 Run `make lint` and `make test`; fix any failures introduced by the change.
- [ ] T020 Run `/brain sync` to refresh frontmatter/`description:` and regenerate stale `index.md` for the touched `docs/`/`specs/` pages; verify `wiki/index.md` entry for `docs/users/koan-md.md` reflects the new content.
- [ ] T021 Ensure `.specify/feature.json` is NOT staged (`git checkout main -- .specify/feature.json`) and run the private leak-pattern grep before staging.

## Dependencies & execution order

- **Phase 2 (T002–T008)** blocks everything: contract-first specs precede code; the
  config reader (T006) and matcher (T008) are used by Phase 3.
- **Phase 3 (US1, T009–T015)** is the MVP; depends on T006 + T008.
  - T010 depends on T008 (matcher) + T009 (test). T012 depends on T008 + T011.
  - T014 depends on T006 + T010 + T012 + T013. T015 depends on T014.
- **Phase 4 (US2, T016–T018)** depends only on the finalized schema (T002/contracts);
  can proceed in parallel with Phase 3 code once the schema is fixed.
- **Phase 5** last.

### Parallel opportunities

- T005 ‖ T007 (different test files).
- T009 ‖ T011 ‖ T013 (different test files) — but each precedes its impl task.
- T016 ‖ T018 (different doc files).

## MVP scope

**User Story 1 (Phase 2 + Phase 3, T002–T015)** delivers the complete behavioral fix:
configured files survive review compression. User Story 2 (docs/sample) makes it
discoverable and ships in the same PR.
