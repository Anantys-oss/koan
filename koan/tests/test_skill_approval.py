"""Tests for app/skill_approval.py — the approval gate for newly installed
or scaffolded skills.

The behaviours covered here are the foundation of the security fix in
audit finding §3: any skill whose directory (or ancestor up to the skills
root) carries a ``.koan-pending`` marker MUST be skipped at registry-build
time, and the operator MUST be able to clear that marker only by supplying
a matching fingerprint.
"""

from pathlib import Path

import pytest

from app.skill_approval import (
    MARKER_NAME,
    clear_pending,
    compute_fingerprint,
    find_pending_ancestor,
    mark_pending,
    read_pending_fingerprint,
    resolve_pending_dir,
)


def _make_skill(dir_path: Path, *, name: str = "hello", body: str = "") -> Path:
    """Build a minimal skill dir and return its SKILL.md path."""
    dir_path.mkdir(parents=True, exist_ok=True)
    skill_md = dir_path / "SKILL.md"
    skill_md.write_text(
        "---\n"
        f"name: {name}\n"
        "description: x\n"
        "version: 1.0.0\n"
        "audience: bridge\n"
        "commands:\n"
        f"  - name: {name}\n"
        "    description: x\n"
        "handler: handler.py\n"
        "---\n"
    )
    (dir_path / "handler.py").write_text(
        body or "def handle(ctx):\n    return 'ok'\n"
    )
    return skill_md


# ---------------------------------------------------------------------------
# compute_fingerprint
# ---------------------------------------------------------------------------

class TestComputeFingerprint:
    def test_deterministic_across_calls(self, tmp_path):
        d = tmp_path / "scope" / "skill"
        _make_skill(d)
        fp1 = compute_fingerprint(d)
        fp2 = compute_fingerprint(d)
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex

    def test_changes_when_file_modified(self, tmp_path):
        d = tmp_path / "scope" / "skill"
        _make_skill(d)
        before = compute_fingerprint(d)
        (d / "handler.py").write_text("def handle(ctx):\n    return 'pwned'\n")
        after = compute_fingerprint(d)
        assert before != after

    def test_changes_when_file_added(self, tmp_path):
        d = tmp_path / "scope" / "skill"
        _make_skill(d)
        before = compute_fingerprint(d)
        (d / "extra.txt").write_text("hi")
        after = compute_fingerprint(d)
        assert before != after

    def test_marker_file_ignored(self, tmp_path):
        """Writing the marker MUST NOT change the fingerprint, otherwise
        mark-then-approve would race itself."""
        d = tmp_path / "scope" / "skill"
        _make_skill(d)
        fp = compute_fingerprint(d)
        (d / MARKER_NAME).write_text("anything")
        assert compute_fingerprint(d) == fp


# ---------------------------------------------------------------------------
# mark / clear / read
# ---------------------------------------------------------------------------

class TestMarker:
    def test_round_trip(self, tmp_path):
        d = tmp_path / "scope" / "skill"
        _make_skill(d)
        mark_pending(d, "abc123")
        assert (d / MARKER_NAME).is_file()
        assert read_pending_fingerprint(d) == "abc123"

    def test_clear_is_idempotent(self, tmp_path):
        d = tmp_path / "scope" / "skill"
        _make_skill(d)
        clear_pending(d)  # no-op
        mark_pending(d, "xyz")
        clear_pending(d)
        clear_pending(d)
        assert not (d / MARKER_NAME).exists()
        assert read_pending_fingerprint(d) is None


# ---------------------------------------------------------------------------
# find_pending_ancestor
# ---------------------------------------------------------------------------

class TestFindPendingAncestor:
    def test_no_marker_returns_none(self, tmp_path):
        skills_root = tmp_path / "skills"
        skill_md = _make_skill(skills_root / "scope" / "skill")
        assert find_pending_ancestor(skill_md, skills_root) is None

    def test_marker_at_skill_dir(self, tmp_path):
        skills_root = tmp_path / "skills"
        skill_dir = skills_root / "scope" / "skill"
        skill_md = _make_skill(skill_dir)
        mark_pending(skill_dir, "fp")
        found = find_pending_ancestor(skill_md, skills_root)
        assert found == skill_dir.resolve()

    def test_marker_at_scope_dir(self, tmp_path):
        skills_root = tmp_path / "skills"
        scope_dir = skills_root / "scope"
        skill_md = _make_skill(scope_dir / "skill")
        mark_pending(scope_dir, "fp")
        found = find_pending_ancestor(skill_md, skills_root)
        assert found == scope_dir.resolve()

    def test_does_not_climb_above_skills_root(self, tmp_path):
        """Even if the *grandparent* of skills_root has a marker, the
        function must stop at skills_root."""
        skills_root = tmp_path / "skills"
        skill_md = _make_skill(skills_root / "scope" / "skill")
        # Marker placed *outside* the skills root
        (tmp_path / MARKER_NAME).write_text("not-relevant")
        assert find_pending_ancestor(skill_md, skills_root) is None


# ---------------------------------------------------------------------------
# resolve_pending_dir
# ---------------------------------------------------------------------------

class TestResolvePendingDir:
    def test_scope_form(self, tmp_path):
        instance = tmp_path / "instance"
        scope_dir = instance / "skills" / "ops"
        _make_skill(scope_dir / "deploy")
        mark_pending(scope_dir, "fp")
        resolved = resolve_pending_dir(instance, "ops")
        assert resolved == scope_dir.resolve()

    def test_scope_slash_name_form(self, tmp_path):
        instance = tmp_path / "instance"
        skill_dir = instance / "skills" / "ops" / "deploy"
        _make_skill(skill_dir)
        mark_pending(skill_dir, "fp")
        resolved = resolve_pending_dir(instance, "ops/deploy")
        assert resolved == skill_dir.resolve()

    @pytest.mark.parametrize("evil", [
        "../etc",
        "/etc/passwd",
        "ops/../../../etc",
        "ops/sub/extra",  # too deep
        "",
        "  ",
        "ops/",
        "/ops",
    ])
    def test_rejects_malformed_or_traversing_refs(self, tmp_path, evil):
        instance = tmp_path / "instance"
        (instance / "skills").mkdir(parents=True)
        assert resolve_pending_dir(instance, evil) is None

    def test_returns_none_when_no_marker(self, tmp_path):
        instance = tmp_path / "instance"
        _make_skill(instance / "skills" / "ops" / "deploy")
        # Directory exists but no marker → not "pending"
        assert resolve_pending_dir(instance, "ops") is None
        assert resolve_pending_dir(instance, "ops/deploy") is None

    def test_returns_none_for_unknown_ref(self, tmp_path):
        instance = tmp_path / "instance"
        (instance / "skills").mkdir(parents=True)
        assert resolve_pending_dir(instance, "missing") is None
