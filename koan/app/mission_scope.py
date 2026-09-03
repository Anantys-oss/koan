"""Per-mission containment boundary — one cgroup nothing can escape.

Kōan had no process sweep on the mission success path: ``run_claude_task``'s
``finally`` closed fds, reaped ``TMPDIR`` and dropped page cache, and ``killpg``
only fired on the abort/timeout branches. A mission that leaves a daemon behind
therefore leaked it into the host's idle baseline.

**Why a cgroup and not a process group.** Gradle's build daemon persists by
design (3-hour idle timeout) and detaches to ``PPID 1`` with its own session, so
it has left the mission's process group by the time the mission ends —
``os.killpg`` can never reach it. A cgroup catches every descendant no matter
how many times it double-forks or calls ``setsid``.

The chain this fixes, observed on a 4 vCPU / 7.75 GiB fleet host with no swap: a
Java/Gradle mission ended, the Gradle daemon stayed resident at 766 MB for 26
minutes, and because that project's test postgres is a Gradle *build service*
the container was started inside the Gradle daemon JVM rather than in a test
fork. So the daemon — not a test fork — held the Testcontainers ``ryuk`` client
socket, and ryuk only reaps once its client disconnects: the container's reap
timer was scoped to the daemon's 3-hour life. Killing the daemon makes ryuk reap
the containers itself, so one lever fixes the whole chain.

**Only the mission's own descendants.** Fleet hosts are shared with co-tenant
workloads that run their own Testcontainers and deliberately leave containers
up. The cgroup gives an exact descendant set, and nothing here is name-based: no
``pkill java``, no ``gradlew --stop``, no ``docker prune``, and **no container
sweep of any kind**. Containers are children of the Docker daemon, not of the
mission, so nothing Kōan can observe distinguishes one this mission started from
a co-tenant's — a creation timestamp inside the mission's window proves overlap,
never ownership. Killing the scope is what handles them: it drops the ryuk
client socket, and ryuk removes the containers that client owned.

Every host must still be able to run missions, so when ``systemd-run`` is
absent or the scope fails to start this degrades to ``start_new_session=True``
plus a process-group kill, warning once.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.run_log import log_safe
from app.signals import MISSION_SCOPES_DIR
from app.subprocess_runner import (
    kill_orphaned_process_group,
    kill_process_group,
    kill_process_group_by_pid,
)

# Transient scope units are named koan-mission-<uuid4 hex>.scope. The ``.scope``
# suffix is load-bearing, not decoration: systemctl appends ``.service`` to any
# abbreviated unit name (systemctl(1)), so a bare name would make every stop,
# kill and property read address a unit that never existed. It also makes a
# stray unit attributable to Kōan in `systemctl list-units` on a shared host.
UNIT_PREFIX = "koan-mission-"
UNIT_SUFFIX = ".scope"

_MEMINFO_PATH = "/proc/meminfo"
_CGROUP_ROOT = "/sys/fs/cgroup"

# MemoryHigh sits at this fraction of MemoryMax: the kernel throttles and
# reclaims against MemoryHigh, so an oversubscribed build feels back-pressure
# before MemoryMax turns into an OOM kill.
_MEMORY_HIGH_RATIO = 0.90

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?\s*$", re.IGNORECASE)
_SIZE_UNITS = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}

# systemd-run probe result, resolved once per process. None means "not probed
# yet"; a probed-absent host caches ``(None, [])`` so every mission does not
# re-shell-out. _fallback_warned keeps the *probe* verdict to one log line —
# it is a static property of the host, so repeating it per mission says
# nothing new. A scope that fails to *start* is not static (a manager can go
# away mid-run) and is logged every time it happens, because every occurrence
# is one more mission running uncontained.
_probe_cache: Optional[Tuple[Optional[str], List[str]]] = None
_fallback_warned = False

# (key, raw value) pairs already reported as unparseable, so a permanently bad
# config.yaml costs one log line rather than one per mission.
_warned_bad_sizes: set = set()


# ---------------------------------------------------------------------------
# Size parsing and cap resolution
# ---------------------------------------------------------------------------

def parse_size(value) -> Optional[int]:
    """Bytes from a config size value, or None when it is unset/unparseable.

    Accepts ``2G`` / ``512M`` / ``1024k`` (also the ``GiB``/``MB`` spellings)
    and a bare byte count. ``None``, ``0`` and junk all return None so callers
    can treat "no value" and "bad value" the same way — as "no cap from here".
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    match = _SIZE_RE.match(str(value))
    if not match:
        return None
    number, suffix = match.groups()
    scaled = int(float(number) * _SIZE_UNITS[suffix.lower()])
    return scaled if scaled > 0 else None


def format_size(num_bytes: Optional[int]) -> str:
    """Render bytes in the suffix style config uses (``5.75G``, ``512M``)."""
    if not num_bytes or num_bytes <= 0:
        return "unknown"
    for suffix, unit in (("T", _SIZE_UNITS["t"]), ("G", _SIZE_UNITS["g"]),
                         ("M", _SIZE_UNITS["m"]), ("K", _SIZE_UNITS["k"])):
        if num_bytes >= unit:
            return f"{num_bytes / unit:.2f}".rstrip("0").rstrip(".") + suffix
    return f"{num_bytes}B"


def read_mem_total(meminfo_path: str = _MEMINFO_PATH) -> Optional[int]:
    """Total physical RAM in bytes from /proc/meminfo, None when unreadable.

    Unreadable is the normal case off Linux (macOS dev boxes), where there is
    no cgroup to cap anyway.
    """
    try:
        with open(meminfo_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _configured_size(config: dict, key: str) -> Optional[int]:
    """:func:`parse_size` for a ``mission_limits`` key, loud about junk.

    ``parse_size`` cannot tell "unset" from "unparseable", and the caller
    coerces None to 0 — so a typo like ``memory_reserve: 2 gigs`` would
    silently drop the reserve and let a mission have all of RAM, which is the
    host meltdown this module exists to prevent. Report it instead; the value
    is still ignored, because guessing at a size an operator mistyped is worse
    than falling back to the documented default behaviour.
    """
    raw = config.get(key)
    parsed = parse_size(raw)
    if parsed is not None or raw is None or raw == "":
        return parsed
    # A value in a size *shape* that resolves to zero ("0", 0) is an explicit
    # "none of this", not a typo. Only text the grammar rejects is reported.
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return parsed
    if _SIZE_RE.match(str(raw)):
        return parsed
    marker = (key, str(raw))
    if marker not in _warned_bad_sizes:
        _warned_bad_sizes.add(marker)
        log_safe(
            "warn",
            f"mission_scope: mission_limits.{key}={raw!r} is not a size "
            f"(2G / 512M / a byte count) — ignoring it",
        )
    return parsed


def resolve_memory_max(config: dict, meminfo_path: str = _MEMINFO_PATH) -> Optional[int]:
    """Bytes for ``MemoryMax``, or None when no cap can be resolved.

    An explicit ``memory_max`` wins verbatim — an operator who names a number
    means it. Otherwise the cap is ``MemTotal - memory_reserve``, floored at
    ``memory_min``.

    A reserve with a floor rather than a percentage of RAM: Kōan's own baseline
    is roughly constant (~500 MB — python ~97 MB + the CLI ~275 MB +
    mcp-atlassian ~129 MB) and the fleet spans 1.9 GiB to 7.7 GiB with no swap
    on any host, so a percentage under-reserves exactly where it hurts. The
    floor keeps the smallest hosts able to run a mission at all.
    """
    explicit = _configured_size(config, "memory_max")
    if explicit:
        return explicit
    total = read_mem_total(meminfo_path)
    if not total:
        return None
    reserve = _configured_size(config, "memory_reserve") or 0
    floor = _configured_size(config, "memory_min") or 0
    cap = max(floor, total - reserve)
    # A reserve larger than RAM with no floor would otherwise produce
    # MemoryMax=0, which the kernel reads as "kill on first page".
    return cap if cap > 0 else None


# ---------------------------------------------------------------------------
# systemd-run probe
# ---------------------------------------------------------------------------

def _probe_systemd_run() -> Tuple[Optional[str], List[str]]:
    """Locate a ``systemd-run`` that can actually create a scope right now.

    Returns ``(path, manager_args)`` — ``manager_args`` is ``["--user"]`` when
    only the per-user manager is reachable, ``[]`` for the system manager.
    ``(None, [])`` means no usable scope on this host.

    ``shutil.which`` alone is not enough. As a non-root user the *system*
    manager needs polkit authentication, which fails non-interactively, so a
    non-root Kōan must go through its own user manager — and that manager has
    to be live, which ``$XDG_RUNTIME_DIR/systemd/private`` proves.
    """
    path = shutil.which("systemd-run")
    if not path:
        return None, []
    try:
        is_root = os.geteuid() == 0
    except AttributeError:  # pragma: no cover - non-POSIX
        return None, []
    if is_root:
        return (path, []) if Path("/run/systemd/system").is_dir() else (None, [])
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime_dir and Path(runtime_dir, "systemd", "private").exists():
        return path, ["--user"]
    return None, []


def systemd_run() -> Tuple[Optional[str], List[str]]:
    """Cached :func:`_probe_systemd_run` result (one probe per process)."""
    global _probe_cache
    if _probe_cache is None:
        _probe_cache = _probe_systemd_run()
    return _probe_cache


def reset_probe_cache() -> None:
    """Forget the cached probe (and the one-shot warnings).

    Exists so tests can exercise both the scope and the fallback path in one
    session — macOS dev machines have no systemd at all, so the probe is always
    stubbed rather than real.
    """
    global _probe_cache, _fallback_warned
    _probe_cache = None
    _fallback_warned = False
    _warned_bad_sizes.clear()


def _fallback_warning(reason: str) -> str:
    return (
        f"mission_limits: no cgroup scope for missions ({reason}) — falling back "
        f"to process-group cleanup, which cannot reach a daemon that re-parents "
        f"to PID 1 (e.g. Gradle's)"
    )


def _warn_fallback_once(reason: str) -> None:
    """One line per process for the *probe* verdict — a static host property.

    Only for "this host has no usable systemd-run", which cannot change while
    the process runs. A per-mission failure uses :func:`_warn_fallback`.
    """
    global _fallback_warned
    if _fallback_warned:
        return
    _fallback_warned = True
    log_safe("warn", _fallback_warning(reason))


def _warn_fallback(reason: str) -> None:
    """Log every occurrence of a scope that failed to start.

    Deliberately not one-shot, and deliberately not sharing the probe's
    budget: a manager that goes away mid-run makes every later mission run
    uncontained, and swallowing those lines reproduces the invisible leak this
    module exists to end — an empty ``systemctl list-units 'koan-mission-*'``
    with a clean log while daemons accumulate.
    """
    log_safe("warn", _fallback_warning(reason))


# ---------------------------------------------------------------------------
# Scope registry (so `make stop` can reach a live mission's descendants)
# ---------------------------------------------------------------------------

def _registry_dir(koan_root: Optional[str] = None) -> Optional[Path]:
    root = koan_root or os.environ.get("KOAN_ROOT", "")
    return Path(root, MISSION_SCOPES_DIR) if root else None


def _registry_entry(koan_root: Optional[str], name: str) -> Optional[Path]:
    directory = _registry_dir(koan_root)
    return directory / name if directory is not None else None


def read_registered_scopes(koan_root: Optional[str] = None) -> List[dict]:
    """Every live mission scope recorded by this instance, newest last.

    One file per scope rather than one shared JSON file: registration and
    de-registration are then a create and an unlink, with no read-modify-write
    for parallel sessions to race on.

    A record that cannot be read or parsed is the *only* durable handle on a
    scope whose descendants have left every process group, so it is reported
    rather than skipped in silence — an invisible handle is indistinguishable
    from "nothing to stop", which is exactly the report an operator must not
    get while a 766 MB daemon is still resident.
    """
    directory = _registry_dir(koan_root)
    if directory is None:
        return []
    records = []
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError:
        return []  # no mission has run yet — the ordinary case
    except OSError as exc:
        log_safe("error", f"mission_scope: registry {directory} unreadable: {exc}")
        return []
    for entry in entries:
        try:
            record = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log_safe(
                "error",
                f"mission_scope: registry record {entry} unusable ({exc}) — its "
                f"scope, if still alive, can no longer be reached from here",
            )
            continue
        if isinstance(record, dict):
            record["_path"] = str(entry)
            records.append(record)
    return records


# A PID record is trusted only when the process's real start time matches the
# one recorded at launch. Spawn-to-record latency is milliseconds; the window
# is generous only against coarse `ps etime` granularity and clock nudges.
_PID_RECORD_START_TOLERANCE = 30.0


def _boot_time() -> Optional[float]:
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _parse_etime(value: str) -> Optional[float]:
    """Seconds from ``ps -o etime=`` (``[[dd-]hh:]mm:ss``)."""
    text = value.strip()
    if not text:
        return None
    days = 0
    if "-" in text:
        day_part, _, text = text.partition("-")
        try:
            days = int(day_part)
        except ValueError:
            return None
    parts = text.split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        return None
    if len(numbers) == 2:
        numbers.insert(0, 0)
    hours, minutes, seconds = numbers
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _process_start_time(pid: int) -> Optional[float]:
    """Epoch seconds when *pid* started, or None when it cannot be determined.

    ``/proc/<pid>/stat`` field 22 (clock ticks since boot) plus ``btime`` on
    Linux; ``ps -o etime=`` everywhere else, so a macOS dev box — where the
    fallback path is the *normal* one — still gets a verified `make stop`.
    """
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as f:
            # The comm field can contain spaces and parentheses; everything
            # after the last ')' is fixed-width, so state is index 0 (field 3)
            # and starttime (field 22) is index 19.
            fields = f.read().rpartition(")")[2].split()
        boot = _boot_time()
        if boot is not None:
            return boot + float(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    binary = shutil.which("ps")
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "-p", str(pid), "-o", "etime="],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    elapsed = _parse_etime(result.stdout or "")
    return time.time() - elapsed if elapsed is not None else None


def _record_still_names_its_process(pid: int, started_at) -> bool:
    """True only when *pid* is still the process the record was written for.

    A PID is recycled; a uuid4 unit name is not. After a reboot (or enough
    churn) ``pid-4242`` can name an unrelated process that leads its own
    group, and signalling it would SIGKILL a stranger's whole group — the one
    destructive mistake this registry can make. Kill only on a start-time
    match; anything unverifiable is dropped instead.
    """
    if not isinstance(started_at, (int, float)) or isinstance(started_at, bool):
        return False
    start = _process_start_time(pid)
    if start is None:
        return False
    return abs(start - float(started_at)) <= _PID_RECORD_START_TOLERANCE


def stop_registered_scopes(koan_root: Optional[str] = None) -> List[str]:
    """Tear down every registered mission scope; return the names handled.

    ``make stop`` SIGTERMs the PID in each ``.koan-pid-*`` file, which leaves
    the whole mission subtree running. Stopping the scope (or, on the fallback
    path, the mission's process group) is what actually takes the descendants
    with it.

    A record is unlinked only once its scope is confirmed stopped. This is the
    retry mechanism, so discarding a record it could not act on would destroy
    the only durable handle on a live scope — and the daemon's process group
    cannot reach descendants that have left it. ``ScopedProcess.teardown``
    follows the same rule for its scope records; only a fallback ``pid-<n>``
    record is dropped unconditionally, because a PID (unlike a uuid4 unit name)
    can be recycled — and for the same reason it is signalled only after
    :func:`_record_still_names_its_process` confirms the PID is the process the
    record was written for. Dropping a stale record afterwards would not undo
    the SIGKILL it had already sent to a stranger's process group.
    """
    handled = []
    for record in read_registered_scopes(koan_root):
        unit = str(record.get("unit") or "")
        pid = record.get("pid")
        manager_args = [str(a) for a in record.get("manager_args") or []]
        if unit:
            if not stop_scope_unit(manager_args, unit):
                log_safe(
                    "error",
                    f"mission_scope: could not stop {unit} — keeping its registry "
                    f"entry so a later `make stop` can retry",
                )
                continue
            handled.append(unit)
        elif isinstance(pid, int) and pid > 0:
            if _record_still_names_its_process(pid, record.get("started_at")):
                kill_process_group_by_pid(pid)
                handled.append(f"pgid {pid}")
            else:
                log_safe(
                    "warn",
                    f"mission_scope: dropping stale registry record pid-{pid} — "
                    f"that PID is no longer the process it was written for",
                )
        else:
            # Neither handle survived registration: no unit name, and a pid
            # that is missing, zero or not an int (which `_register` writes
            # whenever `ScopedProcess.pid` degraded to 0). There is nothing
            # left to signal, so the record is dropped — but never silently,
            # because dropping a handle without acting on it is exactly what
            # the rest of this function refuses to do.
            log_safe(
                "error",
                f"mission_scope: discarding an unusable registry record "
                f"({record.get('_path') or 'unknown path'}) — it names neither "
                f"a unit nor a usable pid, so nothing can be stopped from it",
            )
        path = record.get("_path")
        if path:
            Path(path).unlink(missing_ok=True)
    return handled


# ---------------------------------------------------------------------------
# systemctl / cgroup helpers
# ---------------------------------------------------------------------------

def _systemctl(manager_args: List[str], args: List[str], timeout: float = 10.0):
    """Run ``systemctl [--user] <args>``, returning the CompletedProcess or None."""
    binary = shutil.which("systemctl")
    if not binary:
        return None
    try:
        return subprocess.run(
            [binary, *manager_args, *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_safe("error", f"mission_scope: systemctl {' '.join(args)} failed: {exc}")
        return None


def _unit_property(manager_args: List[str], unit: str, prop: str) -> str:
    result = _systemctl(manager_args, ["show", "-p", prop, "--value", unit], timeout=5)
    return (result.stdout or "").strip() if result is not None else ""


def _cgroup_dir(manager_args: List[str], unit: str) -> Optional[Path]:
    """Filesystem path of the unit's cgroup, or None when it no longer exists."""
    control_group = _unit_property(manager_args, unit, "ControlGroup")
    if not control_group.startswith("/"):
        return None
    path = Path(_CGROUP_ROOT + control_group)
    return path if path.is_dir() else None


def _cgroup_populated(cgroup_dir: Path) -> Optional[bool]:
    """Whether any process remains in the cgroup — None when it cannot be read.

    Only ``FileNotFoundError`` proves the cgroup is gone and therefore holds
    nothing. Every other ``OSError`` (EACCES on a cgroup delegated to another
    manager, EIO, a v1 layout with no ``cgroup.events``) means "cannot tell",
    and reporting that as empty would claim a containment this module refuses
    to claim unverified — the survivor would then be dropped from the registry
    and unreachable by any later `make stop`.
    """
    try:
        events = (cgroup_dir / "cgroup.events").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError as exc:
        log_safe("error", f"mission_scope: {cgroup_dir}/cgroup.events unreadable: {exc}")
        return None
    for line in events.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "populated":
            return parts[1] != "0"
    return False


def _unit_is_gone(manager_args: List[str], unit: str) -> bool:
    """True only when the manager *confirms* the unit no longer exists.

    A non-zero ``systemctl stop`` is ambiguous on its own. ``--collect``
    garbage-collects a transient scope the moment it goes inactive, so on a
    clean mission the stop legitimately fails with "Unit … not loaded" (exit 5)
    — the common case, not an error. A rejected or manager-timed-out stop looks
    identical by exit status.

    ``systemctl show`` answers for unknown units too (exit 0, ``LoadState`` of
    ``not-found``), so it separates the two. An unreachable manager answers
    nothing, and the only safe reading of "cannot inspect" is that the scope may
    still be alive.
    """
    result = _systemctl(
        manager_args, ["show", "-p", "LoadState", "--value", unit], timeout=5,
    )
    if result is None or result.returncode != 0:
        return False
    return (result.stdout or "").strip() == "not-found"


def _await_empty_cgroup(
    manager_args: List[str], unit: str, cgroup_dir: Optional[Path],
) -> bool:
    """Poll until the scope drains. False when it never does.

    With a *cgroup_dir* the cgroup itself is the ground truth. Without one
    there is nothing to read, and ``_cgroup_dir`` returns None both for a
    collected unit and for one it merely could not inspect — so the manager is
    asked instead, on the same deadline: the unit disappearing is the
    confirmation, and never seeing it disappear is a teardown this cannot
    claim. Never pass None obtained by re-resolving a path already known to be
    populated; that would trade certainty for the conflation this module exists
    to refuse.
    """
    deadline = time.time() + 5
    while time.time() < deadline:
        if cgroup_dir is None:
            if _unit_is_gone(manager_args, unit):
                return True
            time.sleep(0.5)
            continue
        populated = _cgroup_populated(cgroup_dir)
        if populated is False:
            return True
        if populated is None and _unit_is_gone(manager_args, unit):
            # Cannot read the cgroup; only the manager can still confirm it.
            return True
        time.sleep(0.2)
    log_safe("error", f"mission_scope: {unit} survived SIGKILL — abandoning")
    return False


def stop_scope_unit(manager_args: List[str], unit: str) -> bool:
    """Stop *unit* and confirm nothing survived it. The single teardown lever.

    Shared by ``ScopedProcess.teardown`` and ``stop_registered_scopes`` so both
    treat a manager failure the same way — ``_systemctl`` runs with
    ``check=False``, so a refusal arrives as a non-zero result, never as None,
    and accepting it would report containment that never happened.

    Returns True only when the scope is confirmed gone or its cgroup confirmed
    empty. False leaves the caller its own last resort: the process group for a
    live ``ScopedProcess``, and for ``make stop`` keeping the registry entry
    that names the scope.
    """
    result = _systemctl(manager_args, ["stop", unit])
    if result is not None and result.returncode == 0:
        cgroup_dir = _cgroup_dir(manager_args, unit)
        populated = _cgroup_populated(cgroup_dir) if cgroup_dir is not None else False
        if populated is False:
            return True
        if populated is None:
            # The cgroup exists but says nothing. "Cannot tell" is not "empty":
            # only the manager confirming the unit is gone counts as contained.
            return _unit_is_gone(manager_args, unit)
        # A process ignoring SIGTERM (the scope's default KillSignal) keeps the
        # cgroup populated. Escalate rather than report a clean teardown.
        log_safe("warn", f"mission_scope: {unit} still populated after stop — SIGKILL")
        _systemctl(manager_args, ["kill", "-s", "SIGKILL", unit])
        # Poll the path already resolved above: it is known to hold a survivor,
        # so re-resolving could only downgrade certainty to None.
        return _await_empty_cgroup(manager_args, unit, cgroup_dir)
    if _unit_is_gone(manager_args, unit):
        return True  # already collected — the ordinary clean-mission ending
    # The stop did not happen: no systemctl, a transient error, a refusal, or a
    # block past the wrapper's 10 s timeout (TimeoutExpired is a
    # SubprocessError) — which is what a SIGTERM-ignoring process produces.
    log_safe("error", f"mission_scope: stopping {unit} failed — SIGKILLing the scope")
    killed = _systemctl(manager_args, ["kill", "-s", "SIGKILL", unit])
    if killed is None or killed.returncode != 0:
        return _unit_is_gone(manager_args, unit)
    return _await_empty_cgroup(manager_args, unit, _cgroup_dir(manager_args, unit))


def _read_oom_kills(cgroup_dir: Path) -> Optional[int]:
    """Kernel OOM kills inside the scope — None when the evidence is unreadable.

    ``0`` means "the kernel killed nothing", which is a genuine outcome. A file
    that is missing, unreadable or malformed means the outcome is *unknown*,
    and collapsing the two hides a cap hit inside an ordinary failure.
    """
    try:
        events = (cgroup_dir / "memory.events").read_text(encoding="utf-8")
    except OSError as exc:
        log_safe(
            "debug",
            f"mission_scope: {cgroup_dir}/memory.events unreadable ({exc}) — "
            f"cap evidence unavailable",
        )
        return None
    for line in events.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "oom_kill":
            try:
                return int(parts[1])
            except ValueError:
                log_safe(
                    "error",
                    f"mission_scope: {cgroup_dir}/memory.events has a "
                    f"non-numeric oom_kill value {parts[1]!r}",
                )
                return None
    return None


def _read_peak_bytes(cgroup_dir: Path) -> Optional[int]:
    """High-water mark of the scope's memory, in bytes.

    ``memory.peak`` is the real high-water mark but needs kernel 5.19+, so fall
    back to ``memory_monitor.read_cgroup_memory_stat``'s ``anon`` — the
    process-memory field that matters here, as opposed to ``memory.current``,
    which also counts reclaimable page cache.
    """
    try:
        return int((cgroup_dir / "memory.peak").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pass
    from app.memory_monitor import read_cgroup_memory_stat
    stat = read_cgroup_memory_stat(str(cgroup_dir / "memory.stat"))
    if not stat:
        return None
    anon_mb = stat.get("anon_mb")
    return int(anon_mb * 1024 * 1024) if anon_mb else None


# ---------------------------------------------------------------------------
# The scoped process
# ---------------------------------------------------------------------------

class ScopedProcess:
    """A mission subprocess plus the boundary that will contain its children.

    ``mode`` is ``"scope"`` when the process runs inside a transient systemd
    scope named by ``unit``, and ``"session"`` on the fallback path where the
    only boundary available is the process group.
    """

    def __init__(
        self,
        proc: subprocess.Popen,
        *,
        unit: str = "",
        manager_args: Optional[List[str]] = None,
        mode: str = "session",
        memory_max: Optional[int] = None,
        koan_root: Optional[str] = None,
    ) -> None:
        self.proc = proc
        self.unit = unit
        self.manager_args = list(manager_args or [])
        self.mode = mode
        self.memory_max = memory_max
        self.started_at = time.time()
        self.peak_bytes: Optional[int] = None
        # None = the kernel's evidence could not be read, which is not the same
        # outcome as "it killed nothing" (0).
        self.oom_kills: Optional[int] = None
        self.cap_exceeded = False
        self._koan_root = koan_root
        self._pgid = self._read_pgid()
        self._registry_name = unit or f"pid-{self.pid}"
        self._torn_down = False
        self._register()

    @property
    def pid(self) -> int:
        """The mission process's PID, or 0 when it cannot be read."""
        try:
            return int(self.proc.pid)
        except (AttributeError, TypeError, ValueError):
            return 0

    def _read_pgid(self) -> int:
        """The mission's process-group id, captured while its leader is alive.

        ``start_new_session=True`` makes the mission its own group leader, so
        the id equals the pid. It has to be read now: once the leader is reaped
        ``os.getpgid`` can no longer recover it, while the group itself lives on
        for as long as any descendant that stayed in it does.
        """
        pid = self.pid
        if pid <= 0:
            return 0
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError, TypeError):
            return 0
        return pgid if pgid == pid else 0

    # -- registry ---------------------------------------------------------

    def _register(self) -> None:
        """Record this scope so ``make stop`` can reach it. Never fatal.

        Written atomically: a crash mid-write would otherwise leave a truncated
        record, and a record that cannot be parsed is a live scope `make stop`
        can no longer name.
        """
        entry = _registry_entry(self._koan_root, self._registry_name)
        if entry is None:
            return
        try:
            from app.utils import atomic_write
            entry.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(entry, json.dumps({
                "unit": self.unit,
                "manager_args": self.manager_args,
                "mode": self.mode,
                "pid": self.pid,
                "started_at": self.started_at,
            }))
        except (OSError, TypeError, ValueError) as exc:
            # Bookkeeping for `make stop` only — never fail a mission over it.
            log_safe("error", f"mission_scope: scope registration failed: {exc}")

    def _deregister(self) -> None:
        entry = _registry_entry(self._koan_root, self._registry_name)
        if entry is not None:
            try:
                entry.unlink(missing_ok=True)
            except OSError as exc:
                log_safe("error", f"mission_scope: scope deregistration failed: {exc}")

    # -- reporting --------------------------------------------------------

    def cap_message(self) -> str:
        """Human phrase for a cap hit, e.g. ``exceeded memory cap (5.9G of 5.75G)``."""
        if not self.cap_exceeded:
            return ""
        cap = format_size(self.memory_max)
        if self.peak_bytes:
            return f"exceeded memory cap ({format_size(self.peak_bytes)} of {cap})"
        return f"exceeded memory cap ({cap})"

    # -- teardown ---------------------------------------------------------

    def teardown(self, *, koan_initiated_kill: bool = False) -> None:
        """Kill everything the mission left behind. Idempotent.

        This is the piece that was missing: it runs on the **success** path, in
        the same ``finally`` that already reaps ``TMPDIR`` and drops page cache
        — not only on abort/timeout.

        *koan_initiated_kill* says the abort / mission-timeout / stagnation
        paths already SIGKILLed the process group, so a ``-9`` exit here is
        ours and must not be reported as a memory-cap hit.
        """
        if self._torn_down:
            return
        self._torn_down = True
        contained = True
        try:
            if self.mode == "scope" and self.unit:
                self._collect_scope_evidence(koan_initiated_kill)
                contained = self._stop_unit()
            else:
                contained = self._kill_session_group()
        finally:
            if contained:
                self._deregister()
            else:
                # Nothing confirmed containment, and a group kill reaches only
                # what stayed in the group — not the re-parented daemon this
                # module exists to contain. The record is then the only durable
                # handle on what is left, so `make stop` keeps its chance to
                # retry. A scope record self-heals: once the scope really is
                # gone the next `make stop` sees LoadState=not-found and unlinks
                # it. A fallback `pid-<n>` record is start-time-verified there
                # before it is signalled, so a recycled PID is dropped rather
                # than acted on.
                log_safe(
                    "error",
                    f"mission_scope: keeping the registry entry for "
                    f"{self._registry_name} — containment unconfirmed",
                )

    def _kill_session_group(self) -> bool:
        """Fallback-path teardown: take the mission's whole process group.

        ``kill_process_group`` alone is not enough here. It returns at its
        ``proc.poll() is not None`` guard, so on the success path — a mission
        that finished on its own, which is the case this module exists for — it
        signals nothing while descendants left inside the group keep running.
        Signalling the pgid captured at launch reaches them; the ``Popen`` call
        stays for the still-running case, where it can ``wait()`` on the child
        instead of polling.

        Returns whether the group is *confirmed* empty. On this path the group
        is the only containment lever there is, so an EPERM refusal, a group
        that outlived SIGKILL, or a pgid that was never captured must not be
        reported as a clean sweep — the caller keeps its registry record so
        ``make stop`` can try again.
        """
        kill_process_group(self.proc)
        return kill_orphaned_process_group(self._pgid)

    def _collect_scope_evidence(self, koan_initiated_kill: bool) -> None:
        """Read peak / OOM evidence before the stop collects the cgroup.

        Best-effort by nature: ``--collect`` garbage-collects the unit once it
        goes inactive, so on a mission whose whole subtree already exited the
        cgroup may be gone before this runs. The exit-status branch below is
        what keeps the common cap-hit reportable anyway.

        "The evidence could not be read" is a different outcome from "the
        kernel killed nothing", and the two are never collapsed. A failure with
        no readable evidence is logged as such and deliberately *not* reported
        as a cap hit: the collected cgroup of a clean mission is unreadable by
        definition, so believing an unknown would mark ordinary missions as
        capped and, since a cap hit is never retried, silently disable the
        retry path. The mirror rule holds too — a readable ``oom_kill 0`` is
        proof the cap did not fire, so no guess may override it.
        """
        cgroup_dir = _cgroup_dir(self.manager_args, self.unit)
        if cgroup_dir is not None:
            self.peak_bytes = _read_peak_bytes(cgroup_dir)
            self.oom_kills = _read_oom_kills(cgroup_dir)
        if self.oom_kills:
            self.cap_exceeded = True
            return
        unit_result = _unit_property(self.manager_args, self.unit, "Result")
        if unit_result == "oom-kill":
            self.cap_exceeded = True
            return
        if self.oom_kills == 0:
            # The kernel's own counter says nothing inside this scope was
            # OOM-killed. That is definitive, and it is readable in exactly the
            # case this module targets: a leaked daemon keeps the scope
            # populated, so `--collect` has not reaped the cgroup yet. The
            # exit-status guess below must never override it — a global OOM
            # kill triggered by a co-tenant, or a SIGKILL Kōan delivered on a
            # path that does not set koan_initiated_kill, would otherwise be
            # relabelled a cap hit and then permanently barred from retry.
            return
        # No cgroup evidence left to read — the unit was already collected, so
        # self.oom_kills is None rather than a number. A SIGKILL exit under a
        # cap that Kōan did not deliver itself is the cap firing.
        if (
            self.oom_kills is None
            and not koan_initiated_kill
            and self.memory_max
            and self.proc.returncode in (-9, 137)
        ):
            self.cap_exceeded = True
            return
        if (
            self.oom_kills is None
            and not unit_result
            and self.memory_max
            and self.proc.returncode not in (0, None)
        ):
            log_safe(
                "warn",
                f"mission_scope: {self.unit} failed (exit "
                f"{self.proc.returncode}) and neither the cgroup nor the "
                f"manager could say whether the memory cap fired — reporting "
                f"an ordinary failure",
            )

    def _stop_unit(self) -> bool:
        """Stop the scope; fall back to the process group only if that failed.

        Returns whether containment was confirmed, which decides whether the
        registry entry may be dropped.

        The unit is the right lever and the group is not a substitute for it:
        on the success path the mission process has already exited, so
        ``kill_process_group`` returns at its ``poll()`` guard and signals
        nothing, and a daemon that re-parented to PID 1 in its own session has
        left the group even while alive — the very shape this module contains.
        The group is what is left when the manager cannot confirm the scope is
        gone, and it reaches only what stayed inside it.
        """
        if stop_scope_unit(self.manager_args, self.unit):
            return True
        log_safe("error",
                 f"mission_scope: {self.unit} unreachable — killing the group")
        self._kill_session_group()
        return False


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

def _require_executable(argv: List[str]) -> None:
    """Raise the ``FileNotFoundError`` an unwrapped ``Popen`` would have raised.

    Wrapped in ``systemd-run``, a missing provider binary is no longer the
    parent's exec failure — systemd-run starts fine and fails inside the scope,
    so ``run_claude_task``'s ``missing_binary_message`` handler (which matches on
    ``err.filename == cmd[0]``) would never fire. Pre-check so that actionable
    message survives the wrapping.
    """
    if not argv:
        return
    target = str(argv[0])
    resolved = target if os.sep in target else shutil.which(target)
    if resolved and os.path.exists(resolved):
        return
    raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), target)


def _default_spawn(argv: List[str], launcher: List[str], **kwargs) -> subprocess.Popen:
    return subprocess.Popen(launcher + argv, **kwargs)


def scope_launcher(unit: str, memory_max: Optional[int]) -> List[str]:
    """The ``systemd-run`` argv prefix that puts a command in its own scope."""
    binary, manager_args = systemd_run()
    if not binary:
        return []
    launcher = [binary, *manager_args, "--scope", "--collect", "--quiet",
                f"--unit={unit}"]
    if memory_max:
        launcher.append(f"--property=MemoryMax={memory_max}")
        launcher.append(
            f"--property=MemoryHigh={int(memory_max * _MEMORY_HIGH_RATIO)}"
        )
    launcher.append("--")
    return launcher


def launch_scoped(
    argv: List[str],
    *,
    config: Optional[dict] = None,
    spawn: Optional[Callable[..., subprocess.Popen]] = None,
    koan_root: Optional[str] = None,
    **popen_kwargs,
) -> ScopedProcess:
    """Start *argv* inside a cgroup scope that will contain every descendant.

    *spawn* is what actually starts the process; it receives
    ``(argv, launcher, **popen_kwargs)`` and must return a ``Popen``. The
    default prepends *launcher* and calls ``subprocess.Popen``;
    ``run_claude_task`` passes a wrapper around ``cli_exec.popen_cli`` so the
    provider's prompt-file stdin and invocation lock still apply — the launcher
    is prefixed there *after* the prompt rewrite, never before it.

    ``start_new_session=True`` is always set: the abort, mission-timeout and
    stagnation paths still ``killpg`` the mission, and the cgroup teardown is
    an addition to that, not a replacement.
    """
    if config is None:
        from app.config import get_mission_limits_config
        config = get_mission_limits_config()
    spawn = spawn or _default_spawn
    popen_kwargs["start_new_session"] = True

    scope_kwargs = {"koan_root": koan_root}

    if not config.get("enabled", True):
        return ScopedProcess(
            spawn(argv, [], **popen_kwargs), mode="session", **scope_kwargs,
        )

    binary, manager_args = systemd_run()
    if not binary:
        _warn_fallback_once("systemd-run unavailable")
        return ScopedProcess(
            spawn(argv, [], **popen_kwargs), mode="session", **scope_kwargs,
        )

    memory_max = resolve_memory_max(config)
    unit = f"{UNIT_PREFIX}{uuid.uuid4().hex}{UNIT_SUFFIX}"
    _require_executable(argv)
    try:
        proc = spawn(argv, scope_launcher(unit, memory_max), **popen_kwargs)
    except FileNotFoundError:
        # The provider binary is missing — the caller's own handler owns this.
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        _warn_fallback(f"scope {unit} failed to start: {exc}")
        return ScopedProcess(
            spawn(argv, [], **popen_kwargs), mode="session", **scope_kwargs,
        )
    return ScopedProcess(
        proc, unit=unit, manager_args=manager_args, mode="scope",
        memory_max=memory_max, **scope_kwargs,
    )
