You are creating a **conventional git commit** for the **{PROJECT_NAME}** project.

Your job: inspect the working tree, craft a high-quality conventional commit message, stage the right files, and create **one** commit. Do **not** open a PR, do **not** push, and do **not** rewrite history.

## Parameters

- **Project**: {PROJECT_NAME}
- **Message hint from human** (optional guidance — honor it when it fits the diff):
{MESSAGE_HINT}

---

## Hard rules (never violate)

1. **Never commit to `main` or `master`.** If HEAD is on a protected base branch, stop and report — do not create a feature branch unless the human already asked for one elsewhere. Prefer failing loudly over committing to the base branch.
2. **Never stage or commit secrets.** Refuse (and unstage if already staged) any of:
   - `.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa*`, `credentials.json`, `secrets.yaml`, `secrets.yml`
   - files that clearly contain API keys, tokens, passwords, or private keys
3. **Never create an empty commit.** If there is nothing meaningful to commit after filtering secrets, stop and report.
4. **Do not push.** Leave the commit local.
5. **Do not amend** an existing commit unless the human explicitly asked (they did not).
6. **One commit only** for this invocation.

---

## Phase 1 — Inspect git state (do this first)

Run these commands and read their output carefully:

```bash
git status
git branch --show-current
git rev-parse --abbrev-ref HEAD
git diff --cached          # staged changes
git diff                   # unstaged tracked changes
git log --oneline -10      # local commit style
```

Also list untracked files that look intentional (`git status -u`).

### Abort conditions

Stop immediately and report (do not commit) if any of these are true:

| Condition | Report |
|---|---|
| Current branch is `main` or `master` | On protected base branch — refuse to commit |
| Unresolved merge conflicts (`UU` / unmerged paths) | Abort — conflicts must be resolved first |
| No staged, unstaged, or untracked changes | Working tree clean — nothing to commit |
| Only secret/credential files would be committed | Refuse — list the blocked paths |

---

## Phase 2 — Decide what to stage

- Prefer **already staged** changes. If something is staged, commit **only** the staged set unless the unstaged set is clearly the same logical change and safe to include.
- If **nothing is staged** but there are unstaged/untracked changes, stage the files that form one coherent logical change. Leave unrelated WIP unstaged.
- Group by intent: if the tree mixes unrelated work, commit the dominant cohesive change and leave the rest unstaged. Mention leftovers in your report.
- Re-check the staged set after `git add` with `git diff --cached --stat` and `git diff --cached`.

---

## Phase 3 — Analyze the change

From the staged diff, determine:

1. **Type** (required, one of):
   - `feat` — new user-facing capability
   - `fix` — bug fix
   - `refactor` — internal restructuring, no behavior change
   - `docs` — documentation only
   - `test` — tests only
   - `chore` — tooling, deps, config, misc maintenance
   - `perf` — performance improvement
   - `style` — formatting / whitespace only
   - `build` / `ci` — build system or CI only
2. **Scope** (optional): short module/area name (`auth`, `missions`, `skills`, …) when one area dominates.
3. **Subject**: imperative mood, ≤ ~72 chars, no trailing period. Describe *what* the commit does, not *how*.
4. **Body** (optional): when the *why* is non-obvious — wrap at ~72 cols, explain motivation and key trade-offs.
5. **Breaking change**: if the commit removes/renames a public API or changes a contract incompatibly, add a footer `BREAKING CHANGE: <description>`.

Honor the human's message hint when it accurately describes the diff. If the hint conflicts with the actual diff, prefer the diff and note the discrepancy.

Match the repository's existing commit style from `git log` when it is already conventional.

---

## Phase 4 — Commit

Use a HEREDOC so multi-line messages stay intact:

```bash
git commit -m "$(cat <<'EOF'
type(scope): short imperative subject

Optional body explaining why.

EOF
)"
```

Omit `(scope)` when no single scope is clear. Omit the body when the subject is enough.

Verify:

```bash
git status
git log -1 --format=full
```

---

## Phase 5 — Report back

Reply with a short summary for the human:

```
COMMITTED
branch: <branch>
sha: <short-sha>
message: <full subject line>
files: <N staged files committed>
left unstaged: <none | brief list>
notes: <any secrets skipped, hint overrides, leftovers>
```

If you aborted, use:

```
ABORTED
reason: <one-line reason>
details: <what the human should do next>
```

Do not invent commits that did not happen. Do not claim success without a new `git log -1` SHA.
