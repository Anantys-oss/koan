---
name: review
scope: core
group: code
emoji: 🔍
description: "Queue a code review mission (ex: /review https://github.com/owner/repo/pull/42)"
version: 1.0.0
audience: hybrid
caveman: false
github_enabled: true
github_context_aware: true
commands:
  - name: review
    description: "Queue a code review for one or more PRs/issues. Use --now to queue at the top. Flags: --architecture (SOLID/layering focus), --errors (silent-failure-hunter pass), --comments (comment quality), --plan-url <issue-url> (plan alignment check), --force (review even if closed/merged)"
    usage: "/review [--now] <github-pr-or-issue-url> [additional-pr-or-issue-url ...] [context] [--architecture] [--errors] [--comments] [--plan-url <issue-url>] [--force] OR /review <github-repo-url> [--limit=N]"
    aliases: [rv, rereview, re_review]
handler: handler.py
---

## Large diffs & partial-coverage reporting

Review diffs are packed to fit a token budget by the diff compressor
(`optimizations.review_compressor.token_budget`, default 80,000 tokens — the
single knob controlling review diff size). The fetch-time character cap is
*derived* from that budget (budget × 3.5 chars/token × 4 headroom), so on
large-context models the compressor — not a blind character cut — decides
coverage. When the compressor is disabled, a token-safe backstop (budget × 3.5,
no headroom) truncates the diff so the size guard holds in every config.

Whenever any file is omitted (fetch-time backstop, compressor packing, or
trivial-file triage), the posted review opens with a `⚠️ Partial review` block
listing every omitted file, so partial coverage is never silent.
