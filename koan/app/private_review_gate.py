"""Backend-only private review/fix gate for PR-producing skills."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from app.github_url_parser import parse_issue_url, parse_pr_url
from app.git_utils import get_current_branch, run_git_strict
from app.prompts import load_prompt

logger = logging.getLogger(__name__)

SEVERITY_LEVELS = ("critical", "warning", "suggestion")


@dataclass
class PrivateReviewGateResult:
    """Outcome of the private review/fix loop."""

    ran: bool
    clean: bool
    summary: str
    rounds: int = 0
    fixed_rounds: int = 0
    remaining_findings: list = field(default_factory=list)
    skipped_reason: str = ""
    exhausted: bool = False
    error: str = ""


def run_private_review_gate(
    *,
    project_path: str,
    project_name: str,
    pr_url: str,
    notify_fn: Optional[Callable[[str], None]] = None,
    plan_url: Optional[str] = None,
    skill_origin: str = "implement",
    review_skill_dir: Optional[Path] = None,
    push_fn: Optional[Callable[[], None]] = None,
) -> PrivateReviewGateResult:
    """Privately review a PR and fix warning/critical findings in a loop.

    The gate never posts review comments, replies, verdicts, or issue comments.
    It may create and push commits to the existing PR branch when it can fix
    actionable findings. ``push_fn`` lets callers that own a special branch
    update strategy, such as /rebase force-pushes, reuse the same review/fix
    loop while keeping their push semantics.
    """
    notify = notify_fn or (lambda _msg: None)

    if not pr_url:
        return _skipped("no PR URL")

    if not Path(project_path).is_dir():
        return _skipped(f"project path does not exist: {project_path}")

    from app.config import get_private_review_gate_config

    cfg = get_private_review_gate_config(
        project_name,
        skill_origin=skill_origin,
    )
    if not cfg["enabled"]:
        return _skipped("disabled by config")

    max_rounds = cfg["max_rounds"]
    if max_rounds <= 0:
        return _skipped("max_rounds is 0")

    min_severity = cfg["min_severity"]
    plan_url = _github_issue_plan_url(plan_url)

    try:
        owner, repo, pr_number = parse_pr_url(pr_url)
    except ValueError as exc:
        return PrivateReviewGateResult(
            ran=False,
            clean=False,
            summary=f"Private review gate skipped: invalid PR URL ({exc}).",
            skipped_reason="invalid PR URL",
            error=str(exc),
        )

    fixed_rounds = 0
    last_findings: list = []
    last_context: dict = {}

    for round_num in range(1, max_rounds + 1):
        notify(
            f"Private review gate: review round {round_num}/{max_rounds} "
            f"for PR #{pr_number}..."
        )
        ok, summary, review_data, context = _run_private_review(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            project_path=project_path,
            notify_fn=notify,
            review_skill_dir=review_skill_dir,
            plan_url=plan_url,
            project_name=project_name,
        )
        if not ok:
            return PrivateReviewGateResult(
                ran=True,
                clean=False,
                summary=f"Private review gate could not complete: {summary}",
                rounds=round_num,
                fixed_rounds=fixed_rounds,
                remaining_findings=last_findings,
                error=summary,
            )

        last_context = context
        last_findings = _actionable_findings(review_data, min_severity)
        if not last_findings:
            clean_summary = (
                "Private review gate passed"
                if fixed_rounds == 0
                else (
                    "Private review gate passed after "
                    f"{fixed_rounds} fix round(s)"
                )
            )
            notify(clean_summary + ".")
            return PrivateReviewGateResult(
                ran=True,
                clean=True,
                summary=clean_summary,
                rounds=round_num,
                fixed_rounds=fixed_rounds,
            )

        notify(
            f"Private review gate found {len(last_findings)} "
            f"{min_severity}+ finding(s); applying fixes..."
        )

        fixed, fix_summary = _fix_findings(
            context=last_context,
            findings=last_findings,
            project_path=project_path,
            skill_origin=skill_origin,
            min_severity=min_severity,
        )
        if not fixed:
            return PrivateReviewGateResult(
                ran=True,
                clean=False,
                summary=(
                    "Private review gate found actionable findings but "
                    f"could not produce a fix: {fix_summary}"
                ),
                rounds=round_num,
                fixed_rounds=fixed_rounds,
                remaining_findings=last_findings,
                error=fix_summary,
            )

        fixed_rounds += 1
        try:
            if push_fn is None:
                _push_current_branch(project_path)
            else:
                push_fn()
        except Exception as exc:
            error = str(exc)[:300]
            return PrivateReviewGateResult(
                ran=True,
                clean=False,
                summary=(
                    "Private review gate applied a fix commit, but push "
                    f"failed: {error}"
                ),
                rounds=round_num,
                fixed_rounds=fixed_rounds,
                remaining_findings=last_findings,
                error=error,
            )

    ok, summary, review_data, _context = _run_private_review(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        project_path=project_path,
        notify_fn=notify,
        review_skill_dir=review_skill_dir,
        plan_url=plan_url,
        project_name=project_name,
    )
    if ok:
        last_findings = _actionable_findings(review_data, min_severity)
        if not last_findings:
            clean_summary = (
                "Private review gate passed after "
                f"{fixed_rounds} fix round(s)"
            )
            notify(clean_summary + ".")
            return PrivateReviewGateResult(
                ran=True,
                clean=True,
                summary=clean_summary,
                rounds=max_rounds,
                fixed_rounds=fixed_rounds,
            )

    exhausted_summary = (
        "Private review gate reached max rounds with "
        f"{len(last_findings)} remaining {min_severity}+ finding(s)."
    )
    notify(exhausted_summary)
    return PrivateReviewGateResult(
        ran=True,
        clean=False,
        summary=exhausted_summary,
        rounds=max_rounds,
        fixed_rounds=fixed_rounds,
        remaining_findings=last_findings,
        exhausted=True,
        error="" if ok else summary,
    )


def _run_private_review(
    *,
    owner: str,
    repo: str,
    pr_number: str,
    project_path: str,
    notify_fn: Callable[[str], None],
    review_skill_dir: Optional[Path],
    plan_url: Optional[str],
    project_name: str,
) -> tuple:
    from app.review_runner import run_private_review

    return run_private_review(
        owner,
        repo,
        pr_number,
        project_path,
        notify_fn=notify_fn,
        skill_dir=review_skill_dir,
        plan_url=plan_url,
        project_name=project_name,
    )


def _actionable_findings(review_data: Optional[dict], min_severity: str) -> list:
    """Return review findings at or above the configured severity."""
    if not isinstance(review_data, dict):
        return []
    comments = review_data.get("file_comments") or []
    allowed = set(_severity_at_or_above(min_severity))
    return [
        c for c in comments
        if isinstance(c, dict) and c.get("severity") in allowed
    ]


def _severity_at_or_above(min_severity: str) -> list:
    try:
        idx = SEVERITY_LEVELS.index(min_severity)
    except ValueError:
        idx = SEVERITY_LEVELS.index("warning")
    return list(SEVERITY_LEVELS[: idx + 1])


def _fix_findings(
    *,
    context: dict,
    findings: list,
    project_path: str,
    skill_origin: str,
    min_severity: str,
) -> tuple[bool, str]:
    """Run a write-capable provider step to fix the private review findings."""
    branch = context.get("branch", "")
    if branch:
        try:
            current = get_current_branch(cwd=project_path, default="")
            if current != branch:
                run_git_strict("checkout", branch, cwd=project_path, timeout=60)
        except Exception as exc:
            return False, f"could not checkout PR branch `{branch}`: {exc}"

    prompt = _build_fix_prompt(context, findings, min_severity)
    actions_log: list = []

    from app.claude_step import run_claude_step
    from app.config import get_skill_max_turns, get_skill_timeout

    step = run_claude_step(
        prompt=prompt,
        project_path=project_path,
        commit_msg=f"{skill_origin}: address private review findings",
        success_label="Applied private review findings",
        failure_label="Private review fix step failed",
        actions_log=actions_log,
        max_turns=get_skill_max_turns(),
        timeout=get_skill_timeout(),
    )

    if step.committed:
        summary = step.output.strip() or "Private review findings fixed."
        return True, summary[-1000:]

    if getattr(step, "quota_exhausted", False):
        return False, "provider quota exhausted while applying fixes"

    error = (step.error or "").strip()
    if error:
        return False, error[:300]
    return False, "no code changes were produced"


def _build_fix_prompt(context: dict, findings: list, min_severity: str) -> str:
    from app.prompt_guard import fence_external_data

    return load_prompt(
        "implementation-review-fix",
        TITLE=fence_external_data(context.get("title", ""), "PR title"),
        BODY=fence_external_data(context.get("body", ""), "PR body"),
        BRANCH=context.get("branch", ""),
        BASE=context.get("base", ""),
        DIFF=fence_external_data(context.get("diff", ""), "PR diff", scan=False),
        MIN_SEVERITY=min_severity,
        FINDINGS_JSON=fence_external_data(
            json.dumps(findings, indent=2),
            "private review findings",
            scan=False,
        ),
    )


def _push_current_branch(project_path: str) -> None:
    branch = get_current_branch(cwd=project_path, default="")
    if not branch or branch == "HEAD":
        raise RuntimeError("could not determine current branch")
    remote = _resolve_push_remote(branch, project_path)
    run_git_strict("push", remote, branch, cwd=project_path, timeout=120)


def _resolve_push_remote(branch: str, project_path: str) -> str:
    """Return the remote the branch tracks, falling back to ``origin``.

    Koan-created PR branches normally track ``origin``, but fork or
    cross-repo workflows push the head branch to a different remote. Reading
    the branch's configured remote keeps the gate's fix push aligned with how
    the branch was originally published.
    """
    from app.git_utils import run_git

    returncode, stdout, _stderr = run_git(
        "config", "--get", f"branch.{branch}.remote", cwd=project_path,
    )
    if returncode == 0 and stdout.strip():
        return stdout.strip()
    return "origin"


def _github_issue_plan_url(plan_url: Optional[str]) -> Optional[str]:
    """Return plan_url only when it is a GitHub issue URL."""
    if not plan_url:
        return None
    try:
        parse_issue_url(plan_url)
    except ValueError:
        return None
    return plan_url


def _skipped(reason: str) -> PrivateReviewGateResult:
    logger.info("Private review gate skipped: %s", reason)
    return PrivateReviewGateResult(
        ran=False,
        clean=True,
        summary=f"Private review gate skipped: {reason}.",
        skipped_reason=reason,
    )
