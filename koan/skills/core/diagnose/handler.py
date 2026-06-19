"""Koan /diagnose skill -- find the last failure and queue a fix attempt."""

import re
from datetime import date, timedelta
from pathlib import Path


_CAUSE_TAG_RE = re.compile(r"\[([a-z_:]+)\]\s*$")
_MAX_JOURNAL_CHARS = 3000


def handle(ctx):
    """Handle /diagnose -- analyze last failure and queue a fix mission."""
    args = ctx.args.strip() if ctx.args else ""

    project_filter = args or None
    if project_filter:
        from app.utils import resolve_project_alias
        project_filter = resolve_project_alias(project_filter) or project_filter

    instance_dir = Path(ctx.instance_dir)
    missions_path = instance_dir / "missions.md"

    if not missions_path.exists():
        return "No missions.md found."

    failure = _find_last_failure(missions_path, project_filter)
    if not failure:
        suffix = f" for project '{project_filter}'" if project_filter else ""
        return f"No failed missions found{suffix}."

    journal_context = _get_journal_context(
        instance_dir, failure["project"], failure["date"],
    )

    return _queue_fix_mission(ctx, failure, journal_context)


def _find_last_failure(missions_path, project_filter=None):
    """Find the most recent failed mission from the mission store.

    Returns dict with keys: text, date, time, project, cause_tag
    or None if no failures found.
    """
    from app.mission_store import MissionStore

    store = MissionStore.load(str(missions_path.parent))
    failed = store.get_by_status("failed")

    if not failed:
        return None

    best = None
    for record in failed:
        # A failed record without a completion timestamp is skipped, mirroring
        # the legacy behaviour of ignoring entries without a ❌ (date time) marker.
        if not record.completed_at:
            continue

        fail_date, _, fail_time = record.completed_at.partition(" ")
        if not fail_date or not fail_time:
            continue

        project = record.project or None
        if project_filter and project and project.lower() != project_filter.lower():
            continue

        # Cause tags like ``[stagnation:tool_loop]`` are not captured as
        # structured store tags (those only cover bare ``[flushed]``/
        # ``[stagnation]``), so they remain embedded at the end of the text.
        text = record.text
        cause_match = _CAUSE_TAG_RE.search(text)
        if cause_match:
            cause_tag = cause_match.group(1)
            clean_text = _CAUSE_TAG_RE.sub("", text).strip()
        elif record.tags:
            cause_tag = record.tags[-1]
            clean_text = text.strip()
        else:
            cause_tag = None
            clean_text = text.strip()

        sort_key = f"{fail_date}T{fail_time}"
        if best is None or sort_key > best["sort_key"]:
            best = {
                "text": clean_text,
                "date": fail_date,
                "time": fail_time,
                "project": project,
                "cause_tag": cause_tag,
                "sort_key": sort_key,
            }

    if best:
        best.pop("sort_key")
    return best


def _get_journal_context(instance_dir, project, fail_date):
    """Read journal entries for the failure date to get context."""
    from app.journal import get_journal_file, read_all_journals

    if project:
        journal_path = get_journal_file(instance_dir, fail_date, project)
        if journal_path.exists():
            content = journal_path.read_text().strip()
            if content:
                if len(content) > _MAX_JOURNAL_CHARS:
                    content = "...\n" + content[-(_MAX_JOURNAL_CHARS - 4):]
                return content

    all_journals = read_all_journals(instance_dir, fail_date)
    if all_journals:
        if len(all_journals) > _MAX_JOURNAL_CHARS:
            all_journals = "...\n" + all_journals[-(_MAX_JOURNAL_CHARS - 4):]
        return all_journals

    yesterday = (date.fromisoformat(fail_date) - timedelta(days=1)).isoformat()
    if project:
        journal_path = get_journal_file(instance_dir, yesterday, project)
        if journal_path.exists():
            content = journal_path.read_text().strip()
            if content:
                if len(content) > _MAX_JOURNAL_CHARS:
                    content = "...\n" + content[-(_MAX_JOURNAL_CHARS - 4):]
                return content

    return None


def _is_already_queued(instance_dir, failure_text):
    """Check if a diagnose mission for this failure is already pending."""
    from app.mission_store import MissionStore

    store = MissionStore.load(str(instance_dir))
    needle = failure_text[:80]
    for record in store.get_by_status("pending") + store.get_by_status("in_progress"):
        if "Diagnose and fix" in record.text and needle in record.text:
            return True
    return False


def _queue_fix_mission(ctx, failure, journal_context):
    """Compose and queue a diagnostic fix mission."""
    from app.utils import insert_pending_mission

    if _is_already_queued(ctx.instance_dir, failure["text"]):
        return "A diagnose mission for this failure is already queued."

    parts = ["Diagnose and fix the following failure:"]
    parts.append(f"  Failed mission: {failure['text']}")
    parts.append(f"  Failed at: {failure['date']} {failure['time']}")
    if failure["cause_tag"]:
        parts.append(f"  Cause: {failure['cause_tag']}")

    if journal_context:
        parts.append("")
        parts.append("Journal context from that session:")
        parts.append(journal_context)

    body = "\n".join(parts)

    insert_pending_mission(body, failure["project"], urgent=True)

    preview = failure["text"][:100]
    cause = f" ({failure['cause_tag']})" if failure["cause_tag"] else ""
    project_label = f" [{failure['project']}]" if failure["project"] else ""
    return (
        f"🔍 Diagnosis queued{project_label}: {preview}"
        f"{'...' if len(failure['text']) > 100 else ''}"
        f"{cause}"
    )
