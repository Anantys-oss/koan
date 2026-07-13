# Architecture

* [Daemon Runtime](daemon.md) - Describes how the Koan daemon is assembled: startup/process management, the bridge's chat/bg worker lanes, the agent loop's modular pieces, runtime modes, parallel sessions, and the bounded-memory model for CLI stdout capture.
* [GitHub And Trackers](github-and-trackers.md) - Covers GitHub/Jira notification flow, PR workflows (footer, receiving-code-review protocol), review issue-tracker enrichment, and the instance/ tracker files used to dedupe work.
* [Lifecycle Hooks & Automation Rules](hooks.md) - Documents the lifecycle-event system (session_start/session_end/pre_mission/post_mission): instance-wide and skill-bound Python hooks via `HookRegistry`, plus the declarative automation-rules layer (notify/create_mission/pause/resume/auto_merge) with its per-rule loop guard.
* [Memory Architecture](memory.md) - Details Koan's Markdown+JSONL memory store, the SQLite FTS5 secondary index (confidence-weighted BM25 ranking, dual-write, fallback), entry schema, read/write paths, and compaction.
* [Mission Lifecycle](mission-lifecycle.md) - Explains the mission queue format and lifecycle (Pending/In Progress/Done/Failed), org-wide missions, branch prep, direct skill dispatch, scheduling, recovery/retries, and missions.md integrity/size-bound safeguards.
* [Architecture Overview](overview.md) - High-level architecture summary of Koan's two main processes (bridge and agent loop), major subsystems, and the human-decides safety model.
* [Provider Architecture](providers.md) - Documents the CLI provider abstraction layer, provider responsibilities, resolution flow, and the current supported providers (Claude, Cline, Codex, Copilot, Local).
* [Shared State](shared-state.md) - Explains Koan's shared state under instance/ — mostly local files (locking/atomic-write conventions, per-uid temp/scratch dirs, config sources), with mission state in an authoritative SQLite store (missions.db) exported to missions.md.
* [Skills System](skills-system.md) - Describes the skill definition format, dispatch paths, the private implementation review gate (challenge loop, cost controls, dedup), and the documentation contract for skill changes.
