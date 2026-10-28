"""Fixed templates for the one chat message a mission ends with.

The same outcome must read the same every time, so the operator can scan the
channel: ``✅ [project] 🔍 Reviewed <url>`` / ``❌ [project] 🔍 Review failed: <url>``,
then the detail or the reason on the lines below. Shared by the agent loop
(``run._notify_mission_end``) and the skill runners
(``messaging_level.report_outcome``). Pure: no I/O, no ``app`` imports.
"""

# Skill → (emoji, success verb, failure noun).
SKILL_COMPLETION = {
    "review": ("🔍", "Reviewed", "Review"),
    "ultrareview": ("🔭", "Ultra reviewed", "Ultra review"),
    "fix": ("🐞", "Fixed", "Fix"),
    "rebase": ("🔄", "Rebased", "Rebase"),
    "plan": ("🧠", "Planned", "Plan"),
    "implement": ("🔨", "Implemented", "Implement"),
    "squash": ("🗜️", "Squashed", "Squash"),
    "recreate": ("♻️", "Recreated", "Recreate"),
    "debug": ("🔬", "Debugged", "Debug"),
    "speckit": ("📐", "Specified", "Speckit"),
}

FAIL_ICON = "❌"
NO_REASON_GIVEN = "No reason given."

# Stdout markers a runner prints instead of sending, when the agent loop owns
# the message (``KOAN_SUPPRESS_RUNNER_OUTCOME=1``). One DETAIL marker per line.
OK_MARKER = "KOAN_OUTCOME_OK:"  # always printed: the runner reported, its URL is final
URL_MARKER = "KOAN_OUTCOME_URL:"
DETAIL_MARKER = "KOAN_OUTCOME_DETAIL:"
REASON_MARKER = "KOAN_OUTCOME_REASON:"

_REASON_MAX_CHARS = 500


def _tag(project: str) -> str:
    return f"[{project}] " if project else ""


def success_line(project: str, skill: str, url: str) -> str:
    """``✅ [p] 🔍 Reviewed <url>`` — *skill* must be a ``SKILL_COMPLETION`` key."""
    emoji, verb, _noun = SKILL_COMPLETION[skill]
    return f"✅ {_tag(project)}{emoji} {verb} {url}".rstrip()


def failure_line(project: str, skill: str, subject: str, icon: str = FAIL_ICON) -> str:
    """``❌ [p] 🔍 Review failed: <url|title>`` — *skill* must be a table key."""
    emoji, _verb, noun = SKILL_COMPLETION[skill]
    return f"{icon} {_tag(project)}{emoji} {noun} failed: {subject}".rstrip(": ")


def one_line_reason(reason: str) -> str:
    """*reason* on one line, capped, for the line under a failure."""
    reason = " ".join((reason or "").split())
    if len(reason) > _REASON_MAX_CHARS:
        reason = reason[: _REASON_MAX_CHARS - 1].rstrip() + "…"
    return reason


def message(first_line: str, *below: str) -> str:
    """The first line, then each non-empty block below it."""
    return "\n".join([first_line, *(b for b in below if b)])


def markers(url: str = "", detail: str = "", reason: str = "", ok: bool = True) -> str:
    """The stdout markers that hand an outcome over to the agent loop."""
    lines = [f"{OK_MARKER} {1 if ok else 0}"]
    if url:
        lines.append(f"{URL_MARKER} {url}")
    if not ok:
        lines.append(f"{REASON_MARKER} {one_line_reason(reason)}")
    lines += [f"{DETAIL_MARKER} {line}" for line in (detail or "").splitlines()]
    return "\n".join(lines)


def read_markers(stdout: str) -> dict:
    """``{"reported", "url", "reason", "detail"}`` from a runner's stdout.

    ``reported`` is true once any outcome was handed over. The last URL /
    REASON marker wins; DETAIL lines are joined in order.
    """
    found = {"reported": False, "url": "", "reason": "", "detail": []}
    for line in (stdout or "").splitlines():
        if line.startswith(OK_MARKER):
            found["reported"] = True
        elif line.startswith(URL_MARKER):
            found["url"] = line[len(URL_MARKER):].strip()
        elif line.startswith(REASON_MARKER):
            found["reason"] = line[len(REASON_MARKER):].strip()
        elif line.startswith(DETAIL_MARKER):
            found["detail"].append(line[len(DETAIL_MARKER):].removeprefix(" "))
    found["detail"] = "\n".join(found["detail"])
    return found
