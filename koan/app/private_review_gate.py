"""Backend-only private review/fix gate for PR-producing skills."""

import json
import logging
import time
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
    converged: bool = False
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

    instance_dir = _resolve_instance_dir()

    # Dedup: skip when this PR head was already reviewed clean (e.g. a /rebase
    # re-run or a no-op in-place /fix that left the head unchanged).
    if cfg.get("dedup", True):
        dedup_reason = _dedup_precheck(
            instance_dir, owner, repo, pr_number, project_path, cfg,
        )
        if dedup_reason:
            return _skipped(dedup_reason)

    # Budget preflight: respect the quota governor — skip or reduce rounds when
    # quota is tight, full rounds when idle quota is plentiful.
    if cfg.get("budget_aware", True):
        effective_rounds, budget_note = _budget_preflight(
            instance_dir, max_rounds,
        )
        if effective_rounds <= 0:
            return _skipped(budget_note or "budget too low")
        if effective_rounds < max_rounds and budget_note:
            notify(f"Private review gate: {budget_note}")
        max_rounds = effective_rounds

    fixed_rounds = 0
    last_findings: list = []
    last_context: dict = {}
    prev_fingerprints: Optional[frozenset] = None

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
            _maybe_record_clean(
                cfg=cfg,
                instance_dir=instance_dir,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                project_path=project_path,
                rounds=fixed_rounds,
            )
            return PrivateReviewGateResult(
                ran=True,
                clean=True,
                summary=clean_summary,
                rounds=round_num,
                fixed_rounds=fixed_rounds,
            )

        # Convergence bail: if this round's findings are identical to the
        # previous round's, the prior fix made no progress. Stop instead of
        # burning the remaining rounds (and the trailing review) re-fixing the
        # same findings. Exact-set equality means any progress continues.
        current_fingerprints = _finding_fingerprints(last_findings)
        if (
            round_num > 1
            and current_fingerprints
            and current_fingerprints == prev_fingerprints
        ):
            converged_summary = (
                "Private review gate stopped: the previous fix made no "
                f"progress on {len(last_findings)} {min_severity}+ "
                f"finding(s) after {fixed_rounds} fix round(s)."
            )
            notify(converged_summary)
            return PrivateReviewGateResult(
                ran=True,
                clean=False,
                summary=converged_summary,
                rounds=round_num,
                fixed_rounds=fixed_rounds,
                remaining_findings=last_findings,
                converged=True,
            )
        prev_fingerprints = current_fingerprints

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

    # Trailing verification review: only needed when the final round applied a
    # fix that no later review has re-checked. A convergence bail returns
    # earlier, so this runs only for loops that genuinely ran every round with
    # changing findings — exactly when verifying the last fix is worthwhile.
    trailing_error = ""
    if fixed_rounds:
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
                _maybe_record_clean(
                    cfg=cfg,
                    instance_dir=instance_dir,
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    project_path=project_path,
                    rounds=fixed_rounds,
                )
                return PrivateReviewGateResult(
                    ran=True,
                    clean=True,
                    summary=clean_summary,
                    rounds=max_rounds,
                    fixed_rounds=fixed_rounds,
                )
        else:
            trailing_error = summary

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
        error=trailing_error,
    )


def run_gate_for_skill(
    *,
    project_path: str,
    project_name: str,
    pr_url: str,
    skill_origin: str,
    notify_fn: Optional[Callable[[str], None]] = None,
    review_skill_dir: Optional[Path] = None,
    plan_url: Optional[str] = None,
) -> Optional[PrivateReviewGateResult]:
    """Run the gate for a PR-producing skill, swallowing failures.

    Shared entry point for /fix and /implement: returns ``None`` (gate did not
    run) when there is no PR or the project path is missing, and never lets a
    gate error fail the owning skill — it logs and notifies instead.
    """
    if not pr_url or not Path(project_path).is_dir():
        return None
    try:
        return run_private_review_gate(
            project_path=project_path,
            project_name=project_name,
            pr_url=pr_url,
            notify_fn=notify_fn,
            plan_url=plan_url,
            skill_origin=skill_origin,
            review_skill_dir=review_skill_dir,
        )
    except Exception as exc:
        logger.warning("Private review gate failed after %s: %s", skill_origin, exc)
        if notify_fn:
            notify_fn(
                f"⚠️ Private review gate failed after {skill_origin}: "
                f"{str(exc)[:200]}"
            )
        return None


def format_gate_note(gate_result: Optional[PrivateReviewGateResult]) -> str:
    """One-line summary note for a gate result, or "" when it did not run."""
    if gate_result is None or not getattr(gate_result, "ran", False):
        return ""
    return f"\nPrivate gate: {gate_result.summary}"


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


def _finding_fingerprints(findings: list) -> frozenset:
    """Stable identity for a finding set, ignoring line numbers.

    Keys each finding on ``(file, normalized title, severity)`` so the same
    issue matches across rounds even though line numbers shift after a fix.
    Used by the convergence bail to detect a fix that made no progress
    (round N's findings identical to round N-1's).
    """
    fingerprints = set()
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        fingerprints.add((
            str(finding.get("file", "")),
            str(finding.get("title", "")).strip().lower(),
            str(finding.get("severity", "")),
        ))
    return frozenset(fingerprints)


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


def _diffstat_from_diff(diff: str) -> str:
    """Render a compact per-file diffstat from a unified diff string.

    Parses the already-fetched PR diff (no subprocess, no base-ref lookup)
    into one line per changed file — ``path | +N -M`` — plus a
    ``K file(s) changed`` footer. This replaces embedding the full diff in the
    fix prompt: each finding already carries its own ``code_snippet`` and the
    fixer can read or ``git diff`` any file itself, so only the PR's blast
    radius is needed here.
    """
    if not diff:
        return "(no diff available)"

    files = []
    path = ""
    adds = dels = 0

    def _flush():
        if path:
            files.append((path, adds, dels))

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            _flush()
            # "diff --git a/x b/y" -> prefer the b/ (new) path
            parts = line.split(" b/", 1)
            path = parts[1] if len(parts) == 2 else line[len("diff --git "):]
            adds = dels = 0
        elif line.startswith(("+++", "---")):
            continue
        elif line.startswith("+"):
            adds += 1
        elif line.startswith("-"):
            dels += 1
    _flush()

    if not files:
        return "(no file changes detected)"

    lines = [f"{p} | +{a} -{d}" for p, a, d in files]
    lines.append(f"{len(files)} file(s) changed")
    return "\n".join(lines)


def _build_fix_prompt(context: dict, findings: list, min_severity: str) -> str:
    from app.prompt_guard import fence_external_data
    from app.utils import truncate_text

    return load_prompt(
        "implementation-review-fix",
        TITLE=fence_external_data(context.get("title", ""), "PR title"),
        BODY=fence_external_data(
            truncate_text(context.get("body", ""), 1500), "PR body",
        ),
        BRANCH=context.get("branch", ""),
        BASE=context.get("base", ""),
        DIFFSTAT=fence_external_data(
            _diffstat_from_diff(context.get("diff", "")),
            "changed files",
            scan=False,
        ),
        MIN_SEVERITY=min_severity,
        FINDINGS_JSON=fence_external_data(
            json.dumps(findings, ensure_ascii=False, indent=1),
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


def _resolve_instance_dir() -> Optional[Path]:
    """Return the instance directory if it exists, else None.

    The budget governor and the dedup tracker live under ``KOAN_ROOT/instance``.
    Returning None when it is unavailable lets the gate run as before (no
    gating, no dedup) rather than fail.
    """
    try:
        from app.utils import KOAN_ROOT

        instance = Path(KOAN_ROOT) / "instance"
        return instance if instance.is_dir() else None
    except Exception as exc:
        logger.debug("Private review gate instance dir resolution failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Budget-aware gating
# ---------------------------------------------------------------------------


def _budget_preflight(
    instance_dir: Optional[Path], max_rounds: int,
) -> tuple[int, str]:
    """Scale review/fix rounds to the quota governor's current verdict.

    Returns ``(effective_max_rounds, note)``. ``effective_max_rounds == 0``
    means skip the gate entirely (note explains why). Falls back to the
    configured ``max_rounds`` (no note) when quota is unlimited/disabled, no
    usage data is available, or anything goes wrong — the gate must never crash
    the owning skill.
    """
    if instance_dir is None:
        return max_rounds, ""
    try:
        from app.config import is_unlimited_quota

        if is_unlimited_quota():
            return max_rounds, ""

        from app.usage_tracker import (
            UsageTracker,
            _get_budget_mode,
            _get_budget_thresholds,
        )

        budget_mode = _get_budget_mode()
        if budget_mode == "disabled":
            return max_rounds, ""

        warn_pct, stop_pct = _get_budget_thresholds()
        tracker = UsageTracker(
            instance_dir / "usage.md",
            0,
            budget_mode=budget_mode,
            warn_pct=warn_pct,
            stop_pct=stop_pct,
        )
        mode = tracker.decide_mode()

        # Near-exhaustion skip: a review pass is the gate's minimum incremental
        # load, so estimate time-to-exhaustion at the review multiplier.
        from app.burn_rate import BurnRateSnapshot
        from app.constants import BURN_RATE_DOWNGRADE_THRESHOLD_MIN

        tte = BurnRateSnapshot(instance_dir).time_to_exhaustion(
            tracker.session_pct, mode="review",
        )
        if tte is not None and tte < BURN_RATE_DOWNGRADE_THRESHOLD_MIN:
            return 0, (
                f"budget near exhaustion (~{tte:.0f} min at current burn rate)"
            )

        rounds_by_mode = {
            "wait": 0,
            "review": 1,
            "implement": 2,
            "deep": max_rounds,
        }
        rounds = min(rounds_by_mode.get(mode, max_rounds), max_rounds)
        if rounds <= 0:
            return 0, f"budget too low (governor mode={mode})"
        if rounds < max_rounds:
            return rounds, (
                f"governor mode={mode} — limiting to {rounds} round(s)"
            )
        return rounds, ""
    except Exception as exc:
        logger.warning("Private review gate budget preflight failed: %s", exc)
        return max_rounds, ""


# ---------------------------------------------------------------------------
# Head-SHA dedup tracker (mirrors ci_dispatch.py)
# ---------------------------------------------------------------------------


def _tracker_path(instance_dir: Path) -> Path:
    return Path(instance_dir) / ".private-review-gate-tracker.json"


def _load_tracker(instance_dir: Path) -> dict:
    path = _tracker_path(instance_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_tracker(instance_dir: Path, data: dict) -> None:
    from app.utils import atomic_write_json

    atomic_write_json(_tracker_path(instance_dir), data)


def _prune_tracker(data: dict, max_age_days: int = 30) -> int:
    """Remove tracker entries older than *max_age_days*. Returns count removed."""
    cutoff = time.time() - max_age_days * 86400
    stale = [
        k for k, v in data.items()
        if not (isinstance(v, dict) and v.get("ts", 0) >= cutoff)
    ]
    for k in stale:
        del data[k]
    return len(stale)


def _dedup_key(owner: str, repo: str, pr_number: str, head_sha: str) -> str:
    return f"{owner}/{repo}#{pr_number}:{head_sha}"


def _pr_head_sha(
    owner: str, repo: str, pr_number: str, project_path: str,
) -> str:
    """Return the PR's current remote head SHA, or "" if unavailable."""
    try:
        from app.github import run_gh

        raw = run_gh(
            "pr", "view", str(pr_number),
            "--repo", f"{owner}/{repo}",
            "--json", "headRefOid",
            "--jq", ".headRefOid",
            cwd=project_path,
            timeout=15,
        )
        return raw.strip()
    except Exception as exc:
        logger.debug("Private review gate head-SHA fetch failed: %s", exc)
        return ""


def _dedup_precheck(
    instance_dir: Optional[Path],
    owner: str,
    repo: str,
    pr_number: str,
    project_path: str,
    cfg: dict,
) -> str:
    """Return a skip reason when this PR head was already reviewed clean.

    No-ops (returns "") when dedup state is unavailable or the tracker is
    empty — the head-SHA fetch is only paid for when there is something to
    dedup against.
    """
    if instance_dir is None:
        return ""
    tracker = _load_tracker(instance_dir)
    if not tracker:
        return ""
    head_sha = _pr_head_sha(owner, repo, pr_number, project_path)
    if not head_sha:
        return ""
    entry = tracker.get(_dedup_key(owner, repo, pr_number, head_sha))
    if isinstance(entry, dict) and entry.get("clean"):
        return f"already reviewed head {head_sha[:8]} (clean)"
    return ""


def _maybe_record_clean(
    *,
    cfg: dict,
    instance_dir: Optional[Path],
    owner: str,
    repo: str,
    pr_number: str,
    project_path: str,
    rounds: int,
) -> None:
    """Record the current PR head as reviewed-clean for future dedup."""
    if not cfg.get("dedup", True) or instance_dir is None:
        return
    head_sha = _pr_head_sha(owner, repo, pr_number, project_path)
    if not head_sha:
        return
    try:
        tracker = _load_tracker(instance_dir)
        _prune_tracker(tracker, cfg.get("tracker_max_age_days", 30))
        tracker[_dedup_key(owner, repo, pr_number, head_sha)] = {
            "clean": True,
            "rounds": rounds,
            "ts": time.time(),
        }
        _save_tracker(instance_dir, tracker)
    except Exception as exc:
        logger.debug("Private review gate dedup record failed: %s", exc)


def _skipped(reason: str) -> PrivateReviewGateResult:
    logger.info("Private review gate skipped: %s", reason)
    return PrivateReviewGateResult(
        ran=False,
        clean=True,
        summary=f"Private review gate skipped: {reason}.",
        skipped_reason=reason,
    )
