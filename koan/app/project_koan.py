"""Readers for a target project's optional .koan/ steering tree.

Raw-content readers only — framing (system-prompt templates) stays with the
callers next to their templates. Mirrors prompt_builder._get_koan_md_section's
absent/blank/unreadable handling: absent is the normal case (no log); a
present-but-unreadable file warns and is treated as empty.
"""
import logging
import re
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_MAX_KOAN_MD_CHARS = 16000
_MAX_KOAN_SKILL_CHARS = 16000

# Caps for the review.always_check pin list — bound worst-case (files × patterns)
# matching work so a pathological repo config cannot degrade review latency.
_MAX_ALWAYS_CHECK_PATTERNS = 100
_MAX_PATTERN_LEN = 200

# Caps for mission hooks (pre_hooks/post_hooks) — bound how many commands a repo
# config can queue and how long each command string may be, so a pathological or
# hostile config cannot flood execution/logging. Execution is separately gated by
# an operator opt-in (see app.mission_hooks); these caps are the parse-time guard.
_MAX_HOOKS_PER_LIST = 20
_MAX_HOOK_CMD_LEN = 1000

# The two hook phases and the config sub-key each maps to.
_HOOK_PHASES = {"pre": "pre_hooks", "post": "post_hooks"}

# Caps for the hooks.<event> skill lists — one lifecycle event must not be able
# to queue an unbounded number of missions.
_MAX_HOOK_SKILLS = 10
_MAX_SKILL_NAME_LEN = 64

# Skill names only: lowercase, digits and hyphens. These values reach an
# agent's prompt in a write-capable mission, so anything that could carry an
# instruction, a path or a shell fragment is rejected outright rather than
# sanitized. A repo owner chooses *which* skill runs; they never supply prose.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Branch names tried when a checkout's remote HEAD symbolic ref was never set
# locally (``git clone`` sets it; ``git remote add`` does not).
_TRUSTED_BRANCH_FALLBACKS = ("main", "master")


def log_context_load(label: str, content: str) -> None:
    """Announce a steering file koan just loaded into a prompt, for ``make logs``.

    Emits ``Detected <label>, loaded N chars (~ M tokens)`` on **stderr** so it
    lands in ``logs/run.log`` (visible via ``make logs``) without ever
    corrupting the JSON some skill runners write to stdout. The ``logging``
    module has no stdout/stderr handler wired in the run loop, so ``logger.info``
    alone would be invisible there — hence the direct ``print``.

    Best-effort: a broken stream (or a missing ``estimate_tokens``) must never
    break prompt assembly, so every failure is swallowed — logged at debug so it
    stays visible without ever raising.
    """
    try:
        from app.diff_compressor import estimate_tokens
        print(
            f"[context] Detected {label}, loaded {len(content)} chars "
            f"(~ {estimate_tokens(content)} tokens)",
            file=sys.stderr,
            flush=True,
        )
    except Exception as e:
        logger.debug("log_context_load failed for %s: %s", label, e)


def _read_or_empty(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except FileNotFoundError:
        return ""
    except OSError as e:
        logger.warning("present but unreadable at %s: %s", path, e)
        return ""


def _cap(content: str, limit: int, label: str) -> str:
    if len(content) > limit:
        return content[:limit] + f"\n\n[{label} truncated — exceeded {limit} chars]"
    return content


def read_general_koan_md(project_path: str) -> str:
    """Root KOAN.md + .koan/KOAN.md, stripped, concatenated (root first).

    Returns "" when project_path is empty or both sources are absent/blank.
    The combined length is capped at _MAX_KOAN_MD_CHARS.
    """
    if not project_path:
        return ""
    root = _read_or_empty(Path(project_path) / "KOAN.md")
    dot = _read_or_empty(Path(project_path) / ".koan" / "KOAN.md")
    parts = []
    if root:
        parts.append(root)
    if dot:
        parts.append(f"# .koan/KOAN.md\n\n{dot}")
    if not parts:
        return ""
    return _cap("\n\n".join(parts), _MAX_KOAN_MD_CHARS, "KOAN.md")


def read_skill_instructions(project_path: str, skill_name: str) -> str:
    """Concatenate <project>/.koan/skills/<skill_name>/*.md, sorted by filename.

    Each fragment is prefixed with a `# <filename>` provenance marker. Ignores
    non-.md files and subdirectories. Returns "" when absent/empty/all-blank.
    Capped at _MAX_KOAN_SKILL_CHARS.
    """
    if not project_path or not skill_name:
        return ""
    skill_dir = Path(project_path) / ".koan" / "skills" / skill_name
    if not skill_dir.is_dir():
        return ""
    parts = []
    for md in sorted(skill_dir.glob("*.md"), key=lambda p: p.name):
        if not md.is_file():
            continue
        body = _read_or_empty(md)
        if body:
            parts.append(f"# {md.name}\n\n{body}")
    if not parts:
        return ""
    return _cap("\n\n".join(parts), _MAX_KOAN_SKILL_CHARS, ".koan skill instructions")


_MAX_CONVENTION_DOC_CHARS = 16000    # per-source cap (applied before the block cap)
_MAX_CONVENTION_BLOCK_CHARS = 16000  # whole-block cap

# Well-known root convention files, in signal-priority order.
_WELL_KNOWN_CONVENTION_FILES = ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md")

# OKF bundle: the small, high-signal root pages worth injecting whole.
_OKF_ROOT_DOCS = ("index.md", "SPEC.md", "SCHEMA.md")


def read_repo_convention_docs(
    project_path: str,
    *,
    well_known=_WELL_KNOWN_CONVENTION_FILES,
    okf_docs_dir: str = "docs",
    include_topic_indexes: bool = True,
    auto_detect_okf: bool = True,
    max_source_chars: int = _MAX_CONVENTION_DOC_CHARS,
    max_block_chars: int = _MAX_CONVENTION_BLOCK_CHARS,
) -> str:
    """Concatenate a repo's own convention/knowledge docs, provenance-labelled.

    Sources, in priority order:
      1. Well-known root files (AGENTS.md, CLAUDE.md, CONTRIBUTING.md).
      2. An OKF/docs bundle detected by ``<docs>/index.md``: the curated bundle
         index + SPEC.md + SCHEMA.md, plus (optionally) each topic folder's
         generated ``index.md`` catalog — never the full topic pages, which the
         reviewer can Read on demand.

    Each fragment is prefixed with a ``# <relpath>`` provenance marker (matching
    :func:`read_skill_instructions`). De-dupes by resolved realpath so an
    ``AGENTS.md -> CLAUDE.md`` symlink is read once. Per-source content is capped
    at ``max_source_chars``; the whole block at ``max_block_chars``. Returns ""
    when ``project_path`` is empty or nothing is found.
    """
    if not project_path:
        return ""
    root = Path(project_path)
    parts: list = []
    seen: set = set()

    def _add(rel: str, path: Path) -> None:
        if not path.is_file():
            return
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            return
        seen.add(key)
        body = _read_or_empty(path)
        if body:
            parts.append(f"# {rel}\n\n{_cap(body, max_source_chars, rel)}")

    for name in well_known:
        _add(str(name), root / str(name))

    if auto_detect_okf and okf_docs_dir:
        docs = root / okf_docs_dir
        if (docs / "index.md").is_file():
            for name in _OKF_ROOT_DOCS:
                _add(f"{okf_docs_dir}/{name}", docs / name)
            if include_topic_indexes:
                try:
                    topic_indexes = sorted(
                        docs.glob("*/index.md"), key=lambda p: p.as_posix())
                except OSError as e:
                    logger.warning(
                        "topic-index glob failed under %s: %s", docs, e)
                    topic_indexes = []
                for idx in topic_indexes:
                    rel = f"{okf_docs_dir}/{idx.parent.name}/index.md"
                    _add(rel, idx)

    if not parts:
        return ""
    return _cap("\n\n".join(parts), max_block_chars, "repo convention docs")


def read_koan_config(project_path: str) -> dict:
    """Parse <project_path>/.koan/config.yaml into a dict.

    A generic, extensible per-repo config surface (distinct from the operator's
    KOAN_ROOT instance/config.yaml). Fail-safe by contract: returns ``{}`` when
    the file is absent, empty, unreadable, unparseable, or its top level is not a
    mapping. Never raises — a broken repo config must never abort a review.
    """
    if not project_path:
        return {}
    path = Path(project_path) / ".koan" / "config.yaml"
    return _parse_koan_config(_read_or_empty(path), str(path))


def _parse_koan_config(text: str, source: str) -> dict:
    """Parse .koan/config.yaml *text* into a dict, or ``{}``. Never raises."""
    if not text:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        logger.warning("unparseable .koan/config.yaml at %s: %s", source, e)
        return {}
    if not isinstance(data, dict):
        logger.warning(".koan/config.yaml top level is not a mapping at %s", source)
        return {}
    return data


def get_review_always_check(project_path: str) -> list[str]:
    """Return the honored ``review.always_check`` glob list from .koan/config.yaml.

    Returns ``[]`` unless the value is a list; keeps only non-blank ``str`` items,
    caps at ``_MAX_ALWAYS_CHECK_PATTERNS`` patterns of ``_MAX_PATTERN_LEN`` chars
    each (dropping the excess with one diagnostic). Fail-safe; never raises.
    """
    review = read_koan_config(project_path).get("review")
    if not isinstance(review, dict):
        return []
    raw = review.get("always_check")
    if not isinstance(raw, list):
        return []
    patterns: list[str] = []
    dropped_long = False
    for item in raw:
        if not isinstance(item, str):
            continue
        pat = item.strip()
        if not pat:
            continue
        if len(pat) > _MAX_PATTERN_LEN:
            dropped_long = True
            continue
        patterns.append(pat)
    if dropped_long:
        logger.warning(
            "dropped over-long review.always_check pattern(s) (> %d chars)",
            _MAX_PATTERN_LEN,
        )
    if len(patterns) > _MAX_ALWAYS_CHECK_PATTERNS:
        logger.warning(
            "review.always_check capped at %d patterns (had %d)",
            _MAX_ALWAYS_CHECK_PATTERNS,
            len(patterns),
        )
        patterns = patterns[:_MAX_ALWAYS_CHECK_PATTERNS]
    return patterns


def _normalize_hook_commands(raw: object) -> list[str]:
    """Validate a raw ``pre_hooks``/``post_hooks`` value into a command list.

    Keeps only non-blank ``str`` items, drops entries longer than
    ``_MAX_HOOK_CMD_LEN``, and caps the list at ``_MAX_HOOKS_PER_LIST`` — logging
    one diagnostic per kind of drop. Any non-list value yields ``[]``. Never raises.
    """
    if not isinstance(raw, list):
        return []
    commands: list[str] = []
    dropped_long = False
    for item in raw:
        if not isinstance(item, str):
            continue
        cmd = item.strip()
        if not cmd:
            continue
        if len(cmd) > _MAX_HOOK_CMD_LEN:
            dropped_long = True
            continue
        commands.append(cmd)
    if dropped_long:
        logger.warning(
            "dropped over-long mission hook command(s) (> %d chars)",
            _MAX_HOOK_CMD_LEN,
        )
    if len(commands) > _MAX_HOOKS_PER_LIST:
        logger.warning(
            "mission hook list capped at %d commands (had %d)",
            _MAX_HOOKS_PER_LIST,
            len(commands),
        )
        commands = commands[:_MAX_HOOKS_PER_LIST]
    return commands


def get_mission_hooks(project_path: str, mission_type: str, phase: str) -> list[str]:
    """Resolve the ``pre_hooks``/``post_hooks`` command list from .koan/config.yaml.

    ``phase`` is ``"pre"`` or ``"post"``; ``mission_type`` is the canonical mission
    command name (e.g. ``"review"``), or ``""`` for a non-skill mission.

    Precedence is **replace, per phase**: if the ``<mission_type>`` section defines a
    non-empty list for this phase it is returned and the ``default`` list is NOT also
    included; otherwise the ``default`` list for this phase; otherwise ``[]``. An empty
    ``mission_type`` matches no type section, so only ``default`` applies. Pure and
    fail-safe — reads via :func:`read_koan_config` and never raises.
    """
    key = _HOOK_PHASES.get(phase)
    if not key:
        return []
    config = read_koan_config(project_path)

    def _section_list(section_name: str) -> list[str]:
        section = config.get(section_name)
        if not isinstance(section, dict):
            return []
        return _normalize_hook_commands(section.get(key))

    if mission_type:
        typed = _section_list(mission_type)
        if typed:
            return typed
    return _section_list("default")


def _select_trusted_remote(remotes: list[str]) -> str:
    """Pick the remote whose branches the repo *owner* controls, or "".

    Only one remote can be trusted: when Kōan rebases a pull request it adds the
    **contributor's fork** as a second remote in this same checkout, and those
    branches are exactly what must not be trusted here. ``origin`` wins; a
    single-remote repo uses that remote; anything more ambiguous returns ``""``
    and the caller reads nothing rather than guessing.
    """
    if "origin" in remotes:
        return "origin"
    if len(remotes) == 1:
        return remotes[0]
    return ""


def _trusted_config_ref(project_path: str, remote: str) -> str:
    """Return the remote-tracking ref of *remote*'s default branch, or "".

    Prefers the local symbolic ref ``refs/remotes/<remote>/HEAD`` (set by
    ``git clone``); falls back to the conventional default branch names when it
    was never set. Never queries the network — this runs inside a lifecycle
    event and must stay cheap.
    """
    from app.git_utils import run_git

    rc, head, _ = run_git(
        "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD",
        cwd=project_path,
    )
    if rc == 0 and head.strip():
        return head.strip()
    for branch in _TRUSTED_BRANCH_FALLBACKS:
        rc, _, _ = run_git(
            "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}",
            cwd=project_path,
        )
        if rc == 0:
            return f"{remote}/{branch}"
    return ""


def read_trusted_koan_config(project_path: str) -> dict:
    """Parse .koan/config.yaml as the repo *owner* published it, not as checked out.

    :func:`read_koan_config` reads the work tree, and the work tree of a
    registered checkout is not owner-controlled: Kōan itself parks it on
    arbitrary contributor branches (``rebase_pr._checkout_pr_branch`` runs
    ``git checkout -B <branch> <fork-remote>/<branch>`` right there), and a
    timeout, a stagnation kill or a crash leaves it parked. For keys that grant
    *execution* — ``hooks.<event>`` — the blob is therefore read from the
    default branch of the trusted remote instead, which needs write access to
    change, so a pull request cannot occupy it.

    The work tree is used only when nothing external can land in it: a
    non-git directory, or a git repo with no remote at all. When the trusted
    ref cannot be resolved, returns ``{}`` — fail-safe, never raises.
    """
    if not project_path:
        return {}
    from app.git_utils import run_git

    rc, _, _ = run_git("rev-parse", "--git-dir", cwd=project_path)
    if rc != 0:
        return read_koan_config(project_path)
    rc, out, _ = run_git("remote", cwd=project_path)
    remotes = [r.strip() for r in out.splitlines() if r.strip()] if rc == 0 else []
    if rc == 0 and not remotes:
        # A local-only checkout: no branch from anywhere else can reach it.
        return read_koan_config(project_path)
    remote = _select_trusted_remote(remotes)
    if not remote:
        logger.warning(
            "no trusted remote for %s — .koan/config.yaml not read", project_path
        )
        return {}
    ref = _trusted_config_ref(project_path, remote)
    if not ref:
        logger.warning(
            "could not resolve the default branch of %s in %s — "
            ".koan/config.yaml not read",
            remote,
            project_path,
        )
        return {}
    rc, text, _ = run_git("show", f"{ref}:.koan/config.yaml", cwd=project_path)
    if rc != 0:
        # Absent on the default branch is the common case (no repo config).
        return {}
    return _parse_koan_config(text.strip(), f"{ref} in {project_path}")


def get_hook_skills(project_path: str, event: str) -> list[str]:
    """Return the honored ``hooks.<event>`` skill names from .koan/config.yaml.

    Lets a repo declare which Claude Code skills koan should run when one of
    its lifecycle events fires, without the operator writing a Python hook.
    The repo supplies *names*; koan composes the mission text itself, so a
    committed config can never inject instructions into a write-capable agent.

    Read via :func:`read_trusted_koan_config` — from the default branch of the
    checkout's trusted remote, never from whatever branch the work tree happens
    to have checked out, so a contributor's PR branch sitting in the registered
    checkout cannot choose which skills run.

    Returns ``[]`` unless the value is a list; keeps only non-blank ``str``
    items matching ``_SKILL_NAME_RE``, drops duplicates, and caps at
    ``_MAX_HOOK_SKILLS`` (one diagnostic per drop reason). Fail-safe; never
    raises — a broken repo config must never disturb the event that fired.
    """
    if not event:
        return []
    hooks = read_trusted_koan_config(project_path).get("hooks")
    if not isinstance(hooks, dict):
        return []
    raw = hooks.get(event)
    if not isinstance(raw, list):
        return []
    skills: list[str] = []
    dropped = 0
    for item in raw:
        # Every drop is counted, including a non-string or blank entry: a repo
        # owner whose config silently loses a line needs to see it in the log.
        if not isinstance(item, str) or not item.strip():
            dropped += 1
            continue
        name = item.strip()
        if len(name) > _MAX_SKILL_NAME_LEN or not _SKILL_NAME_RE.match(name):
            dropped += 1
            continue
        if name not in skills:
            skills.append(name)
    if dropped:
        logger.warning(
            "dropped %d invalid hooks.%s skill name(s) in %s — expected %s",
            dropped,
            event,
            project_path,
            _SKILL_NAME_RE.pattern,
        )
    if len(skills) > _MAX_HOOK_SKILLS:
        logger.warning(
            "hooks.%s capped at %d skills (had %d)",
            event,
            _MAX_HOOK_SKILLS,
            len(skills),
        )
        skills = skills[:_MAX_HOOK_SKILLS]
    return skills
