# Documentation

This directory is the user-facing manual and the implementation reference for
Koan. User docs explain how to operate Koan. Architecture and design docs
capture the current system shape so humans and LLM agents can plan changes from
the same baseline.

When code and docs disagree, treat code as the immediate source of truth, then
update the relevant docs in the same change.

## Start Here

- [User Manual](users/user-manual.md) - daily use, workflows, and command guide.
- [Onboarding](users/onboarding.md) - first-run setup and configuration flow.
- [Skills Reference](users/skills.md) - built-in command reference.
- [Provider Setup](providers/) - Claude, Codex, Copilot, and local providers.
- [Messaging Setup](messaging/) - Telegram, Slack, Matrix, Discord, GitHub, and Jira.

## Implementation Reference

Read these before planning or implementing daemon, lifecycle, provider, skill,
memory, or integration changes:

- [Architecture Overview](architecture/overview.md)
- [Daemon Runtime](architecture/daemon.md)
- [Mission Lifecycle](architecture/mission-lifecycle.md)
- [Shared State](architecture/shared-state.md)
- [Provider Architecture](architecture/providers.md)
- [Skills System](architecture/skills-system.md)
- [Memory Architecture](architecture/memory.md)
- [GitHub And Trackers](architecture/github-and-trackers.md)
- [Design Decisions](design/decisions.md)

## Directory Map

- `users/` - user manual, onboarding, and command references.
- `setup/` - installation and host runtime setup.
- `providers/` - CLI and local model provider setup and behavior.
- `messaging/` - messaging and issue-tracker integration setup.
- `operations/` - maintenance, self-update, and optional operational tools.
- `architecture/` - current daemon design and implementation references.
- `security/` - security review docs and threat models.
- `design/` - durable decisions, design notes, and larger specs.

## Maintenance Rule

Update docs when a change affects user behavior, configuration, command
semantics, daemon flow, provider behavior, shared state, safety boundaries, or an
important implementation decision. Prefer updating an existing page over adding a
new page unless the topic is a new subsystem.
