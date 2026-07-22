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
