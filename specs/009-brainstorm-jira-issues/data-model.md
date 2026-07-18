# Data Model: Brainstorm Jira Issues

No persistent storage. The "entities" here are in-memory/transport shapes.

## ADF document (Atlassian Document Format)

The JSON tree Jira Cloud renders for an issue description.

```text
doc            := { version: 1, type: "doc", content: [ block... ] }
block          := heading | paragraph | bulletList | orderedList
                | rule | blockquote | codeBlock
heading        := { type: "heading", attrs: { level: 1..6 }, content: [ inline... ] }
paragraph      := { type: "paragraph", content: [ inline... ] }
bulletList     := { type: "bulletList", content: [ listItem... ] }
orderedList    := { type: "orderedList", content: [ listItem... ] }
listItem       := { type: "listItem", content: [ paragraph ] }
rule           := { type: "rule" }
blockquote     := { type: "blockquote", content: [ paragraph... ] }
codeBlock      := { type: "codeBlock", attrs?: { language }, content: [ text ] }
inline (text)  := { type: "text", text: str, marks?: [ mark... ] }
mark           := { type: "strong" } | { type: "em" } | { type: "code" }
```

**Validation / rules** (converter invariants):
- Output is always a valid `doc` with a non-empty `content` (empty input → one
  empty `paragraph`), matching current `_text_to_adf` fallback.
- Unknown/unmodeled markdown lines degrade to `paragraph` text, never dropped,
  never raising.
- Inside a fenced code block, no inline-mark or block parsing occurs; content is
  emitted verbatim as a `codeBlock`.
- Inline marks are applied by splitting a line on `**`, `` ` ``, `*`/`_`; a lone
  or unbalanced marker is treated as literal text (no exception).

## Issue reference mapping (SUB-N ↔ identifier)

Produced by `brainstorm_runner` from the created-issues list.

| Field | Meaning |
|---|---|
| `original_pos` (int) | 1-based ordinal from the decomposition |
| `identifier` (str) | `#<number>` (GitHub) or `PROJ-<n>` (Jira key) |

- Built only from **successfully created** issues; unmapped ordinals leave their
  `SUB-N` token untouched (existing behavior, both providers).
- `_format_issue_ref(id)` renders `#123` for all-digit ids, verbatim otherwise —
  already provider-correct.

## Native issue link (Jira only)

| Field | Meaning |
|---|---|
| `type.name` | Link type — fixed `"Relates"` for this feature |
| `outwardIssue.key` | Master tracking issue key |
| `inwardIssue.key` | Sub-issue key |

- One link per successfully-created sub-issue.
- Creation is best-effort; a failure is logged and skipped (FR-008), never fatal.

## Tracker config (read-only, existing)

From `projects.yaml` `projects.<name>.issue_tracker` (unchanged by this feature):

| Field | Meaning |
|---|---|
| `provider` | `github` \| `jira` (default `github`) |
| `jira_project` | Jira project key (e.g. `PROJ`) — used as the create target |
| `jira_issue_type` | default issue type (default `Task`) |
