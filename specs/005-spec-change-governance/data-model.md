# Phase 1 Data Model: Spec Changes Are Architectural Changes

This feature has no persistent storage. The "entities" are the in-memory values the
guard reasons over.

## ChangedFile (input)

A repo-relative path string emitted by `git diff --name-only --diff-filter=AMD
--no-renames <base>...HEAD`. `AMD` covers **A**dds, **M**odifications, and
**D**eletions (retiring a contract is architectural too — a bare `git rm` must not
bypass the gate); `--no-renames` splits a rename into delete-old + add-new so both
sides are evaluated.

- Fields: `path: str` (POSIX-style, repo-relative).
- Derived: `is_durable_contract(path) -> bool`.

## DurableContract (derived)

A `ChangedFile` whose path is a durable design contract.

- Predicate: path matches `specs/components/*.md` **or** `specs/skills/**.md`, AND
  `basename != "index.md"`, AND path `!= "specs/skills/SKILL_SPEC_TEMPLATE.md"`.
- Excluded by construction: `specs/<NNN-slug>/**` (numbered speckit folders — neither
  `components/` nor `skills/`), `specs/README.md`, `specs/SCHEMA.md`, `specs/index.md`.

## ArchitectureDeclaration (input)

The PR body text, parsed for a declaration marker.

- Field: `pr_body: str | None` (None = not supplied).
- Predicate `has_architecture_declaration(pr_body) -> bool`: true iff `pr_body` contains
  a line matching `^\s*[-*]\s*\[x\]\s*.*architectural change` under `re.IGNORECASE |
  re.MULTILINE`. `None`, empty, unchecked `[ ]`, or missing phrase → false.

## GuardVerdict (output)

- `ok: bool` — overall pass/fail.
- `undeclared_contracts: list[str]` — durable contracts changed with no valid
  declaration (empty when `ok`).
- `fail_closed: bool` — true when contracts changed but no PR body was supplied.
- Exit code mapping: `ok -> 0`; `not ok -> 1`; usage error -> `2`.

## State transitions

Stateless. One evaluation per invocation:

```
changed = diff(base)                      # list[ChangedFile]
contracts = [c for c in changed if is_durable_contract(c)]
if not contracts:            -> verdict(ok=True)
elif pr_body is None:        -> verdict(ok=False, fail_closed=True, undeclared=contracts)
elif has_declaration(body):  -> verdict(ok=True)
else:                        -> verdict(ok=False, undeclared=contracts)
```
