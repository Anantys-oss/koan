"""Journal date/day readers (no Flask)."""
import re

from app.dashboard import state


def get_journal_dates(limit: int = 7) -> list[str]:
    """Return up to *limit* most recent journal date strings (YYYY-MM-DD), newest first."""
    if not state.JOURNAL_DIR.exists():
        return []
    dates: set[str] = set()
    for item in state.JOURNAL_DIR.iterdir():
        if item.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", item.name):
            dates.add(item.name)
        elif item.suffix == ".md" and re.match(r"\d{4}-\d{2}-\d{2}", item.stem):
            dates.add(item.stem)
    return sorted(dates, reverse=True)[:limit]


def get_journal_day(day: str) -> list[dict]:
    """Load journal entries for a single date string."""
    day_entries: list[dict] = []
    nested = state.JOURNAL_DIR / day
    if nested.is_dir():
        day_entries.extend(
            {"project": f.stem, "content": f.read_text()}
            for f in sorted(nested.glob("*.md"))
        )
    flat = state.JOURNAL_DIR / f"{day}.md"
    if flat.is_file():
        day_entries.append({"project": "general", "content": flat.read_text()})
    return day_entries


def get_journal_entries(limit: int = 7) -> list:
    """Get recent journal entries."""
    entries = []
    for d in get_journal_dates(limit):
        day_entries = get_journal_day(d)
        if day_entries:
            entries.append({"date": d, "entries": day_entries})
    return entries


def get_rule_history(limit: int = 50) -> list:
    """Read [automation_rule]-tagged journal lines, capped at `limit` entries."""
    entries = []
    if not state.JOURNAL_DIR.exists():
        return entries

    journal_dates = sorted(
        (d for d in state.JOURNAL_DIR.iterdir()
         if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", d.name)),
        reverse=True,
    )

    for day_dir in journal_dates:
        auto_file = day_dir / "automation.md"
        if not auto_file.exists():
            continue
        for line in reversed(auto_file.read_text().splitlines()):
            if "[automation_rule]" in line:
                entries.append({"date": day_dir.name, "line": line.strip()})
                if len(entries) >= limit:
                    return entries
    return entries
