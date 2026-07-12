"""Tests for the sibling stores: CI queue, Ideas, quarantine."""

from app.mission_store.aux_stores import CiQueueStore, IdeaStore, QuarantineStore

PR = "https://github.com/owner/repo/pull/42"
PR2 = "https://github.com/owner/repo/pull/43"


# ---- CI queue --------------------------------------------------------------

def test_ci_add_get_shape(tmp_path):
    ci = CiQueueStore(str(tmp_path))
    ci.add_item("koan", PR, "42", "feat-x", "owner/repo", 5)
    items = ci.get_items()
    assert len(items) == 1
    it = items[0]
    assert it["project"] == "koan" and it["pr_url"] == PR
    assert it["pr_number"] == "42" and it["branch"] == "feat-x"
    assert it["full_repo"] == "owner/repo" and it["attempt"] == 0 and it["max_attempts"] == 5
    assert it["raw_line"].startswith("- [project:koan] ") and "(attempt 0/5)" in it["raw_line"]


def test_ci_add_dedups_and_resets_attempts(tmp_path):
    ci = CiQueueStore(str(tmp_path))
    ci.add_item("koan", PR, "42", "b", "owner/repo", 5)
    ci.update_attempt(PR)
    ci.update_attempt(PR)
    assert ci.get_items()[0]["attempt"] == 2
    # Re-add (e.g. after a rebase force-push) resets to a single entry at attempt 0.
    ci.add_item("koan", PR, "42", "b", "owner/repo", 5)
    items = ci.get_items()
    assert len(items) == 1 and items[0]["attempt"] == 0


def test_ci_update_attempt_caps_at_max(tmp_path):
    ci = CiQueueStore(str(tmp_path))
    ci.add_item("koan", PR, "42", "b", "owner/repo", 2)
    for _ in range(5):
        ci.update_attempt(PR)
    assert ci.get_items()[0]["attempt"] == 2  # never exceeds max_attempts


def test_ci_remove(tmp_path):
    ci = CiQueueStore(str(tmp_path))
    ci.add_item("koan", PR, "42", "b", "owner/repo", 5)
    ci.add_item("koan", PR2, "43", "b2", "owner/repo", 5)
    ci.remove_item(PR)
    items = ci.get_items()
    assert len(items) == 1 and items[0]["pr_url"] == PR2


def test_ci_ingest_and_render_roundtrip(tmp_path):
    src = CiQueueStore(str(tmp_path))
    src.add_item("koan", PR, "42", "b", "owner/repo", 5)
    src.update_attempt(PR)
    items = src.get_items()
    # Fresh store ingests parsed items → identical get_items.
    (tmp_path / "dst").mkdir()
    dst = CiQueueStore(str(tmp_path / "dst"))
    dst.ingest_items(items)
    got = dst.get_items()
    assert got[0]["pr_url"] == PR and got[0]["attempt"] == 1
    assert src.render_lines() == dst.render_lines()


# ---- Ideas -----------------------------------------------------------------

def test_ideas_add_list_delete(tmp_path):
    ideas = IdeaStore(str(tmp_path))
    ideas.add("- first idea")
    ideas.add("- second idea")
    assert ideas.list() == ["- first idea", "- second idea"]
    deleted = ideas.delete(1)
    assert deleted == "- first idea"
    assert ideas.list() == ["- second idea"]


def test_ideas_delete_out_of_range(tmp_path):
    ideas = IdeaStore(str(tmp_path))
    ideas.add("- only")
    assert ideas.delete(2) is None
    assert ideas.delete(0) is None
    assert len(ideas.list()) == 1


def test_ideas_delete_all(tmp_path):
    ideas = IdeaStore(str(tmp_path))
    ideas.add("- a")
    ideas.add("- b")
    popped = ideas.delete_all()
    assert popped == ["- a", "- b"]
    assert ideas.list() == []


def test_ideas_normalizes_leading_dash(tmp_path):
    ideas = IdeaStore(str(tmp_path))
    ideas.add("no dash idea")
    assert ideas.list() == ["- no dash idea"]


# ---- Quarantine ------------------------------------------------------------

def test_quarantine_add_and_list(tmp_path):
    q = QuarantineStore(str(tmp_path))
    assert q.add("evil mission text", "prompt injection", "telegram") is True
    rows = q.list()
    assert len(rows) == 1
    assert rows[0]["reason"] == "prompt injection" and rows[0]["source"] == "telegram"
    assert "evil mission" in rows[0]["text"]
    assert q.render_lines()[0].startswith("- ") and "prompt injection" in q.render_lines()[0]


def test_quarantine_truncates_long_text(tmp_path):
    q = QuarantineStore(str(tmp_path))
    q.add("x" * 1000, "too long", "github")
    assert len(q.list()[0]["text"]) == 500


def test_quarantine_caps_rows(tmp_path):
    from app.mission_store import aux_stores
    q = aux_stores.QuarantineStore(str(tmp_path))
    original = aux_stores._QUARANTINE_KEEP
    aux_stores._QUARANTINE_KEEP = 3
    try:
        for i in range(6):
            q.add(f"m{i}", "reason", "src")
        assert len(q.list()) == 3
    finally:
        aux_stores._QUARANTINE_KEEP = original
