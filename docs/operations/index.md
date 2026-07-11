# Operations

* [Auto-Update](auto-update.md) - Describes Kōan's opt-in auto-update feature that checks for and pulls upstream commits, plus the always-on release-tag notification.
* [Web Dashboard](dashboard.md) - Documents the local Flask web dashboard's architecture, blueprints, pages, passphrase gate, and design-system integration.
* [Interactive launcher (make koan)](interactive-launcher.md) - Describes `make koan`, the TTY-gated interactive launcher and its textual terminal dashboard (tabs, toggles, keybindings).
* [Maintenance & Release](maint.md) - Covers Kōan's release process and branch philosophy (`main` vs `stable`), the `make release` procedure, versioning scheme, and recovery steps.
* [Memory watchdog (#2232)](memory-watchdog.md) - Explains the memory watchdog that restarts the agent loop between missions when RSS stays over a threshold, its config knobs, and health-endpoint observability.
* [PR Activity Reports](pr-reports.md) - Documents the `/report` skill that posts per-project and global GitHub PR activity digests (created/merged/interacted metrics) over weekly/monthly windows.
* [REST API](rest-api.md) - Documents Kōan's optional, token-authenticated HTTP control layer (missions, projects, pause/resume, config, admin, usage/metrics/logs endpoints), its generated OpenAPI spec + drift guard, and its security model.
* [RTK integration](rtk.md) - Explains the optional rtk CLI-proxy integration (detection, awareness injection, hook setup) that compresses dev-command output for token savings.
* [Skill evaluation (eval) harness](skill-evals.md) - Describes the deterministic eval harness that scores LLM-driven skills (review, fix, plan, brainstorm, rebase) against golden datasets in offline (CI) and live modes.
* [Troubleshooting](troubleshooting.md) - Catalogs common operational issues (agent loop, git/worktrees, memory, bridge, GitHub, CLI provider, parallel sessions, config) and their fixes.
