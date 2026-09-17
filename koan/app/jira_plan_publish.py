"""Verified, resumable publishing of the current Jira plan comment.

A plan is an expensive model run, so it is staged on disk before Koan tries to
deliver it. Delivery is then verified by reading the comment back: Jira's write
endpoints report success on responses that never became a visible comment, so
an unverified write is treated as a failure and the staged plan is kept for the
next mission run rather than regenerated.

Plans too large for a single Jira comment are published as consecutive parts.
Jira's public REST API has no reply-to-comment operation, so the parts are
linked to each other with focused-comment URLs instead of being threaded.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import suppress
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from app import jira_notifications as _jira_notifications
from app.github_url_parser import parse_jira_url
from app.jira_notifications import (
    jira_add_comment,
    jira_comment_authored_by_self,
    jira_edit_comment,
    jira_list_comments_checked,
)
from app.run_log import log_safe as _log_runner
from app.security_audit import TRACKER_COMMENT_MUTATION, log_event
from app.utils import atomic_write

# The plan comment carries a human-readable footer rather than an HTML comment:
# Jira renders ADF text literally, so an `<!-- ... -->` marker would show up as
# visible gibberish. The footer doubles as the dedup key (find the previous plan
# comment) and the read-back proof: `rev` is a digest of the whole staged plan,
# so every part of one plan shares it and parts left over from an older plan are
# recognisable as stale.
_FOOTER_LABEL = "Koan current plan"
_FOOTER_RE = re.compile(
    rf"{re.escape(_FOOTER_LABEL)} \(rev ([0-9a-f]{{16}})(?:, part (\d+)/(\d+))?\)\s*$"
)
_SUPERSEDED_BODY = "(Superseded — this part of an earlier Koan plan was replaced.)"
_FENCE_RE = re.compile(r"^\s*```(.*)$")
# The footer is plain text, so a human can reproduce it by quoting the tail of a
# plan. A Jira comment entity property cannot be produced from the comment
# editor — it only exists if something wrote it through the REST comment
# payload — which makes it the authorship proof the footer can never be.
_PLAN_PROPERTY_KEY = "koan.jira.plan"

_PUBLISH_ATTEMPTS = 3
# Jira rejects comments beyond roughly 32k characters. Split well under that so
# the part header, navigation links, and footer always fit in the remainder.
_MAX_COMMENT_CHARS = 30_000
_PART_BODY_CHARS = 29_000
# Publishing is retried across mission runs, but a permanently broken Jira must
# not wedge the issue forever: after this many failed runs the stage is dropped
# so the next `/plan` regenerates instead of replaying a stale plan.
_MAX_PUBLISH_SESSIONS = 3
_STAGE_MAX_AGE_SECONDS = 7 * 24 * 3600


def _instance_path(instance_dir: str) -> Path:
    if instance_dir:
        return Path(instance_dir)
    return Path(os.environ.get("KOAN_ROOT", ".")) / "instance"


def stage_path_for(issue_url: str, instance_dir: str = "") -> Path:
    """Return the on-disk staging path for an issue's pending plan comment."""
    digest = hashlib.sha256(issue_url.encode("utf-8")).hexdigest()[:20]
    return _instance_path(instance_dir) / "pending-jira-plan-publishes" / f"{digest}.json"


def _revision(comment_body: str) -> str:
    return hashlib.sha256(comment_body.encode("utf-8")).hexdigest()[:16]


def _footer_for(revision: str, part_number: int = 1, part_count: int = 1) -> str:
    if part_count > 1:
        return f"{_FOOTER_LABEL} (rev {revision}, part {part_number}/{part_count})"
    return f"{_FOOTER_LABEL} (rev {revision})"


def _part_header(part_number: int, part_count: int) -> str:
    return f"{_FOOTER_LABEL} — Part {part_number} of {part_count}"


# Readers (notably the `implement` skill, which reassembles a split plan) must
# recognise exactly what the renderer above emits. Both directions live here so
# a change to the format cannot silently strand a consumer on the old shape.
_HEADER_LINE_RE = re.compile(
    rf"^{re.escape(_FOOTER_LABEL)} — Part \d+ of \d+\s*$", re.MULTILINE,
)
# One link per line on the wire, but not necessarily on the way back: a middle
# part carries both links, and ADF re-joins sibling text nodes with a space, so
# they can return collapsed onto a single line. Match a run of them rather than
# exactly one, or the whole line survives `strip_plan_envelope` and Jira
# permalinks end up in the plan the implementing agent is handed.
_NAVIGATION_LINE_RE = re.compile(
    r"^[ \t]*(?:(?:Previous|Next) part: https?://\S+[ \t]*)+$", re.MULTILINE,
)
_FOOTER_LINE_RE = re.compile(
    rf"^{re.escape(_FOOTER_LABEL)} \(rev [0-9a-f]{{16}}(?:, part \d+/\d+)?\)\s*$",
    re.MULTILINE,
)

# A split does not always land on a paragraph boundary, and `_plan_parts` has to
# add fence lines that were never in the plan. Both facts have to reach the
# reader or the reassembled plan is not the plan that was published: a
# single-newline cut would come back as a paragraph break that ends the list it
# fell inside, and one code example would come back as two blocks with a stray
# ``` / ```lang pair wedged between them. Jira strips HTML comments and
# `_render_comment` drops each part's trailing whitespace, so the only carrier
# that survives the transport is a visible line — the same reasoning that makes
# the footer plain text rather than a marker.
_CONTINUATION_BREAKS = {"paragraph": "\n\n", "line": "\n", "no": ""}
_CONTINUATION_RE = re.compile(
    rf"^[ \t]*{re.escape(_FOOTER_LABEL)} — continued from the previous part "
    r"\((paragraph|line|no) break(, code block)?\)[ \t]*$",
    re.MULTILINE,
)


def _continuation_marker(break_kind: str, reopened_fence: bool) -> str:
    suffix = ", code block" if reopened_fence else ""
    return (
        f"{_FOOTER_LABEL} — continued from the previous part "
        f"({break_kind} break{suffix})"
    )


def parse_plan_comment(comment_body: str) -> Optional[Tuple[str, int, int]]:
    """Return ``(revision, part_number, part_count)`` for a Koan plan comment.

    ``None`` when the body is not one. A single-part plan reports ``(rev, 1, 1)``.
    """
    match = _FOOTER_RE.search((comment_body or "").rstrip())
    if not match:
        return None
    return match.group(1), int(match.group(2) or 1), int(match.group(3) or 1)


def strip_plan_envelope(comment_body: str) -> str:
    """Drop the part header, navigation links, and footer, keeping plan content."""
    text = _HEADER_LINE_RE.sub("", comment_body or "", count=1)
    text = _NAVIGATION_LINE_RE.sub("", text)
    return _FOOTER_LINE_RE.sub("", text)


def _inside_fence_flags(text: str, limit: int) -> List[bool]:
    """Per-offset "is this offset inside a fenced code block?" up to ``limit``.

    A fence *opener* line counts as outside (cutting just before it is fine) and
    a *closer* line as inside (cutting there would orphan it), so a boundary the
    caller accepts never lands in the middle of a code block.
    """
    flags = [False] * (min(len(text), limit) + 1)
    inside = False
    offset = 0
    for line in text.splitlines(keepends=True):
        if offset >= len(flags):
            break
        end = min(offset + len(line), len(flags))
        flags[offset:end] = [inside] * (end - offset)
        if _FENCE_RE.match(line.rstrip("\n")):
            inside = not inside
        offset += len(line)
    return flags


def _cut_point(remaining: str, floor: int) -> Tuple[int, str]:
    """Where to end the next part, and how the following one attaches to it.

    Only accept a boundary in the back half of the window. Preferring the
    coarsest separator outright collapses on real plans: a File Map table is a
    long blank-line-free run, so the last "\\n\\n" can sit near the very start
    and would emit an absurd 40-character "Part 1 of N" plus a needless extra
    publish. Below the floor, fall through to a finer separator.

    Boundaries inside a fenced code block are skipped so the common case never
    needs the fence pair at all; a single code block larger than one comment
    still forces a cut inside one, which is what the fence pair is for.
    """
    flags = _inside_fence_flags(remaining, _PART_BODY_CHARS)
    for separator, break_kind in (("\n\n", "paragraph"), ("\n", "line")):
        search_end = _PART_BODY_CHARS
        while True:
            candidate = remaining.rfind(separator, 0, search_end)
            if candidate <= floor:
                break
            boundary = candidate + len(separator)
            if not flags[boundary]:
                return boundary, break_kind
            search_end = candidate
    # Nothing usable: cut mid-line. The reader rejoins with no separator at all,
    # so this stays reversible even though it is ugly to look at.
    return _PART_BODY_CHARS, "no"


def _split_comment_body(comment_body: str) -> List[Tuple[str, str]]:
    """Split an oversized plan into ``(text, break kind before it)`` parts.

    The texts concatenate back to the input exactly; the break kind records
    which separator the cut consumed so the reader can put it back.
    """
    if len(comment_body) <= _PART_BODY_CHARS:
        return [(comment_body, "paragraph")]

    parts: List[Tuple[str, str]] = []
    remaining = comment_body
    break_kind = "paragraph"
    floor = _PART_BODY_CHARS // 2
    while len(remaining) > _PART_BODY_CHARS:
        cut, next_break = _cut_point(remaining, floor)
        parts.append((remaining[:cut], break_kind))
        remaining = remaining[cut:]
        break_kind = next_break
    parts.append((remaining, break_kind))
    return parts


def _open_fence_language(body: str) -> Optional[str]:
    """The info string of a fence this text leaves open, else ``None``."""
    open_lang: Optional[str] = None
    for line in body.splitlines():
        match = _FENCE_RE.match(line)
        if match:
            open_lang = None if open_lang is not None else match.group(1).strip()
    return open_lang


def _plan_parts(comment_body: str) -> List[str]:
    """The comment bodies to publish for a plan, each independently renderable.

    Close a code fence left open by a split, and reopen it in the next part:
    comments are rendered with ``markdown_to_adf`` and read back with
    ``_adf_to_text``, which drops ``codeBlock`` content, so a part cut mid-fence
    would swallow its own verification footer and never verify. Every part after
    the first states how it attaches to its predecessor — see
    :func:`reassemble_plan_parts`, which undoes exactly this.
    """
    rendered: List[str] = []
    reopen = ""
    for index, (part, break_kind) in enumerate(_split_comment_body(comment_body)):
        body = reopen + part
        reopened_fence = bool(reopen)
        open_lang = _open_fence_language(body)
        if open_lang is None:
            reopen = ""
        else:
            body = f"{body.rstrip()}\n```"
            reopen = f"```{open_lang}\n"
        if index and (reopened_fence or break_kind != "paragraph"):
            body = f"{_continuation_marker(break_kind, reopened_fence)}\n\n{body}"
        rendered.append(body)
    return rendered


def _read_continuation(content: str) -> Tuple[str, bool, str]:
    """Split a continuing part into ``(joiner, fence was reopened, plan text)``."""
    match = _CONTINUATION_RE.search(content)
    if match is None or content[:match.start()].strip():
        # No marker, or one that is not this part's opening line: either the cut
        # landed on a paragraph boundary, or this is a plan published before
        # markers existed. Both rejoin as paragraphs, which is what the reader
        # did unconditionally before.
        return "\n\n", False, content
    return (
        _CONTINUATION_BREAKS[match.group(1)],
        bool(match.group(2)),
        content[:match.start()] + content[match.end():],
    )


def _drop_leading_fence(text: str) -> str:
    """Remove the fence `_plan_parts` reopened at the top of a continuing part."""
    head, _, tail = text.partition("\n")
    return tail if _FENCE_RE.match(head) else text


def _drop_trailing_fence(text: str) -> str:
    """Remove the bare fence `_plan_parts` appended to close a cut code block."""
    head, _, tail = text.rstrip().rpartition("\n")
    return head if tail.strip() == "```" else text


def reassemble_plan_parts(
    part_bodies: Sequence[str],
    part_numbers: Optional[Sequence[int]] = None,
) -> str:
    """Rebuild the plan text from its published part comment bodies, in order.

    The inverse of :func:`_plan_parts`: it drops the envelope every part wears
    (header, navigation links, footer), removes the fence pair the split had to
    invent, and rejoins the parts with the separator the cut consumed. Without
    it a plan comes back to `/implement` with stray ``` markers splitting one
    code example in two and a paragraph break wherever the cut fell mid-list.

    ``part_numbers`` gives each body's part number, and is required whenever the
    set may be incomplete — a reader can only recover the parts still on the
    issue. A continuation marker describes how a part attaches to *the part
    before it*, so applying it across a gap is worse than ignoring it: the
    "code block" flag would strip a fence line that belongs to the plan rather
    than one the split invented, and a "no break" join would weld two unrelated
    sentences together. Across a gap the marker is therefore dropped (it is
    envelope, not prose) and the neighbours rejoin as separate paragraphs.
    Omit the argument only for a set known to be whole and to start at part 1.

    Boundary whitespace is normalised to the canonical separator (a run of three
    blank lines rejoins as one), which markdown renders identically.
    """
    numbers = list(part_numbers) if part_numbers is not None else []

    def follows_predecessor(index: int) -> bool:
        if index == 0:
            return False
        return not numbers or numbers[index] == numbers[index - 1] + 1

    assembled = ""
    for index, body in enumerate(part_bodies):
        content = strip_plan_envelope(body or "")
        joiner, reopened_fence = "\n\n", False
        # Part 1 never carries a marker; a set that starts later does, and it
        # names a part that is not here.
        if index or numbers[:1] not in ([], [1]):
            joiner, reopened_fence, content = _read_continuation(content)
        if not follows_predecessor(index):
            joiner, reopened_fence = "\n\n", False
        content = content.strip()
        if reopened_fence:
            assembled = _drop_trailing_fence(assembled)
            content = _drop_leading_fence(content)
        assembled = f"{assembled}{joiner}{content}" if assembled else content
    return assembled.strip()


def _navigation(issue_url: str, comment_ids: List[str], index: int) -> str:
    """Build previous/next links; Jira cannot thread a reply under a comment.

    Joined by a blank line so each stays its own ADF paragraph. Run together on
    one line, `markdown_to_adf` folds both into a single paragraph and the
    reader gets one line holding two links.
    """
    if len(comment_ids) < 2:
        return ""
    links = []
    if index > 0:
        links.append(f"Previous part: {issue_url}?focusedCommentId={comment_ids[index - 1]}")
    if index + 1 < len(comment_ids):
        links.append(f"Next part: {issue_url}?focusedCommentId={comment_ids[index + 1]}")
    return "\n\n".join(links)


def _render_comment(
    part: str,
    revision: str,
    part_number: int = 1,
    part_count: int = 1,
    navigation: str = "",
) -> str:
    header = f"{_part_header(part_number, part_count)}\n\n" if part_count > 1 else ""
    nav_block = f"\n\n{navigation.strip()}" if navigation.strip() else ""
    rendered = (
        f"{header}{part.rstrip()}{nav_block}\n\n"
        f"{_footer_for(revision, part_number, part_count)}"
    )
    if len(rendered) > _MAX_COMMENT_CHARS:
        raise ValueError(f"Rendered Jira plan part exceeds {_MAX_COMMENT_CHARS} characters")
    return rendered


def _plan_properties(revision: str, part_number: int, part_count: int) -> List[dict]:
    """The entity property stamped on every plan comment Koan writes."""
    return [{
        "key": _PLAN_PROPERTY_KEY,
        "value": {"revision": revision, "part": part_number, "parts": part_count},
    }]


def _authored_by_koan(comment: dict) -> bool:
    properties = comment.get("properties")
    return isinstance(properties, dict) and _PLAN_PROPERTY_KEY in properties


def _provably_koan(comment: dict) -> bool:
    """Authorship Koan can prove before overwriting a comment body."""
    return (
        _authored_by_koan(comment)
        or jira_comment_authored_by_self(comment) is True
    )


def koan_authorship_check(comments, strict: bool = False) -> Callable[[dict], bool]:
    """"Did Koan write this comment?", keyed on the plan entity property.

    See :func:`app.jira_notifications.koan_authorship_check` for the rule and
    for what ``strict`` buys a caller that is about to replace a comment body;
    the plan comment's proof of authorship is the ``koan.jira.plan`` property.
    """
    return _jira_notifications.koan_authorship_check(
        comments, _PLAN_PROPERTY_KEY, strict=strict,
    )


def _find_plan_comments(
    comments, strict: bool = False,
) -> List[Tuple[dict, str, int, int]]:
    """Return every Koan plan comment as ``(comment, revision, part, count)``.

    Identity is authorship (see :func:`koan_authorship_check`) *and* the
    trailing footer. Authorship answers "is this ours?" — a reviewer who pastes
    the tail of a plan into their own comment ends it with the footer, and
    without the guard that comment becomes what the next revision edits and
    what the retirement pass blanks out. The footer answers "which revision and
    part?" and is still matched at the end of the body, so a plan quoted
    mid-body is not mistaken for a plan comment either.

    ``strict`` is for callers selecting a comment to *overwrite*: it demands
    proof of authorship rather than the absence of a foreign one, so a tenant
    that answers neither ``expand=properties`` nor ``/myself`` costs a stray
    plan part instead of a reviewer's text.
    """
    authored_by_koan = koan_authorship_check(comments, strict=strict)

    found = []
    for comment in comments or []:
        if not authored_by_koan(comment):
            continue
        match = _FOOTER_RE.search((comment.get("body") or "").rstrip())
        if match:
            revision, part, count = match.group(1), match.group(2), match.group(3)
            found.append((comment, revision, int(part or 1), int(count or 1)))
    return found


def _locate_part(comments, part_number: int) -> Optional[dict]:
    """The comment currently holding part N, whatever revision it carries.

    Revision-agnostic on purpose: a new plan revision must *update* the comment
    holding that part rather than post a fresh one beside it.

    Its only caller edits what it returns, so authorship must be proven
    (``strict``) rather than merely unrefuted.
    """
    for comment, _rev, part, _count in _find_plan_comments(comments, strict=True):
        if part == part_number:
            return comment
    return None


def _verify_part(
    comments,
    revision: str,
    part_number: int,
    owned_ids=frozenset(),
) -> Optional[dict]:
    """The comment proving Jira holds this exact revision of part N.

    A read-back is not read-only in its consequences: what it returns is
    reported as the published comment, and its id is handed to the navigation
    pass, which rewrites that body. So only a comment Koan *owns* qualifies —
    one whose authorship is proven, or one this publish itself wrote
    (``owned_ids``). The footer alone is not ownership: a reviewer quoting the
    tail of a plan reproduces it, and on a tenant that drops properties and
    answers no ``/myself`` that quote would otherwise be adopted as the
    published part and then overwritten with plan text. The cost of refusing it
    is a duplicate part on such a tenant, which is what this module already
    trades for everywhere else it cannot prove authorship.
    """
    for comment, rev, part, _count in _find_plan_comments(comments):
        if rev != revision or part != part_number:
            continue
        if _provably_koan(comment) or str(comment.get("id", "")) in owned_ids:
            return comment
    return None


def _plan_prose(text: str) -> str:
    """``text`` minus its code blocks, whitespace-normalised.

    Jira stores ADF and reads back through ``_adf_to_text``, which drops
    ``codeBlock`` content — so this is the part of a published part that a
    read-back can still be compared against.
    """
    kept, inside = [], False
    for line in (text or "").splitlines():
        if _FENCE_RE.match(line):
            inside = not inside
            continue
        if not inside:
            kept.append(line)
    return " ".join(" ".join(kept).split())


def _seen_part(
    comments,
    revision: str,
    part_number: int,
    part_text: str,
) -> Optional[dict]:
    """A comment already carrying this revision of part N *and its content*.

    Enough to skip a write — the issue already shows this part, so writing
    again would only duplicate it — but never enough to pick an edit target,
    which is why it is separate from :func:`_verify_part`.

    The content test is what keeps this usable on a tenant that proves no
    authorship: a reviewer quoting the tail of a plan reproduces the footer but
    not the part's prose, so their comment is not mistaken for Koan's own
    resumed publish and silently left standing in for it.
    """
    prose = _plan_prose(part_text)
    if not prose:
        return None
    for comment, rev, part, _count in _find_plan_comments(comments):
        if rev != revision or part != part_number:
            continue
        if prose in " ".join((comment.get("body") or "").split()):
            return comment
    return None


def _stage_unreadable(issue_url: str, reason: str) -> None:
    """Report a stage that exists but cannot be used.

    Silence here would be indistinguishable from "nothing was staged", and what
    is lost is the model run this whole module exists to protect.
    """
    issue_key = ""
    with suppress(Exception):
        issue_key = parse_jira_url(issue_url) or ""
    # An unparseable URL must not cost the record its only identifier — the
    # whole point of this event is telling an operator *which* issue lost its
    # staged plan. Fall back to the raw URL rather than logging "failed for ?".
    _audit(issue_key or issue_url or "<unknown issue>", "stage_read", "failure", 1,
           error=reason[:180])


def _read_stage(issue_url: str, instance_dir: str) -> Optional[dict]:
    path = stage_path_for(issue_url, instance_dir)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        _stage_unreadable(issue_url, f"{type(exc).__name__}: {exc}")
        return None
    if not isinstance(data, dict):
        _stage_unreadable(issue_url, "staged payload is not an object")
        return None
    if data.get("issue_url") != issue_url or not isinstance(data.get("comment_body"), str):
        _stage_unreadable(issue_url, "staged payload is missing issue_url/comment_body")
        return None
    return data


def _write_stage(issue_url: str, instance_dir: str, payload: dict) -> None:
    path = stage_path_for(issue_url, instance_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _clear_staged_plan(issue_url: str, instance_dir: str) -> bool:
    """Delete the stage, reporting whether it is actually gone.

    A swallowed failure here is not cosmetic: the stage surviving means the
    next `/plan` on this issue resumes and republishes it instead of generating
    the plan the user just asked for. Callers must not claim the stage was
    cleared or abandoned unless this returns True.
    """
    path = stage_path_for(issue_url, instance_dir)
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        issue_key = ""
        with suppress(Exception):
            issue_key = parse_jira_url(issue_url)
        _audit(
            issue_key, "stage_clear", "failure", 1,
            error=str(exc)[:180], stage_path=str(path),
        )
        return False


def stage_plan(
    issue_url: str,
    comment_body: str,
    instance_dir: str = "",
    iterations: int = 1,
) -> None:
    """Atomically persist a generated plan before trying to publish it.

    ``iterations`` records how many critique/refine rounds produced this body,
    so a later run asking for more of them is not served this copy.
    """
    _write_stage(issue_url, instance_dir, {
        "issue_url": issue_url,
        "issue_key": parse_jira_url(issue_url),
        "comment_body": comment_body,
        "staged_at": time.time(),
        "sessions": 0,
        "iterations": _coerce_iterations(iterations),
    })


def _coerce_iterations(value: object) -> int:
    """Critique rounds behind a plan; 1 for anything unusable or legacy."""
    usable = isinstance(value, int) and not isinstance(value, bool) and value > 0
    return int(value) if usable else 1


def load_staged_plan(
    issue_url: str,
    instance_dir: str = "",
    wanted_iterations: int = 0,
) -> Optional[str]:
    """Return the pending plan body for this issue, if a publish needs resuming.

    An expired stage is discarded (and reported absent) so a permanently
    undeliverable plan eventually gives way to a freshly generated one.

    ``wanted_iterations`` lets a caller state how many critique rounds the run
    it is serving asked for. A stage produced with fewer rounds predates that
    request exactly as one predates newly supplied instructions, so it is
    reported absent rather than replayed — republishing it would silently drop
    the refinement the requester asked for and still report success.
    """
    data = _read_stage(issue_url, instance_dir)
    if data is None:
        return None

    staged_at = data.get("staged_at")
    if isinstance(staged_at, (int, float)) and time.time() - staged_at > _STAGE_MAX_AGE_SECONDS:
        # Report absent whether or not the delete lands. Returning the body
        # instead would resurrect exactly the undeliverable plan the expiry
        # exists to retire, and a surviving file is harmless here: the next
        # stage_plan() overwrites this same path. The failed delete is audited
        # by _clear_staged_plan rather than passed back.
        _clear_staged_plan(issue_url, instance_dir)
        return None

    if wanted_iterations > _coerce_iterations(data.get("iterations")):
        # Left on disk deliberately: the caller regenerates, and the fresh
        # plan overwrites this same path a moment later.
        return None
    return data["comment_body"]


def _audit(issue_key: str, action: str, result: str, attempt: int, **details) -> None:
    log_event(
        TRACKER_COMMENT_MUTATION,
        result=result,
        details={
            "provider": "jira", "issue_key": issue_key, "action": action,
            "attempt": attempt, **details,
        },
    )
    if result == "failure":
        # log_event returns early when auditing is disabled, so a failure whose
        # only trace is security.jsonl is invisible to whoever runs `make logs`.
        extra = " ".join(f"{k}={v}" for k, v in details.items() if v not in (None, ""))
        _log_runner(
            "jira",
            f"plan {action} failed for {issue_key or '?'} "
            f"(attempt {attempt}){': ' + extra if extra else ''}",
        )


def _record_failed_session(
    issue_url: str,
    instance_dir: str,
    reason: str = "verification_failed",
) -> Tuple[bool, str]:
    """Count a failed publish run, abandoning the stage once the cap is hit."""
    data = _read_stage(issue_url, instance_dir)
    if data is None:
        return False, reason

    sessions = int(data.get("sessions") or 0) + 1
    if sessions >= _MAX_PUBLISH_SESSIONS:
        # Only report abandonment if the stage is genuinely gone; otherwise the
        # next run would replay a plan we just told the operator we dropped.
        if _clear_staged_plan(issue_url, instance_dir):
            return False, f"abandoned_after_{sessions}_failed_runs"

    data["sessions"] = sessions
    _write_stage(issue_url, instance_dir, data)
    return False, reason


def _contains_navigation(body: str, navigation: str) -> bool:
    """Whether a read-back comment already carries these navigation links.

    Compared on collapsed whitespace: the body comes back through ADF, which
    splits a linked URL into its own text node and re-joins siblings with a
    space. A literal substring test never matches that, so every part would be
    rewritten on every run and re-notify every watcher.
    """
    if not navigation.strip():
        return True
    return " ".join(navigation.split()) in " ".join(body.split())


def _upsert_part(
    issue_key: str,
    revision: str,
    part: str,
    part_number: int,
    part_count: int,
    navigation: str,
    attempts: int,
    always_write: bool = False,
    comment_id: str = "",
) -> Tuple[bool, str, bool]:
    """Create or update one plan part, requiring a Jira read-back match.

    ``always_write`` forces the edit used to attach navigation links, whose
    targets are only known once every part has an id.

    ``comment_id`` is the id this same publish already wrote and read-back
    verified for this part. The navigation pass passes it so the edit target is
    known rather than re-derived from a fresh listing: on a tenant that drops
    comment properties *and* answers no ``/myself``, re-deriving proves nothing
    about authorship, and refusing the overwrite would post a duplicate of a
    part that is already on the issue.

    Returns ``(published, detail, owned)``: the Jira comment id on success, and
    otherwise a machine-readable failure reason (``""`` for the generic
    verification failure the caller names itself). ``owned`` is False when the
    part was merely *found* on the issue rather than written or proven here —
    the caller must not hand such a comment to a pass that rewrites it.
    """
    try:
        rendered = _render_comment(part, revision, part_number, part_count, navigation)
    except ValueError as exc:
        _audit(issue_key, "render", "failure", 0, error=str(exc)[:180], part=part_number)
        return False, "", False

    properties = _plan_properties(revision, part_number, part_count)
    created_unverified = False
    # Comments this publish is entitled to claim as its own writes, on a tenant
    # that can prove nothing: the caller's verified id, plus whatever this loop
    # creates below.
    owned_ids = {comment_id} if comment_id else set()
    for attempt in range(1, max(1, attempts) + 1):
        # A failed lookup is not "no plan comment yet" — creating one here is how
        # a flaky read path turns into a pile of duplicate plan comments.
        try:
            comments = jira_list_comments_checked(issue_key)
        except Exception as exc:
            _audit(issue_key, "lookup", "failure", attempt, error=str(exc)[:180], part=part_number)
            if attempt < attempts:
                time.sleep(attempt)
            continue

        settled = _verify_part(comments, revision, part_number, owned_ids)
        if settled is not None and (
            not always_write or _contains_navigation(settled.get("body") or "", navigation)
        ):
            _audit(
                issue_key, "verify", "success", attempt,
                comment_id=settled.get("id", ""), part=part_number, parts=part_count,
            )
            return True, str(settled.get("id", "")), True

        if settled is None and not always_write:
            seen = _seen_part(comments, revision, part_number, part)
            if seen is not None:
                # The issue already shows this part, content and all, but on a
                # tenant that drops properties and answers no `/myself` nothing
                # *proves* Koan wrote it. Publishing again would duplicate it;
                # editing it would risk someone else's comment. Report it as
                # already published and touch nothing, at the cost of leaving it
                # unlinked (see `publish_staged_plan`).
                _audit(
                    issue_key, "verify", "success", attempt,
                    comment_id=seen.get("id", ""), part=part_number, parts=part_count,
                )
                return True, str(seen.get("id", "")), False

        # From here on `settled` is an edit target — the navigation pass
        # rewrites the comment it already verified. `_verify_part` only returns
        # a comment Koan owns (proven author, or written by this publish), so
        # it is safe to overwrite; a caller-supplied id likewise, since this
        # publish created and verified that comment moments ago.
        existing: Optional[dict] = {"id": comment_id} if comment_id else None
        if existing is None and settled is not None:
            existing = settled
        if existing is None:
            existing = _locate_part(comments, part_number)
        if existing is None and created_unverified:
            # An earlier attempt already tried to create this part and the
            # comment is still not in the listing. Jira's comment read path is
            # not read-your-writes, so this is at least as likely to be a
            # lagging replica as a write that never landed — and creating again
            # is exactly how one plan part becomes two, each notifying every
            # watcher. Stop and let the next mission run re-verify; the stage is
            # kept, so no model run is lost.
            _audit(
                issue_key, "create", "failure", attempt,
                error="create attempted but never read back",
                part=part_number, parts=part_count,
            )
            return False, "created_unverified", False

        known_ids = {str(c.get("id", "")) for c in comments}
        action = "update" if existing is not None else "create"
        if existing is not None:
            owned_ids.add(str(existing.get("id", "")))
        if action == "create":
            # Arm the guard on the *attempt*, not on its reported result: a POST
            # whose response is lost (socket timeout, dropped connection) is
            # reported as a failure by `jira_add_comment` even though Jira
            # created the comment. Trusting that report is how a duplicate
            # `Part N of M` gets posted on the next attempt.
            created_unverified = True
        try:
            ok = (
                jira_edit_comment(
                    issue_key, str(existing.get("id", "")), rendered, properties=properties,
                )
                if existing is not None
                else jira_add_comment(issue_key, rendered, properties=properties)
            )
        except Exception as exc:
            ok = False
            _audit(
                issue_key, action, "failure", attempt,
                error=str(exc)[:180], part=part_number, parts=part_count,
            )
        else:
            _audit(
                issue_key, action, "success" if ok else "failure", attempt,
                part=part_number, parts=part_count,
            )

        try:
            fresh = jira_list_comments_checked(issue_key)
        except Exception as exc:
            verified = None
            _audit(issue_key, "verify", "failure", attempt, error=str(exc)[:180], part=part_number)
        else:
            if action == "create":
                # `jira_add_comment` hands back no id, so a comment that was not
                # in the pre-write listing is how a create claims its own write
                # on a tenant that proves nothing about authorship.
                owned_ids |= {
                    str(c.get("id", ""))
                    for c in fresh
                    if str(c.get("id", "")) not in known_ids
                }
            verified = _verify_part(fresh, revision, part_number, owned_ids)

        # The navigation pass edits a comment that already carries this revision,
        # so a revision match alone proves nothing about whether the edit landed.
        # Read back the artifact this write was for — the links themselves.
        if verified is not None and always_write and not _contains_navigation(
            verified.get("body") or "", navigation
        ):
            verified = None

        if verified is not None:
            _audit(
                issue_key, "verify", "success", attempt,
                comment_id=verified.get("id", ""), part=part_number, parts=part_count,
            )
            return True, str(verified.get("id", "")), True

        _audit(
            issue_key, "verify", "failure", attempt,
            post_result=bool(ok), part=part_number, parts=part_count,
        )
        if attempt < attempts:
            time.sleep(attempt)

    return False, "", False


def _retire_superseded_parts(
    issue_key: str,
    revision: str,
    part_count: int,
    published_ids: Optional[Sequence[str]] = None,
) -> bool:
    """Blank out plan comments left behind by an earlier, longer plan.

    Without this, shrinking a 3-part plan to 2 parts strands part 3 on the issue
    with stale content and a dangling "previous part" link — and because
    `/implement` looks for multipart groups before single-part plans, that
    stranded group is what it would implement.

    Jira exposes no comment delete here, so the body is replaced and its footer
    dropped, which stops the comment being matched as a plan part. Returns True
    only once a read-back shows no superseded part remains: Jira's write
    endpoints report success for writes that never landed, so an unverified
    retirement must not let the caller declare the publish complete.

    This pass destroys a comment body, so it is the one place that demands
    *proof* of authorship — the ``koan.jira.plan`` property, or Jira naming
    Koan's own account as the author. A comment that merely looks like a stale
    part (a human quoting an older plan on a property-less deployment) is left
    untouched: a stranded part is recoverable, an overwritten human comment is
    not.

    ``published_ids`` are the comments this publish just wrote *and* read-back
    verified. They are never orphans, whatever this fresh listing says: Jira's
    comment read path is not read-your-writes (see :func:`_upsert_part`), so a
    lagging replica can still be serving the pre-edit body — old revision,
    ``koan.jira.plan`` property intact — and without this guard the pass would
    blank out the plan it just published, then read no footer on it and report
    the retirement as complete.
    """
    protected = {str(comment_id) for comment_id in (published_ids or []) if comment_id}

    def superseded(comments):
        return [
            comment for comment, rev, part, _count in _find_plan_comments(comments)
            if (rev != revision or part > part_count)
            and str(comment.get("id", "")) not in protected
            and _provably_koan(comment)
        ]

    try:
        orphans = superseded(jira_list_comments_checked(issue_key))
    except Exception as exc:
        _audit(issue_key, "retire", "failure", 1, error=str(exc)[:180])
        return False

    if not orphans:
        return True

    for comment in orphans:
        comment_id = str(comment.get("id", ""))
        try:
            jira_edit_comment(issue_key, comment_id, _SUPERSEDED_BODY)
        except Exception as exc:
            _audit(issue_key, "retire", "failure", 1, comment_id=comment_id, error=str(exc)[:180])

    try:
        remaining = superseded(jira_list_comments_checked(issue_key))
    except Exception as exc:
        _audit(issue_key, "retire", "failure", 1, error=str(exc)[:180])
        return False

    _audit(
        issue_key, "retire", "success" if not remaining else "failure", 1,
        retired=len(orphans) - len(remaining), remaining=len(remaining),
    )
    return not remaining


def publish_staged_plan(
    issue_url: str,
    instance_dir: str = "",
    attempts: int = _PUBLISH_ATTEMPTS,
) -> Tuple[bool, str]:
    """Publish and read-back verify the staged plan comment(s) for ``issue_url``.

    The ``koan.jira.plan`` property plus the footer make an ordinary retry an
    update of Koan's own existing plan comment rather than a second one, and
    the footer's revision proves Jira is holding this exact staged plan before
    success is reported. A failed verification deliberately leaves the staged
    artifact intact so the next mission run does not have to regenerate the
    plan.

    Oversized plans are published as consecutive parts, then revisited to attach
    previous/next links once every part id is known.

    Returns ``(published, detail)`` where ``detail`` is the Jira comment id — or
    comma-joined ids for a split plan — on success, and otherwise a
    machine-readable failure reason.
    """
    comment_body = load_staged_plan(issue_url, instance_dir)
    if comment_body is None:
        return False, "no_staged_plan"

    issue_key = parse_jira_url(issue_url)
    revision = _revision(comment_body)
    parts = _plan_parts(comment_body)
    part_count = len(parts)

    def failure(part_number: int, stage: str) -> Tuple[bool, str]:
        reason = (
            f"part_{part_number}_of_{part_count}_{stage}" if part_count > 1 else stage
        )
        return _record_failed_session(issue_url, instance_dir, reason)

    comment_ids: List[str] = []
    owned: List[bool] = []
    for index, part in enumerate(parts):
        posted, detail, part_owned = _upsert_part(
            issue_key, revision, part, index + 1, part_count, "", attempts,
        )
        if not posted:
            return failure(index + 1, detail or "verification_failed")
        comment_ids.append(detail)
        owned.append(part_owned)

    if part_count > 1:
        for index, part in enumerate(parts):
            if not owned[index]:
                # This part was found on the issue carrying the right revision,
                # but on a tenant that proves no authorship. Attaching links
                # means rewriting a body Koan cannot claim — a reviewer quoting
                # the plan's tail would lose their comment. Leave it unlinked;
                # the part itself is published and reassembly does not need the
                # links.
                _log_runner(
                    "jira",
                    f"Part {index + 1}/{part_count} of plan {revision} on "
                    f"{issue_key} could not be proven Koan's own; publishing it "
                    f"without previous/next links rather than rewriting it.",
                )
                continue
            posted, detail, _owned = _upsert_part(
                issue_key, revision, part, index + 1, part_count,
                _navigation(issue_url, comment_ids, index), attempts,
                always_write=True, comment_id=comment_ids[index],
            )
            if not posted:
                return failure(index + 1, detail or "navigation_failed")
            comment_ids[index] = detail

    # Keep the stage until cleanup is verified. A stranded older group would be
    # picked up by `/implement` in preference to this revision, so the publish
    # is not finished while one survives — the next run resumes and retries.
    if not _retire_superseded_parts(issue_key, revision, part_count, comment_ids):
        return _record_failed_session(
            issue_url, instance_dir, "superseded_parts_not_retired",
        )

    if not _clear_staged_plan(issue_url, instance_dir):
        # The comments are verified, but a surviving stage makes the next
        # `/plan` on this issue resume and republish it instead of generating
        # the plan that was asked for. Report the inconsistency instead of a
        # clean success; the next run re-verifies cheaply (no model call) and
        # retries the delete. Deliberately not counted as a failed publish
        # session — the publish itself worked.
        return False, "stage_clear_failed"

    return True, ", ".join(comment_ids)
