---
type: doc
title: "Spec Changes Are Architectural Changes"
description: "Why durable design-contract specs (specs/components/**, specs/skills/**) are changed contract-first, kept rare, and declared in the PR for review before approval — and how the spec-change guard enforces it."
tags: [design]
created: 2026-07-09
updated: 2026-07-09
---

# Spec Changes Are Architectural Changes

## Context

Kōan adopted a "specs discipline": `specs/` is the single source of truth for design,
read before implementing and updated after. The original wording (Constitution
Principle II, `CLAUDE.md`, `specs/README.md`) told contributors to, *"after
implementing, update the spec in the same branch to reflect the new design."*

A reviewer flagged the problem on
[PR #2052](https://github.com/Anantys-oss/koan/pull/2052#issuecomment-4826440629):

> changes to the spec should come first on their own as approved architecture changes,
> and the code changes to implement the spec after. Otherwise AI agents will just
> modify the specs to match whatever sloppy code they wrote and the problem isn't
> solved. At a minimum … changes to the spec should be rare, and require an explicit
> notification in the PR that "this is an architectural change — the new architecture
> needs to be reviewed before approval."

For an autonomous agent this is decisive. "Update the spec to match what you built"
inverts the direction of authority: the spec is supposed to constrain the code, but the
instruction lets the agent rewrite the contract to rubber-stamp whatever it produced.
The source of truth becomes a mirror of the implementation — the exact failure the
discipline was meant to prevent.

## Decision

A **destructive** change to a **durable design contract** — deleting a contract, or
rewriting/removing existing body lines — is an **architectural change**, governed by
three rules:

1. **Contract-first.** Change the spec to express the *intended* design, then make the
   code conform. Never edit a durable spec afterward to match code already written.
2. **Rare.** Most PRs change zero durable contracts destructively. Rewriting or retiring
   an existing contract is the exception. (Authoring a *first* spec for an un-specced
   component/skill, or growing an existing one, is expected — see "Additive vs
   destructive" below.)
3. **Declared.** The PR must carry an explicit architectural-change declaration — a
   checked "Architectural change" checkbox in the PR body — so a human reviews the new
   architecture before approval. Landing the contract change spec-first, in its own PR
   ahead of the implementing code, is recommended.

### Additive vs destructive

The gate is **additive-friendly**. Growing the specs never invalidates prior design, so
purely additive changes pass freely, with no declaration:

- **Additive (allowed, no declaration):** a brand-new contract file, or new
  lines/paragraphs appended or inserted into an existing one (every diff hunk is an
  insertion — no existing body line is removed or altered).
- **Destructive (needs a declaration):** deleting a contract file, or a diff that removes
  or rewrites existing body lines. This is the only case that can silently *contradict* a
  previously reviewed contract, which is what the human declaration protects against.

The distinction is computed line-by-line on the Markdown body below the frontmatter
(`scripts/spec_change_guard.py::is_addition_only`). This means the common case the
discipline was meant to encourage — writing more specs — is never blocked; only reversals
of reviewed design are.

### What counts as a durable contract

The durable contracts are `specs/components/**.md` and `specs/skills/**.md`, **excluding**
`index.md` bookkeeping and `specs/skills/SKILL_SPEC_TEMPLATE.md`. The ephemeral speckit
planning folders `specs/<NNN-slug>/` are **not** durable contracts — they are the
spec-first *proposal* artifact and are meant to change in-branch before code (see
`specs/README.md`, "two different things named `specs/`"). `docs/` is likewise out of
scope: docs follow behaviour and are updated in the same branch.

**Frontmatter-only changes to a contract are not a contract change.** The durable
*contract* is the Markdown body below the YAML frontmatter; a diff that touches only the
frontmatter (e.g. a `/brain sync` or wiki-sync `updated:` date bump) is bookkeeping,
explicitly exempt from a separate human-reviewed step per the constitution's
wiki-bookkeeping exemption (Principle I) and `CLAUDE.md`. The guard therefore drops
frontmatter-only diffs before deciding, so the wiki-sync backstop's own frontmatter
auto-commit to `specs/components/`/`specs/skills/` (`.github/workflows/wiki-sync.yml`)
can't fire a `synchronize` event that spuriously blocks the PR for a date bump.

## Enforcement

Prose alone is advisory, and an autonomous agent routes around advice (Constitution
Principle V: only code- or git-enforced controls are load-bearing). So the rule is
backed by a git-level gate:

- `scripts/spec_change_guard.py` — classifies each durable-contract change in a PR's diff
  as additive or destructive, and fails only when a *destructive* change lacks a
  declaration in the PR body. Pure, unit-tested detection/classification/decision
  functions plus a CLI with a `0/1/2` exit-code contract and a rich, unicode-framed log
  block; it fails *closed* when no PR body is available.
- `.github/workflows/spec-change-guard.yml` — runs the guard as a **blocking**,
  read-only `pull_request` check on `main`.
- `.github/PULL_REQUEST_TEMPLATE.md` — surfaces the declaration checkbox at author time
  so it is cheap to satisfy and never a surprise CI failure.

## Alternatives considered

- **Prose-only instruction change** — rejected: it is the status quo that produced the
  concern; advisory text does not bind an agent.
- **Mandatory separate spec-first PR for every contract touch** — rejected as a hard
  requirement: it cannot be reliably gated at the git level and would materially slow
  legitimate contract evolution. It is *recommended* in prose; the declaration + rarity
  rule achieves the reviewer's stated minimum bar.
- **Self-fixing backstop** (like the wiki-sync job) — rejected: auto-supplying a missing
  declaration would defeat the point. The human acknowledgement *is* the control, so the
  guard must block, not repair.
- **Commit trailer or PR label instead of a body checkbox** — rejected: less visible to
  reviewers and easier to set reflexively; a checked box in the rendered PR body is the
  most legible, deterministic signal.

## Consequences

- Reviewers get an explicit, up-front signal ("this PR changes the architecture") and
  review the contract deliberately rather than skimming a spec edit as incidental
  cleanup.
- Agents can no longer silently bend a contract to match code; doing so fails CI.
- A small author-time cost (check a box, write one line of rationale) for the rare PR
  that legitimately changes a contract.

## See also

- `.specify/memory/constitution.md` — Principle II (amended, v3.0.0).
- `specs/README.md` — "Spec discipline" and the durable-vs-ephemeral distinction.
- `specs/005-spec-change-governance/` — the speckit planning artifacts for this change.
