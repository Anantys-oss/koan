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

### Implementation Guidelines

- **Be surgical**: Make the smallest changes necessary to fulfill the plan. Don't refactor unrelated code, don't add features not in the plan.
- **Handle ambiguity**: If the plan is unclear about a detail, make your best judgment based on existing code patterns. Document your decision in a code comment if it's non-obvious.
- **Subset scope**: If the additional context specifies a subset (e.g., "Phase 1 to 3"), only implement the specified phases. Skip the others.
- **Update documentation and config files** (if your changes affect user-facing behavior):
    - **Skip this step** if your changes are purely internal refactors with no user-visible impact.
    - **User docs**: Check for `README.md`, `docs/`, and `documentation/` directories at the project root. If any exist and your changes affect commands, configuration, features, or usage — update the relevant sections. Don't generate documentation from scratch for undocumented projects.
    - **Config files**: If you introduced new configuration keys (YAML, TOML, JSON, etc.), add inline comments explaining each new key's purpose, expected type, default value, and valid options. Match the commenting style already present in the file. Also update any sample/example config files (e.g., `*.example.yaml`, `instance.example/`) to include the new keys with documented defaults.
    - Commit doc/config updates as part of the current phase or as a dedicated follow-up commit.

{@include implementation-workflow}

Keep your changes focused, testable, and consistent with the project's existing style.
