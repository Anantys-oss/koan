from pathlib import Path

import app.project_koan as pk


def test_general_absent_returns_empty(tmp_path):
    assert pk.read_general_koan_md(str(tmp_path)) == ""


def test_general_empty_project_path(tmp_path):
    assert pk.read_general_koan_md("") == ""


def test_general_root_only(tmp_path):
    (tmp_path / "KOAN.md").write_text("root rules")
    out = pk.read_general_koan_md(str(tmp_path))
    assert "root rules" in out
    assert ".koan/KOAN.md" not in out


def test_general_both_sources_concatenated(tmp_path):
    (tmp_path / "KOAN.md").write_text("root rules")
    koan_dir = tmp_path / ".koan"
    koan_dir.mkdir()
    (koan_dir / "KOAN.md").write_text("dot-koan rules")
    out = pk.read_general_koan_md(str(tmp_path))
    assert out.index("root rules") < out.index("# .koan/KOAN.md")
    assert "dot-koan rules" in out


def test_general_dot_only(tmp_path):
    koan_dir = tmp_path / ".koan"
    koan_dir.mkdir()
    (koan_dir / "KOAN.md").write_text("dot-koan rules")
    out = pk.read_general_koan_md(str(tmp_path))
    assert "dot-koan rules" in out
    assert "# .koan/KOAN.md" in out


def test_general_blank_ignored(tmp_path):
    (tmp_path / "KOAN.md").write_text("   \n\t\n")
    assert pk.read_general_koan_md(str(tmp_path)) == ""


def test_general_combined_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(pk, "_MAX_KOAN_MD_CHARS", 20)
    (tmp_path / "KOAN.md").write_text("x" * 50)
    out = pk.read_general_koan_md(str(tmp_path))
    assert "truncated" in out


def test_skill_absent_returns_empty(tmp_path):
    assert pk.read_skill_instructions(str(tmp_path), "review") == ""


def test_skill_empty_args(tmp_path):
    assert pk.read_skill_instructions("", "review") == ""
    assert pk.read_skill_instructions(str(tmp_path), "") == ""


def test_skill_sorted_with_provenance(tmp_path):
    d = tmp_path / ".koan" / "skills" / "review"
    d.mkdir(parents=True)
    (d / "b.md").write_text("second")
    (d / "a.md").write_text("first")
    (d / "notes.txt").write_text("ignored")
    out = pk.read_skill_instructions(str(tmp_path), "review")
    assert out.index("# a.md") < out.index("# b.md")
    assert "ignored" not in out


def test_skill_ignores_subdirs(tmp_path):
    d = tmp_path / ".koan" / "skills" / "review"
    d.mkdir(parents=True)
    (d / "a.md").write_text("keep")
    sub = d / "nested.md"
    sub.mkdir()
    out = pk.read_skill_instructions(str(tmp_path), "review")
    assert "keep" in out


def test_skill_all_blank_returns_empty(tmp_path):
    d = tmp_path / ".koan" / "skills" / "review"
    d.mkdir(parents=True)
    (d / "a.md").write_text("  \n")
    assert pk.read_skill_instructions(str(tmp_path), "review") == ""


def test_skill_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(pk, "_MAX_KOAN_SKILL_CHARS", 20)
    d = tmp_path / ".koan" / "skills" / "review"
    d.mkdir(parents=True)
    (d / "a.md").write_text("y" * 50)
    out = pk.read_skill_instructions(str(tmp_path), "review")
    assert "truncated" in out


def test_log_context_load_emits_chars_and_tokens(capsys):
    pk.log_context_load("KOAN.md", "x" * 35)
    err = capsys.readouterr().err
    assert "Detected KOAN.md" in err
    assert "35 chars" in err
    assert "tokens" in err  # ~ 10 tokens at chars/3.5


def test_log_context_load_never_raises(capsys, monkeypatch):
    # A broken token estimator must not break prompt assembly.
    import app.diff_compressor as dc

    def _boom(_):
        raise RuntimeError("estimator down")

    monkeypatch.setattr(dc, "estimate_tokens", _boom)
    pk.log_context_load("KOAN.md", "content")  # must not raise


# --- read_repo_convention_docs ---


def test_conventions_absent_returns_empty(tmp_path):
    assert pk.read_repo_convention_docs(str(tmp_path)) == ""


def test_conventions_empty_project_path():
    assert pk.read_repo_convention_docs("") == ""


def test_conventions_well_known_priority(tmp_path):
    (tmp_path / "AGENTS.md").write_text("agents guide")
    (tmp_path / "CONTRIBUTING.md").write_text("contrib guide")
    out = pk.read_repo_convention_docs(str(tmp_path))
    assert "# AGENTS.md" in out and "# CONTRIBUTING.md" in out
    assert out.index("# AGENTS.md") < out.index("# CONTRIBUTING.md")


def test_conventions_symlink_dedupe(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("shared body ZZZ")
    try:
        (tmp_path / "AGENTS.md").symlink_to(tmp_path / "CLAUDE.md")
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks unsupported on this platform")
    out = pk.read_repo_convention_docs(str(tmp_path))
    # AGENTS.md and CLAUDE.md resolve to the same file — read exactly once.
    assert out.count("shared body ZZZ") == 1


def test_conventions_okf_bundle_detected(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text("bundle index")
    (docs / "SPEC.md").write_text("spec rules")
    (docs / "SCHEMA.md").write_text("schema rules")
    out = pk.read_repo_convention_docs(str(tmp_path))
    assert "docs/index.md" in out
    assert "spec rules" in out and "schema rules" in out


def test_conventions_okf_not_detected_without_index(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "SPEC.md").write_text("spec rules")
    # No docs/index.md -> not an OKF bundle -> SPEC.md not injected.
    assert pk.read_repo_convention_docs(str(tmp_path)) == ""


def test_conventions_topic_indexes_included_pages_excluded(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text("root index")
    arch = docs / "architecture"
    arch.mkdir()
    (arch / "index.md").write_text("arch catalog")
    (arch / "overview.md").write_text("FULL PAGE BODY")
    out = pk.read_repo_convention_docs(str(tmp_path))
    assert "docs/architecture/index.md" in out and "arch catalog" in out
    assert "FULL PAGE BODY" not in out


def test_conventions_topic_indexes_toggle(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text("root index")
    arch = docs / "architecture"
    arch.mkdir()
    (arch / "index.md").write_text("arch catalog")
    out = pk.read_repo_convention_docs(str(tmp_path), include_topic_indexes=False)
    assert "arch catalog" not in out


def test_conventions_block_cap(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("y" * 100)
    out = pk.read_repo_convention_docs(str(tmp_path), max_block_chars=30)
    assert "truncated" in out


def test_conventions_topic_index_glob_error_is_logged(tmp_path):
    """A glob failure while enumerating topic indexes must be observable, not
    swallowed silently — the root OKF docs are still returned (fail-open)."""
    from unittest.mock import patch

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text("root index")
    (docs / "SPEC.md").write_text("spec rules")
    with patch("app.project_koan.logger") as mock_logger, \
         patch("pathlib.Path.glob", side_effect=OSError("perm denied")):
        out = pk.read_repo_convention_docs(str(tmp_path))
    assert "spec rules" in out  # fail-open: other docs still ingested
    assert mock_logger.warning.called
