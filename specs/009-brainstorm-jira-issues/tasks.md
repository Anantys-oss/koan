# Tasks: Brainstorm Jira Issues

**Feature**: `specs/009-brainstorm-jira-issues/` | **Branch**: `koan.atoomic/brainstorm-jira-issues`

**Input**: plan.md, spec.md, research.md, data-model.md, contracts/service-layer.md, quickstart.md

**Tests**: Included — the project's constitution/CLAUDE.md mandate behavior tests
(pytest with `KOAN_ROOT`, mock transport not the LLM). Test tasks precede or
accompany the implementation they cover.

**Commit discipline**: one commit per task (skip empty commits for no-change
tasks), following the repo's conventional-commit style.

---

## Phase 1: Setup

- [ ] T001 Confirm the Jira transport seams exist and are mockable in `koan/app/jira_notifications.py` (`_jira_post`, `_jira_put`, `_jira_auth_from_config`, existing `_text_to_adf`) and the tracker factory seams in `koan/app/issue_tracker/__init__.py` (`client_for_url`, `client_for_project`); no code change — a read-only verification that the plan's integration points are accurate before edits.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: The markdown→ADF converter and the provider-neutral service/ABC
surface that every user story builds on.

- [X] T002 [P] Add `markdown_to_adf(text: str) -> dict` to `koan/app/jira_notifications.py`: converts brainstorm's markdown subset (headings `#`–`####`, unordered lists incl. `- [ ]`/`- [x]`, ordered lists `1.`, horizontal rule `---`, blockquotes `>`, fenced code blocks, inline `**bold**`/`*em*`/`` `code` ``) into an ADF `doc`; unmodeled lines degrade to `paragraph`; empty input → one empty `paragraph` (matches `_text_to_adf` fallback). Pure function, stdlib only. See `contracts/service-layer.md` + `data-model.md`.
- [X] T003 [P] Add unit tests `koan/tests/test_jira_adf.py` for `markdown_to_adf`: heading levels, bullet/ordered lists, rule, blockquote, fenced-code-verbatim, inline marks (`strong`/`em`/`code`), unbalanced-marker-as-literal, and empty/whitespace fallback. Assert on ADF node shapes (behavior, not source).
- [X] T004 Add `jira_update_issue_description(issue_key, body_text) -> bool` and `jira_link_issues(outward_key, inward_key, link_type="Relates") -> bool` to `koan/app/jira_notifications.py` (PUT `/rest/api/3/issue/{key}` description via `_jira_put`; POST `/rest/api/3/issueLink` via `_jira_post`). Both return False on transport failure, never raise. Reuse `_jira_auth_from_config`.
- [X] T005 [P] Add unit tests `koan/tests/test_jira_transport_ops.py` (mock `_jira_put`/`_jira_post`) for `jira_update_issue_description` and `jira_link_issues`: correct endpoint/payload shape, True on success, False on `None`/failure return.
- [X] T006 Add `update_issue(self, url, body) -> bool` (default `return False`) and `link_issues(self, parent_url, child_url, link_type="Relates") -> bool` (default `return False`) as **concrete** members of the `IssueTracker` ABC in `koan/app/issue_tracker/base.py`, with docstrings from `contracts/service-layer.md`. Non-abstract so existing subclasses keep working.
- [X] T007 Add service-layer functions `update_issue(...)` and `link_issues(...)` to `koan/app/issue_tracker/__init__.py` routing through `client_for_url(...)` per `contracts/service-layer.md`.

**Checkpoint**: converter + transport + neutral contract exist and are unit-tested; no user-visible behavior change yet.

---

## Phase 3: User Story 1 — Rich, well-formatted Jira issues (Priority: P1) 🎯 MVP

**Goal**: Brainstorm-created Jira issues render markdown as native ADF elements.

**Independent Test**: With a mocked Jira transport, the description sent for a
brainstorm sub-issue is an ADF doc with heading/list/rule/mark nodes (not raw
markdown).

- [X] T008 [US1] Change `jira_create_issue` in `koan/app/jira_notifications.py` to build the `description` field via `markdown_to_adf(body_text)` instead of `_text_to_adf(body_text)`. Leave `jira_add_comment`/`jira_edit_comment` on `_text_to_adf` (FR-009 — no comment regression).
- [X] T009 [P] [US1] Add/extend tests in `koan/tests/test_jira_create_issue.py` (or nearest existing Jira create test; mock `_jira_post`) asserting the created-issue `description` is a structured ADF doc (heading + bulletList + rule + strong mark) for a representative brainstorm body, and that `jira_add_comment` still uses the plain `_text_to_adf` shape.
- [X] T010 [US1] Add a regression guard in `koan/tests/test_brainstorm_jira.py`: run `run_brainstorm` over a fixed decomposition with a **mocked Jira tracker** (patch `create_issue`/`client_for_project`/`tracker_provider` at the runner's import site) and assert every created Jira issue body was carried through unchanged to the create call (rich rendering is verified at the transport layer in T009; here assert routing selects Jira create).

**Checkpoint**: US1 independently testable — Jira issues render richly; GitHub untouched.

---

## Phase 4: User Story 2 — SUB-N cross-references resolve to real Jira keys (Priority: P1)

**Goal**: `SUB-N` tokens in Jira sub-issue bodies are rewritten to real Jira keys.

**Independent Test**: With a mocked tracker returning known keys, the runner's
post-create update replaces `SUB-2` with the second created issue's key.

- [X] T011 [US2] Implement `update_issue` on both backends in `koan/app/issue_tracker/github.py` (delegate to `app.github.issue_edit`, catch → False) and `koan/app/issue_tracker/jira.py` (delegate to `jira_update_issue_description(parse_jira_url(url), body)`).
- [X] T012 [US2] Rework `_replace_sub_placeholders` in `koan/skills/core/brainstorm/brainstorm_runner.py`: remove the `if provider != "github": return` early-return; build the ordinal→identifier map from `created_issues`; for each changed body call the provider-neutral service `update_issue(url, body, project_name, project_path)` (using each created issue's URL) instead of `issue_edit` directly. Keep per-issue failure logging non-fatal (FR-008). Update the call site in `run_brainstorm` (it currently passes `number`/`repo` for GitHub — pass URLs so both providers work).
- [X] T013 [P] [US2] Extend `koan/tests/test_issue_tracker*.py` to cover `update_issue` routing: GitHub client calls `issue_edit`; Jira client calls `jira_update_issue_description`; both return False (not raise) on backend error.
- [ ] T014 [US2] Extend `koan/tests/test_brainstorm_jira.py`: mocked Jira tracker whose `create_issue` returns known browse URLs/keys; a decomposition where issue 1 references `SUB-2`; assert the runner issues an `update_issue` for issue 1 whose new body contains issue 2's real key and no literal `SUB-2`. Add the GitHub-parity counterpart (SUB-N → `#N`, no behavior change).

**Checkpoint**: US2 independently testable — references resolve on both providers via one code path.

---

## Phase 5: User Story 3 — Master natively linked to sub-issues in Jira (Priority: P2)

**Goal**: The master tracking issue is linked to each sub-issue via Jira's native
issue-link mechanism; no-op on GitHub.

**Independent Test**: Mocked tracker — one `link_issues(master, sub)` per created
sub-issue on Jira; zero on GitHub.

- [ ] T015 [US3] Implement `link_issues` on both backends: `koan/app/issue_tracker/jira.py` delegates to `jira_link_issues(parse_jira_url(parent), parse_jira_url(child), link_type)`; `koan/app/issue_tracker/github.py` returns False (no-op). (Base default already False from T006.)
- [ ] T016 [US3] In `koan/skills/core/brainstorm/brainstorm_runner.py`, after the master issue is created, add a best-effort step that calls the service `link_issues(master_url, sub_url)` for each successfully-created sub-issue. Non-fatal: log and continue on any failure; do not change the success summary/return. GitHub returns no-op so no behavior change there.
- [ ] T017 [P] [US3] Tests in `koan/tests/test_brainstorm_jira.py`: mocked Jira tracker asserts N `link_issues` calls (master→each sub) after master creation, and that a raised/False link failure leaves the run successful with all issues intact; mocked GitHub run asserts zero `link_issues` calls and unchanged output.

**Checkpoint**: US3 independently testable — native links appear on Jira, GitHub unchanged.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T018 [P] Update durable contract `specs/components/issue-tracking.md` — document the new `update_issue`/`link_issues` neutral operations, the ADF rich-rendering responsibility of the Jira transport for issue descriptions (and the comment carve-out), and the master↔sub native-link behavior. (Declared architectural change — PR checkbox.)
- [ ] T019 [P] Update durable skill spec `specs/skills/brainstorm.md` — brainstorm now creates rich, natively-linked issues on Jira via the provider-neutral service layer (rendering + SUB-N resolution + master linking), GitHub unchanged.
- [ ] T020 [P] Update `docs/messaging/jira-integration.md` (rich brainstorm rendering, SUB-N key resolution, native master↔sub links) and, if user-facing behavior warrants, the `/brainstorm` entries in `docs/users/skills.md` / `docs/users/user-manual.md`. Then run `/brain sync` to refresh frontmatter/indexes.
- [ ] T021 Run `make lint` and the full targeted test suite (`KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_jira_adf.py koan/tests/test_jira_transport_ops.py koan/tests/test_brainstorm_jira.py koan/tests/test_issue_tracker*.py -v`); fix any failures. Confirm no `.specify/feature.json` is staged for the PR.

---

## Dependencies & Execution Order

- **Setup (T001)** → **Foundational (T002–T007)** must complete before user stories.
- **US1 (T008–T010)** depends on T002 (converter).
- **US2 (T011–T014)** depends on T006/T007 (neutral contract) + T004 (transport).
- **US3 (T015–T017)** depends on T004 (transport) + T006/T007 (neutral contract).
- US1, US2, US3 are otherwise independent slices and can be verified on their own.
- **Polish (T018–T021)** last.

## Parallel Opportunities

- T002 & T003 (converter + its tests) parallel with T004 & T005 (transport ops + tests).
- Within US2: T013 (routing tests) ∥ T011 implementation once seams exist.
- Docs tasks T018/T019/T020 are mutually parallel.

## MVP Scope

**User Story 1 alone** (rich rendering) is the minimum viable slice — it makes
the Jira path usable. US2 (reference resolution) and US3 (native links) complete
the "link them properly together" goal.

## Task Count

- Total: 21 tasks
- Setup: 1 (T001)
- Foundational: 6 (T002–T007)
- US1: 3 (T008–T010)
- US2: 4 (T011–T014)
- US3: 3 (T015–T017)
- Polish: 4 (T018–T021)
