from pathlib import Path

from app.log_reader import read_logs, tail_log


def _write(p: Path, lines):
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_tail_log_numbers_and_limits(tmp_path):
    log = tmp_path / "run.log"
    _write(log, [f"line{i}" for i in range(1, 11)])
    out = tail_log(log, 3)
    assert [e["n"] for e in out] == [8, 9, 10]
    assert out[0]["text"] == "line8"


def test_tail_log_missing_file_returns_empty(tmp_path):
    assert tail_log(tmp_path / "nope.log", 5) == []


def test_read_logs_source_run_only(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    _write(logs / "run.log", ["r1", "r2"])
    _write(logs / "awake.log", ["a1"])
    result = read_logs(tmp_path, source="run", limit=10, q="")
    assert {e["source"] for e in result["lines"]} == {"run"}
    assert result["total"] == 2


def test_read_logs_substring_filter(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    _write(logs / "run.log", ["ERROR boom", "info ok"])
    _write(logs / "awake.log", ["awake ERROR here"])
    result = read_logs(tmp_path, source="all", limit=100, q="error")
    assert result["total"] == 2
    assert all("error" in e["text"].lower() for e in result["lines"])
