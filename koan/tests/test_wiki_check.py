"""Tests for scripts/wiki_check.py — the wiki-hygiene gap detector.

The checker lives under scripts/ (not on pytest's pythonpath), so it is loaded by
file path via importlib. Focus: the legacy (non --strict) changed-file checks must
skip generated index.md files — wiki_sync_ci.py regenerates them on every run, so
flagging them for frontmatter/wiki-entry gaps makes the CI trip over its own output.
"""

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECK_PATH = _REPO_ROOT / "scripts" / "wiki_check.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("wiki_check", _CHECK_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


class TestConceptPageEligibility:
    def test_reserved_index_md_is_not_a_concept_page(self):
        assert not checker.is_concept_page("docs/architecture/index.md")
        assert not checker.is_concept_page("specs/components/index.md")
        assert not checker.is_concept_page("docs/index.md")

    def test_regular_docs_page_is_a_concept_page(self):
        assert checker.is_concept_page("docs/architecture/daemon.md")
        assert checker.is_concept_page("specs/skills/implement.md")

    def test_speckit_ephemeral_is_not_a_concept_page(self):
        assert not checker.is_concept_page("specs/004-mission-store/spec.md")


class TestRunLegacyChecks:
    def test_generated_index_md_yields_no_findings(self, tmp_path, monkeypatch):
        """Regenerated per-folder index.md files (no frontmatter, no wiki entry by
        design) must not be flagged when they appear in the diff."""
        (tmp_path / "docs" / "architecture").mkdir(parents=True)
        (tmp_path / "docs" / "architecture" / "index.md").write_text(
            "# Architecture\n\n* [Daemon Runtime](daemon.md) - Summary.\n"
        )
        monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
        findings = checker.run_legacy_checks(
            ["docs/architecture/index.md"], index_text="",
        )
        assert findings == []

    def test_concept_page_without_frontmatter_is_still_flagged(self, tmp_path, monkeypatch):
        (tmp_path / "docs" / "architecture").mkdir(parents=True)
        (tmp_path / "docs" / "architecture" / "daemon.md").write_text(
            "# Daemon\n\nBody without frontmatter.\n"
        )
        monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
        findings = checker.run_legacy_checks(
            ["docs/architecture/daemon.md"], index_text="",
        )
        assert any("missing frontmatter block" in f for f in findings)
        assert any("no wiki/index.md entry" in f for f in findings)
