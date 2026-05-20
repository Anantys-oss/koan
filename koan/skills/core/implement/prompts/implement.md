You are implementing a plan from a GitHub issue. Your job is to read the plan carefully and execute it as code changes in the project.

## GitHub Issue

**Issue**: {ISSUE_URL}
**Title**: {ISSUE_TITLE}

## Plan to Implement

{PLAN}

## Additional Context

{CONTEXT}
{PROJECT_MEMORY}
## Instructions

1. **Read the plan carefully**: Understand the overall goal, the phases, and the acceptance criteria for each phase.

2. **Create a dedicated branch**: If you are currently on `main` or `master`, create a new branch before making any changes: `{BRANCH_PREFIX}implement-{ISSUE_NUMBER}`. If you are already on a feature branch, stay on it.

3. **Explore the codebase first**: Use Read, Glob, and Grep to understand the current state of the code. Verify that assumptions in the plan still hold — the codebase may have changed since the plan was written.

{@include implementation-workflow}

Keep your changes focused, testable, and consistent with the project's existing style.
