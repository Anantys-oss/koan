Merge `main` into the Kōan `incubating` branch, but only after a review phase where you summarize the incoming diff and get an explicit go/no-go. On merge, append a dated entry under the `## ${NEXT}` section of the `changes/incubating.md` journal — the curated changelog the release workflow later finalizes.

This is the **only** skill in the release pipeline: it preps `incubating` (merge + changelog); it never tags, never publishes. Releasing is done exclusively by dispatching `release.yml` on the `incubating` ref (Actions UI, or `gh workflow run release.yml --ref incubating -f version=vX.Y.Z`), which replaces `${NEXT}` with the version in `CHANGES.md`, tags, fast-forwards the `stable` branch, moves the `latest` tag, and publishes the GitHub release + Docker images.

Run this from the Kōan repo root.

Arguments:
- `$ARGUMENTS` — optional source branch (defaults to `main`) and/or `--yes` to skip the go/no-go pause (not recommended).

## Pre-flight

1. **Confirm repo & branch**:
   ```bash
   git rev-parse --show-toplevel   # must be the koan repo
   git status --porcelain          # must be clean — else STOP, ask to commit/stash
   ```
2. **Fetch and checkout incubating**:
   ```bash
   git fetch origin --prune
   git checkout incubating
   git pull --ff-only origin incubating 2>/dev/null || true
   ```
3. **Resolve source** = `$ARGUMENTS` branch or `main`. `git fetch origin <source>`.

## Review phase (comment the diff, assess if OK)

4. **Compute what would land**:
   ```bash
   git rev-list --count incubating..origin/<source>          # total commits
   git log incubating..origin/<source> --no-merges --format='%s'
   git diff --stat incubating..origin/<source>
   ```
5. **Group the log by type** (`feat` / `fix` / `refactor` / `perf` / `docs` / `test` / `ci`) and write a **concise grouped summary** — do NOT dump the raw log. Highlight:
   - Notable features (1 line each).
   - Security-relevant fixes.
   - Refactors that move/rename/delete files.

   This summary is for the **go/no-go review only** — it is developer-facing
   (commit types, PR numbers, file churn are fine here). It is **NOT** the
   journal entry: do not paste it verbatim into `changes/incubating.md`. The
   journal is written separately in step 9 for a different audience.
6. **Assess risk** and state it plainly:
   - **Conflicts** — does any file changed on `incubating` also change on `<source>`? (`git merge --no-commit --no-ff` dry check, then `git merge --abort`).
   - **Lost work** — any commit unique to `incubating` (`git log origin/<source>..incubating --oneline`) whose files were refactored/deleted on `<source>`? Flag it — its change may be silently orphaned.
   - Give a one-line verdict: **OK to merge** / **Merge with care (…)** / **Do not merge (…)**.
7. **WAIT for user go/no-go.** Do not merge until the user approves (unless `--yes` was passed).

## Merge

8. On approval:
   ```bash
   git merge origin/<source> --no-edit
   ```
   Resolve any conflicts. **Policy: `<source>` (main) wins** on substantive conflicts unless the user says otherwise; preserve incubating-only additions that don't conflict. Commit the resolution.

## Update the journal

9. Append a new entry under the top `## ${NEXT}` heading in `changes/incubating.md` (create the file/section if missing — the token is literal, one `$`, exactly `## ${NEXT}`; the release workflow matches it verbatim and replaces it with the version).

   **Write this entry for USERS, not developers.** `release.yml` copies the
   `## ${NEXT}` body **verbatim** into `CHANGES.md` and into the published
   GitHub release notes — so this is what your users read, not a diff summary.
   Do NOT reuse the step-5 review text. Instead:
   - **Group by user impact**, Keep-a-Changelog style: `**Added**` /
     `**Changed**` / `**Fixed**` / `**Removed**` / `**Deprecated**` (omit empty
     groups). Do NOT group by commit type (`feat`/`refactor`/`test`/`ci`).
   - **Describe behavior and benefit** — what a user can now do, what changed
     for them, what was broken and is now fixed. Lead with the capability, not
     the mechanism.
   - **Drop internal noise**: no file names, test names, LoC counts, module
     paths, or refactor churn. Pure-internal work (refactors, test-only,
     CI-only, internal docs) is **omitted** unless it changes observable
     behavior. PR/issue numbers are fine as trailing references (e.g. `(#2439)`).
   - **Plain, present-tense, user language.** "Chat stays responsive while a
     mission runs" — not "add chat priority lane to bridge dispatch loop".

   Shape:
   ```markdown
   ### Merged <YYYY-MM-DD> — <source> @ <short-sha> (<N> commits)

   **Added** — new user-facing capabilities …
   **Changed** — behavior changes users will notice …
   **Fixed** — bugs resolved, described by symptom …
   **Removed** / **Deprecated** — as applicable …
   ```
   Get the values with `date -u +%Y-%m-%d` and `git rev-parse --short origin/<source>`.
10. Stage and commit the journal:
    ```bash
    git add changes/incubating.md
    git commit -m "docs(changes): log main merge into incubating (<short-sha>)"
    ```

## Summary

11. Report: commits merged, conflicts resolved, journal entry added. Remind the user to `git push origin incubating` and to redeploy the incubating instance if desired.

## Rules

- **Always run the review phase** and wait for go/no-go unless `--yes`.
- **main wins** on substantive conflicts; never silently drop incubating-only work — flag it instead.
- **Never edit released entries** in `changes/incubating.md` (only append under `## ${NEXT}`).
- **Journal entries are user-facing release notes** (published verbatim by `release.yml`) — group by user impact (Added/Changed/Fixed), describe behavior and benefit, and omit internal-only churn and file/test/LoC detail. The step-5 review summary is separate and developer-facing.
- **`CHANGES.md` is the released changelog** (finalized by `release.yml`); `changes/incubating.md` is only the staging journal. There is no `changes/stable.md` — do not create or reference one.
- **Never tag, never release** — this skill preps `incubating` only; the release itself belongs to the `release.yml` workflow dispatch.
- English only in all commits and journal text.
