# Skills

* [Skill Spec — ask](ask.md) - Specifies the `/ask` skill, which answers a question about a GitHub PR/issue by fetching context and posting an AI-generated reply as a read-only, non-mutating worker.
* [Skill Spec — brainstorm](brainstorm.md) - Specifies the `/brainstorm` skill, which decomposes a topic into structured, linked sub-issues (GitHub or Jira) under a master tracking issue and is covered by the skill-eval harness.
* [Skill Spec — ci_check](ci_check.md) - Specifies the `/ci_check` skill, which checks a PR's CI status, runs the shared CI-fix loop on failures, and toggles automatic CI-fix dispatch.
* [Skill Spec — fix](fix.md) - Specifies the `/fix` skill, which fixes a tracker issue end-to-end (or batch-queues fixes for a repo) and redirects PR URLs to `/rebase --fix`, with eval coverage on its diagnostic output.
* [Skill Spec — implement](implement.md) - Specifies the `/implement` skill, which queues an end-to-end implementation mission for a tracker issue that results in a draft PR, and is eval-exempt as pure orchestration.
* [Skill Spec — mission](mission.md) - Specifies the `/mission` skill, the base primitive that queues a free-form mission to `missions.md` for later agent-loop execution, also eval-exempt as a non-LLM queue utility.
* [Skill Spec — orphans](orphans.md) - Documents the `/orphans` skill that rebases and opens draft PRs for unmerged, PR-less branches, with commit-derived (non-LLM) PR titles/descriptions and per-branch error isolation.
* [Skill Spec — plan](plan.md) - Documents the `/plan` skill that deep-thinks an idea (or iterates an existing issue) into a structured tracker-issue plan via a critic→regenerate loop, covered by the deterministic eval harness.
* [Skill Spec — rebase](rebase.md) - Documents the `/rebase` skill that rebases a PR onto its current base by default and, with `--fix` (or any trailing context), also addresses review feedback, including its already-solved detection JSON scored by the eval harness.
* [Skill Spec — recreate](recreate.md) - Documents the `/recreate` skill that rebuilds a too-far-diverged PR from scratch on current upstream via a fresh branch and reimplementation, rather than rebasing.
* [Skill Spec — review](review.md) - Documents the `/review` skill that queues a code-review mission on PRs/issues, posting findings as a comment with severity-driven LGTM logic and re-review comment handling, covered by the eval harness.
* [Skill Spec — security_audit](security_audit.md) - Documents the `/security_audit` skill that runs a background SDLC security audit of a project and files up to 5 critical-vulnerability tracker issues via the provider-neutral tracker service.
