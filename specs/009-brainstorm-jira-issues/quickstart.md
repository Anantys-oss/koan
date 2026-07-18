# Quickstart / Validation: Brainstorm Jira Issues

## Prerequisites

- `KOAN_ROOT` set to a test root with a `projects.yaml` containing a Jira-backed
  project:
  ```yaml
  projects:
    my-toolkit:
      path: /path/to/repo
      issue_tracker:
        provider: jira
        jira_project: PROJ
        jira_issue_type: Task
  ```
- Jira Cloud credentials configured for `jira_notifications._jira_auth_from_config`
  (for the live path only; unit tests mock the transport).

## Unit validation (no network)

Run the new + extended tests:

```bash
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest \
  koan/tests/test_jira_adf.py \
  koan/tests/test_brainstorm_jira.py -v
```

Expected:
- `markdown_to_adf("## H\n\n- a\n- b\n\n---\n\n**x**")` yields a `doc` with a
  `heading` (level 2), a `bulletList` of two `listItem`s, a `rule`, and a
  `paragraph` whose text node carries a `strong` mark.
- Fenced code block content becomes a single `codeBlock` node, not parsed.
- Empty/whitespace input yields a `doc` with one empty `paragraph` (fallback).
- Runner over a Jira project (mocked tracker): each sub-issue with a `SUB-2`
  reference is updated so the body contains the second issue's real key; one
  `link_issues(master, sub)` call per created sub-issue.
- Runner over a GitHub project (mocked): output identical to pre-feature —
  `#N` refs, no `link_issues` calls.

## Live smoke (optional)

With a real Jira project configured:

```bash
python3 -m skills.core.brainstorm.brainstorm_runner \
  --project-path /path/to/repo \
  --topic "Improve caching strategy"
```

Then in Jira, confirm on any created sub-issue:
- Headings, bullet/ordered lists, and horizontal rules render as native elements
  (no literal `##`, `- `, `---`).
- Bold/inline-code render as marks.
- `SUB-N` references show real `PROJ-<n>` keys.
- The master issue's "Linked issues" panel lists every sub-issue.

## References

- Contracts: [contracts/service-layer.md](./contracts/service-layer.md)
- Shapes: [data-model.md](./data-model.md)
- Rationale: [research.md](./research.md)
