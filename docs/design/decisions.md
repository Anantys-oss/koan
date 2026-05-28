# Design Decisions

This page records durable Koan design decisions. Update it when a change alters
the system philosophy, daemon boundaries, safety model, or documentation rules.

## Human Authority

The core rule is: the agent proposes, the human decides. Koan may plan, inspect,
branch, commit, open draft PRs, and report findings within configured bounds. It
must not introduce broad unsupervised modification, deployment, or direct-main
behavior unless that behavior is explicitly requested and documented.

## Local Files Over Database

Koan uses Markdown, YAML, JSON, and small tracker files under `instance/` instead
of a database. This keeps runtime state auditable, easy to back up, and easy for
LLMs to inspect. New state should follow existing locking and atomic-write
patterns.

## Branch Isolation

Project work happens on branch-prefixed branches, defaulting to `koan/`. The
default workflow is draft PR creation and human review. Configurable automation
such as auto-merge must remain narrow, visible, and protected by existing review
and safety gates.

## Provider Isolation

Provider-specific behavior belongs in `koan/app/provider/` or provider-facing
configuration helpers. Mission, skill, and daemon orchestration code should not
grow provider-specific branches when a provider abstraction can carry the
difference.

## Prompt Files

LLM prompts live in Markdown files, not inline Python strings. Reusable prompt
fragments belong under `koan/system-prompts/_partials/` and should be loaded
through prompt helpers.

## Public Artifacts Stay Generic

Public code, docs, examples, tests, and commit messages must not include private
operator identifiers from `instance/`. Use placeholders such as `my_toolkit`,
`my_team`, `my_fix`, `@koan-bot`, and `PROJ-NNN`.

## Documentation First

Before planning or implementing, agents should inspect relevant documentation
with search tools and then verify behavior against code. After changing user
behavior, configuration, daemon flow, provider behavior, shared state, or an
important implementation decision, update the relevant docs in the same branch.
