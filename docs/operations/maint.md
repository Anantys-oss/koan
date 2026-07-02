# Maintenance & Release

## Philosophy

Kōan has two channels:

- **`main`** — bleeding edge. Every merged PR lands here. Contributors and adventurous users track this branch.
- **`stable`** — contains *only* tagged releases. Fast-forwarded at each `make release`. Users who want a predictable experience track this.

A release is cut **when `main` is healthy and something worth shipping has landed** — not on a fixed cadence. Typical triggers:

- A noteworthy feature is merged and validated.
- A cluster of fixes / polish commits has accumulated (roughly 5–20 commits since the last tag).
- A bug fix on `main` is important enough that stable users need it now.

Do **not** release if:

- The test suite is not 100% green.
- Work-in-progress is merged behind feature flags that aren't ready.
- You haven't actually run the code in your own instance since the last tag.

The human decides. `make release` just enforces the hygiene.

## Procedure

```bash
make release
```

What it does, in order:

1. **Preflight** — must be on `main`, clean tree, synced with `origin/main`, `gh` authenticated.
2. **`make test-strict`** — full pytest run. Any failure aborts the release.
3. **Version prompt** — suggests the next patch bump (e.g. `v0.61` → `v0.62`). You can type any valid `vX.Y` or `vX.Y.Z`.
4. **Changelog** — invokes Claude (Haiku) on `git log <last-tag>..HEAD` to produce a categorized markdown changelog. Falls back to the raw commit list if Claude is unavailable. You can edit it before proceeding.
5. **Confirmation** — nothing is pushed until you confirm.
6. **Tag + push** — `git tag -a vX.Y.Z` with the changelog as the message, then `git push origin vX.Y.Z`.
7. **Fast-forward `stable`** — points `stable` at the new tag and pushes. Creates the branch if it doesn't exist yet.
8. **GitHub release** — `gh release create ... --latest` with the changelog.

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
# merge PR to main, then:
make release   # on main, will fast-forward stable
```

Do not commit directly to `stable`. It must only ever be a fast-forward of a tagged commit on `main`.

## Skill evals (CI)

Prompt-driven skills (like `/review`) produce stochastic LLM output, so their
*quality* can't be unit-tested directly. Instead they ship a **contract eval**
that runs in the normal `fast` CI group as plain pytest — no API token needed.

For `/review` (`tests/test_review_eval.py`, design in
`skills/core/review/eval/PLAN.md`):

- **Prompt-contract** — catches prompt drift: broken `{@include}` partials, or a
  JSON-output prompt that lost its `valid JSON` directive / severity vocabulary.
- **Golden-output anchors** — curated fixtures in `skills/core/review/eval/fixtures/`
  that must stay schema-valid and semantically consistent.
- **Semantic invariants** — `app/review_eval.evaluate_review()` enforces cross-field
  rules (`critical`/`warning` ⇒ `lgtm:false`, `finding_refs` in range) the JSON
  schema can't express.

A model-driven *quality* eval (golden diffs with planted bugs, scored by rubric)
is designed but not built — it needs an API key and so can't run in CI. See the
plan doc.

## Recovery

- **Bad tag pushed** — `git tag -d vX.Y && git push origin :refs/tags/vX.Y && gh release delete vX.Y`. Then re-run `make release`.
- **`stable` diverged** — reset it to the latest tag: `git branch -f stable vX.Y && git push --force-with-lease origin stable`. Force-push is acceptable on `stable` *only* to realign it with a tag.
