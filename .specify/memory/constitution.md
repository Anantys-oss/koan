# Kōan Constitution

<!--
=== Sync Impact Report ===
Version change: 1.0.0 → 2.0.0
  MAJOR: Principle III redefined — the prior absolute "never in a database" no
  longer holds for mission state. SQLite becomes mission state's default authority
  behind the MissionStore port; all OTHER runtime state stays file-first.
Modified principles:
  - III. "Local Files, Atomic State" → "Local Files by Default; Mission State in
    the Store". Mission state is authoritative in the backend chosen through the
    MissionStore port (SQLite / instance/missions.db by default), config-selected,
    documented, and exclusive; missions.md becomes a generated read-only export.
    memory_db-style derived indexes over a file truth remain permitted. Any
    ADDITIONAL database-authoritative state needs a further amendment.
  - VI. "Single Writer, Single Read Path" — mission clause restated: the single
    authority is the active MissionStore (get_mission_store()); agents/code MUST
    NOT mutate mission state outside the port nor treat the missions.md export as
    writable.
Constraints & Technology Stack: mission-storage backend added to the pluggable-
  abstraction list (alongside messaging bridges and CLI providers).
Added / Removed sections: none.
Templates requiring updates:
  - .specify/templates/plan-template.md   ✅ no change — "Constitution Check" gate defers here
  - .specify/templates/spec-template.md    ✅ no change — generic speckit template
  - .specify/templates/tasks-template.md   ✅ no change — generic speckit template
Follow-up reconciliations — DEFERRED to the implementation PR (spec
  004-mission-store, PR 2), NOT done in this amendment. These artifacts describe
  the CURRENT file-based mission behavior, which Principle VII requires we preserve
  until the code actually changes; PR 2 updates them in the same branch as the code:
  - CLAUDE.md — "pulling missions from a shared file"; missions.md as the queue
  - koan/app/CLAUDE.md — "missions.md — Task queue" → generated read-only export
  - specs/components/core.md — mission-queue contract + single-writer invariant
  - docs/architecture/{overview,shared-state,mission-lifecycle}.md — mission state location
Rationale basis: specs/004-mission-store/{spec,plan,data-model,contracts}.md;
  issue #2140; epic #2147. Design PR: #2295. Supersedes the mirror approach in #2209.
Prior history: v1.0.0 initial ratification [2026-06-28]. SPECS_DIR_COLLISION was
  RESOLVED [2026-07-04] — component/skill specs are durable + wiki-indexed while
  speckit `specs/<feature>/` folders are ephemeral and unfrontmattered; a shipped
  feature's durable artifact is the updated specs/components/<group>.md. Full
  rationale in specs/README.md and wiki/SCHEMA.md.
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
- One narrowly-scoped, explicitly documented exception exists for wiki
  bookkeeping (frontmatter dates, `wiki/index.md` entries, `wiki/log.md` lines,
  and `specs/<NNN-slug>/` computed status) — see `wiki/SCHEMA.md` ("Workflow
  customizations"). It may be committed directly onto an existing PR's own
  branch by CI, without a separate human-reviewed step, but never to `main` and
  never for anything beyond that metadata (spec/doc bodies and code keep the
  full discipline above).

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

### III. Local Files by Default; Mission State in the Store

Runtime state lives in plain, inspectable files under `instance/`
(Markdown/YAML/JSON/trackers) by default — **with exactly one exception: mission
state is authoritative in a database (SQLite by default) behind the
`MissionStore` port** (detailed in the first bullet). Everything else stays in
files. Shared files MUST be written through `utils.atomic_write()` (temp file +
rename + `fcntl.flock()`); never perform a raw read-modify-write on an
`instance/` file.

- **Mission state is the one authorized database exception.** It is authoritative
  in the backend selected through the `MissionStore` port — SQLite
  (`instance/missions.db`) by default — resolved by one config accessor,
  documented, and **exclusive** (never a second concurrent authority alongside a
  file). `missions.md` becomes a generated **read-only export**, not an input;
  mission mutations flow only through the port (Principle VI). An alternative
  backend may be supplied by configuration without forking core code.
- A database used purely as a **derived index** over a file source of truth (e.g.
  `memory_db`'s FTS5 index over the JSONL memory log) remains permitted — it is
  not an authority.
- **All other runtime state stays in files** (config, outbox, journal, trackers,
  soul, memory JSONL truth). Any *additional* database-authoritative state
  requires a further amendment.
- The bridge (`awake.py`) and runner (`run.py`) are separate processes; bugs
  harmless in one process corrupt state when both are active. Store transactions
  (mission state) and `atomic_write()` (the remaining files) prevent corruption
  across the two.
- Transient scratch files and the provider invocation lock live under the
  per-uid `utils.koan_tmp_dir()` (`$XDG_RUNTIME_DIR/koan` or `/tmp/koan-<uid>/`,
  mode `0700`) — NOT in `instance/` or a fixed `/tmp` name. This is what lets
  multiple users run Kōan on one host without colliding.

*Rationale*: Plain files keep most state auditable, easy to back up, and easy for
humans and LLMs to inspect. Mission state is the narrow exception: it is the
hottest, most-queried artifact (15+ callsites; count/list scans) and the one
whose schema must keep evolving (terminal status, failure reason, cost). An
indexed, single-authority store removes fragile regex parsing and the dual-write
divergence a file+mirror design cannot escape (see #2209). The exception is
narrow, config-selected, exclusive, and behind an abstraction — file-first holds
everywhere else.

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

- Mission state has exactly one authority — the active `MissionStore`
  implementation, reached through one port (`get_mission_store()`). Agents and
  code MUST NOT mutate mission state outside the port, and MUST NOT treat the
  generated `missions.md` export as writable. (The `file` adapter's realization of
  this, if one is supplied, still funnels through the port.)
- Each config concern has exactly one read path — an accessor in `config.py` or
  `projects_config.py` (`projects.yaml` > `KOAN_PROJECTS`). Never read
  `os.environ`/YAML inline; add or reuse the accessor.
- `run.py` is the single host of the CLI subprocess; every exit from In Progress
  funnels through `_finalize_mission()`.
- Bilingual section headers (`Pending`/`In Progress`/`Done` and the French
  equivalents) MUST be preserved by the `missions.md` export renderer and the
  one-time ingest.

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
  itself has no web framework. Messaging bridges (Telegram/Slack/etc.), CLI
  providers, and the mission-storage backend (the `MissionStore` port) are
  pluggable. Add new providers/bridges/backends behind their abstraction, not by
  forking core code.
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

**Version**: 2.0.0 | **Ratified**: 2026-06-28 | **Last Amended**: 2026-07-09
