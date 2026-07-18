---
type: overview
title: "Documentation"
description: "Top-level router explaining the docs/ tree's purpose, its relationship to specs/ design contracts, and pointers to user, architecture, and directory-map content."
tags: [architecture]
created: 2026-05-28
updated: 2026-07-08
---

# Documentation

This directory is the user-facing manual and the implementation reference for
Koan. User docs explain how to operate Koan. Architecture and design docs
capture the current system shape so humans and LLM agents can plan changes from
the same baseline.

When code and docs disagree, treat code as the immediate source of truth, then
update the relevant docs in the same change.

For **design contracts** (why a component exists, the invariants it upholds, what
breaks if you change it), see [`specs/`](../specs/README.md) — the single source of
truth for design. Specs drive implementation and refactoring; these docs explain how
to operate Koan. Most non-trivial changes update both.

This directory is an independent OKF v0.1 knowledge bundle — see [`SPEC.md`](SPEC.md)
for the normative format spec (shared with `specs/`) and [`SCHEMA.md`](SCHEMA.md) for
the conventions specific to this bundle (page types, tag taxonomy, frontmatter). It is
also indexed, together with the durable half of `specs/`, as an LLM Wiki — see
[`wiki/index.md`](../wiki/index.md) for a flat, one-line-per-page catalog and
[`wiki/SCHEMA.md`](../wiki/SCHEMA.md) for the plugin-level conventions. The **`/brain`
skill** is the preferred entrypoint for consulting or extending either bundle — see
`.claude/skills/brain/SKILL.md`. Use `wiki/index.md` (or `/brain ask`) to find candidate
pages quickly; this file remains the hand-curated entry point for a first read.

## Start Here

- [Quickstart](users/quickstart.md) - the 5-minute zero-to-hero guide: what to type from GitHub, Jira, and your messaging app.
- [User Manual](users/user-manual.md) - daily use, workflows, and command guide.
- [Onboarding](users/onboarding.md) - first-run setup and configuration flow.
- [Skills Reference](users/skills.md) - built-in command reference.
- [Provider Setup](providers/) - Claude, Cline, Codex, Copilot, and Ollama Launch (local models) providers; plus [OpenRouter via the Claude CLI](providers/openrouter.md), [OpenCode Go via the Claude CLI](providers/opencode.md), [Local Ollama via the Claude CLI](providers/ollama-wrapper.md), and [Z.ai (GLM) via the Claude CLI](providers/zai.md).
- [Messaging Setup](messaging/) - Telegram, Slack, Matrix, Discord, GitHub, and Jira.
- [Troubleshooting](operations/troubleshooting.md) - common issues and how to fix them.

## Implementation Reference

Read these before planning or implementing daemon, lifecycle, provider, skill,
memory, or integration changes:

- [Architecture Overview](architecture/overview.md)
- [Daemon Runtime](architecture/daemon.md)
- [Mission Lifecycle](architecture/mission-lifecycle.md)
- [Shared State](architecture/shared-state.md)
- [Lifecycle Hooks & Automation Rules](architecture/hooks.md)
- [Provider Architecture](architecture/providers.md)
- [Skills System](architecture/skills-system.md)
- [Memory Architecture](architecture/memory.md)
- [Artifact DB Harness](architecture/artifact-db.md)
- [GitHub And Trackers](architecture/github-and-trackers.md)
- [GitHub Webhooks](messaging/github-webhooks.md)
- [PR Activity Reports](operations/pr-reports.md)
- [Memory Watchdog](operations/memory-watchdog.md)
- [Skill Evaluation Harness](operations/skill-evals.md) — golden-dataset evals for LLM skills (regression detection + improvement measurement)
- [Messaging Level (bridge verbosity)](messaging/messaging-level.md)
- [Design Decisions](design/decisions.md)

## Directory Map

- `users/` - user manual, onboarding, and command references.
- `setup/` - installation and host runtime setup (see [Deploy on Railway](setup/railway.md), [systemd `--user` service](setup/systemd-user.md), [launchd service](setup/launchd.md)).
- `providers/` - CLI and local model provider setup and behavior.
- `messaging/` - messaging and issue-tracker integration setup.
- `operations/` - maintenance, troubleshooting, self-update, and optional operational tools (dashboard, REST API, auto-update, RTK).
- `architecture/` - current daemon design and implementation references.
- `security/` - security review docs and threat models.
- `design/` - durable decisions, design notes, and larger specs.

## Maintenance Rule

Update docs when a change affects user behavior, configuration, command
semantics, daemon flow, provider behavior, shared state, safety boundaries, or an
important implementation decision. Prefer updating an existing page over adding a
new page unless the topic is a new subsystem.
