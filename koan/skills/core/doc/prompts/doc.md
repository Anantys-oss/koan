You are extracting structured documentation from the **{PROJECT_NAME}** project codebase.

Investigate the project thoroughly and produce documentation that would let a new contributor understand architecture, conventions, and pitfalls within 30 minutes of reading.

## Parameters

- **Categories requested**: {CATEGORIES}
- **Write mode**: {MODE}
- **Existing docs state**:
{EXISTING_DOCS}

---

## Phase 1 — Deep Orientation (do this FIRST, before writing anything)

1. **Read CLAUDE.md** (if it exists) — this is the authoritative source for conventions, architecture, and anti-patterns. Extract every convention, not just the obvious ones.
2. **Read README.md** and any existing `docs/*.md` files — understand what documentation already exists and at what quality level.
3. **Explore directory structure** — use Glob (`**/*.py`, `**/*.ts`, etc.) to map out:
   - Source directories and their purpose
   - Test directories and naming patterns
   - Config files (pyproject.toml, package.json, Makefile, etc.)
   - Entry points (main modules, CLI scripts, __main__.py)
4. **Read 3-5 representative source files** — not just directory listings. Actually read core modules to understand real patterns, naming, and structure.
5. **Read 2-3 test files** — understand how tests are actually written, what fixtures exist, what mocking patterns are used.
6. **Check for linter/formatter config** — ruff, eslint, prettier, black settings reveal enforced conventions.

**Do NOT skip this phase.** Surface-level directory listings produce generic, useless documentation. Read actual code.

---

## Phase 2 — Extract Documentation

For each requested category, investigate systematically and produce documentation grounded in evidence from the codebase.

### architecture
Investigate and document:
- **Module map**: What lives where — key directories, their purpose, entry points. Include actual paths.
- **Data flow**: How information moves between components. Trace at least one request/command through the system.
- **Process boundaries**: Separate processes, threads, IPC mechanisms, shared state.
- **Key abstractions**: Core classes, interfaces, design patterns. Name the actual classes/functions.
- **Dependency graph**: Which modules depend on which — identify the core vs. peripheral modules.

### code-style
Investigate by reading actual source files, then document:
- **Naming conventions**: How variables, functions, classes, files, and directories are named. Show real examples from the codebase (2-3 per convention).
- **Module structure**: Import ordering, export patterns, file organization within modules.
- **Error handling**: How errors are raised, caught, and propagated. Exception hierarchy if any.
- **Forbidden patterns**: Anti-patterns explicitly banned by project conventions (check CLAUDE.md).
- **Tooling**: Linter, formatter, type checker — what's enforced and what's advisory.

### test-style
Investigate by reading test files, then document:
- **Framework and runner**: What test framework, how tests are invoked (Makefile targets, CI config).
- **File organization**: Naming conventions (`test_*.py`, `*_test.go`, etc.), directory structure.
- **Fixture patterns**: Setup/teardown, shared fixtures, factories, temp directories. Show real examples.
- **Mocking rules**: What to mock, what NOT to mock, at what layer. Quote project conventions if they exist.
- **Environment requirements**: Env vars needed, database setup, external service stubs.
- **Known anti-patterns**: Bad test approaches this project explicitly avoids (check CLAUDE.md).

### anti-patterns
Investigate CLAUDE.md, code comments, and code review patterns, then document:
- **Explicitly forbidden patterns**: Anything CLAUDE.md or project docs call out as banned/discouraged.
- **Performance anti-patterns**: Specific to this project's tech stack and scale.
- **Security anti-patterns**: Input validation, auth, secrets handling — what to avoid.
- **Architecture anti-patterns**: Coupling, circular imports, god objects found or warned against.
- For each anti-pattern: state the pattern, explain WHY it's forbidden, and show the correct alternative.

### modules
Investigate dependency files and imports, then document:
- **Key third-party libraries**: What's used and why it's preferred over alternatives.
- **Standard library preferences**: Specific stdlib modules used for specific tasks.
- **Banned or deprecated dependencies**: Libraries NOT to use, with reasoning.
- **Internal utilities**: Project utility modules and when to reach for them vs. rolling your own.

---

## Phase 3 — Output Format

For each category, output a documentation block in this **exact** format:

```
---DOC---
category: <category-name>
title: <Human-Readable Title for This Project>
---
<markdown content — use H2 (##) headings to organize sections>
---END DOC---
```

**Example:**

```
---DOC---
category: code-style
title: Code Style Guide
---
## Naming Conventions

Functions use `snake_case`. Classes use `PascalCase`...

## Import Organization

Standard library first, then third-party, then local...
---END DOC---
```

**Rules:**
- Output **one block per category**, in the order listed above.
- Use `##` (H2) headings inside each block — these are merge keys in update mode.
- The `category` field must exactly match one of: `architecture`, `code-style`, `test-style`, `anti-patterns`, `modules`.
- Do NOT output anything outside of `---DOC---` / `---END DOC---` blocks except brief status notes.
- Do NOT wrap the blocks in markdown code fences — output them as raw text.

---

## Mode Rules

- **create**: Skip any category where existing docs show "already exists". Output nothing for that category.
- **update**: Output all requested categories. Existing content will be merged at the H2 section level — new sections are appended, existing sections are replaced. Produce complete sections, not diffs.
- **replace**: Output all requested categories regardless of existing content.

---

## Quality Checklist (verify before outputting each block)

- [ ] Every file path, class name, and function name I reference actually exists in the codebase
- [ ] I included 2-3 real code examples (not hypothetical) for style-related categories
- [ ] I explained WHY for each convention, not just WHAT
- [ ] Each category is 30-80 lines — dense and useful, not padded
- [ ] No generic advice that could apply to any project — everything is specific to {PROJECT_NAME}

---

## Boundaries

- **Read-only.** Do not modify any source files. Only produce documentation output blocks.
- **Evidence-based.** Every claim must come from something you read in the codebase. Do not guess or assume patterns you haven't verified.
- **No duplication.** If CLAUDE.md already documents something thoroughly, reference it rather than restating it. Focus on what CLAUDE.md doesn't cover or covers only briefly.
