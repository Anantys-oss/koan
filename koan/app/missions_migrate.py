"""One-shot CLI to migrate ``missions.md`` into ``instance/missions.db``.

Defaults to ``--dry-run``: it parses the file, reports how many rows would be
inserted per state, and lists entries that could not be parsed for manual
intervention. Pass ``--apply`` to actually write rows (via ``reconcile()``,
which truncates and rebuilds so the migration is idempotent).

Usage::

    python3 -m app.missions_migrate [instance_dir] [--apply]
"""

from __future__ import annotations

import sys
from pathlib import Path

from app import missions_db


def main(argv: list) -> int:
    apply = "--apply" in argv
    positional = [a for a in argv if not a.startswith("--")]
    instance = positional[0] if positional else "instance"

    if not (Path(instance) / "missions.md").exists():
        print(f"No missions.md found at {instance}/ — nothing to migrate.")
        return 1

    if apply:
        report = missions_db.reconcile(instance)
        print(f"Applied: inserted {report['inserted']} row(s) into {instance}/missions.db")
        for state in missions_db._VALID_STATES:
            print(f"  {state}: {missions_db.mission_count_by_state(instance, state)}")
    else:
        report = missions_db.migrate_md_to_sqlite(instance, dry_run=True)
        print(f"[dry-run] Would migrate {instance}/missions.md → {instance}/missions.db")
        print(f"  total: {report['inserted']}")
        for state in missions_db._VALID_STATES:
            n = report["by_state"].get(state, 0)
            print(f"  {state}: {n}")
    unparseable = report["unparseable"]

    if unparseable:
        print(f"\nManual intervention needed ({len(unparseable)} entr(ies)):")
        for u in unparseable:
            print(f"  - {u}")
    if not apply:
        print("\nRe-run with --apply to write the rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
