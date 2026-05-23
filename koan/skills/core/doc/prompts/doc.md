You are extracting structured documentation from the **{PROJECT_NAME}** project codebase. Your goal is to produce documentation that would let a new contributor understand architecture, conventions, and pitfalls within 30 minutes of reading — grounded in evidence from the actual code, not generic advice.

## Parameters

- **Categories requested**: {CATEGORIES}
- **Write mode**: {MODE}
- **Existing docs state**:
{EXISTING_DOCS}

---

## Phase 1 — Deep Investigation (do this FIRST, before writing anything)

Spend at least half your effort here. The quality of your documentation depends entirely on how well you understand the codebase before writing.

1. **Read CLAUDE.md** (if it exists) — this is the authoritative source for conventions, architecture, and anti-patterns. Extract every convention, not just the obvious ones. Pay special attention to sections about testing, linting, and forbidden patterns.
2. **Read README.md** and any existing `docs/*.md` files — understand what documentation already exists and at what quality level.
3. **Detect tech stack** — read `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `Makefile`, or equivalent. This determines which language-specific patterns to look for.
4. **Explore directory structure** — use Glob to map the project layout:
   - `Glob("**/*.py")` or equivalent for the detected language
   - Identify source directories, test directories, config files, entry points
   - Note anything unusual about the structure (monorepo? multi-package? non-standard layout?)
5. **Read 5-8 representative source files** — pick files from different layers (core logic, API/CLI, utilities, data models). Actually read them, don't just list them. For each file, note:
   - Import patterns and ordering
   - Error handling style
   - Naming conventions in practice (not just what docs say)
   - Inline comments — do they explain WHY or just WHAT?
6. **Read 3-4 test files** — understand how tests are actually written:
   - What gets mocked and at what layer?
   - How are fixtures structured?
   - What assertion style is used?
   - Are there any test utilities or helpers?
7. **Check linter/formatter config** — read ruff/eslint/prettier/black/clippy config. These reveal enforced vs. advisory conventions.
8. **Read recent git history** — run `git log --oneline -20` to understand:
   - Commit message conventions (prefixes, scopes, format)
   - What areas are actively changing
   - Whether there's a pattern in how changes are structured

**Do NOT skip or rush this phase.** If you only glob directories and skim headings, your documentation will be generic and useless. The difference between good and bad documentation is whether you actually read the code.

---

## Phase 2 — Extract Documentation

For each requested category, write documentation grounded in specific evidence from Phase 1. Every claim must cite a real file, function, or pattern you observed.

### architecture
Investigate and document:
- **Module map**: What lives where — key directories, their purpose, entry points. Include actual paths (e.g., `src/app/routes.py`, not "the routes module").
- **Data flow**: How information moves between components. Trace at least one complete request/command through the system end-to-end, naming the actual functions involved.
- **Process boundaries**: Separate processes, threads, IPC mechanisms, shared state. How do they coordinate?
- **Key abstractions**: Core classes, interfaces, design patterns. Name the actual classes/functions and explain what problem each abstraction solves.
- **Dependency graph**: Which modules depend on which — identify the core (imported by many) vs. peripheral (imports many) modules.

### code-style
Investigate by reading actual source files, then document:
- **Naming conventions**: How variables, functions, classes, files, and directories are named. Show 2-3 real examples from the codebase for each convention, citing `file:line`.
- **Module structure**: Import ordering, export patterns, file organization within modules. Show a representative example.
- **Error handling**: How errors are raised, caught, and propagated. Exception hierarchy if any. Cite specific examples.
- **Forbidden patterns**: Anti-patterns explicitly banned by project conventions (check CLAUDE.md). Include the exact rule and the reason.
- **Tooling**: Linter, formatter, type checker — what's enforced and what's advisory. Include the config location.

### test-style
Investigate by reading test files, then document:
- **Framework and runner**: What test framework, how tests are invoked (Makefile targets, CI config). Include the exact command.
- **File organization**: Naming conventions (`test_*.py`, `*_test.go`, etc.), directory structure, how test files map to source files.
- **Fixture patterns**: Setup/teardown, shared fixtures, factories, temp directories. Show a real fixture example from the codebase.
- **Mocking rules**: What to mock, what NOT to mock, at what layer. Quote project conventions verbatim if they exist.
- **Environment requirements**: Env vars needed, database setup, external service stubs. Be specific — what fails if you forget these?
- **Known anti-patterns**: Bad test approaches this project explicitly avoids. Include the reason each is forbidden.

### anti-patterns
Investigate CLAUDE.md, code comments, and actual code patterns, then document:
- **Explicitly forbidden patterns**: Anything CLAUDE.md or project docs call out as banned/discouraged. Quote the rule, explain WHY, show the correct alternative.
- **Performance anti-patterns**: Specific to this project's tech stack and scale. Only include patterns you found evidence of in the codebase or docs — not generic advice.
- **Security anti-patterns**: Input validation, auth, secrets handling — what this project specifically avoids. Cite the relevant code patterns.
- **Architecture anti-patterns**: Coupling, circular imports, god objects found or warned against. Show what the wrong approach looks like and what the right one looks like.
- For each anti-pattern: **pattern** → **why it's forbidden** → **correct alternative**. All three parts are required.

### modules
Investigate dependency files and imports, then document:
- **Key third-party libraries**: What's used and why it's preferred over alternatives. Cite the dependency file and version.
- **Standard library preferences**: Specific stdlib modules used for specific tasks (e.g., "use `pathlib` not `os.path`").
- **Banned or deprecated dependencies**: Libraries NOT to use, with reasoning.
- **Internal utilities**: Project utility modules and when to reach for them vs. rolling your own. Name the actual module and its key functions.

### Cross-category guidance

Avoid restating the same information across categories. If CLAUDE.md documents a pattern thoroughly, reference it (`See CLAUDE.md § "Test suite"`) rather than copying. Each category should add value beyond what the others cover.

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

- [ ] Every file path, class name, and function name I reference actually exists in the codebase — I verified by reading the file
- [ ] I included 2-3 real code examples (not hypothetical) for style-related categories, with `file:line` citations
- [ ] I explained WHY for each convention, not just WHAT
- [ ] Each category is 30-80 lines — dense and useful, not padded with generic advice
- [ ] No generic advice that could apply to any project — everything is specific to {PROJECT_NAME}
- [ ] I did not restate information already in CLAUDE.md — I referenced it instead

---

## Common Failures (avoid these)

- **Directory-listing documentation**: Just describing what files exist without explaining how they relate or why they're structured that way. This is useless — anyone can run `ls`.
- **Generic language advice**: "Use meaningful variable names" or "Write unit tests" applies to every project. Only document conventions specific to this codebase.
- **Undocumented claims**: Stating "the project uses X pattern" without citing the file where you observed it. Every claim needs evidence.
- **Copy-pasting CLAUDE.md**: If CLAUDE.md already says it, reference it. Your job is to add what CLAUDE.md doesn't cover.

---

## Boundaries

- **Read-only.** Do not modify any source files. Only produce documentation output blocks.
- **Evidence-based.** Every claim must come from something you read in the codebase. Do not guess or assume patterns you haven't verified. If you're unsure, read more code before writing.
- **No duplication.** If CLAUDE.md already documents something thoroughly, reference it rather than restating it. Focus on what CLAUDE.md doesn't cover or covers only briefly.
- **Be specific.** Always include exact file paths when referencing code. "The utils module" is insufficient — write `app/utils.py`.
