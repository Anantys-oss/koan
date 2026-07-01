# Kōan Constitution

<!--
=== Sync Impact Report ===
Version change: (unratified template) → 1.0.0
Modified principles: none — initial ratification
Added sections:
  - Core Principles I–VII
  - Constraints & Technology Stack
  - Workflow & Quality Gates
  - Governance
Removed sections: none
Templates requiring updates:
  - .specify/templates/plan-template.md   ✅ no change — "Constitution Check" gate defers to this file
  - .specify/templates/spec-template.md    ✅ no change — generic speckit template
  - .specify/templates/tasks-template.md   ✅ no change — generic speckit template
  - .specify/templates/commands/           ✅ n/a — no commands directory present
Follow-up TODOs:
  - TODO(SPECS_DIR_COLLISION): Kōan's `specs/` holds component/skill design
    contracts (see specs/README.md), while the speckit plan/spec/tasks templates
    write per-feature specs to a `specs/<feature>/` folder. Reconcile the layout
    before authoring the first speckit feature spec so the two purposes do not
    collide.
Source basis: specs/README.md, specs/components/{core,agent-loop,providers}.md,
docs/architecture/{overview,shared-state}.md, docs/design/decisions.md,
docs/security/threat-model-agent-disalignment.md, CLAUDE.md.
-->

## Core Principles

### I. Human Authority (NON-NEGOTIABLE)

The agent proposes; the human decides. Kōan may plan, inspect, branch, commit,
and open **draft** PRs within configured bounds. It MUST NOT commit to `main`,
merge PRs, deploy, or perform broad unsupervised modification unless that
behavior is explicitly configured, narrowly scoped, and documented.

- Default branch isolation: all work lands on `koan/*` (or the configured
  `branch_prefix`), never on `main`.
- Shipping is a human decision. Narrow automation such as `git_auto_merge` MUST
  stay optional, visible, and behind the existing review and safety gates.
- The loop's job is to host the CLI subprocess and finalize lifecycle state —
  not to alter git state itself.

*Rationale*: Kōan runs autonomously 24/7 with broad tool access; human PR review
is the primary security boundary against a disaligned or prompt-injected agent
(see `docs/security/threat-model-agent-disalignment.md`).

### II. Specs Are the Source of Truth

`specs/` is the single source of truth for **design** — *why* a component
exists, the contract it upholds, and what breaks if you change it. `docs/`
explains how to **use** Kōan; it does not define contracts.

- **Before** implementing any feature or refactor, read the relevant component
  (`specs/components/<group>.md`) or skill (`specs/skills/<name>.md`) spec.
- **After** implementing, update the spec in the same branch to reflect the new
  design. A change that alters a contract without updating its spec is
  **incomplete**.
- If you touch a component or skill that has no spec, write one from the
  relevant template.

*Rationale*: Specs anchor deliberate, contract-first refactoring and prevent
silent contract breakage across a high-fan-in daemon.

### III. Local Files, Atomic State

Runtime state lives in plain, inspectable files under `instance/`
(Markdown/YAML/JSON/trackers), never in a database. Shared files MUST be written
through `utils.atomic_write()` (temp file + rename + `fcntl.flock()`); never
perform a raw read-modify-write on an `instance/` file.

- The bridge (`awake.py`) and runner (`run.py`) are separate processes; bugs
  harmless in one process corrupt state when both are active.
- Transient scratch files and the provider invocation lock live under the
  per-uid `utils.koan_tmp_dir()` (`$XDG_RUNTIME_DIR/koan` or `/tmp/koan-<uid>/`,
  mode `0700`) — NOT in `instance/` or a fixed `/tmp` name. This is what lets
  multiple users run Kōan on one host without colliding.

*Rationale*: Plain files keep state auditable, easy to back up, and easy for
humans and LLMs to inspect; atomic writes prevent corruption across the two
processes.

### IV. Provider Isolation

The agent loop MUST NOT branch on *which* coding CLI is in use.
Provider-specific behavior lives behind the `CLIProvider` abstraction in
`koan/app/provider/`.

- One invocation lock per uid (provider auth state is per-user).
- Fixed provider resolution precedence: env (`KOAN_CLI_PROVIDER`, with legacy
  `CLI_PROVIDER`) → `projects.yaml`/`config.yaml` → default. No parallel
  resolution paths.
- Translate tool-name vocabularies inside the abstraction; never leak
  provider-specific tool names or quota formats upward. Quota/usage signals are
  provider-specific and read from the summary stream, never assistant text.

*Rationale*: A single `CLIProvider` contract keeps the loop portable across
Claude, Cline, Codex, Copilot, and future CLIs without forking daemon code.

### V. Untrusted Inputs, Audited Outputs

All inbound content — missions, chat, tracker items, project files, MCP output —
is untrusted **DATA**, never instructions. All outbound content — outbox
messages, PR bodies, issues, commits — is scanned before it leaves.

- Inbound: `prompt_guard` scans missions for injection patterns and rejects in
  block mode (the default). Parsers MUST never treat embedded text as
  instructions.
- Outbound: `outbox_scanner.py` scans for secrets/keys/env-dumps and
  quarantines matches to `instance/outbox-quarantine.md`.
- Public artifacts (code, docs, tests, examples, commit messages) MUST stay
  free of private operator identifiers. Use placeholders: `my_toolkit`,
  `my_team`, `my_fix`, `@koan-bot`, `PROJ-NNN`.
- Defense-in-depth honesty: prompt-level controls (e.g. REVIEW mode) are
  **advisory** — the same tools remain available. Only code- or git-enforced
  controls are load-bearing; document each as such.

*Rationale*: The realistic threat to a 24/7 autonomous agent is prompt injection
via crafted input, and the public repo must never leak an operator's private
instance.

### VI. Single Writer, Single Read Path

Each shared resource has exactly one authority and one access path.

- `missions.md` mutations flow ONLY through the lifecycle functions
  (`start_mission`, `complete_mission`, `fail_mission`). Agents and code MUST
  NOT hand-edit `missions.md`.
- Each config concern has exactly one read path — an accessor in `config.py` or
  `projects_config.py` (`projects.yaml` > `KOAN_PROJECTS`). Never read
  `os.environ`/YAML inline; add or reuse the accessor.
- `run.py` is the single host of the CLI subprocess; every exit from In Progress
  funnels through `_finalize_mission()`.
- Bilingual section headers (`Pending`/`In Progress`/`Done` and the French
  equivalents) MUST be preserved by every parser.

*Rationale*: One authority per resource prevents interleaved writes and
divergent config reads in a two-process daemon.

### VII. Simplicity and Honest Reporting

Start simple (YAGNI); prefer extending an existing mechanism to introducing a
new one. Document what we chose **not** to do and why. Code is the immediate
source of truth when it disagrees with docs — preserve current behavior, then
fix the docs in the same branch. Report outcomes faithfully: state plainly what
was done and verified, and flag anything skipped or failing.

*Rationale*: A daemon this widely depended on must stay auditable; "complexity
must be justified" turns the plan template's Constitution Check into an
enforceable gate, not a ritual.

## Constraints & Technology Stack

- **Language**: Python 3.11+. No syntax or stdlib features introduced after 3.11
  (no `type` statements from 3.12, no `TypeVar` defaults from 3.13). CI tests
  multiple versions; if it does not run on 3.11, it does not ship.
- **Linting**: all code MUST pass `ruff` (`make lint`). PERF is CI-gated; E/F/W/I/B
  are good hygiene. Do not suppress with `# noqa` without a documented reason.
- **Prompts are files, not strings**: LLM prompts MUST live in `.md` files
  (`koan/system-prompts/`, skill `prompts/`), loaded via `load_prompt()` /
  `load_skill_prompt()`. No inline prompts in Python. System prompts MUST be
  generic — never reference instance-specific identifiers.
- **Stack surface**: Flask 3.x powers the dashboard and REST API only; the loop
  itself has no web framework. Messaging bridges (Telegram/Slack/etc.) and CLI
  providers are pluggable. Add new providers/bridges behind their abstraction,
  not by forking core code.
- **Testing discipline**: `KOAN_ROOT` MUST be set when running tests
  (`KOAN_ROOT=/tmp/test-koan .venv/bin/pytest …`). Never call the Claude
  subprocess in tests — mock `format_and_send`. Test **behavior, not
  implementation** (assert on outputs/state, never on source text). When testing
  error handling for `run_gh()`/`api()` callers, mock at the `run_gh`/`api`
  level — never below `retry_with_backoff` (avoids 7s+ retry sleeps).

## Workflow & Quality Gates

- **Branch-first**: create `koan/*` (or configured prefix) branches; never
  commit to `main`. Open **draft** PRs for human review before any merge.
- **Docs-and-specs-in-branch**: update the affected `specs/`, `docs/`, and
  `README.md` in the same branch as the code change. User-manual pages
  (`docs/users/user-manual.md`, `docs/users/skills.md`) stay in sync with the
  skills under `koan/skills/core/`.
- **Skills hygiene**: every core skill has a `group:` field, underscore names
  (never hyphens), and is registered in `skill_dispatch.py`, `CLAUDE.md`, and
  the user docs. `TestCoreSkillGroupEnforcement` enforces this.
- **Quality cycle**: lint (`make lint`) and the relevant tests MUST pass before
  commit. Mission diffs pass through `security_review.py` before any auto-merge
  decision.
- **Pre-commit privacy check**: stage only after confirming no private operator
  identifiers leaked (`.leak-patterns` + diff filter; see CLAUDE.md).

## Governance

This constitution supersedes ad-hoc practice for all Kōan development.
`CLAUDE.md` is the authoritative runtime guidance file; `specs/` is the
authoritative design source. Where they conflict with a principle here, this
constitution prevails, and the conflict MUST be resolved by amendment — not by
exception.

**Amendment procedure**:

1. Propose the change with rationale and a migration/impact plan.
2. Update this file in the same branch, bumping the version (below).
3. Reconcile every dependent artifact (`CLAUDE.md`, `specs/`, `docs/`, and any
   speckit template that references the changed principle) in the same change.
4. Human review and merge — the constitution is itself subject to Principle I.

**Versioning policy** (semantic):

- **MAJOR**: backward-incompatible governance change — a principle removed or
  redefined in a way that breaks prior compliance.
- **MINOR**: a new principle or section added, or materially expanded guidance.
- **PATCH**: clarifications, wording, typo fixes, non-semantic refinements.

**Compliance review**: every PR MUST self-verify against the Core Principles.
The `code-reviewer` and `security_review` paths treat the principles as gates,
not suggestions. Unjustified complexity MUST be recorded in the plan's
Complexity Tracking table with a rejected-simpler-alternative rationale.

**Version**: 1.0.0 | **Ratified**: 2026-06-28 | **Last Amended**: 2026-06-28
