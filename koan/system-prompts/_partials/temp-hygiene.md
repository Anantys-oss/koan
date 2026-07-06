# Temp Files

Kōan runs you with a per-mission `$TMPDIR` that is deleted automatically when the
mission ends. ALL scratch output must be created under it:

- Scratch file: `f=$(mktemp "${TMPDIR:-/tmp}/koan-<purpose>-XXXXXX")`
- Scratch directory (repo checkouts, build trees, large artifacts):
  `d=$(mktemp -d "${TMPDIR:-/tmp}/koan-<purpose>-XXXXXX")`
- Never hardcode bare `/tmp/...` paths or fixed filenames — multiple Kōan instances
  may share this host, and anything created outside `$TMPDIR` is never cleaned up.
