You are extracting structured documentation from the **{PROJECT_NAME}** project codebase.

Your goal is to investigate the project and produce high-quality documentation files that capture architecture, conventions, testing patterns, anti-patterns, and recommended modules.

## Parameters

- **Categories**: {CATEGORIES}
- **Mode**: {MODE}
- **Existing docs**: {EXISTING_DOCS}

## Instructions

### Phase 1 — Orientation

1. **Read the project's CLAUDE.md** (if it exists) for architecture overview and conventions.
2. **Explore the directory structure**: Use Glob to understand the layout — source directories, test directories, config files, build files.
3. **Check existing docs/**: Read any existing documentation files to understand what already exists.

### Phase 2 — Extract Documentation

For each requested category, analyze the codebase systematically:

#### architecture
- Module map: what lives where, key directories, entry points
- Data flow: how information moves between components
- Process boundaries: separate processes, IPC mechanisms
- Key abstractions: core classes, interfaces, design patterns used

#### code-style
- Naming conventions: variables, functions, classes, files
- Module structure: imports, exports, organization patterns
- Formatting: line lengths, string styles, indentation patterns
- Forbidden patterns: anti-patterns explicitly avoided per project conventions

#### test-style
- Test framework and runner (pytest, jest, etc.)
- File naming and organization (`tests/test_*.py`, `__tests__/`, etc.)
- Fixture patterns: setup/teardown, shared fixtures, factories
- Mocking conventions: what to mock, where, common patterns
- Anti-patterns: known bad test approaches in this project

#### anti-patterns
- Patterns explicitly forbidden by the project (from CLAUDE.md or conventions)
- Common mistakes derived from code review history or comments
- Performance anti-patterns specific to the tech stack
- Security anti-patterns to avoid

#### modules
- Recommended third-party libraries and why they're preferred
- Standard library modules used for specific patterns
- Banned or deprecated alternatives with reasoning
- Internal utility modules and when to use them

### Phase 3 — Output

For each category, output a documentation block in this exact format:

```
---DOC---
category: <category-name>
title: <human-readable title>
---
<markdown documentation content>
---END DOC---
```

## Mode Rules

- **create**: Produce documentation for all requested categories. If existing docs note "already exists" for a category, skip it entirely (output nothing for that category).
- **update**: Produce documentation for all requested categories. Existing content will be merged by section (H2 headings as merge keys) — produce full output including sections you want to keep or update.
- **replace**: Produce documentation for all requested categories regardless of existing content.

## Quality Standards

- **Be specific to this project.** Generic advice is worthless. Reference actual file paths, actual class names, actual patterns found in the code.
- **Include examples.** For code-style and test-style, show 2-3 short real examples from the codebase.
- **Explain the why.** Don't just document what the convention is — explain why it exists (performance, readability, historical reason).
- **Keep it concise.** Each category should be 30-80 lines. Dense, useful information beats verbose explanations.
- **Read-only.** Do not modify any source files. Only produce documentation output blocks.
