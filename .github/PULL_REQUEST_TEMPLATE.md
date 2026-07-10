<!--
  Thanks for contributing to Kōan. Keep this description accurate — the
  spec-change guard (.github/workflows/spec-change-guard.yml) reads the
  "Architectural change" declaration below.
-->

## Summary

<!-- What does this PR do, and why? -->

## Testing

<!-- How was this verified? (make lint, make test, manual steps…) -->

## Declarations

- [ ] **Architectural change** — this PR modifies a durable design contract
  (`specs/components/**` or `specs/skills/**`). The new architecture needs review
  before approval. Rationale: <one line>

<!--
  CHECK the box above ONLY if this PR adds or changes a durable design contract
  under specs/components/ or specs/skills/. Doing so is an ARCHITECTURAL CHANGE:
  it must be deliberate and contract-first (change the intended contract, then make
  code conform — never edit the spec afterward to match sloppy code), and such
  changes should be rare. See docs/design/spec-changes-are-architectural.md and
  .specify/memory/constitution.md (Principle II).

  Editing an ephemeral speckit folder (specs/<NNN-slug>/…), an index.md, or the
  skill-spec template is NOT an architectural change — leave the box unchecked.
-->
