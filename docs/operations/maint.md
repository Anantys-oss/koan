---
type: doc
title: "Maintenance & Release"
description: "Covers Kōan's release pipeline (incubate → incubating → release workflow), branch philosophy (`main` / `incubating` / `stable`), the curated changelog flow, versioning scheme, and recovery steps."
tags: [operations]
created: 2026-05-28
updated: 2026-07-13
---

# Maintenance & Release

## Philosophy

Kōan has three channels:

- **`main`** — bleeding edge, the *unstable integration branch*. Every merged PR lands here. It moves constantly and may contain commits that have not been validated nor documented yet. **Releases are never cut from `main`.**
- **`incubating`** — the validated pre-release branch, and the *authority for releases*. It only receives `main` through reviewed `/koan.incubate` merges, each of which appends a curated, human-reviewed changelog entry to `changes/incubating.md`. An operator instance runs this branch continuously, so its content is field-tested.
- **`stable`** — contains *only* tagged releases, fast-forwarded from `incubating` at each release. Users who want a predictable experience track this branch (or the moving `stable` tag).

A release is cut **when `incubating` is healthy and something worth shipping has landed** — not on a fixed cadence. Typical triggers:

- A noteworthy feature has landed on `incubating` and been validated by the operator instance.
- A cluster of fixes / polish commits has accumulated (roughly 5–20 commits since the last tag).
- A bug fix is important enough that stable users need it now.

Do **not** release if:

- The test suite is not 100% green.
- Work-in-progress is merged behind feature flags that aren't ready.
- The candidate commits haven't actually run in an instance since the last tag.

The human decides. The tooling just enforces the hygiene.

## Pipeline: incubate → incubating → release workflow

The release pipeline has a single source of truth for release notes — the curated
changelog built by incubate merges — and a single place that tags: the
`release.yml` GitHub Actions workflow.

1. **`/koan.incubate`** (skill, human-reviewed) — merges `main` into `incubating`
   after a summarized diff review and go/no-go, and appends a grouped changelog
   entry (`### Merged <date> — main @ <sha>`) under `## Unreleased` in
   `changes/incubating.md`.
2. **`/koan.release`** (skill, human-confirmed version) — rolls everything under
   `## Unreleased` into a `## <version>` section of `changes/stable.md`, resets
   the incubating journal, commits on `incubating`, fast-forwards the `stable`
   branch, pushes, then dispatches the release workflow:
   `gh workflow run release.yml --ref stable -f version=<version>`.
3. **`release.yml`** (GitHub Actions, `workflow_dispatch`) — refuses to run from
   any ref other than `incubating` or `stable`; validates the version format and
   tag uniqueness; **extracts the release notes from the `## <version>` section
   of `changes/stable.md`** (it fails if the section is missing — it never falls
   back to a raw git log); creates the annotated tag, moves the `stable` tag,
   publishes the GitHub release, and builds/pushes the Docker images (semver
   tags, `latest`, `stable`).

The ad-hoc `publish-container.yml` workflow remains the out-of-band channel for
publishing dev images (`devel`, `pr-123`, …) without cutting a release.

## Version scheme

Currently `v0.NN` (single minor). When we hit 1.0, switch to semver `vX.Y.Z`:

- **patch** (`Z`) — fixes, docs, internal refactors
- **minor** (`Y`) — new features, backward-compatible
- **major** (`X`) — breaking changes (config format, skill API, etc.)

## Hotfix on stable

If stable needs a fix and `main` has unreleasable work in flight:

```bash
git checkout -b hotfix/xyz stable
# fix + commit
git checkout main && git cherry-pick hotfix/xyz
# merge PR to main, then run /koan.incubate followed by /koan.release
```

Do not commit directly to `stable`. It must only ever be a fast-forward of a
tagged commit on `incubating`.

## Legacy: `make release`

The old `make release` target (`scripts/release.sh`) tagged directly from `main`
with a Claude-generated changelog. It predates the incubating pipeline and is
**deprecated**: it bypasses the incubate validation pass and the curated
changelog. Use `/koan.incubate` + `/koan.release` instead.

## Recovery

- **Bad tag pushed** — `git tag -d vX.Y && git push origin :refs/tags/vX.Y && gh release delete vX.Y`. Then re-dispatch the release workflow.
- **`stable` diverged** — reset it to the latest tag: `git branch -f stable vX.Y && git push --force-with-lease origin stable`. Force-push is acceptable on `stable` *only* to realign it with a tag.
