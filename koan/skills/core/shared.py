"""Shared helpers for core skill runners.

Issue-body and footer construction was copy-pasted across the debug,
fix, implement, and incident runners. A change to the comment-filtering
rule would previously land in only one skill's filed issues — silent
divergence. Both helpers live here so all runners share one definition.
"""

from typing import List


def build_issue_body(body: str, comments: List[dict]) -> str:
    """Build full issue content including relevant comments.

    Includes the issue body and any comments that add context
    (e.g. reproduction steps, additional details). Skips bot comments
    and very short comments.
    """
    parts = [body.strip()] if body else []

    for comment in comments:
        comment_body = comment.get("body", "").strip()
        author = comment.get("author", "")

        # Skip bot comments and very short comments
        if "[bot]" in author or len(comment_body) < 20:
            continue

        parts.append(f"\n---\n**Comment by {author}**:\n{comment_body}")

    return "\n".join(parts)


def build_footer() -> str:
    from app.pr_footer import build_koan_footer
    return build_koan_footer()
