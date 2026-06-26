"""Tests for the REST API server entrypoint (app/api/server.py main())."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from app.api import server


@pytest.fixture
def fake_waitress():
    """Inject a stub waitress module so main() doesn't actually block."""
    mod = MagicMock()
    with patch.dict(sys.modules, {"waitress": mod}):
        yield mod


@pytest.fixture
def patched_config():
    """Default config getters returning loopback host + a token."""
    with patch.multiple(
        "app.config",
        get_api_host=MagicMock(return_value="127.0.0.1"),
        get_api_port=MagicMock(return_value=8420),
        get_api_token=MagicMock(return_value="secret-token"),
        get_api_threads=MagicMock(return_value=8),
    ):
        yield


def _run_main(argv=None):
    with patch.object(sys, "argv", ["server.py", *(argv or [])]):
        server.main()


class TestKoanRoot:
    def test_missing_koan_root_exits(self, monkeypatch, patched_config, capsys):
        # patched_config supplies a valid token so the KOAN_ROOT guard is the
        # ONLY possible exit reason — pinning the branch the PR claims to cover.
        # Asserting on the stderr message proves the KOAN_ROOT guard fired, not
        # the token guard (which `Path("")` -> "." would otherwise slip past).
        monkeypatch.delenv("KOAN_ROOT", raising=False)
        with pytest.raises(SystemExit) as exc:
            _run_main()
        assert exc.value.code == 1
        assert "KOAN_ROOT must be set" in capsys.readouterr().err

    def test_koan_root_not_a_dir_exits(self, monkeypatch, tmp_path):
        bogus = tmp_path / "does-not-exist"
        monkeypatch.setenv("KOAN_ROOT", str(bogus))
        with pytest.raises(SystemExit) as exc:
            _run_main()
        assert exc.value.code == 1


class TestToken:
    def test_no_token_exits(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        with patch.multiple(
            "app.config",
            get_api_host=MagicMock(return_value="127.0.0.1"),
            get_api_port=MagicMock(return_value=8420),
            get_api_token=MagicMock(return_value=""),
            get_api_threads=MagicMock(return_value=8),
        ), pytest.raises(SystemExit) as exc:
            _run_main()
        assert exc.value.code == 1


class TestWaitressMissing:
    def test_missing_waitress_exits(self, monkeypatch, tmp_path, patched_config):
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        # sys.modules entry of None makes `import waitress` raise ImportError
        with patch.dict(sys.modules, {"waitress": None}), pytest.raises(SystemExit) as exc:
            _run_main()
        assert exc.value.code == 1


class TestServe:
    def test_success_path_serves(self, monkeypatch, tmp_path, patched_config, fake_waitress):
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        fake_app = object()
        with patch("app.api.create_app", return_value=fake_app) as mk, \
             patch("app.pid_manager.acquire_pid") as acq:
            _run_main()
        mk.assert_called_once()
        acq.assert_called_once()
        fake_waitress.serve.assert_called_once_with(
            fake_app, host="127.0.0.1", port=8420, threads=8
        )

    def test_cli_overrides_host_and_port(self, monkeypatch, tmp_path, patched_config, fake_waitress):
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        with patch("app.api.create_app", return_value=object()), \
             patch("app.pid_manager.acquire_pid"):
            _run_main(["--host", "0.0.0.0", "--port", "9999"])
        _, kwargs = fake_waitress.serve.call_args
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == 9999

    def test_non_loopback_warns(self, monkeypatch, tmp_path, patched_config, fake_waitress, capsys):
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        with patch("app.api.create_app", return_value=object()), \
             patch("app.pid_manager.acquire_pid"):
            _run_main(["--host", "0.0.0.0"])
        assert "non-loopback" in capsys.readouterr().err

    def test_hostname_skips_loopback_check(self, monkeypatch, tmp_path, patched_config, fake_waitress, capsys):
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        with patch("app.api.create_app", return_value=object()), \
             patch("app.pid_manager.acquire_pid"):
            _run_main(["--host", "example.local"])
        # A hostname (not an IP) must not trip the non-loopback warning
        assert "non-loopback" not in capsys.readouterr().err
        fake_waitress.serve.assert_called_once()

    def test_pid_error_is_non_fatal(self, monkeypatch, tmp_path, patched_config, fake_waitress, capsys):
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        with patch("app.api.create_app", return_value=object()), \
             patch("app.pid_manager.acquire_pid", side_effect=OSError("boom")):
            _run_main()
        # Serve still happens despite the PID file failure
        fake_waitress.serve.assert_called_once()
        assert "PID file error" in capsys.readouterr().err
