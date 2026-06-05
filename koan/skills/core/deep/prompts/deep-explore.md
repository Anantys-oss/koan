You are performing a **deep autonomous exploration** of the project **{PROJECT_NAME}**.

This is not a quick survey — you have full tool access and extended time. Explore thoroughly.

{FOCUS_CONTEXT}

## Recent activity

{GIT_ACTIVITY}

## Project structure

{PROJECT_STRUCTURE}

## Current state

{MISSIONS_CONTEXT}

{PROJECT_MEMORY}

{PROJECT_HEALTH}

## Your mission

Perform a thorough, autonomous exploration of this codebase. You have full tool access —
use it aggressively. Read code, trace execution paths, check test coverage, run the test
suite, and verify assumptions by looking at actual code rather than guessing from names.

### Phase 1: Understand

Start by building a mental model of the project:
- Read the main entry points and trace how data flows through the system
- Identify the core abstractions and how modules interact
- Look at test coverage — what's tested, what's not
- Check for CLAUDE.md, README, or other project docs

### Phase 2: Analyze

With understanding in hand, look for:
- **Bugs**: Real bugs, not style nitpicks. Race conditions, off-by-one errors, unchecked
  error paths, data loss risks, silent failures
- **Architecture issues**: Misplaced responsibilities, circular dependencies, abstractions
  that leak, modules doing too many things
- **Missing tests**: Critical paths with no test coverage, edge cases untested
- **Security concerns**: Input validation gaps, injection risks, improper auth checks
- **Performance**: O(n²) where O(n) exists, unnecessary I/O, missing caching
- **Reliability**: Error handling that swallows exceptions, retry logic without backoff,
  resource leaks (file handles, connections)

### Phase 3: Propose

Generate **5-10 concrete, actionable missions** ranked by impact. Each must be specific
enough that another agent can implement it without re-reading the entire codebase.

Rules:
- **Read before suggesting.** Every finding must reference actual code you read, with
  file paths and line numbers. "The error handling in X" is not specific enough —
  "Line 42 of src/handler.py catches Exception and returns None, silently dropping
  database errors" is.
- **Prioritize real impact.** Bugs and security issues over style preferences.
  Things that will break in production over things that look ugly.
- **Don't duplicate existing work.** Cross-reference missions context above and project
  learnings. If something is already being worked on or was already tried, skip it.
- **Be concrete about scope.** Each mission should be completable in one focused session.
  "Refactor the entire API layer" is too broad. "Extract retry logic from the 4 API
  callers in src/clients/ into a shared decorator" is right-sized.

External project constraints:
- **CI matrix**: never remove existing entries from CI test matrices
- **Dependencies**: don't suggest removing or downgrading existing dependencies without justification
- **Conventions**: respect the project's existing code style and structure

## Output format

Write your analysis as a natural-language report — architecture observations, interesting
findings, risk areas. This report is sent to the human via Telegram.

At the END of your response, output each proposed mission as a structured `---MISSION---`
block. These are parsed programmatically and queued automatically.

```
---MISSION---
TITLE: Fix silent exception swallowing in database retry handler
PRIORITY: high
CATEGORY: bug
SCOPE: src/db/retry.py:42-58
RATIONALE: The retry decorator catches bare Exception and returns None on exhaustion. When a query fails with IntegrityError, callers receive None and proceed as if the row doesn't exist, leading to duplicate inserts. Should re-raise the last exception after retry exhaustion.
---MISSION---
TITLE: Add test coverage for concurrent session cleanup
PRIORITY: medium
CATEGORY: testing
SCOPE: src/sessions/manager.py, tests/test_sessions.py
RATIONALE: SessionManager.cleanup() acquires a lock but the concurrent code path (lines 89-112) has zero test coverage. The threading.Timer callback could fire during cleanup, and without tests, a race condition here would only surface in production.
```

### Field reference

| Field | Required | Values |
|-------|----------|--------|
| TITLE | yes | One-line description specific enough to implement as a standalone mission |
| PRIORITY | yes | `high` / `medium` / `low` |
| CATEGORY | yes | `bug` / `security` / `testing` / `architecture` / `performance` / `reliability` |
| SCOPE | yes | File path(s) with line numbers — where the work is |
| RATIONALE | yes | 2-4 sentences: what's wrong, why it matters, what the fix should look like |

### Rules for ---MISSION--- blocks
- Use `---MISSION---` as the exact separator
- One block per mission
- TITLE must be specific: mention file names, function names, or patterns
- SCOPE must reference actual files and lines you verified
- No `[project:name]` tag in TITLE (added automatically)
- Only propose missions you're confident are worth implementing
