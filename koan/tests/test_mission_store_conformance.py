"""Abstract conformance suite for the MissionStore port.

Parametrized over every backend: the in-tree SqliteMissionStore and an in-repo
in-memory reference adapter. The reference adapter doubles as proof of the
out-of-tree seam (a store Kōan doesn't ship, loaded only by the tests) and as a
store-agnostic double for callsite refactors. The suite is the executable
definition of the contract in specs/004-mission-store/contracts/mission-store.md.
"""

import threading

import pytest

from app.mission_store.base import MissionStore, Mission


class InMemoryMissionStore(MissionStore):
    """A minimal reference MissionStore (test double + out-of-tree seam proof)."""

    def __init__(self, instance=None):
        self._rows = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._initialized = False

    def backend_name(self):
        return "memory"

    def _mk(self, row):
        return Mission(id=str(row["id"]), text=row["text"], state=row["state"],
                       project=row["project"], sequence=row["sequence"],
                       complexity=row["complexity"], queued_at=row["queued_at"],
                       started_at=row["started_at"], completed_at=row["completed_at"])

    def _tail(self):
        return (max((r["sequence"] for r in self._rows), default=0)) + 1

    def _head(self):
        return (min((r["sequence"] for r in self._rows), default=0)) - 1

    def add_pending(self, text, *, project="default", complexity=None, urgent=False):
        with self._lock:
            row = {"id": self._next_id, "text": text, "state": "pending",
                   "project": project or "default",
                   "sequence": self._head() if urgent else self._tail(),
                   "complexity": complexity, "queued_at": "t", "started_at": None,
                   "completed_at": None, "recovery_count": 0}
            self._next_id += 1
            self._rows.append(row)
            return self._mk(row)

    def add_pending_many(self, texts, *, project="default"):
        return [self.add_pending(t, project=project) for t in texts]

    def claim_next(self, *, projects=None):
        with self._lock:
            cands = [r for r in self._rows if r["state"] == "pending"
                     and (not projects or r["project"] in projects)]
            if not cands:
                return None
            row = min(cands, key=lambda r: r["sequence"])
            row["state"] = "in_progress"
            row["started_at"] = "t"
            return self._mk(row)

    def _find(self, mission_id):
        return next((r for r in self._rows if str(r["id"]) == str(mission_id)), None)

    def _finalize(self, mission_id, state, ts):
        row = self._find(mission_id)
        if row is None or row["state"] in ("done", "failed"):
            return False
        row["state"] = state
        row[ts] = "t"
        return True

    def complete(self, mission_id):
        return self._finalize(mission_id, "done", "completed_at")

    def fail(self, mission_id):
        return self._finalize(mission_id, "failed", "completed_at")

    def requeue(self, mission_id):
        row = self._find(mission_id)
        if row is None or row["state"] in ("done", "failed"):
            return False
        row["state"] = "pending"
        row["sequence"] = self._head()
        row["started_at"] = None
        return True

    def set_complexity(self, mission_id, complexity):
        row = self._find(mission_id)
        if row is None:
            return False
        row["complexity"] = complexity
        return True

    def count_by_state(self, state):
        return sum(1 for r in self._rows if r["state"] == state)

    def counts(self):
        out = {s: 0 for s in ("pending", "in_progress", "done", "failed")}
        for r in self._rows:
            out[r["state"]] = out.get(r["state"], 0) + 1
        return out

    def list_by_state(self, state, *, project=None, limit=None):
        rows = [r for r in self._rows if r["state"] == state
                and (project is None or r["project"] == project)]
        rev = state in ("done", "failed")
        rows.sort(key=lambda r: (r["sequence"], r["id"]), reverse=rev)
        if limit:
            rows = rows[:limit]
        return [self._mk(r) for r in rows]

    def get(self, mission_id):
        row = self._find(mission_id)
        return self._mk(row) if row else None

    def peek_next(self, *, projects=None):
        cands = [r for r in self._rows if r["state"] == "pending"
                 and (not projects or r["project"] in projects)]
        if not cands:
            return None
        return self._mk(min(cands, key=lambda r: r["sequence"]))

    def prune_terminal(self, done_keep, failed_keep):
        deleted = 0
        for state, keep in (("done", done_keep), ("failed", failed_keep)):
            rows = sorted([r for r in self._rows if r["state"] == state],
                          key=lambda r: r["id"])
            drop = rows if keep <= 0 else rows[:-keep] if keep < len(rows) else []
            for r in drop:
                self._rows.remove(r)
                deleted += 1
        return deleted

    def is_initialized(self):
        return self._initialized

    def ingest_from_file(self, missions_md_path):
        from app.mission_store.base import IngestReport
        self._initialized = True
        return IngestReport()

    def export_view(self, missions_md_path):
        pass

    def recover_stale(self, *, max_recover=3):
        from app.mission_store.base import RecoverReport
        report = RecoverReport()
        for r in self._rows:
            if r["state"] == "in_progress":
                r["state"] = "pending"
                report.requeued += 1
        return report


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path):
    if request.param == "sqlite":
        from app.mission_store.sqlite_store import SqliteMissionStore
        return SqliteMissionStore(str(tmp_path))
    return InMemoryMissionStore()


# ---- contract tests --------------------------------------------------------

def test_add_claim_complete(store):
    m = store.add_pending("do a thing", project="koan")
    assert m.state == "pending" and m.project == "koan"
    claimed = store.claim_next()
    assert claimed.id == m.id and claimed.state == "in_progress"
    assert store.count_by_state("pending") == 0
    assert store.complete(claimed.id) is True
    assert store.count_by_state("done") == 1


def test_claim_none_when_empty(store):
    assert store.claim_next() is None


def test_fifo_order(store):
    a = store.add_pending("first")
    b = store.add_pending("second")
    assert store.claim_next().id == a.id
    assert store.claim_next().id == b.id


def test_urgent_goes_to_front(store):
    store.add_pending("normal")
    urgent = store.add_pending("urgent one", urgent=True)
    assert store.claim_next().id == urgent.id


def test_requeue_to_front_no_duplicate(store):
    a = store.add_pending("a")
    b = store.add_pending("b")
    claimed = store.claim_next()  # a
    assert store.requeue(claimed.id) is True
    assert store.count_by_state("pending") == 2
    assert store.count_by_state("in_progress") == 0
    # requeued mission is back at the front
    assert store.claim_next().id == a.id


def test_stable_identity_rerun_gets_new_id(store):
    a = store.add_pending("recurring task")
    store.complete(store.claim_next().id)
    b = store.add_pending("recurring task")  # identical text, later
    assert a.id != b.id
    assert store.count_by_state("done") == 1
    assert store.count_by_state("pending") == 1


def test_terminal_not_rewritten_by_live_transition(store):
    a = store.add_pending("task")
    store.complete(store.claim_next().id)   # a is done
    # A fresh identical mission; transitioning it must not touch the done row.
    b = store.add_pending("task")
    store.claim_next()
    store.fail(b.id)
    assert store.count_by_state("done") == 1
    assert store.count_by_state("failed") == 1
    # completing an already-terminal id is a no-op
    assert store.complete(a.id) is False


def test_counts_and_list(store):
    store.add_pending("p1", project="koan")
    store.add_pending("p2", project="web")
    c = store.counts()
    assert c["pending"] == 2 and c["in_progress"] == 0
    koan = store.list_by_state("pending", project="koan")
    assert len(koan) == 1 and koan[0].project == "koan"


def test_prune_terminal(store):
    for i in range(4):
        m = store.add_pending(f"d{i}")
        store.complete(store.claim_next().id)
    assert store.count_by_state("done") == 4
    removed = store.prune_terminal(done_keep=2, failed_keep=0)
    assert removed == 2
    assert store.count_by_state("done") == 2


def test_set_complexity_no_new_identity(store):
    m = store.add_pending("classify me")
    assert store.set_complexity(m.id, "high") is True
    assert store.count_by_state("pending") == 1
    assert store.get(m.id).complexity == "high"


def test_peek_does_not_claim(store):
    m = store.add_pending("peek")
    assert store.peek_next().id == m.id
    assert store.count_by_state("pending") == 1  # still pending
    assert store.claim_next().id == m.id


def test_claim_is_atomic_under_concurrency(store):
    n = 25
    for i in range(n):
        store.add_pending(f"m{i}")
    claimed = []
    lock = threading.Lock()

    def worker():
        while True:
            m = store.claim_next()
            if m is None:
                return
            with lock:
                claimed.append(m.id)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == n, "every pending mission claimed exactly once"
    assert len(set(claimed)) == n, "no mission claimed twice"
    assert store.count_by_state("in_progress") == n
