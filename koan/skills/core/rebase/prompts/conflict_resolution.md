# Rebase — Resolve Merge Conflicts

You are resolving merge conflicts that occurred while rebasing a pull request branch onto its target.

## Pull Request: {TITLE}

**Branch**: `{BRANCH}` → `{BASE}`

### PR Description

{BODY}

---

## Conflicted Files

{CONFLICTED_FILES}

---

## Your Task

**IMPORTANT: Do NOT create new branches, switch branches, or run git rebase/merge commands.
Stay on the current branch. You are in the middle of a rebase — your job is to resolve the conflicts
in the files listed above so the rebase can continue.**

1. Inspect only the listed conflicted files and their conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).
2. During a rebase, `HEAD`/`ours` is the target branch's current version; `theirs` is the
   PR commit being replayed. Do not blindly take either side. Keep the target branch's
   current structure and incorporate the PR change needed to preserve its intent.
3. Remove every conflict marker, then stage every resolved file with `git add <file>`.
4. Before responding, run `git diff --name-only --diff-filter=U` and confirm it prints
   nothing. If it lists a file, resolve and stage it before responding.
5. Do not run `git rebase --continue`, tests, network commands, unrelated investigation,
   branch changes, or any rebase/merge command. The caller handles continuation and tests.

When you're done, output a concise summary of how you resolved each conflict.
