# Phase 1 Data Model: Spec Changes Are Architectural Changes

This feature has no persistent storage. The "entities" are the in-memory values the
guard reasons over.

## ChangedFile (input)

A repo-relative path plus its diff status, emitted by `git diff --name-status
--diff-filter=AMD --no-renames <base>...HEAD`. `AMD` covers **A**dds, **M**odifications,
and **D**eletions (retiring a contract is destructive too — a bare `git rm` must not
bypass the gate); `--no-renames` splits a rename into delete-old + add-new so both sides
are evaluated.

- Fields: `path: str` (POSIX-style, repo-relative), `status: str` (`A`/`M`/`D`).
- Derived: `is_durable_contract(path) -> bool`.

## DurableContract (derived)

A `ChangedFile` whose path is a durable design contract.

- Predicate: path matches `specs/components/*.md` **or** `specs/skills/**.md`, AND
  `basename != "index.md"`, AND path `!= "specs/skills/SKILL_SPEC_TEMPLATE.md"`.
- Excluded by construction: `specs/<NNN-slug>/**` (numbered speckit folders — neither
  `components/` nor `skills/`), `specs/README.md`, `specs/SCHEMA.md`, `specs/index.md`.

## ContractChange (derived)

A classified durable-contract change. **The classification is the core of the gate:**
growing the specs is encouraged and passes freely; only removing or rewriting reviewed
design needs a human declaration.

- Fields: `path: str`, `kind: str` (`added`/`deleted`/`rewritten`, human-readable),
  `destructive: bool` (the gate signal).
- Classification (`_classify(path, status, old_ref)`):
  - `A` (new file) → `added`, `destructive=False`.
  - `D` (deletion) → `deleted`, `destructive=True`.
  - `M` (modification) → compare merge-base body to HEAD body:
    - frontmatter-only diff → **dropped** (bookkeeping, exempt; see below).
    - `is_addition_only()` true (every diff hunk is `equal`/`insert`) → `added`,
      `destructive=False`.
    - otherwise (existing body lines removed/altered) → `rewritten`, `destructive=True`.
- `is_addition_only(old, new)` compares the Markdown bodies below frontmatter with
  `difflib.SequenceMatcher`; true iff no `delete`/`replace` opcode appears.
- Frontmatter-only diffs are bookkeeping (a `/brain sync` or wiki-sync `updated:` bump),
  exempt per the constitution's wiki-bookkeeping exemption (Principle I).

## ArchitectureDeclaration (input)

The PR body text, parsed for a declaration marker.

- Field: `pr_body: str | None` (None = not supplied).
- Predicate `has_architecture_declaration(pr_body) -> bool`: true iff `pr_body` contains
  a line matching `^\s*[-*]\s*\[x\]\s*.*architectural change` under `re.IGNORECASE |
  re.MULTILINE`. `None`, empty, unchecked `[ ]`, or missing phrase → false.

## GuardVerdict (output)

- `ok: bool` — overall pass/fail.
- `undeclared_contracts: list[str]` — **destructive** contracts changed with no valid
  declaration (empty when `ok`).
- `allowed_additions: list[ContractChange]` — additive contract changes (informational;
  never block).
- `flagged: list[ContractChange]` — the destructive changes considered (for output).
- `fail_closed: bool` — true when a destructive change was seen but no PR body was
  supplied.
- Exit code mapping: `ok -> 0`; `not ok -> 1`; usage error -> `2`.

## State transitions

Stateless. One evaluation per invocation:

```
changed  = classify(diff(base))                 # list[ContractChange]
additive = [c for c in changed if not c.destructive]
destruct = [c for c in changed if c.destructive]
if not destruct:            -> verdict(ok=True, allowed_additions=additive)
elif pr_body is None:       -> verdict(ok=False, fail_closed=True, undeclared=destruct)
elif has_declaration(body): -> verdict(ok=True)
else:                       -> verdict(ok=False, undeclared=destruct)
```

A pure `evaluate()` also accepts bare path strings (the `--changed-file` override and
unit tests); a bare string carries no diff information, so it is treated conservatively
as a `destructive` rewrite.
