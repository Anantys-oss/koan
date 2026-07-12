"""S8 foundation: full-content render + round-trip fidelity.

The S8 write path is render_content -> transform -> reconcile_all -> export.
This suite proves the store faithfully round-trips a full missions.md (all
sections, incl. CI and Ideas) so that path preserves behavior.
"""

from app import missions
from app.mission_store.sqlite_store import SqliteMissionStore
from app.mission_store.transition import reconcile_all

FULL = (
    "# Missions\n\n"
    "## CI\n"
    "- [project:koan] https://github.com/o/r/pull/7 branch:b repo:o/r "
    "queued:2026-07-01T10:00 (attempt 1/5)\n\n"
    "## Ideas\n- a good idea\n\n"
    "## Pending\n- do thing [project:koan] ⏳(2026-07-01T09:00)\n\n"
    "## In Progress\n- running [project:web] ⏳(2026-07-01T09:01) ▶(2026-07-01T09:05)\n\n"
    "## Done\n- finished ✅ (2026-06-30 12:00)\n\n"
    "## Failed\n- broke ❌ (2026-06-29 08:00)\n"
)


def test_render_content_semantic_fidelity(tmp_path):
    inst = str(tmp_path)
    reconcile_all(inst, FULL)
    rendered = SqliteMissionStore(inst).render_content()

    sec = missions.parse_sections(rendered)
    assert missions.count_pending(rendered) == 1
    assert len(sec["in_progress"]) == 1
    assert len(sec["done"]) == 1
    assert len(sec["failed"]) == 1
    assert len(missions.get_ci_items(rendered)) == 1
    assert missions.parse_ideas(rendered) == ["- a good idea"]
    # timestamps survive the round-trip in the parsers' expected formats
    ip = sec["in_progress"][0]
    assert missions.extract_timestamps(ip)["started"] is not None
    done = sec["done"][0]
    assert missions.extract_timestamps(done)["completed"] is not None


def test_render_content_is_idempotent(tmp_path):
    inst = str(tmp_path)
    reconcile_all(inst, FULL)
    once = SqliteMissionStore(inst).render_content()
    # Feed the render back through the whole cycle: it must be a fixed point.
    reconcile_all(inst, once)
    twice = SqliteMissionStore(inst).render_content()
    assert once == twice


def test_render_content_survives_a_transform(tmp_path):
    # Simulate the S8 write path: render -> transform -> reconcile -> render.
    inst = str(tmp_path)
    reconcile_all(inst, FULL)
    content = SqliteMissionStore(inst).render_content()
    # Insert a new pending mission via the existing content transform.
    content2 = missions.insert_mission(content, "- brand new [project:koan]")
    reconcile_all(inst, content2)
    out = SqliteMissionStore(inst).render_content()
    assert missions.count_pending(out) == 2
    assert "brand new" in out
    # CI + Ideas + terminal states preserved through the transform.
    assert len(missions.get_ci_items(out)) == 1
    assert missions.parse_ideas(out) == ["- a good idea"]
    assert len(missions.parse_sections(out)["done"]) == 1


def test_export_view_writes_full_content(tmp_path):
    inst = str(tmp_path)
    reconcile_all(inst, FULL)
    store = SqliteMissionStore(inst)
    out = tmp_path / "export.md"
    store.export_view(out)
    assert out.read_text() == store.render_content()
    assert "## CI" in out.read_text() and "## Ideas" in out.read_text()
