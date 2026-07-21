# Research: Brainstorm Jira Issues

## R1 — How to render rich content in Jira Cloud

**Decision**: Emit Atlassian Document Format (ADF) JSON, produced by a new
`markdown_to_adf()` in `koan/app/jira_notifications.py`, replacing the naive
`_text_to_adf()` for issue *descriptions*.

**Rationale**: The existing transport already targets Jira Cloud REST API v3
(`/rest/api/3/...`), whose `description`/`comment.body` fields are ADF documents
(`{version:1, type:"doc", content:[...]}`). The current `_text_to_adf()` only
groups non-blank lines into `paragraph` nodes, so markdown structure (`##`,
`- `, `---`, `**`) survives as literal text. A structural converter that maps the
markdown subset brainstorm emits to ADF `heading`/`bulletList`/`orderedList`/
`rule`/`blockquote`/`codeBlock` block nodes and `strong`/`em`/`code` inline marks
is the minimal correct fix.

**Alternatives considered**:
- *Jira wiki markup instead of ADF*: rejected — REST v3 description is ADF; wiki
  markup is v2/Server-era and would not render on Cloud.
- *Third-party markdown→ADF library (e.g. `md2adf`, `mistune`+adapter)*:
  rejected per Principle VII (YAGNI / no new dependency). Brainstorm emits a
  small, known markdown subset; a hand-rolled converter (~1 module function) is
  auditable and dependency-free, matching how `_text_to_adf`/`_adf_to_text`
  already live inline.

## R2 — Which markdown constructs must be supported

**Decision**: Support exactly the constructs brainstorm bodies contain:
- Headings `#`–`####` (`## Why This Matters`, `### < 1 day`, `## Scores`, etc.)
- Unordered lists including task items `- [ ] ...` / `- [x] ...` and plain `- `
- Ordered lists `1. ...` (Top Ranked)
- Horizontal rule `---`
- Blockquotes `> ...` (defensive; alerts already flattened elsewhere)
- Fenced code blocks ```` ``` ```` (verbatim, not re-parsed)
- Inline: bold `**...**`, italic `*...*`/`_..._`, inline code `` `...` ``
- Everything else → plain paragraph text (safe degradation, FR edge case).

**Rationale**: Matches `REQUIRED_ISSUE_SECTIONS` and `_build_master_body` output
in `brainstorm_runner.py`. Task-item checkboxes map to ADF `taskList`/`taskItem`
if desired, but a plain `bulletList` with the leading `[ ]`/`[x]` stripped/kept
is simpler and renders cleanly; decision: render task items as bullet-list items
preserving a leading ☐/☑ or `[ ]` text is unnecessary — strip the checkbox
marker into an ADF `taskItem` only if trivial, else bullet. Chosen: **bulletList
with the `[ ]`/`[x]` preserved as leading text** for simplicity and zero risk of
malformed `taskList` (which requires `localId`s). This keeps the converter pure
and dependency-free while remaining readable.

**Alternatives considered**: full ADF `taskList` with per-item `localId` —
rejected as extra complexity for marginal gain; can be a later enhancement.

## R3 — How to resolve SUB-N cross references on Jira

**Decision**: Reuse brainstorm's existing two-pass approach (create all issues,
then patch each body) but route the patch through a new provider-neutral
`update_issue(url, body)` service function instead of the GitHub-only
`issue_edit`. Remove the `if provider != "github": return` guard in
`_replace_sub_placeholders`; build the ordinal→identifier map from the created
issues (works for both `#N` and `PROJ-N` since `_format_issue_ref` already
handles both) and update each changed body via the neutral op.

**Rationale**: The SUB-N replacement logic is already provider-agnostic except
for the transport call. `_format_issue_ref` returns `#123` for digits and the
key verbatim otherwise, so Jira keys flow through unchanged. The only missing
piece is a neutral "update an existing issue's body" capability.

**Alternatives considered**: pre-resolve SUB-N before creation — impossible, real
keys are unknown until Jira returns them; same reason GitHub does it in two
passes.

## R4 — Jira transport for update + link

**Decision**:
- **Update description**: `PUT /rest/api/3/issue/{key}` with
  `{"fields": {"description": <adf>}}` via the existing `_jira_put` helper
  (already used by `jira_edit_comment`).
- **Native link**: `POST /rest/api/3/issueLink` with
  `{"type": {"name": "Relates"}, "inwardIssue": {"key": sub}, "outwardIssue":
  {"key": master}}` via the existing `_jira_post` helper.

**Rationale**: Both endpoints are standard Jira Cloud REST v3; "Relates" is a
built-in, always-present link type. Reusing `_jira_put`/`_jira_post` inherits the
module's auth (`_jira_auth_from_config`), error handling, and (per module
conventions) non-fatal `None`-on-failure behavior.

**Alternatives considered**:
- *Sub-task hierarchy / Epic-link*: rejected — requires the master to be an Epic
  and sub-issues to be sub-tasks, imposing an issue-type structure Kōan does not
  control (`jira_issue_type` is a single configured type). "Relates" links work
  regardless of issue types.
- *Configurable link type*: deferred (Assumptions) — "Relates" as a fixed
  default keeps scope tight; can be a `projects.yaml` knob later.

## R5 — GitHub path must not change

**Decision**: `GitHubIssueTracker.update_issue()` delegates to the existing
`issue_edit` (identical to today's call). `GitHubIssueTracker.link_issues()`
returns `False`/no-op (GitHub uses task-list + `#N` mentions already emitted).
Confirm brainstorm's GitHub output (bodies, `#N` refs, labels, master task list)
is unchanged by a byte-comparison test on a fixed decomposition.

**Rationale**: SC-005 requires zero observable GitHub change. Routing GitHub's
update through the same `issue_edit` call it uses today guarantees parity.

## R6 — Comment path regression risk

**Decision**: Leave `jira_add_comment` / `jira_edit_comment` on `_text_to_adf`
unchanged (do not switch them to `markdown_to_adf`) for this feature. Only
`jira_create_issue`'s `description` and the new `jira_update_issue_description`
use `markdown_to_adf`.

**Rationale**: FR-009 / the issue-tracking spec invariant that human `/comment`
content must not be mangled. Scoping the rich converter to issue descriptions
avoids any comment regression. Comment enrichment can be a separate, later
change.
