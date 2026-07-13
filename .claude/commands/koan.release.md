Cut a stable Kōan release: roll every `## Unreleased` entry from `changes/incubating.md` into `changes/stable.md` under a version, fast-forward `stable` from `incubating`, and dispatch the `release.yml` GitHub Actions workflow which does the tagging, GitHub release (notes = the curated changelog), and Docker publishing.

**Never release from `main`** — `main` is the unstable integration branch; `incubating` is the authority (validated by incubate reviews, documented in the journal). The workflow itself refuses any ref other than `incubating`/`stable`.

Run this from the Kōan repo root.

Arguments:
- `$ARGUMENTS` — optional version (e.g. `v1.4.0`). If empty, propose the next version from the latest tag and confirm with the user.

## Pre-flight

1. **Confirm repo & clean tree**:
   ```bash
   git rev-parse --show-toplevel
   git status --porcelain          # must be clean — else STOP
   git fetch origin --prune --tags
   ```
2. **Ensure `incubating` is current**:
   ```bash
   git checkout incubating
   git pull --ff-only origin incubating 2>/dev/null || true
   ```

## Determine the version

3. Latest tag: `git tag --list 'v*' --sort=-v:refname | head -1`.
   - If `$ARGUMENTS` gives a version, use it (must be > latest tag).
   - Else propose the next semver bump (patch by default; minor if the unreleased journal has any `**Features**`) and **confirm with the user**.

## Roll the changelog

4. Read `changes/incubating.md`. Collect **everything under `## Unreleased`** (all `### Merged …` entries). If empty, STOP — nothing to release.
5. Prepend a new section to `changes/stable.md` (create the file with a title if missing):
   ```markdown
   ## <version> — <YYYY-MM-DD>

   <all the collected Unreleased entries, verbatim>
   ```
   Newest release on top. Use `date -u +%Y-%m-%d`. The `## <version>` heading must match the version exactly — `release.yml` extracts this section as the GitHub release notes and **fails if it's missing**.
6. **Reset** `changes/incubating.md`: keep the header/intro and leave a single empty `## Unreleased` section (no entries).
7. Commit the changelog rotation on `incubating`:
   ```bash
   git add changes/incubating.md changes/stable.md
   git commit -m "docs(changes): release <version>"
   ```

## Promote

8. Fast-forward `stable` from `incubating` (create it if missing):
   ```bash
   git checkout stable 2>/dev/null || git checkout -b stable
   git merge --ff-only incubating || git merge incubating --no-edit
   git checkout incubating
   ```

## Push & dispatch the release workflow

9. Show what will be pushed and **ask before pushing** (release is outward-facing):
   ```bash
   git push origin incubating stable
   ```
10. Dispatch the workflow — it validates the version, refuses non-`incubating`/`stable` refs, extracts the `## <version>` notes from `changes/stable.md`, creates the annotated tag + `stable` tag, publishes the GitHub release, and pushes the Docker images:
    ```bash
    gh workflow run release.yml --ref stable -f version=<version>
    ```
11. Watch it: `gh run watch $(gh run list --workflow=release.yml --limit 1 --json databaseId -q '.[0].databaseId')` — report the result and the release URL.

## Rules

- **Confirm the version** before writing anything.
- **Never invent changelog entries** — only move what already exists under `## Unreleased`.
- **Never tag locally and never release from `main`** — tagging and publishing belong to `release.yml`, dispatched on `stable`.
- **Ask before pushing** branches.
- English only in all commits and changelog text.
