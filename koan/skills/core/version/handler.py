"""Kōan version skill — returns the version string."""


def handle(ctx):
    from app.version import get_version

    version = get_version()
    return version if version else "unknown"
