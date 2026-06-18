#!/usr/bin/env python3
"""
Kōan -- Recurring mission scheduler (CLI entry point)

Called at the top of each loop iteration to inject
due recurring missions into the pending queue.

Usage:
    python3 recurring_scheduler.py <instance_dir>

Output:
    Prints each injected mission (for logging).
    Exit code 0 always (errors are non-fatal).
"""

import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: recurring_scheduler.py <instance_dir>", file=sys.stderr)
        sys.exit(1)

    instance_dir = Path(sys.argv[1])
    recurring_path = instance_dir / "recurring.json"

    if not recurring_path.exists():
        sys.exit(0)

    try:
        from app.recurring import check_and_inject
        injected = check_and_inject(recurring_path, instance_dir)
        for desc in injected:
            print(f"[recurring] Injected: {desc}")
    except Exception as e:
        print(f"[recurring] Error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
