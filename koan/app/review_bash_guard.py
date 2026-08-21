"""PreToolUse gate for the read-only review shell.

Adjudicates every ``Bash`` command a `/review` session attempts, allowing a
narrow set of read-only inspection commands and denying everything else.

Run as a subprocess by the Claude CLI, wired in per-invocation via ``--settings``
(never by mutating ``~/.claude/settings.json``). Stdin carries the hook payload;
stdout carries the decision.

**Stdlib only, and no ``app.*`` imports.** The hook runs with the review
worktree as cwd and no Kōan ``PYTHONPATH``; invoking it as
``python3 /abs/path/to/koan/app/review_bash_guard.py`` puts ``koan/app`` on
``sys.path[0]``, so an ``import app.foo`` would fail. Keep the import list to
stdlib modules that have no same-named sibling in ``koan/app/``.

Two properties this module depends on, both measured on Claude CLI 2.1.235:

1. ``Bash`` is never placed in ``--allowedTools``. A hook returning ``allow`` is
   what permits a command, so if this gate fails to load for any reason the
   shell is simply unavailable — the failure mode is "no shell", never
   "unguarded shell".
2. A decision must be emitted explicitly in both directions. Exiting 0 with no
   stdout means "no decision", which falls through to the permission system and
   yields no shell at all.

Design note on parsing: :func:`shlex.split` is a *word splitter*, not a shell
parser. ``shlex.split("git log > /etc/passwd")`` returns
``['git','log','>','/etc/passwd']`` — the redirect becomes an ordinary token, so
a token-level check on ``argv[0]`` would pass it and Bash would then execute the
redirect. Structural rejects therefore run against the RAW string, before any
splitting.
"""

import json
import os
import re
import shlex
import sys
from typing import Sequence, Tuple

# Cap on the raw command string. Obfuscation and accidental megabyte pastes both
# get cheaper to reason about when there is a bound.
MAX_COMMAND_LEN = 4096

# Shell metacharacters rejected on the RAW string. Redirection and chaining are
# the whole ballgame: a single ``>`` turns any reader into a writer, and a single
# ``;`` turns a validated command into an arbitrary one.
#
# Pipelines are rejected in v1 by DECISION, not oversight. Validating a pipeline
# means validating every stage, and one ``tee`` / ``sh`` / ``dd`` in stage two
# defeats stage one entirely. ``rg -m``, ``head -n`` and ``git log -n`` cover the
# common uses natively. The additive extension, if ever wanted, is: split on
# ``|``, validate each stage against this same allowlist, reject any writer.
_STRUCTURAL_REJECTS: Sequence[Tuple[str, str]] = (
    (">", "output redirection"),
    ("<", "input redirection"),
    ("|", "pipes"),
    (";", "command chaining"),
    ("&", "backgrounding or chaining"),
    ("`", "command substitution"),
    ("$(", "command substitution"),
    ("${", "parameter expansion"),
    ("\n", "multi-line commands"),
    ("\r", "carriage returns"),
)

# Read-only ``git`` subcommands. Everything else is denied, which covers push,
# commit, checkout, reset, clean, config, gc, worktree, fetch, remote, stash,
# apply, am, filter-branch, submodule, bundle and update-ref without listing
# them — the same fail-closed shape as the tool allowlist itself.
_GIT_READ_SUBCOMMANDS = frozenset({
    "blame", "cat-file", "describe", "diff", "for-each-ref", "grep", "log",
    "ls-files", "ls-tree", "merge-base", "rev-list", "rev-parse", "shortlog",
    "show", "status",
})
# NOTE: "tag" is deliberately absent. `git tag` with no args lists, but
# `git tag <name>` CREATES one -- the read and write spellings differ only by an
# argument, which is not a distinction worth carrying here.

# ``git`` global flags that relocate the repository or inject configuration.
# ``-C`` and ``--git-dir`` escape the pinned review worktree; ``-c`` can set
# ``core.pager`` / ``alias.*`` to run an arbitrary program.
_GIT_GLOBAL_FLAG_REJECTS = ("-C", "-c", "--git-dir", "--work-tree", "--exec-path")

# `git diff`/`git show` accept the diff machinery's --output=FILE, which writes.
# Verified against git on this host: `git diff --output=/tmp/x HEAD~1` creates it.
_GIT_WRITE_FLAG_REJECTS = ("--output", "-o")

# Commands allowed with no per-command flag restrictions.
_PLAIN_READ_COMMANDS = frozenset({
    "basename", "cat", "comm", "cut", "diff", "dirname", "echo", "file", "grep",
    "head", "jq", "ls", "nl", "pwd", "realpath", "stat", "tail", "tr", "uniq",
    "wc",
})

# Per-command flag denials. Each entry is (prefix, reason); a token matches when
# it equals the prefix or starts with it, so ``-i.bak`` is caught by ``-i``.
_FLAG_REJECTS = {
    "find": (
        (("-exec", "-execdir", "-ok", "-okdir"), "command execution"),
        (("-delete", "-fprint", "-fprintf", "-fls"), "filesystem mutation"),
    ),
    "rg": ((("--pre", "--pre-glob", "--generate"), "external program execution"),),
    "sort": ((("-o", "--output"), "writes to a file"),),
}

# Never allowed, each for a reason worth keeping so nobody re-adds them:
#   shells/interpreters  -- `-c` is an arbitrary program
#   xargs/env/nice/...   -- command-prefix laundering
#   awk                  -- an interpreter: system(), | "cmd", print > "file"
#   curl/wget/nc/ssh     -- exfiltration of a private repo under review
#   gh                   -- writes to GitHub; Kōan posts the review itself
#   tee/dd/truncate/...  -- writers
# The meta-test in the suite asserts the allowlists never intersect this set.
_KNOWN_DANGEROUS = frozenset({
    "awk", "bash", "chmod", "cp", "curl", "dd", "docker", "env", "eval", "exec",
    "gh", "install", "ln", "make", "mkdir", "mv", "nc", "nice", "node", "nohup",
    "npm", "patch", "perl", "pip", "python", "python3", "rm", "ruby",
    "scp", "sh", "ssh", "sudo", "tee", "time", "timeout", "touch", "truncate",
    "wget", "xargs", "zsh",
})

# ``sed`` cannot be gated by denying flags. Its script language WRITES: the ``w``
# command and the ``s///w file`` flag both create files, so ``sed -n '1w /tmp/x'``
# is a write with no ``-i`` anywhere in sight. Verified on this host -- it creates
# the file. Parsing sed scripts safely is a second parser nobody should maintain,
# so ``sed`` is allowed ONLY in the one shape a reviewer actually needs: print a
# line range. Anything else is refused and pointed at Read/Grep.
_SED_SAFE_SCRIPT = re.compile(r"^\$?\d*(,\$?\d*)?p$")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _has_expandable_dollar(command: str) -> bool:
    """True if ``command`` contains a ``$`` that Bash would expand.

    ``$(`` and ``${`` are rejected structurally already; this closes the
    remaining hole where a bare ``$VAR`` (e.g. ``cat $HOME/.ssh/id_rsa`` or
    ``echo $TOKEN``) reaches Bash, which expands it into a real host path or
    secret the literal operand check never sees. Only a ``$`` inside single
    quotes is inert -- Bash does not expand within them (e.g. the ``$p`` last-
    line range in ``sed -n '10,$p'``). Any other ``$`` -- unquoted or inside
    double quotes -- expands.
    """
    in_single = False
    in_double = False
    escaped = False
    for ch in command:
        if escaped:
            escaped = False
            continue
        if in_single:
            if ch == "'":
                in_single = False
            continue
        if in_double:
            if ch == '"':
                in_double = False
            elif ch == "\\":
                escaped = True
            elif ch == "$":
                return True
            continue
        # Not inside any quote.
        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "$":
            return True
        elif ch == "\\":
            escaped = True
    return False


def _reject(reason: str) -> Tuple[bool, str]:
    return False, reason


def _flag_denied(command: str, argv: Sequence[str]) -> Tuple[bool, str]:
    """Apply per-command flag policy. Returns (denied, reason)."""
    for prefixes, why in _FLAG_REJECTS.get(command, ()):
        for token in argv[1:]:
            if any(token == p or token.startswith(p) for p in prefixes):
                return True, f"`{command} {token}` is not read-only ({why})"
    return False, ""


def _check_sed(argv: Sequence[str]) -> Tuple[bool, str]:
    """Allow only ``sed -n '<range>p' <file>`` -- see _SED_SAFE_SCRIPT."""
    flags = [t for t in argv[1:] if t.startswith("-")]
    operands = [t for t in argv[1:] if not t.startswith("-")]
    if flags != ["-n"]:
        return _reject(
            "`sed` is allowed only as `sed -n '<range>p' <file>` in a review "
            "shell (its script language can write files). Use the Read tool for "
            "anything else."
        )
    if not operands or not _SED_SAFE_SCRIPT.match(operands[0]):
        return _reject(
            "`sed` script must be a plain line-range print such as "
            "'10,40p' (its `w` command writes files)"
        )
    return True, ""


def _flag_matches(token: str, flag: str) -> bool:
    """Does ``token`` carry ``flag`` in any spelling git accepts?

    Long flags (``--git-dir``) take ``=``-attached arguments
    (``--git-dir=/x``). Short flags (``-C``, ``-c``, ``-o``) let the argument
    be folded into the same token -- ``git -Cb log`` == ``git -C b log`` and
    ``git -ccore.pager=x log`` == ``git -c core.pager=x log`` -- so a bare
    ``startswith`` catches ``-Cb``, ``-cname=val`` and ``-oFILE`` alike. It is
    safe against false positives on long tokens: ``--oneline`` does not start
    with ``-o`` because its second character is ``-``, not ``o``.
    """
    if flag.startswith("--"):
        return token == flag or token.startswith(flag + "=")
    return token.startswith(flag)


def _is_pathish(token: str) -> bool:
    """Heuristic: does ``token`` look like a file/dir operand rather than a flag?

    A token is treated as a path operand unless it is a flag (starts with
    ``-``) or is purely structural (a lone ``-``). Everything else -- a bare
    path, a ``--``-separated operand, an explicit ``path=...`` value -- is a
    candidate. The gate is deliberately conservative: refusing a token that is
    merely path-shaped is cheap (the model rephrases); permitting an escape is
    the review-scope escalation this exists to close.
    """
    if token == "-" or token.startswith("-"):
        return False
    # ``--name=value`` operands are real, e.g. ``wc --files0-from=x`` and
    # ``rg --glob '!tests/'``. Only the value is path-ish; the ``--name=`` prefix
    # is a flag, and ``!tests/`` is a glob that names no concrete file.
    head, sep, value = token.partition("=")
    if sep and ("/" in head or head.startswith("--")):
        return _is_pathish(value.strip("\"'"))
    return True


def _check_operands(
    argv: Sequence[str], cwd: str, what: str,
) -> Tuple[bool, str]:
    """Ensure every file/dir operand resolves inside *cwd* (the review worktree).

    The gate grants read access to the ``cwd`` the review runs in -- the
    disposable pinned checkout of the PR head. Without this, operands like
    ``cat ~/.ssh/id_rsa`` or ``head /etc/passwd`` would read arbitrary host
    paths as the Kōan user, turning "read-only" into "read-everything the
    operator can read" and giving a hostile PR's injected prompt a channel to
    exfiltrate credentials and other projects' private checkouts into a
    "finding" that gets posted as the review body.

    * ``~`` is rejected outright: it is expanded by the shell (not ``shlex``,
      which passes it through untouched), so it can resolve outside ``cwd`` even
      when the literal token looks relative. There is no legitimate use for the
      home directory in a review of a worktree.
    * Absolute and ``..``-escaping paths are rejected: both are how an operand
      points at a file outside the worktree. ``head /etc/passwd``,
      ``git diff -- /etc/hosts`` and ``cat ../other`` are in this class.
    * Relative operands are resolved against ``cwd`` and their resolved path is
      checked to stay under it, so ``cat x/../../etc/passwd`` cannot escape even
      though no single segment is ``..``. Nothing is required to exist -- the
      path is checked lexically plus symlink resolution when the referent does
      exist (a symlink inside the worktree to a host file still points outside).
    """
    for token in argv:
        if not _is_pathish(token):
            continue
        raw = token.strip("\"'")
        if "/" not in raw and "~" not in raw:
            # A bare relative name like ``cat f.py`` resolved against cwd stays
            # inside it by construction. Skip the (throwaway) Path work; the
            # model's common case stays cheap and correct.
            continue
        if raw.startswith(("~", "/")) or ".." in raw.split("/"):
            return _reject(
                f"`{token}` resolves outside the review worktree "
                f"({what}); only paths within it may be read"
            )
        resolved = os.path.realpath(os.path.join(cwd, raw))
        if not (resolved == cwd or resolved.startswith(cwd + os.sep)):
            return _reject(
                f"`{token}` resolves to {resolved!r}, outside the review "
                f"worktree ({what}); only paths within it may be read"
            )
    return True, ""


def _check_git(argv: Sequence[str]) -> Tuple[bool, str]:
    """Validate a ``git`` invocation: no relocating flags, read-only subcommand."""
    for token in argv[1:]:
        for bad in _GIT_GLOBAL_FLAG_REJECTS:
            if _flag_matches(token, bad):
                return _reject(
                    f"`git {bad}` is not allowed (it can relocate the repository "
                    "or inject configuration)"
                )
        for bad in _GIT_WRITE_FLAG_REJECTS:
            if _flag_matches(token, bad):
                return _reject(f"`git {bad}` writes to a file")
    subcommand = ""
    for token in argv[1:]:
        if not token.startswith("-"):
            subcommand = token
            break
    if not subcommand:
        return _reject("`git` needs a read-only subcommand")
    if subcommand not in _GIT_READ_SUBCOMMANDS:
        allowed = ", ".join(sorted(_GIT_READ_SUBCOMMANDS))
        return _reject(
            f"`git {subcommand}` is not a read-only subcommand. Allowed: {allowed}"
        )
    return True, ""


def is_allowed(command: str) -> Tuple[bool, str]:
    """Return ``(allowed, reason)`` for a raw Bash command string.

    The pure function under test. ``reason`` is empty when allowed and names the
    rule that fired when denied, so the model can rephrase rather than guess.
    """
    if not isinstance(command, str) or not command.strip():
        return _reject("empty command")

    if len(command) > MAX_COMMAND_LEN:
        return _reject(f"command exceeds {MAX_COMMAND_LEN} characters")

    # Reject control and non-ASCII characters outright rather than normalizing
    # them: homoglyphs and bidirectional overrides make a command read as one
    # thing to a human and parse as another.
    if _CONTROL_CHARS.search(command):
        return _reject("command contains control characters")
    if not command.isascii():
        return _reject("command contains non-ASCII characters")

    # Structural rejects run against the RAW string. See the module docstring:
    # shlex would turn these into ordinary tokens and hide them.
    for token, why in _STRUCTURAL_REJECTS:
        if token in command:
            return _reject(
                f"{why} is not allowed in a review shell (found {token!r}). "
                "Run one simple command per call; use the Grep tool for "
                "patterns containing shell metacharacters."
            )

    # Dollar expansion is the operand-scope parent of command substitution and
    # parameter expansion, so it is rejected outright rather than normalized.
    # A bare ``$VAR`` bypasses _check_operands: the guard would validate the
    # literal ``$HOME/...`` (resolving inside cwd) while Bash then reads the
    # real host path the variable expands to. ``$`` inside single quotes is
    # inert (Bash never expands it there), so only expandable occurrences are
    # rejected -- keeping ``sed -n '10,$p'`` legal.
    if _has_expandable_dollar(command):
        return _reject(
            "environment/parameter expansion is not allowed in a review shell. "
            "The Read tool resolves paths for you; pass literal paths."
        )

    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        return _reject(f"could not parse command ({exc})")

    if not argv:
        return _reject("empty command")

    # Defence in depth: if a quoting subtlety let an operator through the raw
    # scan, catch it post-split too.
    for tok in argv:
        for bad, why in _STRUCTURAL_REJECTS:
            if bad in tok:
                return _reject(f"{why} is not allowed (found {bad!r})")

    program = argv[0]
    if "=" in program:
        return _reject("environment-variable prefixes are not allowed")
    if "/" in program:
        return _reject(
            "the command must be a bare program name, not a path "
            f"(got {program!r})"
        )

    if program in _KNOWN_DANGEROUS:
        return _reject(f"`{program}` is not available in a read-only review shell")

    if program == "git":
        return _check_git(argv)

    if program == "sed":
        return _check_sed(argv)

    if program in _PLAIN_READ_COMMANDS or program in _FLAG_REJECTS:
        denied, why = _flag_denied(program, argv)
        if denied:
            return _reject(why)
        return True, ""

    return _reject(
        f"`{program}` is not in the read-only review shell allowlist. "
        "Use the Read, Glob and Grep tools, or a read-only git/rg/sed command."
    )


def _decision(allow: bool, reason: str) -> str:
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow" if allow else "deny",
            "permissionDecisionReason": reason,
        }
    })


def main(argv: Sequence[str] = ()) -> int:
    """Read a PreToolUse payload on stdin, emit a decision on stdout.

    Always exits 0: a non-zero exit reads as a hook crash rather than a
    decision. Every failure to understand the payload denies.
    """
    del argv
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print(_decision(False, "unparseable hook payload"))
        return 0

    if not isinstance(payload, dict):
        print(_decision(False, "unparseable hook payload"))
        return 0

    # The matcher should scope this to Bash, but never rely on caller config for
    # a security decision.
    tool_name = payload.get("tool_name")
    if tool_name and tool_name != "Bash":
        print(_decision(True, f"{tool_name} is not gated by this hook"))
        return 0

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        print(_decision(False, "hook payload has no tool_input"))
        return 0

    command = tool_input.get("command")
    if not isinstance(command, str):
        print(_decision(False, "hook payload has no command string"))
        return 0

    allowed, reason = is_allowed(command)
    if allowed:
        # cwd is the review worktree the CLI started the session in -- the only
        # tree this shell may read. Constrain file/dir operands to it so a
        # read-only command cannot open an arbitrary host path.
        cwd = payload.get("cwd") or os.getcwd()
        allowed, reason = _check_operands(
            shlex.split(command, posix=True), str(cwd), "Bash command",
        )
    print(_decision(allowed, reason or "read-only review shell"))
    return 0


def hook_settings(python: str = "", script: str = "", timeout: int = 10) -> dict:
    """Build the ``--settings`` payload that installs this gate.

    Kept here so the JSON shape lives next to the script it points at.
    """
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                f"{python or sys.executable} "
                                f"{script or os.path.abspath(__file__)}"
                            ),
                            "timeout": timeout,
                        }
                    ],
                }
            ]
        }
    }


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess tests
    sys.exit(main(sys.argv[1:]))
