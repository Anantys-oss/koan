"""Tests for codex_update.py — automatic Codex CLI self-update."""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import codex_update
from app.codex_update import (
    _check_update_available,
    _codex_is_active,
    _due_for_check,
    _load_config,
    check_codex_update,
)


def _patch_config(config_dict):
    """Patch load_config at its source module (lazy import target)."""
    return patch("app.utils.load_config", return_value=config_dict)


def _completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class TestLoadConfig:
    def test_defaults_opt_out(self):
        with _patch_config({}):
            cfg = _load_config()
        assert cfg == {"enabled": True, "notify": True}

    def test_disabled_from_config(self):
        with _patch_config({"codex_update": {"enabled": False}}):
            assert _load_config()["enabled"] is False

    def test_notify_can_be_disabled(self):
        with _patch_config({"codex_update": {"notify": False}}):
            assert _load_config()["notify"] is False

    def test_non_dict_section_treated_as_empty(self):
        with _patch_config({"codex_update": "nope"}):
            assert _load_config() == {"enabled": True, "notify": True}

    def test_config_load_failure_returns_defaults(self):
        with patch("app.utils.load_config", side_effect=Exception("boom")):
            assert _load_config()["enabled"] is True


class TestCodexIsActive:
    def test_true_when_codex(self):
        with patch("app.provider.get_provider_name", return_value="codex"):
            assert _codex_is_active() is True

    def test_false_for_other_provider(self):
        with patch("app.provider.get_provider_name", return_value="claude"):
            assert _codex_is_active() is False

    def test_false_on_error(self):
        with patch("app.provider.get_provider_name", side_effect=Exception("x")):
            assert _codex_is_active() is False


class TestDueForCheck:
    def test_due_when_no_tracker(self, tmp_path):
        assert _due_for_check(str(tmp_path)) is True

    def test_not_due_when_recent(self, tmp_path):
        import time
        (tmp_path / codex_update.TRACKER_FILE).write_text(
            json.dumps({"last_check_ts": time.time()})
        )
        assert _due_for_check(str(tmp_path)) is False

    def test_due_when_stale(self, tmp_path):
        import time
        stale = time.time() - (codex_update.MIN_CHECK_INTERVAL_HOURS + 1) * 3600
        (tmp_path / codex_update.TRACKER_FILE).write_text(
            json.dumps({"last_check_ts": stale})
        )
        assert _due_for_check(str(tmp_path)) is True

    def test_due_when_tracker_corrupt(self, tmp_path):
        (tmp_path / codex_update.TRACKER_FILE).write_text("{not json")
        assert _due_for_check(str(tmp_path)) is True


class TestCheckUpdateAvailable:
    def test_up_to_date(self):
        with patch.object(
            codex_update, "_run_codex",
            return_value=_completed(stdout="Codex is up to date"),
        ):
            assert _check_update_available("codex") is False

    def test_update_available(self):
        with patch.object(
            codex_update, "_run_codex",
            return_value=_completed(stdout="A new version is available: 0.140.0"),
        ):
            assert _check_update_available("codex") is True

    def test_unsupported_flag_is_inconclusive(self):
        with patch.object(
            codex_update, "_run_codex",
            return_value=_completed(stderr="error: unexpected argument '--check' found"),
        ):
            assert _check_update_available("codex") is None

    def test_exception_is_inconclusive(self):
        with patch.object(codex_update, "_run_codex", side_effect=OSError("boom")):
            assert _check_update_available("codex") is None


class TestCheckCodexUpdate:
    """End-to-end behavior of check_codex_update (observable outcomes)."""

    def _base_patches(self, tmp_path, versions):
        """Common patches: codex active, binary found, version sequence."""
        return [
            _patch_config({}),
            patch.object(codex_update, "_codex_is_active", return_value=True),
            patch.object(codex_update, "_codex_binary", return_value="codex"),
            patch.object(codex_update, "_codex_version", side_effect=versions),
        ]

    def test_skips_when_disabled(self, tmp_path):
        with _patch_config({"codex_update": {"enabled": False}}), \
             patch.object(codex_update, "_codex_is_active", return_value=True):
            assert check_codex_update("/koan", str(tmp_path), force=True) is False

    def test_skips_when_provider_not_codex(self, tmp_path):
        with _patch_config({}), \
             patch.object(codex_update, "_codex_is_active", return_value=False):
            assert check_codex_update("/koan", str(tmp_path), force=True) is False

    def test_skips_when_not_due_and_not_forced(self, tmp_path):
        import time
        (tmp_path / codex_update.TRACKER_FILE).write_text(
            json.dumps({"last_check_ts": time.time()})
        )
        with _patch_config({}), \
             patch.object(codex_update, "_codex_is_active", return_value=True):
            assert check_codex_update("/koan", str(tmp_path), force=False) is False

    def test_no_update_when_up_to_date(self, tmp_path):
        patches = self._base_patches(tmp_path, ["0.139.0"])
        with patches[0], patches[1], patches[2], patches[3], \
             patch.object(codex_update, "_check_update_available", return_value=False), \
             patch.object(codex_update, "_run_codex") as run, \
             patch("app.notify.send_telegram") as notify:
            result = check_codex_update("/koan", str(tmp_path), force=True)
        assert result is False
        run.assert_not_called()  # never runs `codex update` when up to date
        notify.assert_not_called()
        # timestamp still recorded so the daily throttle advances
        assert (tmp_path / codex_update.TRACKER_FILE).exists()

    def test_updates_and_notifies_on_version_change(self, tmp_path):
        # _codex_version called: before (0.139.0), after (0.140.0)
        patches = self._base_patches(tmp_path, ["0.139.0", "0.140.0"])
        with patches[0], patches[1], patches[2], patches[3], \
             patch.object(codex_update, "_check_update_available", return_value=True), \
             patch.object(codex_update, "_run_codex", return_value=_completed()) as run, \
             patch("app.notify.send_telegram") as notify:
            result = check_codex_update("/koan", str(tmp_path), force=True)
        assert result is True
        run.assert_called_once()
        notify.assert_called_once()
        sent = notify.call_args[0][0]
        assert "0.139.0" in sent and "0.140.0" in sent

    def test_inconclusive_check_still_runs_update(self, tmp_path):
        # --check unsupported → run update, confirm via version delta
        patches = self._base_patches(tmp_path, ["0.139.0", "0.140.0"])
        with patches[0], patches[1], patches[2], patches[3], \
             patch.object(codex_update, "_check_update_available", return_value=None), \
             patch.object(codex_update, "_run_codex", return_value=_completed()) as run, \
             patch("app.notify.send_telegram") as notify:
            result = check_codex_update("/koan", str(tmp_path), force=True)
        assert result is True
        run.assert_called_once()
        notify.assert_called_once()

    def test_no_notify_when_version_unchanged_after_update(self, tmp_path):
        patches = self._base_patches(tmp_path, ["0.139.0", "0.139.0"])
        with patches[0], patches[1], patches[2], patches[3], \
             patch.object(codex_update, "_check_update_available", return_value=True), \
             patch.object(codex_update, "_run_codex", return_value=_completed()), \
             patch("app.notify.send_telegram") as notify:
            result = check_codex_update("/koan", str(tmp_path), force=True)
        assert result is False
        notify.assert_not_called()

    def test_notify_suppressed_when_notify_disabled(self, tmp_path):
        with _patch_config({"codex_update": {"notify": False}}), \
             patch.object(codex_update, "_codex_is_active", return_value=True), \
             patch.object(codex_update, "_codex_binary", return_value="codex"), \
             patch.object(codex_update, "_codex_version", side_effect=["0.139.0", "0.140.0"]), \
             patch.object(codex_update, "_check_update_available", return_value=True), \
             patch.object(codex_update, "_run_codex", return_value=_completed()), \
             patch("app.notify.send_telegram") as notify:
            result = check_codex_update("/koan", str(tmp_path), force=True)
        assert result is True
        notify.assert_not_called()

    def test_missing_binary_skips(self, tmp_path):
        with _patch_config({}), \
             patch.object(codex_update, "_codex_is_active", return_value=True), \
             patch.object(codex_update, "_codex_binary", return_value=None), \
             patch("app.notify.send_telegram") as notify:
            result = check_codex_update("/koan", str(tmp_path), force=True)
        assert result is False
        notify.assert_not_called()

    def test_update_subprocess_failure_returns_false(self, tmp_path):
        patches = self._base_patches(tmp_path, ["0.139.0"])
        with patches[0], patches[1], patches[2], patches[3], \
             patch.object(codex_update, "_check_update_available", return_value=True), \
             patch.object(codex_update, "_run_codex",
                          side_effect=subprocess.TimeoutExpired("codex", 300)), \
             patch("app.notify.send_telegram") as notify:
            result = check_codex_update("/koan", str(tmp_path), force=True)
        assert result is False
        notify.assert_not_called()
