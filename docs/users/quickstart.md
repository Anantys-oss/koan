# Kōan Quickstart — Zero to Hero

**The 5-minute guide to driving Kōan.** This page shows the handful of commands you'll
actually use, where to type them, and exactly what they look like. When you want the full
list, graduate to the [Skills Reference](skills.md) and [User Manual](user-manual.md).

Kōan is an agent you steer from where you already work:

- On **GitHub** and **Jira**, you comment `@koan-bot <command>`.
- In your **messaging app** (Telegram / Slack), you send `/command`.

> **The agent proposes. The human decides.** Kōan opens draft PRs and posts plans — it never
> merges or pushes to your main branch on its own.

> **Placeholders:** examples use `@koan-bot` (the bot's handle), `myproject` (a project id),
> `PROJ-123` (a Jira key), and `myrepo` (a repo name). **Swap these for your own.** Ask your
> Kōan admin for the real bot handle and your project ids.

> **Context is optional.** Every command works on its own, but you can add free-text after it
> to steer the work (e.g. *"focus on security"*, *"only touch the auth module"*). Each command
> below shows one **minimal** example and one **with context**.

---

## 1. From a GitHub Pull Request

Comment on the PR. Kōan reacts ( 👍) and queues the work, then reports back on the thread.

**`review`** (alias: `rv`) — queue a code review of the pull request.
```
@koan-bot review
```
```
@koan-bot review focus on error handling and security
```

**`rebase`** (alias: `rb`) — rebase the PR onto its base branch (resolve drift / conflicts).
```
@koan-bot rebase
```
```
@koan-bot rebase keep the test refactor as a separate commit
```

**`rr`** (full name: `reviewrebase`) — review **then** rebase, so the review insights inform the rebase.
```
@koan-bot rr
```
```
@koan-bot rr pay attention to the API changes
```

**`squash`** (alias: `sq`) — squash all the PR's commits into one clean commit.
```
@koan-bot squash
```
```
@koan-bot squash use a conventional-commit message
```

**`explain`** (alias: `xp`) — explain the PR's changes in plain language, with examples.
```
@koan-bot explain
```
```
@koan-bot explain explain it for someone new to this codebase
```

**`ask`** (alias: `question`) — ask a question about the PR; Kōan posts an AI reply on the thread.
*(`ask` always needs a question.)*
```
@koan-bot ask does this handle the null case?
```
```
@koan-bot ask why a queue here instead of a direct call? compare the tradeoffs
```

---

## 2. From a GitHub Issue

Comment on the issue with the same `@koan-bot <command>` syntax.

**`fix`** — diagnose → plan → test → implement → open a draft PR, end-to-end.
```
@koan-bot fix
```
```
@koan-bot fix add a regression test and only touch the auth module
```

**`plan`** — deep-think the issue and post a structured plan (no code yet).
```
@koan-bot plan
```
```
@koan-bot plan keep it backwards compatible and reuse the existing cache layer
```

**`implement`** (alias: `impl`) — implement the issue and open a draft PR.
```
@koan-bot implement
```
```
@koan-bot implement do phase 1 only for now
```

**`planit`** (full name: `planimplement`; also `doit`) — plan **then** implement in one go.
```
@koan-bot planit
```
```
@koan-bot planit focus on the database migration path
```

---

## 3. From Jira

Same `@koan-bot <command>` syntax, typed in a **Jira issue comment**. The same commands as
GitHub issues work here: `fix`, `plan`, `implement` / `impl`, and `planit`.

Kōan maps the Jira key prefix (e.g. `PROJ` in `PROJ-123`) to the right project automatically,
so you usually type nothing extra.

**`fix`** — diagnose and fix the issue end-to-end, opening a draft PR.
```
@koan-bot fix
```
```
@koan-bot fix add a regression test and only touch the auth module
```

**`plan`** — post a structured implementation plan on the ticket (no code yet).
```
@koan-bot plan
```
```
@koan-bot plan keep the public API stable
```

**`implement`** (alias: `impl`) — implement the ticket and open a draft PR.
```
@koan-bot implement
```
```
@koan-bot implement do phase 1 only for now
```

**`planit`** (also `doit`) — plan then implement in one go.
```
@koan-bot planit
```
```
@koan-bot planit focus on the migration path
```

### Advanced: target a specific repo or branch

When a Jira project spans more than one repo, or you need to work on a maintenance/release
branch, add these tokens anywhere in your comment:

- **`repo:myrepo`** — choose which project/repo Kōan works in *(default: the one mapped from the Jira key)*.
- **`branch:mybranch`** — choose the base branch Kōan targets *(default: the project's configured default branch)*.

```
@koan-bot fix branch:release-2.0
```
```
@koan-bot fix repo:myrepo branch:release-2.0 only patch the auth path
```

The `repo:` / `branch:` tokens are case-insensitive and stripped out — whatever text remains
becomes the focus context. (The `branch:` token also works after a PR/issue URL with `/fix`,
`/implement`, `/plan`, and `/deepplan` on other surfaces — see the [Skills Reference](skills.md).)

---

## 4. From your messaging app (Telegram / Slack)

Message the bot directly. A `/command` runs a skill; plain text becomes a mission.

**Basic mission** — start the message with the **project id**, then say what you want done.
*(This works when `myproject` is a configured project.)*
```
myproject add a /healthz endpoint
```
```
myproject add a /healthz endpoint that checks the DB and returns 200 or 503
```
> Two equivalent forms if you prefer them: `[project:myproject] <task>` (explicit) or
> `/mission <task>` (let Kōan pick the project).

**`/brainstorm`** — break a broad topic into linked issues plus a master tracking issue.
```
/brainstorm myproject improve our onboarding flow
```
```
/brainstorm myproject improve our onboarding flow --tag onboarding
```

**`/ai`** (alias: `/ia`) — queue an AI exploration mission with full codebase access.
```
/ai myproject
```
```
/ai myproject explore the notification pipeline for refactor opportunities
```

**`/audit`** — audit the project and file a tracker issue per finding (top 5).
```
/audit myproject
```
```
/audit myproject focus on performance limit=3
```

**`/deep`** — a thorough autonomous deep-exploration session (more in-depth than `/ai`).
```
/deep myproject
```
```
/deep myproject the error-handling paths in the API layer
```

---

## 5. Finding your way around

A few messaging-app commands to orient yourself:

| Command | What it does |
|---------|--------------|
| `/help` | List the commands available to you. |
| `/status` (alias `/st`) | Agent state, current mission, and loop health. |
| `/list` (aliases `/queue`, `/ls`) | Show pending and in-progress missions. |
| `/focus [duration]` | Lock the agent to one project (default 5h); `/unfocus` to exit. |

---

## 6. Advanced: natural language

Kōan also understands plain English. A longer imperative message ("*add a retry to the
webhook sender and write a test*") is queued as a mission, and if your message **starts with a
skill name** it's promoted to that command automatically — e.g. `audit myproject look for N+1
queries` runs `/audit`.

> **Recommendation:** prefer the preset commands above. They're predictable, accept flags and
> context, and are discoverable via `/help`. Reach for free-form natural language only for
> one-off, unstructured requests.

**Go deeper:** the [Skills Reference](skills.md) lists every command and flag, and the
[User Manual](user-manual.md) walks you from beginner to power user with full workflows.
