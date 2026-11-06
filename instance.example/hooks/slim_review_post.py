"""Post-mission slim review hook.

Automatically runs a lightweight Claude review (haiku model) on the diff
of any PR created during a mission, then writes findings to the project's
daily journal.

Setup:
    1. Copy this file to instance/hooks/slim_review_post.py
    2. Copy slim_review_prompt.md to instance/hooks/slim_review_prompt.md
    3. Add to instance/config.yaml:
           slim_review_hook:
             enabled: true
    4. Restart Koan

Configuration (instance/config.yaml):
    slim_review_hook:
      enabled: true          # Master switch (default: false)

The hook is completely inert when disabled — no file I/O, no subprocess
calls, no imports beyond stdlib.

Dedup: The hook tracks a SHA-256 hash of each PR's diff in
instance/.slim-review-tracker.json. If the diff hasn't changed since
the last review, the Claude call is skipped.
"""

import hashlib
import json
import re
import sys
import threading
from pathlib import Path

# Mirrors mission_runner._extract_pr_url() — the post_mission context does not
# carry a pr_url key, so the URL is recovered from the truncated result_text.
_PR_URL_RE = re.compile(r'https?://[^/]*github[^\s)]+/pull/\d+')
_SKIP_COMMANDS = frozenset(('/review', '/rebase', '/slim_review', '/review_rebase'))
_TRACKER_FILE = ".slim-review-tracker.json"


def on_post_mission(ctx):
    """Entry point — called by hooks.py for every post_mission event."""
    # --- Fast exit: config check (no heavy imports) ---
    instance_dir = ctx.get("instance_dir", "")
    if not instance_dir:
        return

    try:
        from app.utils import load_config
        config = load_config() or {}
    except Exception:
        return

    hook_cfg = config.get("slim_review_hook", {})
    if not isinstance(hook_cfg, dict):
        return
    if not hook_cfg.get("enabled", False):
        return

    # --- Only successful missions ---
    if ctx.get("exit_code", -1) != 0:
        return

    # --- Skip review/rebase/slim_review missions (prevent loops) ---
    mission_title = ctx.get("mission_title", "") or ""
    tokens = re.findall(r'/\w+', mission_title.lower())
    if any(t in _SKIP_COMMANDS for t in tokens):
        return

    # --- Extract PR URL from result_text ---
    result_text = ctx.get("result_text", "") or ""
    match = _PR_URL_RE.search(result_text)
    if not match:
        return
    pr_url = match.group(0)

    # --- Dispatch to background thread (haiku takes 5-10s; don't block loop) ---
    project_name = ctx.get("project_name", "unknown")
    project_path = ctx.get("project_path", "")

    t = threading.Thread(
        target=_run_slim_review,
        args=(instance_dir, project_name, project_path, pr_url),
        daemon=True,
        name=f"slim-review-{project_name}",
    )
    t.start()


def _run_slim_review(instance_dir, project_name, project_path, pr_url):
    """Background thread: fetch diff, dedup, analyze, journal."""
    try:
        _run_slim_review_inner(instance_dir, project_name, project_path, pr_url)
    except Exception as exc:
        print(
            f"[slim_review_hook] Error reviewing {pr_url}: {exc}",
            file=sys.stderr,
        )


def _run_slim_review_inner(instance_dir, project_name, project_path, pr_url):
    from app.github import run_gh

    # 1. Fetch diff
    pr_number = pr_url.rstrip("/").split("/")[-1]
    repo_parts = pr_url.split("github.com/")[-1].split("/pull/")[0]
    try:
        diff = run_gh("pr", "diff", pr_number, "--repo", repo_parts, timeout=30)
    except RuntimeError as exc:
        print(f"[slim_review_hook] gh pr diff failed: {exc}", file=sys.stderr)
        return

    if not diff or not diff.strip():
        return

    # 2. Dedup via content hash — hashing the diff (not the URL) re-triggers
    #    analysis when new commits are pushed to the same PR.
    diff_hash = hashlib.sha256(diff.encode()).hexdigest()
    tracker_path = Path(instance_dir) / _TRACKER_FILE
    tracker = _load_tracker(tracker_path)

    if tracker.get(pr_url) == diff_hash:
        return  # Already reviewed this exact diff

    # 3. Load prompt and build CLI command
    prompt_path = Path(__file__).parent / "slim_review_prompt.md"
    if not prompt_path.exists():
        print(
            f"[slim_review_hook] Prompt file not found: {prompt_path}",
            file=sys.stderr,
        )
        return
    prompt_template = prompt_path.read_text(encoding="utf-8")
    prompt = prompt_template.replace("{DIFF}", diff)

    from app.cli_provider import build_full_command
    from app.config import get_model_config

    models = get_model_config(project_name)
    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=[],
        model=models.get("lightweight", "haiku"),
        fallback=models.get("fallback", "sonnet"),
        max_turns=1,
    )

    # 4. Run Claude CLI
    from app.cli_exec import run_cli_with_retry

    result = run_cli_with_retry(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=project_path or None,
    )

    if result.returncode != 0:
        print(
            f"[slim_review_hook] Claude analysis failed (rc={result.returncode}): "
            f"{(result.stderr or '')[:200]}",
            file=sys.stderr,
        )
        return

    findings = (result.stdout or "").strip()
    if not findings:
        return

    # 5. Write to journal
    from datetime import datetime

    from app.journal import append_to_journal

    now = datetime.now().strftime("%H:%M")
    entry = (
        f"\n### Slim Review — {now}\n"
        f"PR: {pr_url}\n\n"
        f"{findings}\n"
    )
    append_to_journal(Path(instance_dir), project_name, entry)

    # 6. Update tracker
    tracker[pr_url] = diff_hash
    _save_tracker(tracker_path, tracker)


def _load_tracker(path):
    """Load tracker JSON, returning empty dict on any error."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_tracker(path, data):
    """Save tracker JSON atomically."""
    try:
        from app.utils import atomic_write_json
        atomic_write_json(path, data, indent=2)
    except Exception as exc:
        print(f"[slim_review_hook] Tracker save failed: {exc}", file=sys.stderr)


HOOKS = {
    "post_mission": on_post_mission,
}
