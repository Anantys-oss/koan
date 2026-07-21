# Implementation Plan: Brainstorm Jira Issues

**Branch**: `koan.atoomic/brainstorm-jira-issues` | **Date**: 2026-07-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/009-brainstorm-jira-issues/spec.md`

## Summary

Make `/brainstorm` produce rich, natively-linked issues when a project's
configured tracker is Jira, while leaving the GitHub path byte-identical. Three
pieces: (1) a markdown→ADF converter in `jira_notifications.py` so brainstorm's
markdown bodies render as real Jira headings/lists/rules/marks instead of literal
text; (2) a provider-neutral `update_issue()` service-layer operation so
brainstorm's existing SUB-N cross-reference resolution works for Jira as well as
GitHub; (3) a provider-neutral `link_issues()` service-layer operation that
creates native Jira "Relates" links from the master issue to each sub-issue and
is a no-op on GitHub. All new Jira writes degrade non-fatally.

## Technical Context

**Language/Version**: Python 3.11+ (constitution constraint)

**Primary Dependencies**: existing `app.issue_tracker` service layer + backends
(`github.py`, `jira.py`), `app.jira_notifications` REST transport (Jira Cloud
REST API v3, ADF), `app.github` (`issue_edit`); stdlib only for the converter
(no new third-party markdown lib — YAGNI, Principle VII).

**Storage**: N/A — no new persistent state. Config read from `projects.yaml`
via the existing `issue_tracker.config` accessors.

**Testing**: pytest with `KOAN_ROOT` set; mock at `run_gh`/Jira `_jira_post`
(transport) level, never the Claude subprocess. New unit tests for the converter
(markdown constructs → ADF nodes) and the runner's Jira linking/SUB-N path with
a mocked tracker.

**Target Platform**: Linux/macOS daemon; Jira Cloud.

**Project Type**: Single Python package (`koan/`), skill runner +
issue-tracker component.

**Performance Goals**: N/A — a handful of REST calls per brainstorm run;
converter is linear in body size.

**Constraints**: Provider neutrality (Principle IV analog for trackers): no new
provider `if/else` in `brainstorm_runner` beyond selecting service-layer ops.
Non-fatal degradation for all new Jira writes. No inline prompts (N/A — no new
prompts). Must pass `ruff` / `make lint`.

**Scale/Scope**: One skill runner (`brainstorm_runner.py`), the issue-tracker
service layer + ABC + two backends, and the Jira transport module. ~3–8
sub-issues + 1 master per run.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Human Authority**: PASS — brainstorm still only *creates issues* the human
  requested; no merges/commits-to-main. No change to authority model.
- **II. Specs Are the Source of Truth**: **ARCHITECTURAL CHANGE — declared.**
  This adds `update_issue()` and `link_issues()` to the `IssueTracker` durable
  contract (`specs/components/issue-tracking.md`). Per Principle II the spec is
  changed **contract-first** (this plan defines the intended contract; the
  component spec is updated in the same branch before/with the code) and the PR
  MUST check the "Architectural change" box. The skill spec
  `specs/skills/brainstorm.md` is also updated (provider-neutral Jira behavior).
  New methods are added as **concrete, non-abstract** members with safe defaults
  (`update_issue` best-effort, `link_issues` no-op) so out-of-tree `IssueTracker`
  subclasses keep working — no breaking change to existing adapters.
- **III. Local Files / Mission State**: PASS — no new state; no mission-store
  changes.
- **IV. Provider Isolation**: PASS (and reinforced) — the whole design keeps the
  GitHub/Jira split behind the service layer; `brainstorm_runner` gains no raw
  provider calls. The one existing `if provider != "github": return` guard in
  `_replace_sub_placeholders` is *removed* in favor of the neutral
  `update_issue()` path — a net reduction in provider branching.
- **V. Untrusted Inputs, Audited Outputs**: PASS — issue bodies still flow
  through the same outbound path; GitHub `issue_edit` already runs
  `scan_and_redact`. The Jira `update_issue` path posts the same body content the
  create path already posts, so no new exfiltration surface; converter is a pure
  transform.
- **VI. Single Writer, Single Read Path**: PASS — tracker routing stays the
  single `get_tracker_for_project()` path; new ops route through the same
  `client_for_url`/`client_for_project` factories.
- **VII. Simplicity & Honest Reporting**: PASS — no new dependency; a small
  hand-rolled markdown subset converter (only the constructs brainstorm emits)
  rather than pulling a full CommonMark→ADF library. Rejected alternatives noted
  in `research.md`.

**Result**: PASS with one **declared architectural change** (issue-tracker
contract additions). Recorded in Complexity Tracking below.

## Project Structure

### Documentation (this feature)

```text
specs/009-brainstorm-jira-issues/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (service-layer + ADF contracts)
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
koan/app/
├── jira_notifications.py          # ADD markdown_to_adf(); route jira_create_issue
│                                   #   description + jira_update_issue_description()
│                                   #   + jira_link_issues() through REST v3
├── issue_tracker/
│   ├── base.py                     # ADD update_issue() (best-effort default),
│   │                               #   link_issues() (no-op default) to ABC
│   ├── github.py                   # implement update_issue() via issue_edit;
│   │                               #   link_issues() = no-op (returns False)
│   ├── jira.py                     # implement update_issue() (PUT description ADF),
│   │                               #   link_issues() (issueLink API)
│   └── __init__.py                 # ADD update_issue()/link_issues() service fns
└── github.py                       # (unchanged) issue_edit reused by GitHub backend

koan/skills/core/brainstorm/
└── brainstorm_runner.py            # _replace_sub_placeholders → provider-neutral
                                     #   update_issue(); add master→sub link step

koan/tests/
├── test_jira_adf.py                # NEW: markdown_to_adf unit tests
├── test_issue_tracker*.py          # extend: update_issue/link_issues routing
└── test_brainstorm*.py             # extend: Jira SUB-N resolution + linking

specs/components/issue-tracking.md   # durable contract update (declared)
specs/skills/brainstorm.md           # durable skill-spec update (declared)
docs/messaging/jira-integration.md   # capture: rich brainstorm rendering + linking
docs/users/skills.md / user-manual.md# brainstorm Jira behavior note (if user-facing)
```

**Structure Decision**: Single-package layout. The feature lives at the seam
already defined by the issue-tracking component (service layer + ABC + backends +
Jira transport) plus the one skill runner. No new modules/packages — the ADF
converter is a function in the existing `jira_notifications.py`, consistent with
where `_text_to_adf` already lives.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Durable-contract change to `specs/components/issue-tracking.md` (new `update_issue`/`link_issues` ABC members) | SUB-N resolution and native linking require an update/link capability, and the whole point of the component is that skills never branch on provider — so the capability must exist on the neutral contract, not as a Jira-only call in the runner | A Jira-only branch in `brainstorm_runner` was rejected: it violates Principle IV/the component's "no provider branching" invariant and duplicates the GitHub `issue_edit` two-pass logic. Adding to the contract is the smaller long-term surface. Methods are non-abstract with safe defaults so no existing adapter breaks. |
