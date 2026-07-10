"""Shared test helper for the mission store (S8: the store is authoritative).

Use ``seed_missions`` in place of ``missions.md.write_text(...)`` whenever a test
sets up mission state and then drives code that reads/writes **through the store**
(pickers, skills, API, recover). It writes the file *and* rebuilds the store from
that content + marks it synced, so store-backed code observes the seeded state.
"""

from pathlib import Path


def seed_missions(instance, content):
    from app.mission_store import get_mission_store
    from app.mission_store.transition import reconcile_all
    inst = Path(instance)
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "missions.md").write_text(content)
    reconcile_all(str(inst), content)
    get_mission_store(str(inst)).mark_synced()
