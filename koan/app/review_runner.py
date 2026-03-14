"""
Kōan -- Code review runner.

Performs a read-only code review of a GitHub PR and posts findings as a
comment. Unlike /pr (which modifies code and pushes), /review only reads
and comments.

Pipeline:
1. Fetch PR metadata, diff, and existing comments from GitHub
2. Build a review prompt with PR context
3. Run Claude Code CLI (read-only tools) to analyze the code
4. Parse Claude's review output
5. Post the review as a GitHub comment

CLI:
    python3 -m app.review_runner <github-pr-url> --project-path <path>
"""

import json
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

from app.github import run_gh
from app.prompts import load_prompt_or_skill
from app.rebase_pr import fetch_pr_context
from app.review_schema import validate_review


def build_review_prompt(
    context: dict,
    skill_dir: Optional[Path] = None,
    architecture: bool = False,
) -> str:
    """Build a prompt for Claude to review a PR."""
    prompt_name = "review-architecture" if architecture else "review"
    return load_prompt_or_skill(
        skill_dir, prompt_name,
        TITLE=context["title"],
        AUTHOR=context["author"],
        BRANCH=context["branch"],
        BASE=context["base"],
        BODY=context["body"],
        DIFF=context["diff"],
        REVIEW_COMMENTS=context["review_comments"],
        REVIEWS=context["reviews"],
        ISSUE_COMMENTS=context["issue_comments"],
    )


def _run_claude_review(prompt: str, project_path: str, timeout: int = 600) -> str:
    """Run Claude CLI with read-only tools and return the output text.

    Args:
        prompt: The review prompt.
        project_path: Path to the project for codebase context.
        timeout: Maximum seconds to wait.

    Returns:
        Claude's review text, or empty string on failure.
    """
    from app.claude_step import run_claude
    from app.cli_provider import build_full_command
    from app.config import get_model_config

    models = get_model_config()
    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=["Read", "Glob", "Grep"],
        model=models["mission"],
        fallback=models["fallback"],
        max_turns=15,
    )

    result = run_claude(cmd, project_path, timeout=timeout)
    if result["success"]:
        return result["output"]
    return ""


def _extract_review_body(raw_output: str) -> str:
    """Extract structured review from Claude's raw output.

    Tries to find markdown-structured review content. If the output
    looks like JSON, attempts to parse and format it as markdown.
    Falls back to the full output if no structure is detected.
    """
    # Look for the new format: ## PR Review — ...
    match = re.search(r'(## PR Review\b.*)', raw_output, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Legacy format: ## Summary
    match = re.search(r'(## Summary\b.*)', raw_output, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Safety net: if the output contains JSON, try to parse and format it
    # rather than posting raw JSON to GitHub.
    json_text = _extract_json_text(raw_output)
    if json_text is not None:
        try:
            data = json.loads(json_text)
            is_valid, _ = validate_review(data)
            if is_valid:
                return _format_review_as_markdown(data)
        except (json.JSONDecodeError, ValueError):
            pass

    # Fall back to full output (Claude may format differently)
    return raw_output.strip()


def _extract_json_text(text: str) -> Optional[str]:
    """Extract a JSON object string from text that may contain surrounding prose.

    Tries multiple strategies:
    1. Direct parse of the full text (pure JSON)
    2. Strip markdown code fences (```json ... ```)
    3. Extract JSON from code fences anywhere in the text
    4. Find the outermost { ... } in the text
    """
    stripped = text.strip()

    # Strategy 1: pure JSON
    try:
        json.loads(stripped)
        return stripped
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: text wrapped entirely in code fences
    fence_stripped = stripped
    if fence_stripped.startswith("```json"):
        fence_stripped = fence_stripped[len("```json"):]
    elif fence_stripped.startswith("```"):
        fence_stripped = fence_stripped[len("```"):]
    if fence_stripped.endswith("```"):
        fence_stripped = fence_stripped[:-3]
    fence_stripped = fence_stripped.strip()
    if fence_stripped != stripped:
        try:
            json.loads(fence_stripped)
            return fence_stripped
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 3: code fences embedded in surrounding text
    fence_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', stripped, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 4: find outermost { ... } with brace matching
    start = stripped.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(stripped)):
            c = stripped[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = stripped[start:i + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except (json.JSONDecodeError, ValueError):
                        break
    return None


def _parse_review_json(raw_output: str) -> Optional[dict]:
    """Attempt to parse and validate JSON review output.

    Handles JSON wrapped in markdown code fences or surrounded by
    preamble/postamble text. Returns the validated review dict, or
    None if parsing/validation fails.
    """
    json_text = _extract_json_text(raw_output)
    if json_text is None:
        return None

    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return None

    is_valid, errors = validate_review(data)
    if not is_valid:
        print(
            f"[review_runner] JSON validation errors: {errors}",
            file=sys.stderr,
        )
        return None
    return data


_SEVERITY_EMOJI = {
    "critical": "🔴",
    "warning": "🟡",
    "suggestion": "🟢",
}

_SEVERITY_HEADING = {
    "critical": "Blocking",
    "warning": "Important",
    "suggestion": "Suggestions",
}


def _format_review_as_markdown(review_data: dict, title: str = "") -> str:
    """Convert validated review JSON into the markdown format for GitHub.

    Produces the standard ## PR Review format with severity sections,
    checklist, and summary.
    """
    comments = review_data["file_comments"]
    summary_data = review_data["review_summary"]

    lines: list = []

    # Header
    header = f"## PR Review — {title}" if title else "## PR Review"
    lines.append(header)
    lines.append("")
    lines.append(summary_data["summary"])
    lines.append("")
    lines.append("---")
    lines.append("")

    # Group comments by severity
    by_severity: dict = {"critical": [], "warning": [], "suggestion": []}
    for c in comments:
        sev = c.get("severity", "suggestion")
        by_severity.setdefault(sev, []).append(c)

    # Emit severity sections (skip empty ones)
    for sev in ("critical", "warning", "suggestion"):
        items = by_severity.get(sev, [])
        if not items:
            continue
        emoji = _SEVERITY_EMOJI[sev]
        heading = _SEVERITY_HEADING[sev]
        lines.append(f"### {emoji} {heading}")
        lines.append("")
        for i, item in enumerate(items, 1):
            loc = f"`{item['file']}`"
            if item.get("line_start") and item["line_start"] > 0:
                loc += f", L{item['line_start']}"
                if item.get("line_end") and item["line_end"] != item["line_start"]:
                    loc += f"-{item['line_end']}"
            lines.append(f"**{i}. {item['title']}** ({loc})")
            lines.append(item["comment"])
            if item.get("code_snippet"):
                lines.append("")
                lines.append("```")
                lines.append(item["code_snippet"])
                lines.append("```")
            lines.append("")

    # Checklist
    checklist = summary_data.get("checklist", [])
    if checklist:
        lines.append("---")
        lines.append("")
        lines.append("### Checklist")
        lines.append("")
        for ci in checklist:
            mark = "x" if ci["passed"] else " "
            ref = f" — {ci['finding_ref']}" if ci.get("finding_ref") else ""
            lines.append(f"- [{mark}] {ci['item']}{ref}")
        lines.append("")

    # Summary (always present)
    lines.append("---")
    lines.append("")
    lines.append("### Summary")
    lines.append("")
    lines.append(summary_data["summary"])

    return "\n".join(lines)


def _post_review_comment(
    owner: str, repo: str, pr_number: str, review_text: str,
) -> bool:
    """Post the review as a comment on the PR.

    Returns True on success.
    """
    # Truncate if too long for GitHub (max ~65536 chars)
    max_len = 60000
    if len(review_text) > max_len:
        review_text = review_text[:max_len] + "\n\n_(Review truncated)_"

    # If body already starts with a ## heading, don't add another
    if review_text.startswith("## "):
        body = f"{review_text}\n\n---\n_Automated review by Kōan_"
    else:
        body = f"## Code Review\n\n{review_text}\n\n---\n_Automated review by Kōan_"

    try:
        run_gh(
            "pr", "comment", pr_number,
            "--repo", f"{owner}/{repo}",
            "--body", body,
        )
        return True
    except Exception as e:
        print(f"[review_runner] failed to post comment: {e}", file=sys.stderr)
        return False


def run_review(
    owner: str,
    repo: str,
    pr_number: str,
    project_path: str,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
    architecture: bool = False,
) -> Tuple[bool, str, Optional[dict]]:
    """Execute a read-only code review on a PR.

    Args:
        owner: GitHub owner.
        repo: GitHub repo name.
        pr_number: PR number as string.
        project_path: Local path to the project.
        notify_fn: Optional callback for progress notifications.
        skill_dir: Optional path to the review skill directory for prompts.
        architecture: If True, use architecture-focused review prompt.

    Returns:
        (success, summary, review_data) tuple. review_data is the validated
        JSON review dict, or None if JSON parsing failed (fallback was used).
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    full_repo = f"{owner}/{repo}"

    # Step 1: Fetch PR context
    notify_fn(f"Reviewing PR #{pr_number} ({full_repo})...")
    try:
        context = fetch_pr_context(owner, repo, pr_number)
    except Exception as e:
        return False, f"Failed to fetch PR context: {e}", None

    if not context.get("diff"):
        return False, f"PR #{pr_number} has no diff — nothing to review.", None

    # Step 2: Build review prompt
    prompt = build_review_prompt(context, skill_dir=skill_dir, architecture=architecture)

    # Step 3: Run Claude review (read-only)
    notify_fn(f"Analyzing code changes on `{context['branch']}`...")
    raw_output = _run_claude_review(prompt, project_path)
    if not raw_output:
        return False, f"Claude review produced no output for PR #{pr_number}.", None

    # Step 4: Parse structured JSON review (with retry)
    review_data = _parse_review_json(raw_output)
    if review_data is None:
        # Retry once with explicit JSON instruction
        retry_prompt = (
            prompt
            + "\n\nIMPORTANT: Your previous response was not valid JSON. "
            "You MUST respond with ONLY a valid JSON object matching the "
            "schema described above. No markdown, no text, just JSON."
        )
        retry_output = _run_claude_review(retry_prompt, project_path)
        if retry_output:
            review_data = _parse_review_json(retry_output)

    # Step 5: Convert to markdown for posting
    if review_data is not None:
        review_body = _format_review_as_markdown(
            review_data, title=context.get("title", ""),
        )
    else:
        # Fallback: use regex extraction for non-JSON responses
        print(
            "[review_runner] JSON parsing failed, falling back to regex extraction",
            file=sys.stderr,
        )
        review_body = _extract_review_body(raw_output)

    # Step 6: Post review comment
    notify_fn(f"Posting review on PR #{pr_number}...")
    posted = _post_review_comment(owner, repo, pr_number, review_body)

    if posted:
        summary = f"Review posted on PR #{pr_number} ({full_repo})."
        return True, summary, review_data
    else:
        return False, f"Review generated but failed to post comment on PR #{pr_number}.", review_data


# ---------------------------------------------------------------------------
# CLI entry point -- python3 -m app.review_runner
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for review_runner.

    Returns exit code (0 = success, 1 = failure).
    """
    import argparse

    from app.github_url_parser import parse_pr_url

    parser = argparse.ArgumentParser(
        description="Review a GitHub PR and post findings as a comment."
    )
    parser.add_argument("url", help="GitHub PR URL")
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--architecture", action="store_true",
        help="Use architecture-focused review (SOLID, layering, coupling)",
    )
    cli_args = parser.parse_args(argv)

    try:
        owner, repo, pr_number = parse_pr_url(cli_args.url)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "core" / "review"

    success, summary, _review_data = run_review(
        owner, repo, pr_number, cli_args.project_path,
        skill_dir=skill_dir,
        architecture=cli_args.architecture,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
