# PR Activity Reports

The `/report` skill posts a digest of Kōan's GitHub Pull-Request activity to the
communication channel, covering **per-project and global** metrics over a trailing
7-day or 30-day window. Output is a single fenced markdown code block, so it stays
readable in Telegram and other chat channels.

## Commands

| Command | Window |
|---|---|
| `/report` | both week (7 days) **and** month (30 days) — default |
| `/report --week` | week (7 days) |
| `/report --month` | month (30 days) |
| `/report --week --month` | both windows |
| `/weekly_report` | week (7 days) |
| `/monthly_report` | month (30 days) |

A plain `/report` with no flag emits two stacked digests (weekly first, then
monthly). Add `--week` or `--month` to narrow it to a single window. The
`/weekly_report` and `/monthly_report` aliases always report their own window.

## Metrics

All counts are scoped to **Kōan's own GitHub user** (`get_gh_username()`), resolved
from `gh auth`. For each project (and summed globally):

| Metric | Definition | GitHub search |
|---|---|---|
| **Created** | PRs Kōan authored, created in the window | `author:USER created:WINDOW` |
| **Merged (%)** | **Cohort**: of the PRs *created in the window*, how many are merged *now*. Percentage = `merged / created`. | `author:USER created:WINDOW is:merged` |
| **Interacted** | PRs Kōan was involved in (authored, commented, reviewed, mentioned), updated in the window. Includes human-authored PRs. | `involves:USER updated:WINDOW` |
| **Interacted+merged** | PRs Kōan interacted with (any time) that **merged during the window** | `involves:USER merged:WINDOW` |

Because **Merged** is a cohort metric, the percentage can read below 100% even when
older PRs land this week — it answers "of what we opened this period, how much shipped",
not "how many merges happened this period".

### Known limitations

"Interacted" is sourced from GitHub's search `involves:` qualifier, which matches
author / commenter / reviewer / mentioned / assignee. Two consequences to keep in mind:

- **Undercounts silent rebases.** A bare force-push or rebase by Kōan on a
  *human-authored* PR — with no accompanying comment — does **not** surface in
  `involves:`. Kōan's own PRs are always captured (it is the author).
- **Overcounts stale involvement.** `involves:USER updated:WINDOW` matches any PR
  where Kōan was *ever* involved that was *updated by anyone* during the window — a CI
  bump or someone else's comment on an old Kōan PR counts as "interacted". GitHub
  search cannot time-scope the involvement itself, so "interacted" means "a PR Kōan has
  touched that saw activity this period", which is broader than "Kōan acted on it this
  period".

A failed search for one repo is isolated: the report retries that repo on its own and
only zeroes the repo that truly can't be fetched (tagging the report `(partial)`), so a
single inaccessible repo never zeroes its batch-mates.

## Data source and cost

PR counts come from **GitHub**, not from the dashboard usage page (`cost_tracker.py`,
which tracks tokens/cost only). The optional token/cost line in the report is the one
piece pulled from `cost_tracker.summarize_by_project()`.

To stay under the GitHub search API rate limit across many projects, counts are fetched
with **aliased GraphQL `search` queries** — extending the pattern in
`app.github.batch_count_open_prs`. Each repo contributes four aliased
`search(query: …){ issueCount }` fields, batched up to `_REPOS_PER_QUERY` (15) repos per
GraphQL call. A full report therefore costs a handful of API calls regardless of project
count. If a chunk fails, its repos count as `0` and the report is tagged `(partial)`.

Implementation: `koan/app/pr_report.py` (engine) and `koan/skills/core/report/`
(`SKILL.md` + `handler.py`, a `worker: true` bridge skill).

## Example output

```
PR Report — week (2026-06-18 .. 2026-06-24)

GLOBAL
  Created:            12
  Merged:             9  (75% of created)
  Interacted:         20
  Interacted+merged:  11
  Usage:              4.2M tok  |  $6.10

project        creat  merg    %  inter   i+m
--------------------------------------------
koan               7     6  86%     11     7
my-toolkit         5     3  60%      9     4
--------------------------------------------
TOTAL             12     9  75%     20    11
```

## Auto-scheduling

`/report` is manual by design. To post it automatically (e.g. every Monday morning),
wire it to Kōan's existing scheduler rather than adding bespoke timing:

- **Recurring injection** — use `/recurring` to register `/weekly_report` on a cadence;
  the loop injects it like any other recurring mission.
- **One-shot scheduled events** — drop a JSON file in `instance/events/` (consumed by
  `event_scheduler.py`) to fire `/monthly_report` at a specific datetime.

Both paths reuse the normal mission pipeline, so the report is built and posted exactly
as if you had typed the command.
