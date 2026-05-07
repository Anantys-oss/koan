You are performing a performance profile analysis of the **{PROJECT_NAME}** project. Your goal is to identify performance bottlenecks, inefficiencies, and optimization opportunities.

## Instructions

### Phase 1 — Orientation

1. **Read the project's CLAUDE.md** (if it exists) for architecture overview and key modules.
2. **Explore the directory structure**: Use Glob to understand the project layout — source directories, entry points, hot paths.

### Phase 2 — Profile Analysis

Systematically analyze the codebase for performance issues:

#### A. I/O and External Calls
- Identify synchronous I/O in hot paths (file reads, network calls, subprocess invocations).
- Look for missing caching where repeated external calls occur.
- Check for unbuffered reads/writes on large files.

#### B. Algorithmic Complexity
- Search for O(n²) or worse patterns: nested loops over collections, repeated linear searches.
- Look for unnecessary re-computation (values computed inside loops that could be hoisted).
- Check for string concatenation in loops instead of join patterns.

#### C. Memory and Resource Usage
- Identify large data structures loaded entirely into memory when streaming would work.
- Look for resource leaks: unclosed files, connections, or subprocesses.
- Check for unnecessary object creation in tight loops.

#### D. Startup and Import Costs
- Look for heavy module-level imports or initialization that slows startup.
- Check for lazy-import opportunities where modules are only needed conditionally.
- Identify circular import patterns that force restructuring.

#### E. Concurrency Bottlenecks
- Look for global locks, shared mutable state, or serial processing of independent tasks.
- Check for thread-safety issues in shared data structures.
- Identify opportunities for parallel execution.

### Phase 3 — Produce the Report

Output a structured report in this exact format:

```
Performance Profile — {PROJECT_NAME}

## Summary

[2-3 sentence overview of the project's performance posture]

**Performance Score**: [1-10]/10

(1 = highly optimized, 10 = severe performance issues)

## Findings

### Critical (likely user-visible impact)

[Numbered list of critical findings with file paths and line numbers]

### Moderate (measurable but not immediately user-visible)

[Numbered list of moderate findings]

### Minor (micro-optimizations or preventive)

[Numbered list of minor findings]

## Suggested Missions

1. [Most impactful optimization — one sentence]
2. [Second most impactful optimization]
3. [Third most impactful optimization]
```

## Rules

- **Read-only.** Do not modify any files. This is a pure analysis task.
- **Be specific.** Always include file paths and line numbers in findings.
- **Be actionable.** Each finding should explain what to change, not just what's slow.
- **Prioritize by impact.** Critical items are those causing user-visible latency or resource exhaustion. Minor items are micro-optimizations.
- **Limit scope.** Report at most 5 findings per priority level. Focus on real bottlenecks, not theoretical concerns.
- **Suggested missions must be self-contained.** Each should be achievable in a single focused session.
