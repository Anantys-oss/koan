"""Hook system for extensible pre/post-action events.

Discovers lifecycle hooks from two locations at startup:

1. Instance-wide hooks: ``instance/hooks/<name>.py`` — any module name; the
   module exports a ``HOOKS`` dict mapping event names to callables. These
   run first for every event, across all skills and projects.

2. Skill-bound hooks: ``instance/skills/<scope>/<name>/<event>.py`` — the
   filename is the event name (e.g. ``post_mission.py``) and the module
   exports a ``run(ctx)`` function. These run after instance-wide hooks and
   let a custom skill own its lifecycle behavior without touching Kōan core.

Both flavors are fire-and-forget: errors are logged to stderr but never
block the agent loop.

Example instance-wide hook (instance/hooks/my_hook.py):

    def on_post_mission(ctx):
        print(f"Mission completed: {ctx['mission_title']}")

    HOOKS = {
        "post_mission": on_post_mission,
    }

Example skill-bound hook (instance/skills/my/fix/post_mission.py):

    def run(ctx):
        if "myfix" not in ctx.get("mission_title", ""):
            return
        # ... skill-owned post-mission work ...

Supported events:
    - session_start: Fired after startup completes
    - session_end: Fired on shutdown (in finally block)
    - pre_mission: Fired before Claude execution
    - post_mission: Fired after post-mission pipeline completes
    - post_review: Fired after a PR review is successfully posted

Automation rules:
    Declarative rules from instance/automation_rules.yaml are evaluated
    after user hook modules on every fire() call. Each rule maps an event
    to an action (notify, create_mission, pause, resume, auto_merge).
    A per-rule loop guard prevents runaway rule execution.
"""

import contextlib
import importlib.util
import os
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from app.automation_rules import AutomationRule, load_rules


_VALID_SKILL_HOOK_EVENTS = (
    "session_start",
    "session_end",
    "pre_mission",
    "post_mission",
    "post_review",
)

# Delimited token stamped into every mission queued by a project hook skill.
# Doubles as the self-replication guard (a mission carrying it does not queue
# more hook skills) and the exact-match dedup key (so ``docs`` is never masked
# by an already-queued ``docs-lint``).
_HOOK_SKILL_MARKER_PREFIX = "[hook-skill:"

# Delimited token stamped alongside the skill marker so the dedup below matches
# the subject exactly rather than as a bare substring. Without the closing ``]``,
# a shorter PR URL nests inside a longer one (``pull/7`` is a substring of
# ``pull/70``) and would be wrongly treated as already queued.
_HOOK_SUBJECT_MARKER_PREFIX = "[hook-subject:"


def _hook_subject(ctx: dict) -> str:
    """Return the firing event's subject, normalized as the mission store holds it.

    The subject (the PR URL, else the mission title) is the dedup key stamped
    into every queued entry, so it MUST be normalized exactly as the store will
    normalize it — otherwise the token written here is not the token the next
    fire searches for and every re-fire re-queues. A ``mission_title`` arrives
    carrying its ⏳/▶ lifecycle timestamps and ``[complexity:…]``/``[r:N]``
    metadata, all of which the store strips on ingest; newlines are flattened to
    spaces by ``insert_mission``. An embedded ⏳ is doubly harmful —
    ``insert_mission`` would treat the entry as already stamped and the new
    mission would inherit the previous mission's queue time.

    Returns ``""`` when the event carries no subject at all, which the caller
    treats as "queue nothing" (see :meth:`_fire_project_hook_skills`).
    """
    from app.missions import strip_all_lifecycle_markers, strip_system_metadata

    raw = str(ctx.get("pr_url") or ctx.get("mission_title") or "")
    return " ".join(strip_system_metadata(strip_all_lifecycle_markers(raw)).split())


def _trusted_project_path(ctx: dict) -> Optional[str]:
    """Return the operator-registered checkout for the firing project, if any.

    ``ctx["project_path"]`` is NOT a trusted source of repo config. On the
    ``post_review`` path it is a detached worktree of the **pull request head**
    (``review_runner.run_review`` pins it via ``pinned_review_worktree``), so a
    ``.koan/config.yaml`` read from it is whatever the *contributor* committed,
    not what the repo owner did. Reading hook skills there would let any PR
    author queue a write-capable mission on the operator's quota.

    Resolve the path from the project registry instead — ``project_name`` first,
    then ``project_path`` but only when it *is* a registered checkout. Anything
    else (a review worktree, an unregistered directory) returns ``None`` and the
    caller queues nothing.
    """
    from app.utils import get_known_projects

    known = get_known_projects()
    name = str(ctx.get("project_name") or "").strip().lower()
    if name:
        for pname, ppath in known:
            if str(pname).strip().lower() == name:
                return str(ppath)
    raw = str(ctx.get("project_path") or "")
    if not raw:
        return None
    target = os.path.realpath(raw)
    for _pname, ppath in known:
        if os.path.realpath(str(ppath)) == target:
            return str(ppath)
    return None


class HookRegistry:
    """Discovers and manages hook modules from a directory."""

    def __init__(self, hooks_dir: Path, instance_dir: Optional[str] = None):
        self._handlers: Dict[str, List[Callable]] = {}
        self._instance_dir: Optional[str] = instance_dir
        # Per-rule fire timestamps for the loop guard: {rule_id: [timestamp, ...]}
        self._rule_fire_times: Dict[str, List[float]] = defaultdict(list)
        self._discover(hooks_dir)
        # Also discover skill-bound hooks under instance/skills/<scope>/<name>/.
        # Instance-wide hooks above are registered first, so they fire first
        # for each event; skill-bound hooks run afterward.
        if instance_dir:
            self._discover_skill_hooks(Path(instance_dir) / "skills")

    def _discover(self, hooks_dir: Path) -> None:
        """Scan hooks_dir for .py files and register their HOOKS dicts."""
        if not hooks_dir.is_dir():
            return

        for hook_file in sorted(hooks_dir.glob("*.py")):
            if hook_file.name.startswith("_"):
                continue
            try:
                self._load_module(hook_file)
            except Exception as e:
                print(
                    f"[hooks] Failed to load {hook_file.name}: {e}",
                    file=sys.stderr,
                )

    def _load_module(self, path: Path) -> None:
        """Load a single hook module and register its HOOKS dict."""
        module_name = f"koan_hook_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        hooks_dict = getattr(module, "HOOKS", None)
        if not isinstance(hooks_dict, dict):
            return

        for event_name, handler in hooks_dict.items():
            if callable(handler):
                self._handlers.setdefault(event_name, []).append(handler)

    def _discover_skill_hooks(self, skills_root: Path) -> None:
        """Scan instance/skills/<scope>/<name>/ for <event>.py lifecycle modules.

        Convention: the file name is the event name (e.g. ``post_mission.py``)
        and the module exports a ``run(ctx)`` function. This lets a custom
        skill own its lifecycle behavior alongside its handler.py without
        touching Kōan core.
        """
        if not skills_root.is_dir():
            return

        for scope_dir in sorted(skills_root.iterdir()):
            if not scope_dir.is_dir() or scope_dir.name.startswith((".", "_")):
                continue
            for skill_dir in sorted(scope_dir.iterdir()):
                if not skill_dir.is_dir() or skill_dir.name.startswith((".", "_")):
                    continue
                # Only probe known event filenames — any other .py file in the
                # skill directory (handler.py, helpers.py, utils.py, …) is
                # silently ignored, not registered under a nonsense event.
                for event_name in _VALID_SKILL_HOOK_EVENTS:
                    hook_file = skill_dir / f"{event_name}.py"
                    if not hook_file.is_file():
                        continue
                    try:
                        self._load_skill_module(
                            hook_file, event_name, scope_dir.name, skill_dir.name,
                        )
                    except Exception as exc:
                        print(
                            f"[hooks] Failed to load skill hook "
                            f"{scope_dir.name}/{skill_dir.name}/{hook_file.name}: {exc}",
                            file=sys.stderr,
                        )

    def _load_skill_module(
        self, path: Path, event_name: str, scope: str, name: str,
    ) -> None:
        """Load a skill hook module and register its ``run`` function."""
        module_name = f"koan_skill_hook_{scope}_{name}_{event_name}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        handler = getattr(module, "run", None)
        if not callable(handler):
            print(
                f"[hooks] Skill hook {scope}/{name}/{event_name}.py has no "
                f"callable run() — skipping.",
                file=sys.stderr,
            )
            return
        self._handlers.setdefault(event_name, []).append(handler)

    def fire(self, event: str, **kwargs) -> Dict[str, str]:
        """Call all handlers for event, catching exceptions per-handler.

        After user hook modules execute, evaluates matching automation rules
        from instance/automation_rules.yaml (if instance_dir was provided),
        then the reviewed project's own ``hooks.<event>`` skill list from its
        ``.koan/config.yaml`` (if the event carries a ``project_path``).

        Returns a dict mapping failed handler names to error messages.
        Empty dict means all handlers succeeded.
        """
        failures: Dict[str, str] = {}
        handlers = self._handlers.get(event, [])
        for handler in handlers:
            func_name = getattr(handler, "__name__", repr(handler))
            module_name = getattr(handler, "__module__", "")
            handler_name = f"{module_name}.{func_name}" if module_name else func_name
            try:
                handler(kwargs)
            except Exception as exc:
                failures[handler_name] = str(exc)
                print(
                    f"[hooks] Error in {event} handler "
                    f"{handler_name}:\n"
                    f"{traceback.format_exc()}",
                    file=sys.stderr,
                )

        # Execute matching automation rules
        if self._instance_dir is not None:
            self._fire_automation_rules(event, kwargs)
            self._fire_project_hook_skills(event, kwargs)

        return failures

    def has_hooks(self, event: str) -> bool:
        """Check if any hooks are registered for event."""
        return bool(self._handlers.get(event))

    # ------------------------------------------------------------------
    # Project-declared hook skills (.koan/config.yaml)
    # ------------------------------------------------------------------

    def _fire_project_hook_skills(self, event: str, ctx: dict) -> None:
        """Queue a mission per skill the reviewed repo declared for *event*.

        Reads ``hooks.<event>`` from the project's own ``.koan/config.yaml``,
        letting a repo owner wire a skill to a lifecycle event without the
        operator writing a Python hook.

        The config is read from the **operator-registered checkout**
        (:func:`_trusted_project_path`), never from the path the event carries:
        on ``post_review`` that path is a worktree of the PR head, so trusting
        it would hand the choice of skill to the contributor.

        The repo supplies skill *names* only and the mission sentence is
        composed here, so a config committed by whoever can open a pull
        request cannot inject instructions into the write-capable mission that
        will run the skill.

        Queued rather than executed: handlers run inline in the firing
        process, and a skill pipeline can take minutes. The mission loop is
        also the path that loads the project's own Claude Code skills, which
        a read-only review subprocess does not.

        Only events that carry a subject (a PR URL or a mission title) queue
        anything — the subject is the dedup key, and an event without one would
        re-queue on every fire.

        Fire-and-forget — a broken repo config never disturbs the event.
        """
        project_path = ctx.get("project_path")
        if not project_path or self._instance_dir is None:
            return
        # A mission this mechanism queued must not queue more hook skills, or a
        # repo declaring hooks.post_mission (or pre_mission) would self-replicate
        # without bound: each queued mission's own post_mission would re-queue,
        # forever. The marker embedded in the queued entry rides along in
        # mission_title, so its presence means we are already inside such a
        # mission — stop the chain here.
        if _HOOK_SKILL_MARKER_PREFIX in str(ctx.get("mission_title") or ""):
            return
        try:
            from app.project_koan import get_hook_skills

            # Every queued entry is de-duplicated on its subject, so an event
            # that carries none has no identity to match against and would
            # re-queue on every fire. That is not hypothetical: koan's own
            # autonomous and contemplative iterations run through this same
            # pre_mission/post_mission path with an empty mission_title and no
            # pr_url, so a project that merely declares hooks.post_mission would
            # otherwise ping-pong autonomous session → hook-skill mission →
            # autonomous session indefinitely. Subject-less events queue
            # nothing, by contract.
            subject = _hook_subject(ctx)
            if not subject:
                return
            trusted = _trusted_project_path(ctx)
            if not trusted:
                print(
                    f"[hooks] {event}: no registered checkout for "
                    f"{ctx.get('project_name') or project_path} — hook skills skipped",
                    file=sys.stderr,
                )
                return
            skills = get_hook_skills(trusted, event)
        except Exception as exc:
            print(
                f"[hooks] project hook skills failed for {event}: {exc}",
                file=sys.stderr,
            )
            return
        # Per-skill isolation: one failing queue (a locked store, an OSError on
        # the export write) must not cancel the skills after it — post_review
        # fires once per review, so a skipped sibling is lost for good.
        for skill in skills:
            try:
                self._queue_hook_skill(event, skill, ctx, subject)
            except Exception as exc:
                print(
                    f"[hooks] {event}: could not queue {skill}: {exc}",
                    file=sys.stderr,
                )

    def _queue_hook_skill(
        self, event: str, skill: str, ctx: dict, subject: str
    ) -> None:
        """Append one pending mission running *skill*, unless already queued.

        *subject* is the already-normalized, non-empty identity of the firing
        event (see :func:`_hook_subject`); it is what the dedup below keys on.
        """
        from app.missions import insert_mission, parse_sections
        from app.utils import modify_missions_file

        project = str(ctx.get("project_name") or "").strip()
        prefix = f"[project:{project}] " if project else ""
        # The marker is a stable, delimited token — both the self-replication
        # guard and the dedup below match on it exactly, so a skill name can
        # never be masked by a longer name it is a substring of (docs vs.
        # docs-lint), and a queued mission is recognizable as this mechanism's.
        marker = f"{_HOOK_SKILL_MARKER_PREFIX}{skill}]"
        subject_marker = f"{_HOOK_SUBJECT_MARKER_PREFIX}{subject}]"
        entry = (
            f"{prefix}Use the {skill} skill for {subject}. Queued by the {event} "
            f"lifecycle event via .koan/config.yaml. {marker}{subject_marker}"
        )

        missions_path = Path(self._instance_dir) / "missions.md"
        inserted = False

        def _transform(content: str) -> str:
            # insert_pending_mission only dedups entries shaped like
            # "/<command> <github-url>", so a composed sentence needs its own
            # check or a re-review would queue the same work twice. Doing it
            # inside the locked read-modify-write closes the TOCTOU window: the
            # review subprocess and the run loop can fire the same event
            # concurrently, and an unlocked read would let both observe "not
            # queued" and both insert.
            nonlocal inserted
            sections = parse_sections(content)
            queued = sections.get("pending", []) + sections.get("in_progress", [])
            # Both tokens are delimited by a closing ``]`` and matched exactly,
            # so neither a longer skill name nor a longer PR URL can mask this
            # one (``docs`` vs ``docs-lint``; ``pull/7`` vs ``pull/70``).
            if any(marker in item and subject_marker in item for item in queued):
                return content
            inserted = True
            return insert_mission(content, entry)

        modify_missions_file(missions_path, _transform)
        if inserted:
            print(
                f"[hooks] {event}: queued {skill} for {subject}",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # Automation rules
    # ------------------------------------------------------------------

    def _fire_automation_rules(self, event: str, ctx: dict) -> None:
        """Evaluate and execute all enabled rules matching event."""
        try:
            rules = load_rules(self._instance_dir)
        except Exception as exc:
            print(f"[hooks] Failed to load automation rules: {exc}", file=sys.stderr)
            return

        matching = [r for r in rules if r.event == event and r.enabled]
        if not matching:
            return

        from app.utils import load_config
        try:
            config = load_config() or {}
        except Exception as exc:
            print(f"[hooks] Could not load config for loop guard: {exc}", file=sys.stderr)
            config = {}
        max_fires = config.get("automation_rules", {}).get("max_fires_per_minute", 5)

        for rule in matching:
            if self._loop_guard(rule, max_fires=max_fires):
                print(
                    f"[hooks] Loop guard triggered for rule {rule.id} "
                    f"(action={rule.action}) — skipping.",
                    file=sys.stderr,
                )
                continue
            try:
                self._execute_rule(rule, ctx)
                self._write_rule_journal(rule)
            except Exception as exc:
                print(
                    f"[hooks] Error executing automation rule {rule.id} "
                    f"({rule.event} → {rule.action}): {exc}\n"
                    f"{traceback.format_exc()}",
                    file=sys.stderr,
                )

    def _loop_guard(self, rule: AutomationRule, max_fires: int = 5) -> bool:
        """Return True (skip) if rule has exceeded max_fires_per_minute.

        The counter is in-memory and resets on process restart. The threshold
        is resolved once per event by the caller (from instance/config.yaml
        under automation_rules.max_fires_per_minute, default 5) so all rules
        in the same event fire see a consistent value.
        """
        window = 60.0  # seconds
        now = time.monotonic()

        # Prune old timestamps outside the window
        self._rule_fire_times[rule.id] = [
            t for t in self._rule_fire_times[rule.id] if now - t < window
        ]

        if len(self._rule_fire_times[rule.id]) >= max_fires:
            return True  # over limit — skip

        self._rule_fire_times[rule.id].append(now)
        return False

    def _execute_rule(self, rule: AutomationRule, ctx: dict) -> None:
        """Execute a single automation rule action. Fire-and-forget."""
        instance_dir = self._instance_dir
        action = rule.action
        params = rule.params or {}

        if action == "notify":
            self._action_notify(instance_dir, params, ctx)
        elif action == "create_mission":
            self._action_create_mission(instance_dir, params, ctx)
        elif action == "pause":
            self._action_pause(instance_dir)
        elif action == "resume":
            self._action_resume(instance_dir)
        elif action == "auto_merge":
            self._action_auto_merge(instance_dir, ctx)
        else:
            print(f"[hooks] Unknown action '{action}' in rule {rule.id}", file=sys.stderr)

    def _action_notify(self, instance_dir: str, params: dict, ctx: dict) -> None:
        """Append a message to instance/outbox.md."""
        message = params.get("message", "Automation rule fired.")
        outbox_path = Path(instance_dir) / "outbox.md"
        from app.utils import atomic_write
        existing = outbox_path.read_text() if outbox_path.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        atomic_write(outbox_path, existing + f"- {message}\n")

    def _action_create_mission(self, instance_dir: str, params: dict, ctx: dict) -> None:
        """Append a mission to the Pending section of instance/missions.md."""
        text = params.get("text", "Automation rule: create mission")
        missions_path = Path(instance_dir) / "missions.md"
        from app.utils import insert_pending_mission
        insert_pending_mission(missions_path, text)

    def _action_pause(self, instance_dir: str) -> None:
        """Pause the agent via the pause_manager protocol.

        A direct one-line write produces a malformed pause file (no
        timestamp), which strands `should_auto_resume` on its
        `timestamp <= 0` early-return — the pause never lifts. Going
        through `create_pause` stamps the current time so the standard
        5h cooldown applies.
        """
        from app.pause_manager import create_pause
        koan_root = str(Path(instance_dir).parent)
        create_pause(
            koan_root,
            reason="automation_rule",
            display="Paused by automation rule",
        )

    def _action_resume(self, instance_dir: str) -> None:
        """Remove .koan-pause if it exists."""
        pause_file = Path(instance_dir).parent / ".koan-pause"
        # Already absent — idempotent
        with contextlib.suppress(FileNotFoundError):
            pause_file.unlink()

    def _action_auto_merge(self, instance_dir: str, ctx: dict) -> None:
        """Call git_auto_merge.auto_merge_branch() if project context present."""
        project_path = ctx.get("project_path")
        project_name = ctx.get("project_name")
        branch = ctx.get("branch")
        if not project_path or not project_name:
            print(
                "[hooks] auto_merge action skipped — project_path or project_name absent in ctx.",
                file=sys.stderr,
            )
            return
        if not branch:
            # Try to read current branch from git
            import subprocess
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                branch = result.stdout.strip()
            except Exception as exc:
                print(f"[hooks] auto_merge: failed to get branch: {exc}", file=sys.stderr)
                return
        from app.git_auto_merge import auto_merge_branch
        auto_merge_branch(instance_dir, project_name, project_path, branch)

    def _write_rule_journal(self, rule: AutomationRule) -> None:
        """Write a [automation_rule]-tagged entry to today's journal."""
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            journal_dir = Path(self._instance_dir) / "journal" / today
            journal_dir.mkdir(parents=True, exist_ok=True)
            journal_file = journal_dir / "automation.md"
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            entry = f"[automation_rule] {ts} rule={rule.id} event={rule.event} action={rule.action}\n"
            with open(journal_file, "a") as f:
                f.write(entry)
        except Exception as exc:
            print(f"[hooks] Failed to write rule journal: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: Optional[HookRegistry] = None


def init_hooks(instance_dir: str) -> None:
    """Initialize the global hook registry from instance/hooks/.

    Creates the hooks directory if it doesn't exist.
    Safe to call multiple times — reinitializes the registry.
    """
    global _registry
    hooks_dir = Path(instance_dir) / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    _registry = HookRegistry(hooks_dir, instance_dir=instance_dir)


def read_automation_rules(instance_dir: str) -> list:
    """Load and return automation rules from instance/automation_rules.yaml."""
    return load_rules(instance_dir)


def fire_hook(event: str, **kwargs) -> Dict[str, str]:
    """Fire a hook event. No-op if registry not initialized.

    Returns a dict mapping failed handler names to error messages.
    Empty dict means all handlers succeeded (or no registry).
    """
    if _registry is not None:
        return _registry.fire(event, **kwargs)
    return {}


def get_registry() -> Optional[HookRegistry]:
    """Return the current registry (for testing)."""
    return _registry


def reset_registry() -> None:
    """Reset the global registry to None (for testing)."""
    global _registry
    _registry = None
