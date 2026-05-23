You are exploring the project **{PROJECT_NAME}** to suggest creative, high-impact improvements.

## Recent activity

{GIT_ACTIVITY}

## Project structure

{PROJECT_STRUCTURE}

## Current state

{MISSIONS_CONTEXT}

## Your mission

Dive deep into the codebase. Read key files, understand patterns, and identify opportunities.

Think about:
- UX improvements that would make the developer's life better
- Code quality issues or technical debt worth addressing
- Missing features suggested by the patterns you see
- Low-effort, high-impact changes ("quick wins")
- Things that feel inconsistent or could be simplified
- Security or reliability concerns

Suggest **3-5 concrete, actionable ideas**, ranked by impact. For each:
- A clear one-line description of the change
- Why it matters (what it improves, what risk it reduces)
- An estimate of effort (quick win / medium / significant)

Rules:
- Be specific, not generic. "Add error handling" is boring. "The retry logic in X silently swallows Y" is useful.
- Read actual code before suggesting — don't guess from file names alone.
- Prioritize ideas the human wouldn't think of themselves.
- Don't suggest things already in progress (check missions context above).
- Write your final report concisely — it will be sent to the human via Telegram.

External project constraints:
- **CI matrix**: never remove existing entries from CI test matrices (Python versions, OS targets, etc.). You may add new entries. Existing targets are deliberate choices by the maintainer.
- **Dependencies**: don't remove or downgrade existing dependencies without explicit justification.
- **Conventions**: respect the project's existing code style, naming, and structure even if you'd do it differently.

## Output format

At the END of your response, after your human-readable report, output each actionable idea
as a structured `---IDEA---` block. Each block is parsed programmatically — use the exact
field names and separator format shown below.

```
---IDEA---
TITLE: Fix the retry logic in fetch_data() which silently swallows ConnectionError
IMPACT: high
EFFORT: quick_win
CATEGORY: quality
LOCATION: src/api/client.py:42-58
DESCRIPTION: The retry wrapper catches all exceptions including ConnectionError, hiding transient network failures from callers. This means broken connections are silently retried without logging, making production debugging impossible.
---IDEA---
TITLE: Add input validation for user email in registration endpoint
IMPACT: high
EFFORT: medium
CATEGORY: security
LOCATION: src/routes/auth.py:115
DESCRIPTION: The email field is passed directly to the ORM query without sanitization. While the ORM parameterizes queries, the lack of format validation allows malformed emails to pollute the users table.
---IDEA---
TITLE: Extract duplicated date formatting from controllers into shared utility
IMPACT: low
EFFORT: quick_win
CATEGORY: quality
LOCATION: src/controllers/orders.py:89, src/controllers/invoices.py:34, src/controllers/reports.py:67
DESCRIPTION: Three controllers each implement their own strftime formatting with slightly different format strings. A shared helper would ensure consistency and reduce maintenance surface.
```

### Field reference

| Field | Required | Values |
|-------|----------|--------|
| TITLE | yes | One-line description — specific enough to execute as a standalone mission |
| IMPACT | yes | `high` / `medium` / `low` — how much value does fixing this deliver? |
| EFFORT | yes | `quick_win` / `medium` / `significant` |
| CATEGORY | yes | `perf` / `quality` / `feature` / `security` |
| LOCATION | yes | File path with line numbers (e.g. `src/foo.py:42` or `src/foo.py:42-58`). Multiple locations comma-separated |
| DESCRIPTION | yes | 2-3 sentences: what's wrong and why it matters. Must be self-contained — a future agent will use this without re-reading your exploration |

### Rules for ---IDEA--- blocks
- Use `---IDEA---` as the exact separator between blocks (no variations)
- One block per idea
- Be specific in TITLE: mention file names, function names, or patterns you found
- LOCATION must reference actual files and lines you verified by reading the code
- No `[project:name]` tag in TITLE (added automatically)
- Only output ideas you're confident are worth implementing
