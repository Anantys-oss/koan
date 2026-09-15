---
type: doc
title: "Bounding a stalled provider: inactivity, not wall-clock"
description: "Why run_command_streaming's timeout never reached its read loop, why the replacement bound is on inactivity rather than duration, and why session isolation and the group SIGKILL are scoped to the armed watchdog."
tags: [design, providers, decision]
created: 2026-09-10
updated: 2026-09-15
---

# Bounding a stalled provider: inactivity, not wall-clock

The durable contract lives in `specs/components/providers.md` (§ Invariants,
"The streaming read loop must be inactivity-bounded"). This page records the
*why*.

## The symptom

On 2026-09-10 a `/review` mission produced this and nothing else:

```
[18:51:18] [review] Reviewing PR #NNN ...
[cli] Starting claude CLI session
[cli] session init (model=…)
[19:01:29] [error] No output for 600s — killing stuck process (elapsed: 619s)
[19:01:29] [error] Skill runner timed out (liveness: 600s)
```

The provider opened its stdout pipe, printed a session banner, and went silent
for ten minutes. The mission was reported as a bare "Skill runner timed out":
no verdict, no partial result, no indication of *which* pass stalled.

## Why the existing timeout did nothing

`run_command_streaming` takes a `timeout` argument, and it looks like it covers
the run. It does not. The function reads the child with a blocking

```python
for line in proc.stdout:
    ...
proc.wait(timeout=timeout)
```

`timeout` is applied only by that `proc.wait()`, which is reached **after
stdout EOF**. A provider that holds the pipe open and emits nothing never ends
the loop, so the wait — and its timeout — is never reached. The declared bound
was unreachable in exactly the scenario it appears to exist for.

The only thing that ever ended such a run was run.py's *outer* skill-runner
liveness watchdog (`first_output_timeout`, default 600s). That watchdog
SIGKILLs the whole runner, which is a blunt instrument: it kills the pipeline
mid-flight, discards work already completed by earlier passes, and attributes
nothing.

`specs/skills/review.md` already *claimed* the guarantee — "Pairs with the
provider-side per-pass stall watchdog … which makes a stalled enrichment pass
degrade to empty rather than hang" — and cited a `specs/components/providers.md`
section that did not exist. The contract was written; the code never
implemented it. This change makes reality match the declaration.

## Why inactivity and not duration

The obvious fix — actually enforce `timeout` as a wall-clock cap around the
read loop — is wrong, and would have caused a worse regression than the bug.

A healthy review pass streams progress events for many minutes: observed runs
of 5–6 minutes are routine and longer ones are legitimate on large PRs. A hard
600s cap would start killing productive work. What distinguishes a stall from a
long job is not elapsed time, it is **silence**. So the bound is on inactivity:
every consumed stdout line heartbeats a `LivenessWatchdog`, and only a gap
longer than `idle_timeout` ends the pass.

`idle_timeout` defaults to `None`, so every caller that has not opted in keeps
its exact previous behaviour.

## Why the inner bound is derived, not configured

An inner bound is only useful strictly below the outer one. At or above the
outer budget the outer watchdog fires first, SIGKILLs the runner, and the inner
bound never gets to report anything — it becomes decorative while looking
configured. Rather than add a knob an operator can silently set into
uselessness, `review_runner._review_stall_timeout()` derives the value:

- `outer - 60`, when that leaves at least 60s;
- `0` (no inner bound) when the operator disabled the outer watchdog, or when
  the margin would leave less than the 60s floor below which a brief
  legitimate pause reads as a stall.

"Outer" is not a single knob. run.py arms its skill-runner watchdog from
`rebase_first_output_timeout` for a `/rebase` mission and from
`first_output_timeout` for everything else, and a review pass runs under both:
`/review` directly, and inside `/rebase` via the private review gate
(`rebase_pr` → `private_review_gate` → `review_runner`). The first version read
`first_output_timeout` unconditionally, and review caught it. With
`rebase_first_output_timeout: 1800` — a knob whose only purpose is to *widen*
that budget — the inner bound would still have been `600 - 60 = 540`, so a
synthesis turn silent for 600s (well inside the configured allowance, and
exactly the long-single-turn case the flat margin exists to protect) would be
killed with 1260s unused, and the gate would lose the verdict the rebase was
configured to gate on. `_outer_skill_runner_budget()` therefore mirrors run.py's
own selection, keyed off `KOAN_MISSION_COMMAND` — an env var run.py already
exports into the skill runner after canonicalising aliases (`/rb`,
`/core.rebase`) through `mission_command_name`.

The margin is a flat 60s rather than half the budget, and that distinction was
caught in review. On this path the inner and outer clocks are the **same
clock**: run.py's watchdog resets on the per-event `print()` inside
`run_command_streaming`'s read loop, which is exactly what heartbeats the
inner watchdog. So `outer // 2` would not have added a bound where none
existed — it would have *halved* the silence a review pass has always been
allowed (600s → 300s). Kōan never passes `--include-partial-messages`, so
plain `stream-json` emits one line per complete message, and a single long
turn — notably the final synthesis turn on a large PR — is silent for its
whole duration. A 350s synthesis turn would have degraded to an empty verdict
where it previously finished: the exact outcome this change set out to
eliminate, reachable at half the previous threshold. All the margin has to buy
is room for the pass to fail and be reported, and `review_runner` prints
immediately afterwards, which resets the outer watchdog — so seconds suffice.

The postcondition is exact and directly tested: the result is either `0`, or a
value strictly below `first_output_timeout`.

## Why session isolation is scoped to the armed watchdog

A fired watchdog group-kills, and `run_command_streaming` previously spawned
the child **without** `start_new_session=True` — the child shared Kōan's
process group, so the kill would have sent `SIGKILL` to the daemon itself. An
armed watchdog therefore *requires* session isolation.

The first version of this change applied it unconditionally, on the reasoning
that a future caller opting in should not have to remember it. That was wrong,
and PR review caught it. Isolation is not free in the other direction:
`run.py`'s skill-runner teardown and `mission_scope`'s fallback path both reap
by **process group**, and a child in its own session is outside both. Applying
it to every call would have put each non-opted-in caller's provider beyond that
teardown while buying nothing — there is no watchdog there to protect — so a
stuck provider could outlive a skill timeout, an abort, or the outer liveness
kill, and keep burning quota after its parent was gone.

So `start_new_session=True` is passed only when `idle_timeout` is set: exactly
where a group kill can happen, and nowhere else.

**Residual, accepted.** An opted-in child *is* outside the outer group
teardown, so an abort or `skill_timeout` firing while the provider is actively
streaming leaves it running. Its own idle watchdog does not save it there: that
watchdog is a `threading.Timer` living inside the skill-runner process, and the
teardown being discussed kills that process, so the timer dies with it. What
actually bounds such a child is narrower — an actively streaming provider takes
`EPIPE`/`SIGPIPE` on its next write once the read end closes, and on a systemd
host the mission cgroup still contains it (session isolation does not escape a
cgroup). Neither applies to a manual abort during a *silent* window on a
non-systemd host (macOS, where `mission_scope` degrades to
`start_new_session=True` plus a process-group kill), where the child survives
unbounded. That is the case the follow-up has to close, by teaching the outer
teardown to track isolated provider sessions — worth doing, but a larger change
than this fix.

Inside `run_command_streaming` the same asymmetry forces one kill that is *not*
optional. An exception raised mid-loop — a `BrokenPipeError` on Kōan's own
stdout, a malformed event — reaches the `finally`, which disarms the watchdog
and calls `popen_cli`'s `cleanup()`. That cleanup closes stdin, deletes the
prompt file and releases the provider invocation lock; it never kills. So for an
opted-in (session-isolated) child, disarming first left nothing that could reach
it: it outlived the call, kept burning quota, and the next mission acquired the
lock just released — two providers running concurrently against the
serialization that lock exists to enforce. Review caught this. The `finally` now
force-kills the group before disarming whenever the bound was armed.

That kill is deliberately *not* gated on the leader still running, and review
caught the first version for gating it on `proc.poll() is None`. The leader
exiting says nothing about its descendants: a helper the CLI left behind in the
isolated session is reachable from nowhere — not from `run.py`'s skill-runner
teardown, not from `mission_scope`'s fallback — so a pass that finished
*normally* was exactly the case that leaked one. Removing the gate only works
alongside killing by **pgid** rather than via the leader: once `proc.wait()` has
reaped the leader its pid is gone, so `force_kill_process_group`'s
`getpgid(proc.pid)` would fail and degrade to a single-process kill on a dead
process — a no-op dressed as cleanup. The pgid itself stays valid, because POSIX
forbids reusing a process-group ID while the group still has members; the signal
either reaches the survivors or fails with `ESRCH` on an empty group. All of
this is safe only because an armed bound implies `start_new_session=True`, so
the pgid is a dedicated session and never Kōan's own group.

## Why the kill is SIGKILL-to-the-group, not SIGTERM-first

`LivenessWatchdog` defaulted to `kill_process_group`, which SIGTERMs the group
and escalates to SIGKILL only if the **leader** is still alive after the grace
period. A descendant that handles or ignores SIGTERM survives that — and if it
inherited the stdout write end, the reader never sees EOF. The read loop stays
blocked, never reaches the `fired` check, and never reports the stall: the hang
the watchdog was armed to end, re-entered through the kill path itself.

`LivenessWatchdog` therefore gained a `graceful` flag mirroring the one
`ProcessWatchdog` already had. The class default is unchanged, but both
existing call sites now pass `graceful=False`: this one, and
`cli_exec.stream_with_timeout` (the `/rebase` review and CI phases), which
reads an inherited pipe in exactly the same shape. Leaving that one graceful
would have made `rebase_review_idle_timeout` silently ineffective against a
SIGTERM-surviving descendant, with the resulting hang misattributed to the
much looser `rebase_review_max_duration` — the invariant documented here,
violated by a caller in the same file.

The regression test spawns a child that forks a SIGTERM-ignoring grandchild
holding the same stdout, then goes silent. Under the graceful kill it blocks
for the full 30s; under the group SIGKILL it returns in about two.

**run.py's outer watchdog is a deliberate exception.** It feeds a pipe read
loop too (`_pump_skill_stdout`) and keeps the graceful default, so the same
survivor hang is reachable there. Its group is the whole skill runner plus the
build tooling `/review`, `/fix` and `/implement` start, and SIGTERM-first is
what lets that tooling release git index locks and containers before dying —
with the `TimeoutExpired` path escalating afterwards. Making it safe is not a
matter of flipping the flag: it means bounding that read loop independently of
the kill mode, which belongs to the agent loop's teardown, not to this fix.

## Why a stall after the result envelope is not a stall

The read loop drains to EOF rather than breaking on the terminal `result`
event, so a CLI that emits its verdict and then goes quiet tearing down (MCP
servers, telemetry) trips the same watchdog. Raising there would turn a
finished review into a 200-character error suffix — the outcome this change
exists to eliminate, reached from the other end. So the stall is reported only
when no terminal envelope arrived; otherwise the verdict is returned and the
resulting `exit -9` is recognised as the watchdog's own kill rather than a
provider crash. A genuine stall persists the usage snapshot first (it is the
run that burned the most quota) and carries the drained stderr, which is
usually the only statement of *why* the provider went silent — and says so
explicitly when that drain itself fails, rather than degrading to a bare
timeout message.

The drain is skipped outright when the post-kill `wait()` expires, which review
flagged and the first version suppressed silently. An expired wait means the
group SIGKILL did not reap everything: something escaped the group, most
plausibly a `setsid`'d MCP helper. Such a survivor can hold the stderr write
end, and `read()` returns only at EOF — so draining there blocks forever with
the watchdog already disarmed, which is this change's own bug re-entered one
pipe over. The expired wait is reported in its place, on the returning branch as
well as the raising one: that branch hands back stderr as plain text, so a drain
that never happened would otherwise look exactly like a clean empty one.

"A terminal envelope arrived" is decided on the **event shape**
(`_is_terminal_result_event`), not on whether a result *string* came out of it.
The first version keyed off `final_result`, and review caught that this is not
the same question: `_extract_result_text` deliberately returns `None` for Grok
Build's `end` event (the text arrives as deltas) and for any `result` envelope
whose text fields are empty. On those providers a pass that completed normally
and then hung in teardown would have taken the stall branch and been thrown
away down to a 200-character suffix — the exact demotion this section exists to
prevent, re-entered through a different provider's envelope shape. A provider
without stream-json emits no envelope at all and still reports the stall: for
it there is no way to distinguish a completed run from a truncated one, and
reporting a truncated pass as a finished verdict is the worse failure.

That shape test is an **explicit whitelist**, not a `.completed` / `.done`
suffix match, and review caught the second version for reusing the suffix rule.
The looser rule is fine where it has always lived — deciding whether an event
*might carry* the final text, where a false positive just overwrites a string
the next write replaces. As the terminality latch it is not, because the latch
is one-way: the first match disarms the bound for the rest of the run. Two real
mid-stream types match those suffixes. Codex emits `item.completed` per stream
item — after its very first tool call, with `turn.completed` as the actual
terminal envelope — and `response.output_text.done` is already classified in
this module as an assistant *text* event. Either would have let a Codex-shaped
review go silent for the full idle window and come back a *successful*
truncated verdict, with the usage snapshot skipped too: a quieter failure than
the hang the bound replaced, and the precise "truncated pass reported as a
finished verdict" outcome the paragraph above rules out. So `_is_result_like_event`
keeps the suffix rule for text extraction and `_is_terminal_result_event`
matches the whitelist only; a provider whose stream closes on a new type gets
that type added to the set.

Recognising the kill must not swallow the rest of the exit handling either.
Returning early on `stalled_after_result` skipped the `failed_result_status`
raise below it, so a session that reported a failed terminal status (a headless
permission denial) and *then* hung in teardown came back as a successful
partial result. The flag now only suppresses the `_format_cli_error` block —
`failed_result_status` and the max-turns warning still govern.

## Why the watchdog is disarmed the moment stdout ends

`proc.stderr.read()` and `proc.wait()` run after stdout EOF and emit no
heartbeats. A watchdog left armed across them would SIGKILL a run that had
already streamed everything, and — since the `fired` check has already been
evaluated by then — the kill would surface as an opaque `exit -9` rather than
a stall: a successful review reported as a provider crash. So the watchdog is
disarmed at loop exit, before the stderr drain, which is what
`cli_exec.stream_with_timeout` already did.

`cancel()` is not sufficient on its own. `threading.Timer.cancel()` is a no-op
once `_fire` has begun running, and the `graceful=False` path deliberately has
no `poll()` guard — so a late fire would reach `os.killpg` on a possibly
recycled PID. `LivenessWatchdog` therefore also gained `ProcessWatchdog`'s
`mark_completed()` flag, checked under the lock at the top of `_fire()`, and
both call sites set it alongside `cancel()`.

## Related

- Contract: `specs/components/providers.md`
- Review-side pairing: `specs/skills/review.md`, `docs/design/review-consistency.md`
- Outer watchdog knob: `first_output_timeout` in `docs/users/user-manual.md`
