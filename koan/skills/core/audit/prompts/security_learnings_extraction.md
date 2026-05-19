You are analyzing a completed codebase audit for the **{PROJECT_NAME}** project. Your job is to extract reusable security intelligence from the audit findings.

## Audit Output to Analyze

{AUDIT_OUTPUT}

## Instructions

Extract security learnings that will help future audits of this and other projects. Be **conservative** — only emit a learning when the evidence in the audit output is clear and specific. Do not hallucinate patterns not present in the findings.

For each learning, produce a block in this exact format, using `---LEARNING---` as separator:

```
---LEARNING---
CATEGORY: <one of: detection_pattern|exploitation_heuristic|remediation_knowledge|framework_weakness|historical_false_positive>
TRUST: ephemeral
SCOPE: <local|global>
CONTENT: <concise, actionable learning — one sentence, no project-specific file paths or variable names if global>
SOURCE: audit-session
```

## Category Definitions

- **detection_pattern**: A specific code smell, API misuse, or structural pattern that indicates a vulnerability (e.g., "raw SQL string concatenation with user input indicates injection risk")
- **exploitation_heuristic**: A heuristic about how a vulnerability class is typically exploited in this type of codebase (e.g., "unvalidated redirect targets in OAuth flows enable open redirect attacks")
- **remediation_knowledge**: A concrete fix strategy for a recurring vulnerability class (e.g., "parameterized queries eliminate SQL injection; never concatenate user input into SQL")
- **framework_weakness**: A known weakness in a specific framework or library version pattern used by the project (e.g., "Flask debug mode enabled in production exposes Werkzeug debugger")
- **historical_false_positive**: A pattern that looks suspicious but is safe in this codebase's context (e.g., "base64-encoded data in config is a known non-secret internal constant")

## Scope Rules

Set SCOPE to **global** ONLY when the learning is:
- Broadly applicable to any project using the same technology/framework
- Free of any project-specific infrastructure, credentials, internal APIs, or file paths
- A general security principle, not a finding specific to this repo

Set SCOPE to **local** when the learning is specific to this project's architecture, conventions, or current code state.

## Rules

- **Be conservative**: Emit zero learnings rather than uncertain ones.
- **No project identifiers in global learnings**: Never reference specific file names, variable names, class names, or internal API names in global-scoped entries.
- **One sentence per CONTENT**: Keep entries concise and actionable.
- **Maximum 10 learnings** per audit session. Quality over quantity.
- If no clear learnings can be extracted, output nothing (no blocks at all).
