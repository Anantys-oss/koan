"""Tests for bin/ollama-claude — argument translation behavior.

Stubs `ollama` on PATH, runs the wrapper, and asserts the translated argv.
Tests observable behavior (input argv -> output argv), not source code.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "bin" / "ollama-claude"

DEFAULT_MODEL = "qwen2.5-coder:14b"


def _make_fake_ollama(tmp_path: Path):
    """Create a fake `ollama` that records its argv to a file."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    record = tmp_path / "argv.txt"
    fake = bindir / "ollama"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" > "{record}"\n'
    )
    fake.chmod(0o755)
    return bindir, record


def _run(args, env_extra, tmp_path):
    bindir, record = _make_fake_ollama(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
    env.pop("OLLAMA_CLAUDE_MODEL", None)
    env.update(env_extra)
    proc = subprocess.run(
        [str(WRAPPER), *args], env=env, capture_output=True, text=True
    )
    recorded = record.read_text().splitlines() if record.exists() else []
    return proc, recorded


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_explicit_model_is_extracted_and_forwarded(tmp_path):
    proc, argv = _run(
        ["-p", "hello world", "--model", "qwen2.5-coder:14b",
         "--output-format", "json"],
        {},
        tmp_path,
    )
    assert proc.returncode == 0
    # ollama launch claude --model qwen2.5-coder:14b -- -p "hello world" ...
    assert argv[:4] == ["launch", "claude", "--model", "qwen2.5-coder:14b"]
    assert argv[4] == "--"
    assert argv[5:] == ["-p", "hello world", "--output-format", "json"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_model_equals_form_is_supported(tmp_path):
    _, argv = _run(["--model=llama3.1:8b", "-p", "hi"], {}, tmp_path)
    assert argv[:4] == ["launch", "claude", "--model", "llama3.1:8b"]
    assert "--model=llama3.1:8b" not in argv[5:]
    assert argv[5:] == ["-p", "hi"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_falls_back_to_env_var(tmp_path):
    _, argv = _run(["-p", "hi"], {"OLLAMA_CLAUDE_MODEL": "deepseek-coder-v2"},
                   tmp_path)
    assert argv[:4] == ["launch", "claude", "--model", "deepseek-coder-v2"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_falls_back_to_hard_default(tmp_path):
    _, argv = _run(["-p", "hi"], {}, tmp_path)
    assert argv[2:4] == ["--model", DEFAULT_MODEL]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_explicit_model_beats_env_fallback(tmp_path):
    _, argv = _run(
        ["-p", "hi", "--model", "llama3.1:8b"],
        {"OLLAMA_CLAUDE_MODEL": "deepseek-coder-v2"},
        tmp_path,
    )
    assert argv[2:4] == ["--model", "llama3.1:8b"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_remaining_args_forwarded_verbatim(tmp_path):
    _, argv = _run(
        ["--model", "qwen2.5-coder:14b", "--output-format", "stream-json",
         "--verbose"],
        {},
        tmp_path,
    )
    # model stripped, rest forwarded after --
    assert argv[4] == "--"
    rest = argv[5:]
    assert "--output-format" in rest
    assert "stream-json" in rest
    assert "--verbose" in rest
    assert "--model" not in rest


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_fallback_model_is_stripped(tmp_path):
    # ollama launch forwards post-`--` args to the real claude CLI; a sonnet
    # fallback would route to an Anthropic tier Ollama cannot serve, so the
    # wrapper drops --fallback-model (both flag forms).
    _, argv = _run(
        ["-p", "hi", "--model", "qwen2.5-coder:14b",
         "--fallback-model", "sonnet"],
        {},
        tmp_path,
    )
    assert argv[2:4] == ["--model", "qwen2.5-coder:14b"]
    rest = argv[5:]
    assert "--fallback-model" not in rest
    assert "sonnet" not in rest
    assert rest == ["-p", "hi"]

    _, argv = _run(["-p", "hi", "--fallback-model=sonnet"], {}, tmp_path)
    assert "--fallback-model=sonnet" not in argv[5:]
    assert argv[5:] == ["-p", "hi"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_empty_args_after_model_stripping(tmp_path):
    # Every arg consumed as a flag -> args array empty. Must not crash under
    # set -u on bash 3.2.
    proc, argv = _run(["--model", "qwen2.5-coder:14b"], {}, tmp_path)
    assert proc.returncode == 0
    assert argv == ["launch", "claude", "--model", "qwen2.5-coder:14b", "--"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_trailing_model_flag_does_not_abort(tmp_path):
    # --model as the last token (no value) must not trip set -e on shift.
    proc, argv = _run(["-p", "hi", "--model"], {}, tmp_path)
    assert proc.returncode == 0
    assert argv[2:4] == ["--model", DEFAULT_MODEL]
    assert argv[5:] == ["-p", "hi"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_missing_ollama_fails_clearly(tmp_path):
    # Use an empty custom bin dir — but keep bash reachable. Only ollama
    # is absent.
    empty_bin = tmp_path / "empty"
    empty_bin.mkdir()
    bash_dir = str(Path(shutil.which("bash")).parent)
    env = dict(os.environ)
    env["PATH"] = f"{empty_bin}:{bash_dir}"
    proc = subprocess.run(
        [str(WRAPPER), "-p", "hi"], env=env, capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert "ollama" in (proc.stderr + proc.stdout).lower()
