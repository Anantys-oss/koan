"""Show current server date and time."""

from datetime import datetime


def handle(ctx):
    now = datetime.now()
    return now.strftime("🕐 %A %B %d, %Y — %H:%M:%S")
