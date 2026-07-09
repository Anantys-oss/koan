# Components

* [Component Spec — Agent Loop Pipeline](agent-loop.md) - Design contract for the core mission pipeline (iteration manager, mission executor/runner, quota handling, stagnation monitor) that pulls missions, invokes the CLI provider, and finalizes lifecycle state.
* [Component Spec — Telegram Bridge](bridge.md) - Design contract for the Telegram bridge process that classifies human messages into chat vs. mission, dispatches commands/skills, and flushes the agent's outbox crash-safely.
* [Component Spec — Core Data & Config](core.md) - Design contract for the foundation layer (mission queue contract, config resolution, atomic-write/lock primitives) that every other Kōan component depends on.
* [Component Spec — Git & GitHub](git-github.md) - Design contract for everything touching git history or the GitHub API: branch/PR creation, sync, webhook/notification handling, and rebase/recreate/CI-fix workflows.
* [Component Spec — Issue Tracking](issue-tracking.md) - Design contract for the provider-neutral issue-tracker abstraction (GitHub/Jira) that routes fetch/comment/create calls through one service layer.
* [Component Spec — CLI Provider Abstraction](providers.md) - Design contract for the CLI provider abstraction that decouples the agent loop from any single AI coding CLI (Claude, Cline, Codex, Copilot) behind one `CLIProvider` contract.
* [Component Spec — Skills System](skills.md) - Documents the skills system that discovers, routes, and executes `/command` skills (SKILL.md contract, dispatch, the new-skill checklist, and the eval harness).
* [Component Spec — Web Dashboard & REST API](web.md) - Documents the Flask dashboard and token-gated REST API, their shared `dashboard_service`/`usage_service`/`log_reader` logic, and the invariants keeping the two surfaces from drifting.
