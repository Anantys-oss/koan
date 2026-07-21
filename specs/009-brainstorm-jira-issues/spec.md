# Feature Specification: Brainstorm Jira Issues

**Feature Branch**: `koan.atoomic/brainstorm-jira-issues`

**Created**: 2026-07-18

**Status**: Draft

**Input**: User description: "The /brainstorm skill decomposes a topic into linked issues under a master tracking issue. Today it produces markdown-rich GitHub issues. When a project is configured with a Jira issue tracker, brainstorm must create the sub-issues and master issue in Jira using that project key and default issue type, and link them properly together — with rich Jira formatting and native cross-issue linking."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Rich, well-formatted Jira issues from brainstorm (Priority: P1)

A maintainer whose project is configured with a Jira issue tracker runs
`/brainstorm <topic>`. Kōan decomposes the topic and files the sub-issues and a
master tracking issue in the project's Jira project. Each issue renders with
proper Jira formatting — headings as headings, bullet and task lists as lists,
the score bars and horizontal rules as structured content, bold and inline code
as marks — not as a wall of literal markdown characters.

**Why this priority**: This is the core defect. Brainstorm already creates Jira
issues via the provider-neutral service layer, but the bodies render as raw
markdown text because the ADF converter only splits on blank lines. Without
readable issues the Jira path is effectively unusable, so this is the MVP.

**Independent Test**: Configure a project with `issue_tracker.provider: jira`,
run brainstorm (or exercise the runner with a mocked Claude decomposition and a
mocked Jira transport), and confirm the ADF document sent to Jira contains
`heading`, `bulletList`, `orderedList`, `rule`, and inline `strong`/`code`
nodes derived from the markdown body — rather than a single paragraph of raw
markdown.

**Acceptance Scenarios**:

1. **Given** a project with a Jira tracker, **When** brainstorm generates a
   sub-issue whose body has `## Why This Matters`, a bullet list, a score-bar
   line, `---`, and `**bold**`, **Then** the created Jira issue's description is
   an ADF document with a heading node, a bullet-list node, a rule node, and a
   bold mark — no literal `##`, `-`, `---`, or `**` markers in rendered text.
2. **Given** a project with a GitHub tracker, **When** brainstorm runs, **Then**
   the GitHub issues are created exactly as they are today (markdown bodies, no
   behavior change).

---

### User Story 2 - Cross-issue references resolve to real Jira keys (Priority: P1)

The decomposition emits `SUB-N` placeholders inside sub-issue bodies (and in the
master body's synthesis sections) that reference sibling issues by ordinal. On
GitHub these are rewritten to `#<number>` after creation. On Jira they must be
rewritten to the real Jira issue keys (e.g. `PROJ-123`) so a reader can follow
the references.

**Why this priority**: Unresolved `SUB-1` tokens are meaningless to a human
reading a Jira issue and defeat the "link them properly together" goal. This
ships together with US1 as the linking half of the MVP.

**Independent Test**: With a mocked Jira transport that returns known keys for
created issues, run the runner over a decomposition whose bodies contain `SUB-2`
references and confirm the follow-up update calls replace `SUB-2` with the real
key of the second created issue.

**Acceptance Scenarios**:

1. **Given** a Jira project and a decomposition where issue 1's body references
   `SUB-2`, **When** brainstorm creates the issues, **Then** issue 1's body is
   updated so `SUB-2` reads as the real key of the second created issue.
2. **Given** a GitHub project, **When** brainstorm creates the issues, **Then**
   `SUB-N` tokens are rewritten to `#<number>` exactly as today.

---

### User Story 3 - Master issue natively linked to its sub-issues in Jira (Priority: P2)

Beyond textual references, the master tracking issue is linked to each of its
sub-issues using Jira's native issue-link mechanism, so the relationship is
visible in Jira's "Linked issues" panel rather than only inside the description
text.

**Why this priority**: Native links are the idiomatic Jira way to express "this
tracking issue relates to these work items" and make the decomposition navigable
in the Jira UI. It is valuable but secondary to the issues being readable and
their textual references being correct; the feature still delivers value without
it (the master body already lists the sub-issue keys).

**Independent Test**: With a mocked Jira transport, run the runner on a Jira
project and confirm one issue-link create call is made from the master issue to
each successfully created sub-issue; confirm GitHub runs make no such calls.

**Acceptance Scenarios**:

1. **Given** a Jira project with N created sub-issues, **When** the master issue
   is created, **Then** N native Jira issue links are created from the master to
   each sub-issue.
2. **Given** a link-create call fails, **When** brainstorm finishes, **Then** the
   failure is non-fatal — the issues remain created and the run still reports
   success.

---

### Edge Cases

- **Unmapped Jira project**: if the project has `provider: jira` but no
  `jira_project` key, tracker creation fails the same way it does today
  ("No issue tracker configured for this project.") — no partial Jira writes.
- **Partial sub-issue creation**: if some sub-issues fail to create, the SUB-N
  mapping still maps only successfully-created ordinals to real keys; unknown
  placeholders are left as-is (current behavior preserved for both providers).
- **Update-after-create failure on Jira**: if rewriting a body to resolve SUB-N
  fails, it is logged and skipped per-issue — it never aborts the run (mirrors
  the GitHub `issue_edit` failure path).
- **Markdown constructs not modeled in ADF**: any markdown the converter does not
  explicitly model degrades to a readable paragraph/text node rather than being
  dropped or raising.
- **Fenced code blocks**: content inside a fenced code block is emitted as an ADF
  code block verbatim, not parsed for headings/lists/marks.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When a project's configured tracker provider is Jira, brainstorm
  MUST create its sub-issues and master issue in that project's Jira project
  using the configured project key and default issue type from `projects.yaml`
  (already the behavior via the service layer; MUST be preserved).
- **FR-002**: When a project's configured tracker provider is GitHub, brainstorm
  behavior MUST be unchanged (markdown bodies, `#N` cross-references, labels,
  task-list master body).
- **FR-003**: Jira issue descriptions produced by brainstorm MUST render rich
  formatting for the markdown constructs brainstorm emits: headings (`#`–`####`),
  unordered lists (including `- [ ]` task items), ordered lists, horizontal
  rules (`---`), blockquotes, fenced code blocks, and inline bold (`**`),
  italic, and inline code (`` ` ``).
- **FR-004**: The rich Jira rendering MUST be produced through the issue-tracker
  service/transport layer, not by adding provider `if/else` branches to
  `brainstorm_runner` beyond selecting service-layer operations.
- **FR-005**: `SUB-N` placeholders inside Jira sub-issue bodies and master-issue
  synthesis text MUST be rewritten to the real Jira issue keys of the
  corresponding created issues after creation.
- **FR-006**: Rewriting an already-created issue's body MUST be available through
  a provider-neutral service-layer operation so brainstorm can resolve SUB-N for
  both GitHub and Jira via one code path.
- **FR-007**: The master tracking issue MUST be natively linked to each created
  sub-issue when the tracker is Jira; this MUST be a no-op for GitHub.
- **FR-008**: All new Jira write operations (rich create, body update, issue
  link) MUST degrade non-fatally — a failure in any one MUST NOT abort the
  brainstorm run or lose already-created issues, and MUST be logged.
- **FR-009**: Human-authored Jira comment posting (`/comment` and other
  `add_comment` callers) MUST NOT be regressed by the rich-formatting change —
  existing comment behavior and its invariants are preserved.

### Key Entities *(include if feature involves data)*

- **ADF document**: Atlassian Document Format JSON tree (`{version, type: doc,
  content: [...]}`) that Jira renders. Nodes of interest: `heading`,
  `paragraph`, `bulletList`/`orderedList`/`listItem`, `rule`, `blockquote`,
  `codeBlock`, and inline `text` with `strong`/`em`/`code` marks.
- **Issue reference (SUB-N ↔ key)**: the mapping from a decomposition ordinal to
  the real created issue identifier (`#<number>` on GitHub, `PROJ-<n>` on Jira).
- **Native issue link**: a Jira link record relating the master issue to a
  sub-issue, of a configured/standard link type (e.g. "Relates").

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a Jira-configured project, 100% of brainstorm sub-issues and
  the master issue are created in Jira (not GitHub), using the project's
  configured key and issue type.
- **SC-002**: A brainstorm sub-issue body containing all seven required sections
  renders in Jira with zero literal markdown structural markers (`##`, leading
  `- `, `---`, `**`) shown as text; every such construct appears as its native
  Jira element.
- **SC-003**: 100% of resolvable `SUB-N` references in Jira issue bodies display
  the real Jira issue key rather than the `SUB-N` placeholder.
- **SC-004**: The master issue shows every successfully-created sub-issue in
  Jira's native "Linked issues" panel.
- **SC-005**: GitHub-configured projects show no observable change in brainstorm
  output (byte-identical issue bodies and cross-references to the pre-feature
  behavior for the same decomposition).

## Assumptions

- Jira Cloud REST API v3 (ADF descriptions) is the target, consistent with the
  existing `jira_notifications.py` transport (`/rest/api/3/...`).
- The default native link type is a standard, always-available type ("Relates");
  it is not configurable in this feature (may be a future enhancement).
- The decomposition prompt and required-section contract are unchanged; this
  feature changes only how the resulting bodies are transported/rendered to Jira
  and how references are linked — not what Claude is asked to produce.
- The markdown → ADF converter targets the subset of markdown brainstorm
  actually emits; exhaustive CommonMark support is out of scope. Unmodeled
  constructs degrade to readable text.
- Native Jira issue linking requires the account/token to have link-create
  permission; absence degrades non-fatally per FR-008.
