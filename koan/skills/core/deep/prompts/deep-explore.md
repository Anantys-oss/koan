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

## Phase 2: Analyze

With understanding in hand, run a focused deep analysis pass.

You may work alone, but if the tooling/runtime supports sub-agents, you should start **up to 5 parallel sub-agents** to broaden the search.

The orchestrator must assign each sub-agent **one distinct focus area** selected from the list below. If there are more focus areas than available sub-agents, choose the most relevant areas based on project shape, recent activity, risk profile, and current state. If no clear priority exists, assign areas randomly.

The orchestrator remains responsible for the final judgment. Sub-agents gather evidence; the orchestrator validates, reconciles, deduplicates, and prioritizes findings.

### Focus Areas

#### 1. Bugs and Correctness
- Real bugs, not style nitpicks.
- Race conditions.
- Data corruption risks.
- Off-by-one errors.
- Broken edge cases.
- Silent failures.
- Incorrect assumptions.
- State consistency issues.
- User-visible defects.

#### 2. Architecture and Maintainability
- Misplaced responsibilities.
- Circular dependencies.
- Leaky abstractions.
- Excessive coupling.
- Duplicate business logic.
- Modules doing too many things.
- Designs slowing future delivery.

Do not recommend large rewrites unless there is clear evidence the current architecture is blocking progress.

#### 3. Testing and SDLC Confidence
- Missing test coverage on critical paths.
- Missing regression tests.
- Untested edge cases.
- Fragile mocks.
- Flaky tests.
- Gaps between CI validation and production behavior.

Favor tests that would catch real failures.

#### 4. Security and Trust Boundaries
- Input validation issues.
- Injection risks.
- Improper authorization.
- Secret exposure.
- Unsafe defaults.
- Insecure file or network operations.
- Boundary violations involving untrusted input.

Security findings must be backed by concrete evidence.

#### 5. Performance and Scalability
- Inefficient algorithms.
- Repeated I/O.
- Missing caching.
- Expensive queries.
- Excessive serialization.
- Unnecessary network calls.
- Blocking operations in critical paths.

Distinguish theoretical concerns from realistic bottlenecks.

#### 6. Reliability and Operations
- Swallowed exceptions.
- Missing timeouts.
- Retries without backoff.
- Resource leaks.
- Weak observability.
- Poor logging.
- Recovery path weaknesses.
- Startup/shutdown fragility.

Focus on production impact.

#### 7. Product Impact and Bold Opportunities
- User experience improvements.
- Onboarding improvements.
- Automation opportunities.
- Operational cost reductions.
- Support burden reductions.
- Adoption improvements.
- Missing capabilities with high leverage.

Do not be afraid to identify bold opportunities when supported by evidence.

### Sub-Agent Requirements

Each sub-agent must:

- Inspect actual code.
- Read files, not only names.
- Reference concrete evidence.
- Include file paths and line numbers.
- Report confidence level.
- Report implementation complexity.
- Report risks and unknowns.

Prefer a few strong findings over many speculative ones.

If no worthwhile finding exists, explicitly state that.

---

## Phase 2.5: Reconcile Findings

Before generating missions, the orchestrator must perform a reconciliation pass.

### Objectives

- Merge overlapping findings.
- Eliminate duplicates.
- Resolve contradictions.
- Validate high-impact findings through direct code inspection.
- Identify root causes behind multiple symptoms.
- Convert observations into implementable opportunities.

### Validation Rules

Re-read the underlying code before accepting any major finding.

For each accepted finding record:

- Root cause
- Evidence
- Impact
- Confidence
- Implementation effort

Reject findings that are:

- Style-only
- Speculative
- Unsupported by code evidence
- Already covered by a higher-level finding

### Ranking Criteria

Rank findings by:

1. User impact
2. Production risk
3. Security impact
4. Data integrity risk
5. Reliability impact
6. Engineering velocity impact
7. Confidence level

The output of this phase should be a reconciled set of distinct opportunities rather than a collection of observations.

---

## Phase 3: Propose

Generate **5-10 concrete, actionable missions** ranked by impact.

Every mission must originate from a validated finding produced during Phase 2.5.

### Mission Selection Strategy

Do not optimize for the number of missions.

Optimize for total project impact.

The first mission should represent the single highest-value improvement discovered during the entire analysis.

When selecting missions, prioritize:

1. Critical production risks
2. Security vulnerabilities
3. Data integrity risks
4. Reliability and operational risks
5. User-facing product improvements
6. Performance bottlenecks
7. Developer productivity improvements
8. Architecture improvements
9. Test coverage improvements
10. Cleanup and maintenance work

A mission with significantly higher impact should always outrank multiple lower-value missions.

Prefer:

- One mission preventing production outages over five cleanup tasks.
- One mission improving a critical user workflow over several code-quality improvements.
- One mission eliminating a major source of support burden over multiple refactors.

Do not attempt to balance categories.

The final mission list may contain multiple missions from the same category if they represent the highest-value opportunities.

### Mission Uniqueness Rules

Missions must be UNIQUE.

- Never generate multiple missions for the same root cause.
- Merge overlapping findings.
- Prefer root-cause fixes over symptom fixes.
- Remove duplicate implementation work.
- Ensure each mission addresses a distinct problem or opportunity.

### Final Prioritization Check

Before emitting missions, ask:

"If I could only implement ONE mission from this entire report, which would I choose?"

That mission must appear first.

Then repeat the exercise for the remaining findings until all missions are ordered.

Assume engineering resources are limited.

If only the first 1–3 missions were implemented, they should still deliver substantial value to the project.

The resulting mission list should resemble an executive investment portfolio:

- High conviction
- High impact
- Minimal overlap
- Clear implementation scope
- Maximum return per engineering hour

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
