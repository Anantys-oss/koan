"""Tests for app.mission_scope — the per-mission containment boundary.

Nothing here requires systemd to be installed: the ``systemd-run`` probe is
stubbed, so both the scope path and the fallback path are exercised on a macOS
dev box. The one test that needs a *real* cgroup to prove containment
end-to-end is skipped when no usable ``systemd-run`` exists.
"""

import ast
import contextlib
import itertools
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app import mission_scope
from app.mission_scope import (
    ScopedProcess,
    format_size,
    launch_scoped,
    parse_size,
    read_mem_total,
    resolve_memory_max,
    scope_launcher,
    stop_registered_scopes,
)


# A daemon in the Gradle shape: double-fork + setsid, so it is re-parented to
# PID 1 in its own session and has therefore LEFT the caller's process group.
# The direct child stays alive (as the provider CLI would) after reaping the
# intermediate fork.
_DETACHING_DAEMON = """
import os, sys, time
pid_path = sys.argv[1]
pid = os.fork()
if pid == 0:
    os.setsid()
    if os.fork() != 0:
        os._exit(0)
    with open(pid_path, "w") as fh:
        fh.write(str(os.getpid()))
        fh.flush()
    time.sleep(60)
    os._exit(0)
os.waitpid(pid, 0)
time.sleep(60)
"""

# A mission that finishes cleanly while leaving a child inside its process
# group — the success path on a host with no usable systemd-run. The leader
# waits for a handshake file so the test can capture the pgid before it exits.
_GROUP_CHILD = """
import os, sys, time
pid_path, go_path = sys.argv[1], sys.argv[2]
if os.fork() == 0:
    with open(pid_path, "w") as fh:
        fh.write(str(os.getpid()))
        fh.flush()
    time.sleep(60)
    os._exit(0)
while not os.path.exists(go_path):
    time.sleep(0.05)
os._exit(0)
"""


# Captured at import time, before any fixture stubs the probe, so the one
# end-to-end test below can still see what the real host can do.
_REAL_PROBE = mission_scope._probe_systemd_run


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """Each test starts from an unprobed host and an unfired fallback warning."""
    mission_scope.reset_probe_cache()
    yield
    mission_scope.reset_probe_cache()


@pytest.fixture
def stub_systemd():
    """Pretend a usable system-manager ``systemd-run`` is on PATH."""
    with patch.object(
        mission_scope, "_probe_systemd_run", return_value=("/usr/bin/systemd-run", []),
    ):
        mission_scope.reset_probe_cache()
        yield


@pytest.fixture
def stub_no_systemd():
    """Pretend no usable ``systemd-run`` exists (the macOS / no-manager case)."""
    with patch.object(mission_scope, "_probe_systemd_run", return_value=(None, [])):
        mission_scope.reset_probe_cache()
        yield


def _systemctl_recorder(calls):
    """Fake ``_systemctl`` that records argv and reports a successful call."""
    def fake(manager_args, args, timeout=10.0):
        calls.append(list(args))
        result = MagicMock()
        result.stdout = ""
        result.returncode = 0
        return result
    return fake


def _suppress():
    """Swallow the races inherent in killing a process we do not own."""
    return contextlib.suppress(OSError, ProcessLookupError)


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _wait_for_pid_file(path, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            text = path.read_text().strip()
        except OSError:
            text = ""
        if text:
            return int(text)
        time.sleep(0.05)
    raise AssertionError(f"daemon never wrote its pid to {path}")


def _wait_until_dead(pid, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


# ── size parsing ────────────────────────────────────────────────────────

class TestParseSize:
    @pytest.mark.parametrize("value,expected", [
        ("2G", 2 * 1024 ** 3),
        ("2g", 2 * 1024 ** 3),
        ("2GiB", 2 * 1024 ** 3),
        ("512M", 512 * 1024 ** 2),
        ("1024k", 1024 * 1024),
        ("1.5G", int(1.5 * 1024 ** 3)),
        (4096, 4096),
    ])
    def test_parses_suffixes_and_bare_bytes(self, value, expected):
        assert parse_size(value) == expected

    @pytest.mark.parametrize("value", [None, "", "lots", 0, -1, True, False, {}])
    def test_unusable_values_are_none(self, value):
        assert parse_size(value) is None

    def test_format_size_round_trips_the_config_spelling(self):
        assert format_size(parse_size("5.75G")) == "5.75G"
        assert format_size(parse_size("2G")) == "2G"
        assert format_size(None) == "unknown"


# ── cap resolution ──────────────────────────────────────────────────────

def _meminfo(tmp_path, total_kb):
    path = tmp_path / "meminfo"
    path.write_text(
        f"MemTotal:       {total_kb} kB\n"
        f"MemFree:        123456 kB\n"
        f"SwapTotal:            0 kB\n"
    )
    return str(path)


class TestResolveMemoryMax:
    def test_reserve_is_subtracted_from_memtotal(self, tmp_path):
        # 7.75 GiB fleet host minus the 2G reserve.
        total_kb = int(7.75 * 1024 * 1024)
        cap = resolve_memory_max(
            {"memory_reserve": "2G", "memory_min": "1G"},
            _meminfo(tmp_path, total_kb),
        )
        assert cap == total_kb * 1024 - 2 * 1024 ** 3
        assert format_size(cap) == "5.75G"

    def test_floor_applies_on_a_small_host(self, tmp_path):
        # 1.9 GiB host: MemTotal - 2G is negative, so the floor is the cap.
        cap = resolve_memory_max(
            {"memory_reserve": "2G", "memory_min": "1G"},
            _meminfo(tmp_path, int(1.9 * 1024 * 1024)),
        )
        assert cap == 1024 ** 3

    def test_explicit_memory_max_wins_verbatim(self, tmp_path):
        cap = resolve_memory_max(
            {"memory_max": "3G", "memory_reserve": "2G", "memory_min": "1G"},
            _meminfo(tmp_path, int(7.75 * 1024 * 1024)),
        )
        assert cap == 3 * 1024 ** 3

    def test_explicit_memory_max_needs_no_meminfo(self):
        cap = resolve_memory_max({"memory_max": "3G"}, "/nonexistent/meminfo")
        assert cap == 3 * 1024 ** 3

    def test_no_meminfo_and_no_override_means_no_cap(self):
        assert resolve_memory_max({"memory_reserve": "2G"}, "/nonexistent/meminfo") is None

    def test_reserve_over_ram_with_no_floor_yields_no_cap(self, tmp_path):
        # Never resolve to MemoryMax=0, which the kernel reads as "kill at once".
        cap = resolve_memory_max(
            {"memory_reserve": "8G", "memory_min": None},
            _meminfo(tmp_path, 1024 * 1024),
        )
        assert cap is None

    def test_read_mem_total_is_none_off_linux(self):
        assert read_mem_total("/nonexistent/meminfo") is None


# ── launcher construction ───────────────────────────────────────────────

class TestScopeLauncher:
    def test_sets_memory_high_at_ninety_percent_of_max(self, stub_systemd):
        argv = scope_launcher("koan-mission-abc", 10 * 1024 ** 3)
        assert argv[0] == "/usr/bin/systemd-run"
        assert "--scope" in argv and "--collect" in argv
        assert "--unit=koan-mission-abc" in argv
        assert f"--property=MemoryMax={10 * 1024 ** 3}" in argv
        assert f"--property=MemoryHigh={int(10 * 1024 ** 3 * 0.9)}" in argv
        assert argv[-1] == "--"

    def test_omits_memory_properties_when_no_cap_resolved(self, stub_systemd):
        argv = scope_launcher("koan-mission-abc", None)
        assert not [a for a in argv if a.startswith("--property=Memory")]

    def test_user_manager_flag_is_carried_through(self):
        with patch.object(
            mission_scope, "_probe_systemd_run",
            return_value=("/usr/bin/systemd-run", ["--user"]),
        ):
            mission_scope.reset_probe_cache()
            assert "--user" in scope_launcher("koan-mission-abc", None)


# ── launch_scoped: scope, disabled and fallback modes ───────────────────

class _RecordingSpawn:
    """Stand-in for the real spawn that records how it was called."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, launcher, **kwargs):
        self.calls.append({"argv": list(argv), "launcher": list(launcher),
                           "kwargs": dict(kwargs)})
        proc = MagicMock()
        proc.pid = 4242
        proc.poll.return_value = None
        proc.returncode = 0
        return proc


_BASE_CONFIG = {
    "enabled": True,
    "memory_reserve": "2G",
    "memory_min": "1G",
    "memory_max": "4G",
}

# argv[0] for tests that care about the wrapping, never about the binary.
# launch_scoped pre-checks the mission binary on PATH (_require_executable), so
# a bare "claude" here would make scope-mode tests pass only on a host that
# happens to have the provider CLI installed — CI does not. sys.executable is
# absolute and always exists, so the pre-check short-circuits on os.sep.
_MISSION_ARGV = [sys.executable, "-c", "pass"]


class TestLaunchScoped:
    def test_scope_mode_wraps_the_command(self, stub_systemd, tmp_path):
        spawn = _RecordingSpawn()
        argv = [*_MISSION_ARGV, "--flag"]
        scoped = launch_scoped(
            argv, config=dict(_BASE_CONFIG), spawn=spawn,
            koan_root=str(tmp_path),
        )
        assert scoped.mode == "scope"
        assert scoped.unit.startswith("koan-mission-")
        assert scoped.memory_max == 4 * 1024 ** 3
        launcher = spawn.calls[0]["launcher"]
        assert launcher[0] == "/usr/bin/systemd-run"
        assert f"--unit={scoped.unit}" in launcher
        # The mission argv itself is never rewritten.
        assert spawn.calls[0]["argv"] == argv

    def test_the_unit_name_is_one_systemctl_can_address(self, stub_systemd, tmp_path):
        """The `.scope` suffix is load-bearing, not cosmetic.

        systemctl appends `.service` to an abbreviated unit name (systemctl(1)),
        so a bare `koan-mission-<uuid>` would make every stop, kill and property
        read address a unit that never existed — silently turning the whole
        containment feature into a no-op on a real systemd host.
        """
        spawn = _RecordingSpawn()
        scoped = launch_scoped(list(_MISSION_ARGV), config=dict(_BASE_CONFIG),
                               spawn=spawn, koan_root=str(tmp_path))
        assert scoped.unit.endswith(".scope")
        # systemd-run creates exactly the name every later systemctl call uses.
        assert f"--unit={scoped.unit}" in spawn.calls[0]["launcher"]
        calls = []
        with patch.object(mission_scope, "_systemctl",
                          side_effect=_systemctl_recorder(calls)), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "_read_peak_bytes", return_value=None), \
             patch.object(mission_scope, "_read_oom_kills", return_value=0), \
             patch.object(mission_scope, "_unit_property", return_value=""):
            scoped.teardown()
        assert ["stop", scoped.unit] in calls

    def test_start_new_session_is_always_set(self, stub_systemd, tmp_path):
        spawn = _RecordingSpawn()
        launch_scoped(list(_MISSION_ARGV), config=dict(_BASE_CONFIG), spawn=spawn,
                      koan_root=str(tmp_path))
        assert spawn.calls[0]["kwargs"]["start_new_session"] is True

    def test_disabled_reproduces_the_unscoped_spawn(self, stub_systemd, tmp_path):
        """enabled: false must behave exactly as Kōan did before this module."""
        config = {**_BASE_CONFIG, "enabled": False}
        spawn = _RecordingSpawn()
        scoped = launch_scoped(list(_MISSION_ARGV), config=config, spawn=spawn,
                               koan_root=str(tmp_path))
        assert scoped.mode == "session"
        assert scoped.unit == ""
        assert spawn.calls[0]["launcher"] == []
        assert spawn.calls[0]["kwargs"]["start_new_session"] is True
        assert len(spawn.calls) == 1
        # Teardown degrades to the pre-existing process-group kill.
        with patch.object(mission_scope, "kill_process_group") as killer, \
             patch.object(mission_scope, "_systemctl") as systemctl:
            scoped.teardown()
        killer.assert_called_once_with(scoped.proc)
        systemctl.assert_not_called()

    def test_disabled_does_not_probe_for_systemd(self, tmp_path):
        config = {**_BASE_CONFIG, "enabled": False}
        with patch.object(mission_scope, "_probe_systemd_run") as probe:
            launch_scoped(list(_MISSION_ARGV), config=config, spawn=_RecordingSpawn(),
                          koan_root=str(tmp_path))
        probe.assert_not_called()

    def test_fallback_when_systemd_run_is_absent(self, stub_no_systemd, tmp_path):
        spawn = _RecordingSpawn()
        scoped = launch_scoped(list(_MISSION_ARGV), config=dict(_BASE_CONFIG), spawn=spawn,
                               koan_root=str(tmp_path))
        assert scoped.mode == "session"
        assert spawn.calls[0]["launcher"] == []
        with patch.object(mission_scope, "kill_process_group") as killer:
            scoped.teardown()
        killer.assert_called_once_with(scoped.proc)

    def test_fallback_warning_is_logged_once(self, stub_no_systemd, tmp_path):
        with patch.object(mission_scope, "log_safe") as logger:
            for _ in range(3):
                launch_scoped(list(_MISSION_ARGV), config=dict(_BASE_CONFIG),
                              spawn=_RecordingSpawn(), koan_root=str(tmp_path))
        warnings = [c for c in logger.call_args_list if c.args[0] == "warn"]
        assert len(warnings) == 1
        assert "systemd-run unavailable" in warnings[0].args[1]

    def test_scope_start_failure_falls_back_and_still_spawns(self, stub_systemd, tmp_path):
        attempts = []

        def spawn(argv, launcher, **kwargs):
            attempts.append(list(launcher))
            if launcher:
                raise OSError("scope could not be created")
            proc = MagicMock()
            proc.pid = 99
            proc.poll.return_value = None
            return proc

        with patch.object(mission_scope, "log_safe") as logger:
            scoped = launch_scoped(list(_MISSION_ARGV), config=dict(_BASE_CONFIG),
                                   spawn=spawn, koan_root=str(tmp_path))
        assert scoped.mode == "session"
        assert attempts[0] and attempts[1] == []
        assert any(c.args[0] == "warn" for c in logger.call_args_list)

    def test_missing_binary_still_raises_filenotfound(self, stub_systemd, tmp_path):
        """systemd-run would otherwise swallow the provider's exec failure."""
        with pytest.raises(FileNotFoundError) as excinfo:
            launch_scoped(["definitely-not-a-real-binary-xyz"],
                          config=dict(_BASE_CONFIG), spawn=_RecordingSpawn(),
                          koan_root=str(tmp_path))
        # missing_binary_message() matches on err.filename == cmd[0].
        assert excinfo.value.filename == "definitely-not-a-real-binary-xyz"


# ── teardown: which lever gets pulled ───────────────────────────────────

class TestTeardown:
    def _scoped(self, tmp_path, **kwargs):
        proc = MagicMock()
        proc.pid = 4242
        proc.poll.return_value = None
        proc.returncode = 0
        scoped = ScopedProcess(
            proc, unit="koan-mission-test", mode="scope",
            memory_max=4 * 1024 ** 3, koan_root=str(tmp_path), **kwargs,
        )
        # 4242 is a made-up pid, but `_read_pgid` resolved it for real at
        # construction — on a host where some unrelated same-user process holds
        # it and leads its own group, the fallback teardown these tests exercise
        # would SIGTERM/SIGKILL that group. Pin it to the "no group captured"
        # value; the group-kill path has its own real-process test.
        scoped._pgid = 0
        return scoped

    def test_stops_the_unit_instead_of_the_process_group(self, tmp_path):
        calls = []
        scoped = self._scoped(tmp_path)
        with patch.object(mission_scope, "_systemctl",
                          side_effect=_systemctl_recorder(calls)), \
             patch.object(mission_scope, "kill_process_group") as killer:
            scoped.teardown()
        assert ["stop", "koan-mission-test"] in calls
        killer.assert_not_called()

    def test_is_idempotent(self, tmp_path):
        calls = []
        scoped = self._scoped(tmp_path)
        with patch.object(mission_scope, "_systemctl",
                          side_effect=_systemctl_recorder(calls)):
            scoped.teardown()
            scoped.teardown()
        assert len([c for c in calls if c[0] == "stop"]) == 1

    @staticmethod
    def _no_wall_clock():
        """Spend no real time in _await_empty_cgroup's drain poll.

        Patching only ``time.sleep`` leaves the ``while time.time() < deadline``
        loop busy-spinning for the full 5 s. A monotonically advancing fake
        clock reaches the deadline immediately and keeps the test honest about
        what it is asserting: the report, not the wait.
        """
        return patch.object(
            mission_scope.time, "time",
            side_effect=itertools.count(0.0, 1.0).__next__,
        )

    @staticmethod
    def _stop_fails(calls=None, kill_returncode=0):
        """A systemctl whose ``stop`` never completes; ``kill`` answers as given.

        ``show -p LoadState`` reports ``active`` until a SIGKILL the manager
        accepts, then ``not-found`` — a transient scope is ``--collect``ed the
        moment it dies, and that disappearance is what confirms containment
        when there is no cgroup left to read.
        """
        state = {"gone": False}

        def fake(manager_args, args, timeout=10.0):
            if calls is not None:
                calls.append(list(args))
            if args[0] == "stop":
                return None  # e.g. blocked past the wrapper's 10 s timeout
            result = MagicMock()
            result.returncode = kill_returncode if args[0] == "kill" else 0
            if args[0] == "kill" and kill_returncode == 0:
                state["gone"] = True
            result.stdout = (
                ("not-found" if state["gone"] else "active")
                if args[0] == "show" else ""
            )
            return result
        return fake

    def test_escalates_to_sigkill_when_the_cgroup_stays_populated(self, tmp_path):
        cgroup = tmp_path / "cg"
        cgroup.mkdir()
        (cgroup / "cgroup.events").write_text("populated 1\nfrozen 0\n")
        calls = []
        scoped = self._scoped(tmp_path)
        with patch.object(mission_scope, "_systemctl",
                          side_effect=_systemctl_recorder(calls)), \
             patch.object(mission_scope, "_cgroup_dir", return_value=cgroup), \
             patch.object(mission_scope, "_read_peak_bytes", return_value=None), \
             patch.object(mission_scope, "_read_oom_kills", return_value=0), \
             patch.object(mission_scope, "_unit_property", return_value=""), \
             patch.object(mission_scope.time, "sleep", return_value=None), \
             self._no_wall_clock():
            scoped.teardown()
        assert ["kill", "-s", "SIGKILL", "koan-mission-test"] in calls

    def test_no_systemctl_falls_back_to_the_process_group(self, tmp_path):
        """A scope we cannot stop must not leave the mission running."""
        scoped = self._scoped(tmp_path)
        with patch.object(mission_scope, "_systemctl", return_value=None), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "kill_process_group") as killer:
            scoped.teardown()
        killer.assert_called_once_with(scoped.proc)

    def test_failed_stop_sigkills_the_scope_before_the_group(self, tmp_path):
        """A stop that never completes must still reach the cgroup.

        `_systemctl` reports None when the call times out — precisely what a
        SIGTERM-ignoring process inside the scope produces, since the 10 s
        wrapper timeout raises TimeoutExpired (a SubprocessError). The process
        group is not a substitute: it never reaches a re-parented daemon.
        """
        calls = []
        scoped = self._scoped(tmp_path)
        with patch.object(mission_scope, "_systemctl",
                          side_effect=self._stop_fails(calls)), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "_read_peak_bytes", return_value=None), \
             patch.object(mission_scope, "_read_oom_kills", return_value=0), \
             patch.object(mission_scope, "kill_process_group") as killer:
            scoped.teardown()
        assert ["kill", "-s", "SIGKILL", "koan-mission-test"] in calls
        # The unit answered, so there is nothing for the group kill to add.
        killer.assert_not_called()

    def test_failed_stop_kills_the_scope_even_on_the_success_path(self, tmp_path):
        """The regression: an exited mission process leaves killpg with no target.

        The real kill_process_group runs here (only os.killpg underneath it is
        intercepted) so the guard itself is exercised: with the mission process
        already reaped, `proc.poll() is not None` returns before any signal is
        sent. That is the success path — the whole point of this module — where
        the old fallback silently signalled nothing while logging that it had
        killed the group. Reaching the scope cannot depend on it.
        """
        calls = []
        scoped = self._scoped(tmp_path)
        scoped.proc.poll.return_value = 0  # mission finished cleanly
        with patch.object(mission_scope, "_systemctl",
                          side_effect=self._stop_fails(calls)), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "_read_peak_bytes", return_value=None), \
             patch.object(mission_scope, "_read_oom_kills", return_value=0), \
             patch("app.subprocess_runner.os.killpg") as killpg:
            scoped.teardown()
        assert ["kill", "-s", "SIGKILL", "koan-mission-test"] in calls
        killpg.assert_not_called()

    def test_failed_stop_falls_back_to_the_group_when_the_kill_is_refused(self, tmp_path):
        """A non-zero `systemctl kill` is a refusal, not a teardown.

        `_systemctl` runs with check=False, so "Failed to kill unit … not
        loaded" comes back as a result rather than None. Nothing on the systemd
        side reached a survivor, so the group — the last lever — must still fire.
        """
        scoped = self._scoped(tmp_path)
        with patch.object(mission_scope, "_systemctl",
                          side_effect=self._stop_fails(kill_returncode=1)), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "_read_peak_bytes", return_value=None), \
             patch.object(mission_scope, "_read_oom_kills", return_value=0), \
             patch.object(mission_scope, "kill_process_group") as killer:
            scoped.teardown()
        killer.assert_called_once_with(scoped.proc)

    def test_failed_stop_waits_for_the_cgroup_to_drain(self, tmp_path):
        """A unit that survives the SIGKILL is reported, not called clean."""
        cgroup = tmp_path / "cg"
        cgroup.mkdir()
        (cgroup / "cgroup.events").write_text("populated 1\nfrozen 0\n")
        scoped = self._scoped(tmp_path)
        with patch.object(mission_scope, "_systemctl",
                          side_effect=self._stop_fails()), \
             patch.object(mission_scope, "_cgroup_dir", return_value=cgroup), \
             patch.object(mission_scope, "_read_peak_bytes", return_value=None), \
             patch.object(mission_scope, "_read_oom_kills", return_value=0), \
             patch.object(mission_scope, "kill_process_group") as killer, \
             patch.object(mission_scope.time, "sleep", return_value=None), \
             self._no_wall_clock(), \
             patch.object(mission_scope, "log_safe") as logger:
            scoped.teardown()
        assert any("survived SIGKILL" in str(c.args[1]) for c in logger.call_args_list)
        # Unconfirmed containment: the group is the last lever left to pull.
        killer.assert_called_once_with(scoped.proc)

    def test_a_refused_stop_escalates_instead_of_reporting_success(self, tmp_path):
        """`_systemctl` runs with check=False, so a refusal is a result, not None.

        Accepting a non-zero `systemctl stop` reported containment that never
        happened: `_cgroup_dir` then returns None for a unit it merely could not
        inspect, and teardown returned as though the scope were gone.
        """
        calls = []
        state = {"gone": False}

        def fake(manager_args, args, timeout=10.0):
            calls.append(list(args))
            result = MagicMock()
            result.returncode = 1 if args[0] == "stop" else 0
            if args[0] == "kill":
                state["gone"] = True  # the manager accepts the SIGKILL
            # Loaded until then — a real failure, not an already-collected scope.
            result.stdout = (
                ("not-found" if state["gone"] else "active")
                if args[0] == "show" else ""
            )
            return result

        scoped = self._scoped(tmp_path)
        with patch.object(mission_scope, "_systemctl", side_effect=fake), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "_read_peak_bytes", return_value=None), \
             patch.object(mission_scope, "_read_oom_kills", return_value=0), \
             patch.object(mission_scope, "kill_process_group") as killer:
            scoped.teardown()
        assert ["kill", "-s", "SIGKILL", "koan-mission-test"] in calls
        killer.assert_not_called()

    def test_a_collected_unit_is_not_escalated(self, tmp_path):
        """Every clean mission ends here — it must be quiet.

        `--collect` removes the scope as soon as it goes inactive, so the stop
        exits 5 ("Unit … not loaded"). `LoadState=not-found` is what separates
        that from a refusal; without it, success would SIGKILL a phantom and log
        an error on every mission.
        """
        calls = []

        def fake(manager_args, args, timeout=10.0):
            calls.append(list(args))
            result = MagicMock()
            result.returncode = 5 if args[0] == "stop" else 0
            result.stdout = "not-found" if args[0] == "show" else ""
            return result

        scoped = self._scoped(tmp_path)
        with patch.object(mission_scope, "_systemctl", side_effect=fake), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "_read_peak_bytes", return_value=None), \
             patch.object(mission_scope, "_read_oom_kills", return_value=0), \
             patch.object(mission_scope, "kill_process_group") as killer, \
             patch.object(mission_scope, "log_safe") as logger:
            scoped.teardown()
        assert not any(c[0] == "kill" for c in calls)
        killer.assert_not_called()
        assert not [c for c in logger.call_args_list if c.args[0] == "error"]

    def test_cap_hit_is_reported_with_the_real_peak(self, tmp_path):
        scoped = self._scoped(tmp_path)
        with patch.object(mission_scope, "_systemctl", return_value=None), \
             patch.object(mission_scope, "_cgroup_dir", return_value=tmp_path), \
             patch.object(mission_scope, "_read_peak_bytes",
                          return_value=int(5.9 * 1024 ** 3)), \
             patch.object(mission_scope, "_read_oom_kills", return_value=1):
            scoped.memory_max = int(5.75 * 1024 ** 3)
            scoped.teardown()
        assert scoped.cap_exceeded is True
        assert scoped.cap_message() == "exceeded memory cap (5.9G of 5.75G)"

    def test_koan_initiated_sigkill_is_not_a_cap_hit(self, tmp_path):
        scoped = self._scoped(tmp_path)
        scoped.proc.returncode = -9
        with patch.object(mission_scope, "_systemctl", return_value=None), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "_unit_property", return_value=""):
            scoped.teardown(koan_initiated_kill=True)
        assert scoped.cap_exceeded is False
        assert scoped.cap_message() == ""

    def test_unexplained_sigkill_under_a_cap_is_a_cap_hit(self, tmp_path):
        scoped = self._scoped(tmp_path)
        scoped.proc.returncode = -9
        with patch.object(mission_scope, "_systemctl", return_value=None), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "_unit_property", return_value=""):
            scoped.teardown(koan_initiated_kill=False)
        assert scoped.cap_exceeded is True
        assert scoped.cap_message() == "exceeded memory cap (4G)"

    def test_clean_exit_is_never_a_cap_hit(self, tmp_path):
        scoped = self._scoped(tmp_path)
        with patch.object(mission_scope, "_systemctl", return_value=None), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "_unit_property", return_value=""):
            scoped.teardown()
        assert scoped.cap_exceeded is False


# ── scope registry (make stop) ──────────────────────────────────────────

class TestScopeRegistry:
    def test_launch_registers_and_teardown_deregisters(self, stub_systemd, tmp_path):
        scoped = launch_scoped(list(_MISSION_ARGV), config=dict(_BASE_CONFIG),
                               spawn=_RecordingSpawn(), koan_root=str(tmp_path))
        entry = tmp_path / ".koan-mission-scopes" / scoped.unit
        assert json.loads(entry.read_text())["unit"] == scoped.unit
        with patch.object(mission_scope, "_systemctl",
                          side_effect=self._manager([])), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "_unit_property", return_value=""):
            scoped.teardown()
        assert not entry.exists()

    def test_unconfirmed_teardown_keeps_the_entry_for_make_stop(self, tmp_path):
        """An unreachable manager is not proof the scope died.

        The fallback group kill reaches only what stayed in the group, so if the
        scope is still alive this record is the only thing that names it. Unlike
        a fallback `pid-<n>` record the unit is a uuid4 that can never be
        recycled, and it self-heals: once the scope is really gone the next
        `make stop` sees LoadState=not-found and unlinks it.
        """
        scoped = launch_scoped(list(_MISSION_ARGV), config=dict(_BASE_CONFIG),
                               spawn=_RecordingSpawn(), koan_root=str(tmp_path))
        entry = tmp_path / ".koan-mission-scopes" / scoped.unit
        with patch.object(mission_scope, "_systemctl", return_value=None), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "_unit_property", return_value=""), \
             patch.object(mission_scope, "kill_process_group") as killer, \
             patch.object(mission_scope, "kill_orphaned_process_group") as group_killer:
            scoped.teardown()
        assert entry.exists()
        # Both last-resort levers: the Popen (for a mission still running) and
        # the pgid captured at launch (for children an exited leader left).
        killer.assert_called_once_with(scoped.proc)
        group_killer.assert_called_once_with(scoped._pgid)

    @staticmethod
    def _manager(calls, *, stop_rc=0, kill_rc=0, load_state="not-found"):
        """A reachable systemctl: records argv and answers with the given codes."""
        def fake(manager_args, args, timeout=10.0):
            calls.append((manager_args, args))
            result = MagicMock()
            result.returncode = {"stop": stop_rc, "kill": kill_rc}.get(args[0], 0)
            result.stdout = load_state if args[0] == "show" else ""
            return result
        return fake

    def test_stop_registered_scopes_stops_the_unit(self, tmp_path):
        directory = tmp_path / ".koan-mission-scopes"
        directory.mkdir()
        (directory / "koan-mission-xyz.scope").write_text(json.dumps({
            "unit": "koan-mission-xyz.scope", "manager_args": ["--user"],
            "mode": "scope", "pid": 1234,
        }))
        calls = []
        with patch.object(mission_scope, "_systemctl",
                          side_effect=self._manager(calls)):
            handled = stop_registered_scopes(str(tmp_path))
        assert handled == ["koan-mission-xyz.scope"]
        assert (["--user"], ["stop", "koan-mission-xyz.scope"]) in calls
        assert not (directory / "koan-mission-xyz.scope").exists()

    def test_an_already_collected_scope_needs_no_escalation(self, tmp_path):
        """The ordinary clean-mission ending, not a failure.

        `--collect` garbage-collects the scope the moment it goes inactive, so
        `systemctl stop` legitimately exits non-zero with "Unit … not loaded".
        Escalating on the exit status alone would SIGKILL a phantom and log an
        error after every successful mission.
        """
        directory = tmp_path / ".koan-mission-scopes"
        directory.mkdir()
        entry = directory / "koan-mission-gone.scope"
        entry.write_text(json.dumps({
            "unit": "koan-mission-gone.scope", "manager_args": [], "mode": "scope",
        }))
        calls = []
        with patch.object(mission_scope, "_systemctl",
                          side_effect=self._manager(calls, stop_rc=5,
                                                    load_state="not-found")):
            handled = stop_registered_scopes(str(tmp_path))
        assert handled == ["koan-mission-gone.scope"]
        assert not entry.exists()
        assert not any(args[0] == "kill" for _, args in calls)

    def test_a_scope_it_could_not_stop_keeps_its_registry_entry(self, tmp_path):
        """`make stop` must not discard the only handle on a live scope.

        The record names a scope whose descendants have left the daemon's
        process group, so throwing it away leaves nothing that can reach them —
        and a later `make stop` would not even know the scope exists.
        """
        directory = tmp_path / ".koan-mission-scopes"
        directory.mkdir()
        entry = directory / "koan-mission-stuck.scope"
        entry.write_text(json.dumps({
            "unit": "koan-mission-stuck.scope", "manager_args": [], "mode": "scope",
        }))
        calls = []
        with patch.object(mission_scope, "_systemctl",
                          side_effect=self._manager(calls, stop_rc=1, kill_rc=1,
                                                    load_state="active")):
            handled = stop_registered_scopes(str(tmp_path))
        assert handled == []
        assert entry.exists(), "a scope that could not be stopped lost its record"
        # It did try the cgroup's own lever before giving up.
        assert any(args[0] == "kill" for _, args in calls)

    def test_stop_registered_scopes_kills_the_group_on_the_fallback_path(self, tmp_path):
        directory = tmp_path / ".koan-mission-scopes"
        directory.mkdir()
        (directory / "pid-777").write_text(json.dumps({
            "unit": "", "mode": "session", "pid": 777,
        }))
        with patch.object(mission_scope, "kill_process_group_by_pid") as killer:
            handled = stop_registered_scopes(str(tmp_path))
        killer.assert_called_once_with(777)
        assert handled == ["pgid 777"]

    def test_missing_registry_is_not_an_error(self, tmp_path):
        assert stop_registered_scopes(str(tmp_path / "absent")) == []


# ── containers are ryuk's to reap, never Kōan's ─────────────────────────

class TestNoContainerSweep:
    """Teardown must never remove a container itself.

    A container is a child of the Docker daemon, not of the mission, so nothing
    Kōan can observe tells this mission's containers apart from a co-tenant's: a
    creation time inside the mission's window proves overlap, never ownership.
    Any sweep written on that basis can `docker rm -f` a live unrelated
    workload, which is why there is none — killing the scope drops the ryuk
    client socket and ryuk reaps what that client owned.
    """

    def test_teardown_never_reaches_for_docker(self, tmp_path):
        proc = MagicMock()
        proc.pid = 4242
        proc.poll.return_value = None
        proc.returncode = 0
        scoped = ScopedProcess(proc, unit="koan-mission-test.scope", mode="scope",
                               koan_root=str(tmp_path))
        scoped._pgid = 0
        which_calls = []

        def fake_which(binary):
            which_calls.append(binary)
            return f"/usr/bin/{binary}"

        systemctl_calls = []
        with patch.object(mission_scope, "_systemctl",
                          side_effect=_systemctl_recorder(systemctl_calls)), \
             patch.object(mission_scope, "_cgroup_dir", return_value=None), \
             patch.object(mission_scope, "_read_peak_bytes", return_value=None), \
             patch.object(mission_scope, "_read_oom_kills", return_value=0), \
             patch.object(mission_scope, "_unit_property", return_value=""), \
             patch.object(mission_scope.shutil, "which", side_effect=fake_which), \
             patch.object(mission_scope.subprocess, "run") as run:
            scoped.teardown()
        assert "docker" not in which_calls
        assert not any("docker" in str(c) for c in run.call_args_list)
        assert not any("docker" in " ".join(args) for args in systemctl_calls)

    def test_the_module_has_no_container_code_at_all(self):
        """specs/components/agent-loop.md: "no container sweep at all".

        The previous sweep was merely defaulted off, which left a setting an
        operator could switch on and lose a co-tenant's database to. There is no
        safe value for it, so the mechanism is gone rather than disabled.

        Asserted over the parsed module rather than a list of the names it used
        to have: a sweep reintroduced as `prune_stale_containers`, or inlined as
        `subprocess.run(["docker", "rm", "-f", …])`, would slip past a blocklist
        and past the behavioural test above (which only sees an *unconditional*
        call). Prose may discuss Docker — comments and docstrings are excluded —
        but no executable line may name it.
        """
        tree = ast.parse(Path(mission_scope.__file__).read_text(encoding="utf-8"))
        docstrings = {
            node.body[0].value
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        banned = ("docker", "testcontainer")
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.lower()
                if node not in docstrings and any(b in text for b in banned):
                    offenders.append(f"line {node.lineno}: string {node.value[:40]!r}")
            elif isinstance(node, ast.Name) and any(b in node.id.lower() for b in banned):
                offenders.append(f"line {node.lineno}: {node.id}")
            elif isinstance(node, ast.Attribute) and any(
                b in node.attr.lower() for b in banned
            ):
                offenders.append(f"line {node.lineno}: .{node.attr}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                b in node.name.lower() for b in banned
            ):
                offenders.append(f"line {node.lineno}: def {node.name}")
        assert not offenders, (
            "mission_scope grew container-removal code again — a sweep cannot "
            f"establish ownership: {offenders}"
        )


# ── the regression guard: a daemon that leaves the process group ────────

class TestDetachedDaemonContainment:
    """The Gradle-daemon shape — the bug this module exists to fix."""

    def _launch_daemon_holder(self, tmp_path, launcher=()):
        pid_path = tmp_path / "daemon.pid"
        argv = [sys.executable, "-c", _DETACHING_DAEMON, str(pid_path)]
        proc = subprocess.Popen(
            list(launcher) + argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            daemon_pid = _wait_for_pid_file(pid_path)
        except AssertionError:
            proc.kill()
            raise
        return proc, daemon_pid

    def test_process_group_cannot_reach_a_detached_daemon(self, tmp_path):
        """Proves the premise: killpg is structurally unable to do this job.

        The daemon double-forks and calls setsid, so it is in its own session
        with PPID 1 — exactly what Gradle's build daemon does. A regression that
        swaps the cgroup teardown back for a process-group kill would make the
        second half of this test a false claim about cleanup.
        """
        proc, daemon_pid = self._launch_daemon_holder(tmp_path)
        try:
            assert os.getpgid(daemon_pid) != os.getpgid(proc.pid), (
                "daemon should have left the mission's process group"
            )
            mission_scope.kill_process_group(proc)
            proc.wait(timeout=10)
            # The direct child is gone, but the daemon survived the killpg.
            time.sleep(0.5)
            assert _pid_alive(daemon_pid), (
                "killpg reached a process in another session — test premise broken"
            )
        finally:
            with _suppress():
                os.kill(daemon_pid, signal.SIGKILL)
            if proc.poll() is None:
                proc.kill()

    @pytest.mark.skipif(
        _REAL_PROBE()[0] is None,
        reason="no usable systemd-run (macOS dev box / no systemd manager)",
    )
    def test_scope_teardown_kills_a_detached_daemon(self, tmp_path):
        """End-to-end containment: the cgroup catches what the group cannot.

        Runs only where a real transient scope can be created (the Linux fleet
        hosts); the stubbed tests above cover the logic everywhere else.
        """
        pid_path = tmp_path / "daemon.pid"
        argv = [sys.executable, "-c", _DETACHING_DAEMON, str(pid_path)]
        with patch.object(mission_scope, "_probe_systemd_run", side_effect=_REAL_PROBE):
            mission_scope.reset_probe_cache()
            scoped = launch_scoped(
                argv,
                config={**_BASE_CONFIG, "memory_max": None, "memory_reserve": "0",
                        "memory_min": "512M"},
                koan_root=str(tmp_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        daemon_pid = None
        try:
            assert scoped.mode == "scope"
            daemon_pid = _wait_for_pid_file(pid_path)
            assert os.getpgid(daemon_pid) != os.getpgid(scoped.proc.pid)
            scoped.teardown()
            assert _wait_until_dead(daemon_pid), (
                "the detached daemon survived the scope teardown"
            )
        finally:
            if daemon_pid is not None:
                with _suppress():
                    os.kill(daemon_pid, signal.SIGKILL)
            if scoped.proc.poll() is None:
                scoped.proc.kill()

    def test_session_teardown_kills_a_child_the_exited_leader_left_behind(self, tmp_path):
        """The fallback path must sweep the success path too.

        Hosts with no usable systemd-run get `start_new_session=True` and a
        process-group kill. `kill_process_group` returns at its
        `proc.poll() is not None` guard, so once the mission process exits on its
        own — the success path this module exists for — it signals nothing while
        a child left in the group keeps running. Teardown must reach that child
        via the pgid captured at launch.
        """
        pid_path = tmp_path / "child.pid"
        go_path = tmp_path / "leader-may-exit"
        proc = subprocess.Popen(
            [sys.executable, "-c", _GROUP_CHILD, str(pid_path), str(go_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        child_pid = None
        try:
            # Constructed while the leader lives, which is when the pgid is
            # readable — after it is reaped, os.getpgid can no longer find it.
            scoped = ScopedProcess(proc, mode="session", koan_root=str(tmp_path))
            child_pid = _wait_for_pid_file(pid_path)
            go_path.write_text("go")
            proc.wait(timeout=10)

            # The old teardown, verbatim: a no-op now that the leader is reaped.
            mission_scope.kill_process_group(proc)
            time.sleep(0.3)
            assert _pid_alive(child_pid), (
                "kill_process_group reached a child past its poll() guard — "
                "test premise broken"
            )

            scoped.teardown()
            assert _wait_until_dead(child_pid), (
                "the mission's child survived the fallback-path teardown"
            )
        finally:
            if child_pid is not None:
                with _suppress():
                    os.kill(child_pid, signal.SIGKILL)
            if proc.poll() is None:
                proc.kill()

