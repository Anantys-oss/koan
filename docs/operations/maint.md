---
type: doc
title: "Maintenance & Release"
description: "Covers Kōan's release pipeline (/koan.incubate preps, release.yml executes), branch philosophy (`main` / `incubating` / `stable` branch + `latest` tag), the ${NEXT} changelog flow into CHANGES.md, versioning scheme, and recovery steps."
tags: [operations]
created: 2026-05-28
updated: 2026-07-14
---

# Maintenance & Release

## Philosophy

Kōan has three channels:

- **`main`** — bleeding edge, the *unstable integration branch*. Every merged PR lands here. It moves constantly and may contain commits that have not been validated nor documented yet. **Releases are never cut from `main`.**
- **`incubating`** — the validated pre-release branch, and the *single source of truth for releases*. It only receives `main` through reviewed `/koan.incubate` merges, each of which appends a curated, human-reviewed changelog entry to `changes/incubating.md`. An operator instance runs this branch continuously, so its content is field-tested.
- **`stable`** — the released channel, maintained *exclusively* by the release workflow in two forms:
  - the **`stable` branch** — a fast-forward of the released commit on `incubating`; kept because deploy platforms (Railway) track a branch;
  - the **`latest` git tag** — a moving tag pointing at the last release, for users who pin refs. (Named `latest`, not `stable`, precisely to avoid colliding with the branch.)

A release is cut **when `incubating` is healthy and something worth shipping has landed** — not on a fixed cadence. Typical triggers:

- A noteworthy feature has landed on `incubating` and been validated by the operator instance.
- A cluster of fixes / polish commits has accumulated (roughly 5–20 commits since the last tag).
- A bug fix is important enough that stable users need it now.

Do **not** release if:

- The test suite is not 100% green.
- Work-in-progress is merged behind feature flags that aren't ready.
- The candidate commits haven't actually run in an instance since the last tag.

The human decides. The tooling just enforces the hygiene.

## Pipeline: the skill preps, the workflow releases

One skill, one workflow. The skill never tags or publishes; the workflow is the
sole executor of a release.

1. **`/koan.incubate`** (skill, human-reviewed) — merges `main` into `incubating`
   after a summarized diff review and go/no-go, and appends a grouped changelog
   entry (`### Merged <date> — main @ <sha>`) under the literal **`## ${NEXT}`**
   heading in `changes/incubating.md`. `${NEXT}` is a placeholder for the
   not-yet-chosen version number.
2. **`release.yml`** (GitHub Actions, `workflow_dispatch` on the `incubating`
   ref, version as input) — refuses any other ref; validates the version format
   and tag uniqueness; then:
   - **Finalizes the changelog**: replaces `## ${NEXT}` with `## <version> — <date>`
     and writes the released history to **`CHANGES.md`** (repo root); resets
     `changes/incubating.md` to a fresh empty `## ${NEXT}` section on top of the
     released history; commits both on `incubating`. A missing or empty
     `${NEXT}` section is a hard failure — never a git-log fallback.
   - **Tags**: creates the annotated `v<version>` tag and force-moves the
     `latest` tag to it.
   - **Fast-forwards the `stable` branch** to the released commit (fails loudly
     if non-ff — never a merge commit, never a force-push).
   - **Publishes**: the GitHub release (notes = the finalized `${NEXT}` section)
     and the Docker images (semver tags, `latest`, `stable`).

The ad-hoc `publish-container.yml` workflow remains the out-of-band channel for
publishing dev images without cutting a release: dispatch it on `main` or
`incubating` (any other ref is refused) with a tag such as `devel` (the default)
or `pr-123`. The release-owned tags — `stable`, `latest`, and semver — are
rejected there; they are minted only by `release.yml`.

## Version scheme

Currently `v0.NN` (single minor). When we hit 1.0, switch to semver `vX.Y.Z`:

- **patch** (`Z`) — fixes, docs, internal refactors
- **minor** (`Y`) — new features, backward-compatible
- **major** (`X`) — breaking changes (config format, skill API, etc.)

## Hotfix on stable

If stable needs a fix and `main` has unreleasable work in flight:

```bash
git checkout -b hotfix/xyz refs/tags/latest
# fix + commit
git checkout main && git cherry-pick hotfix/xyz
# merge PR to main, then run /koan.incubate and dispatch release.yml on incubating
```

Do not commit directly to the `stable` branch. It must only ever be a
fast-forward of a released commit on `incubating`.

## Legacy: `make release`

The old `make release` target (`scripts/release.sh`) tagged directly from `main`
with a Claude-generated changelog. It predates the incubating pipeline and is
**deprecated**: it bypasses the incubate validation pass and the curated
changelog. Use `/koan.incubate`, then dispatch the Release workflow:
`gh workflow run release.yml --ref incubating -f version=vX.Y.Z`.

## Recovery

- **Bad tag pushed** — `git tag -d vX.Y && git push origin :refs/tags/vX.Y && gh release delete vX.Y`. Revert the changelog-rotation commit on `incubating` if needed, then re-dispatch the release workflow.
- **`stable` branch diverged** — reset it to the latest release tag: `git branch -f stable vX.Y && git push --force-with-lease origin refs/heads/stable`. Force-push is acceptable on the `stable` branch *only* to realign it with a release tag.
- **`latest` tag stale** — repoint it: `git tag -f latest vX.Y && git push origin +refs/tags/latest`.
