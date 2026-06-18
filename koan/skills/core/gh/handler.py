"""Show GitHub CLI authentication status."""

import subprocess


def handle(ctx):
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (result.stdout or result.stderr).strip()
    except FileNotFoundError:
        output = "gh CLI not found"
    except subprocess.TimeoutExpired:
        output = "gh auth status timed out"

    if not output:
        output = "No output from gh auth status"

    return f"```\n{output}\n```"
