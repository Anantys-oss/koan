# Architecture Overview

Koan is a local autonomous coding daemon. It keeps user intent in Markdown files,
uses configured CLI providers to work on local projects, and reports progress
through messaging bridges. The operating principle is: the agent proposes, the
human decides.

## Main Processes

- `koan/app/awake.py` runs the messaging bridge. It polls the configured
  messaging provider, classifies incoming messages as chat or missions, queues
  mission work, and flushes `instance/outbox.md` back to the user.
- `koan/app/run.py` runs the agent loop. It refreshes usage, chooses pending
  work, resolves the project, executes the mission through a provider or direct
  skill runner, writes status, and records outcomes.

The two processes do not call each other directly. They coordinate through files
under `instance/`, guarded by file locks and atomic writes.

## Major Subsystems

- Mission queue and lifecycle: `missions.py`, `iteration_manager.py`,
  `mission_runner.py`, `recover.py`, and scheduling modules.
- Runtime management: `pid_manager.py`, `pause_manager.py`,
  `restart_manager.py`, `focus_manager.py`, `passive_manager.py`, and
  `stagnation_monitor.py`.
- Provider abstraction: `koan/app/provider/` with provider-specific command
  building and streaming behavior.
- Skills: `koan/app/skills.py`, `skill_dispatch.py`, `external_skill_dispatch.py`,
  and `koan/skills/`.
- Memory and journals: `memory_manager.py`, `skill_memory.py`,
  `post_mission_reflection.py`, and daily journal helpers.
- GitHub and trackers: GitHub notification handling, issue tracker routing,
  PR workflows, CI dispatch, review-comment dispatch, and branch sync tracking.

## Safety Model

Koan works in project branches, normally using the configured branch prefix such
as `koan/`. It does not commit directly to `main`, and shipping work remains a
human decision unless an explicit project configuration enables a narrower
automation such as auto-merge. Keep new features aligned with this boundary.

See [Design Decisions](../design/decisions.md) for durable design rules.
