# Feature Specification: Chat Priority Lane

**Feature Branch**: `koan.atoomic/chat-lane-1084`

**Created**: 2026-07-17

**Status**: Draft

**Input**: GitHub issue [#1084](https://github.com/Anantys-oss/koan/issues/1084) — "Telegram chat handler should live in its own process, able to invoke Claude even while a mission runs." Fresh, cleaner alternative to PR [#1088](https://github.com/Anantys-oss/koan/pull/1088).

## Problem

When a mission is executing and the human sends a chat message via Telegram, they
frequently receive **"⚠️ I didn't get a response — please try again"** instead of a
real answer.

Verified root cause in the current code:

- Three callers invoke the underlying AI CLI **concurrently against the same account**:
  the mission runner (agent loop), the chat handler (bridge), and the outbox message
  formatter (bridge). The default provider takes **no cross-invocation lock**, so these
  calls genuinely overlap.
- Concurrent sessions on one account hit rate/concurrency limits. The chat call then
  returns an **empty response** (exit 0, empty stdout) or **times out**.
- The chat handler's empty-response branch **gives up immediately** and emits the
  apology above. It only retries on a *timeout*, never on the far more common empty
  response — so the user-visible symptom fires on the first contention hit.

## Prior art to avoid (PR #1088)

PR #1088 extracted chat into a **third OS process** with a JSONL inbox/outbox file
protocol and its own PID management. That approach is rejected here because it:

1. **Contradicts the documented architecture.** `docs/architecture/daemon.md` states the
   chat-vs-background split is deliberately realized with **threads inside the existing
   bridge process** — *"No extra OS process is forked."*
2. **Does not address the real contention.** A second process still invokes the CLI
   against the same account; account-level concurrency limits are unchanged.
3. **Adds complexity and review debt** — stale personality context, duplicated
   mission-active checks, and a queue-depth-of-one regression were all flagged in review.

This feature is a **fresh, cleaner** implementation that stays inside the existing bridge
process and its outbox manager.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Chat stays responsive during a mission (Priority: P1)

The human is chatting with the agent while a long mission runs in the background. Every
chat message should get a genuine reply, not the "I didn't get a response" apology,
unless the AI service is genuinely, totally unavailable.

**Why this priority**: This is the reported bug (#1084) and the core value of the
feature. Without it the feature delivers nothing.

**Independent Test**: With a mission marked as actively executing, drive the chat handler
so its first CLI call returns an empty response and its retry returns real text; assert
the user receives the real reply and never the apology.

**Acceptance Scenarios**:

1. **Given** a mission is actively executing, **When** the human sends a chat message and
   the first AI call returns an empty response, **Then** the handler retries (with backoff
   and lighter context) and delivers the retried reply — the apology is not sent.
2. **Given** a mission is actively executing, **When** the human sends a chat message and
   the first AI call times out, **Then** the handler retries and delivers the retried
   reply.
3. **Given** every attempt (initial + retries) returns empty or times out, **When** the
   attempts are exhausted, **Then** exactly one degraded message is shown to the human,
   and the exchange is recorded in conversation history.

---

### User Story 2 - Notifications stop stealing chat's AI headroom during missions (Priority: P2)

While a mission runs, agent→human notifications (outbox) should still arrive, but they
must not compete with chat for the AI account. During a mission they are delivered with
the instant local formatter; polished AI formatting resumes once no mission is executing.

**Why this priority**: Removing the lowest-value concurrent caller materially reduces the
contention that causes Story 1's symptom. It is a strong contributor but Story 1's retry
is the guaranteed fix, so this is P2.

**Independent Test**: With a mission marked as actively executing, flush an outbox message
and assert the local fallback formatter is used (no AI formatting call); with no mission
executing, assert the AI formatter is used.

**Acceptance Scenarios**:

1. **Given** a mission is actively executing, **When** the outbox is flushed, **Then** the
   message is formatted by the local fallback formatter and delivered, with no AI CLI call
   for formatting.
2. **Given** no mission is executing, **When** the outbox is flushed, **Then** the message
   is formatted by the AI formatter as before.
3. **Given** a mission finishes between two outbox flushes, **When** the second flush runs,
   **Then** it uses AI formatting again (behavior is not sticky).

---

### User Story 3 - One authoritative "mission is executing" signal (Priority: P3)

Any bridge-side code that needs to know whether a mission is actively executing reads it
from a single, authoritative helper backed by the existing provider-liveness signal — not
from fragile parsing of a human-readable status string, and not duplicated per call site.

**Why this priority**: This is the internal quality guarantee that keeps Stories 1–2
correct and prevents the duplication/fragility that burdened PR #1088. It has no direct
user-visible behavior of its own, so it is P3.

**Independent Test**: Point the helper at a state directory with an active provider-liveness
signal and assert it reports "executing"; clear the signal and assert it reports "not
executing"; corrupt/absent signal degrades to "not executing" without raising.

**Acceptance Scenarios**:

1. **Given** a live provider subprocess is recorded (working/stalled), **When** the helper
   is queried, **Then** it returns true.
2. **Given** no provider subprocess is recorded (idle) or the signal is
   absent/unreadable, **When** the helper is queried, **Then** it returns false and does
   not raise.

---

### Edge Cases

- **Total AI outage**: every attempt fails. The human sees a single degraded message (not
  one per attempt), and it is saved to conversation history exactly once.
- **Mission ends mid-chat-retry**: the retry still completes and delivers a reply; no
  special handling needed because the retry does not depend on mission state.
- **Signal file is stale/corrupt/absent**: the mission-active helper degrades to "not
  executing" (fail-open toward normal AI formatting) rather than raising, so a corrupt
  signal never breaks the outbox flush.
- **Rapid successive chat messages**: existing chat-lane back-pressure is unchanged — a
  second message while one is in flight still gets the existing "busy" behavior; this
  feature does not alter lane concurrency.
- **Zombie/stalled provider**: a recorded-but-dead provider PID must not be treated as an
  executing mission (would needlessly degrade outbox formatting forever); "executing"
  means a live subprocess or a live parallel session.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The chat handler MUST treat an empty AI response (successful exit, empty
  output) as a transient/retryable outcome — the same class as a timeout — rather than an
  immediate failure.
- **FR-002**: On a retryable chat outcome, the handler MUST retry the AI call with a
  backoff delay and a lighter/shorter context, before showing any message to the human.
- **FR-003**: The handler MUST attempt the AI call up to a small bounded number of times
  (initial attempt plus a fixed number of retries) and only show a degraded message after
  all attempts fail.
- **FR-004**: When attempts are exhausted, the handler MUST show exactly one degraded
  message and record exactly one assistant entry in conversation history.
- **FR-005**: On any successful (non-empty) response — initial or retried — the handler
  MUST deliver that reply and record it in conversation history, and MUST NOT show a
  degraded message.
- **FR-006**: While a mission is actively executing, the outbox flush MUST format messages
  with the local fallback formatter and MUST NOT invoke the AI CLI for formatting.
- **FR-007**: While no mission is executing, the outbox flush MUST format messages with the
  AI formatter (existing behavior).
- **FR-008**: Mission-aware outbox formatting MUST NOT be sticky — each flush re-evaluates
  the current mission state.
- **FR-009**: The system MUST expose a single authoritative helper answering "is a mission
  actively executing?", derived from the existing provider-liveness signal, and both the
  outbox path and any other caller MUST use it rather than re-deriving the answer.
- **FR-010**: The mission-active helper MUST return "not executing" (without raising) when
  the signal is absent, unreadable, or names a dead/zombie provider.
- **FR-011**: The feature MUST NOT introduce a new OS process, PID file, inter-process
  file protocol, or process-management command; all behavior stays within the existing
  bridge process and outbox manager.
- **FR-012**: Message delivery, conversation-history persistence, prompt-injection
  scanning, and outbox crash-safety (staging/recovery) MUST remain unchanged in behavior.

### Key Entities

- **Chat request**: the human's inbound message handled on the bridge's chat lane, which
  triggers one-or-more AI attempts and at most one outbound reply.
- **Outbox message**: a queued agent→human notification, formatted (AI or fallback) then
  delivered; its crash-safety staging is unchanged.
- **Mission-active signal**: the existing provider-liveness record indicating whether a
  live AI subprocess (or parallel session) is executing; the sole input to the
  mission-active helper.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: While a mission is executing, a chat message whose first AI attempt returns
  empty still results in a real reply reaching the human (the "I didn't get a response"
  apology does not appear) in 100% of cases where at least one attempt succeeds.
- **SC-002**: The apology/degraded message appears only when **all** bounded attempts fail
  (genuine outage), not on the first contention hit.
- **SC-003**: During a mission, outbox notifications are still delivered, formatted by the
  local fallback formatter, with zero AI CLI calls made for outbox formatting.
- **SC-004**: The running system continues to operate with exactly two managed processes
  (bridge + agent loop); no third process is introduced.
- **SC-005**: The existing test suite passes, and new unit tests cover: the empty-response
  retry path, the mission-aware outbox formatting skip (both branches), and the
  mission-active helper (executing / not-executing / corrupt-signal).

## Assumptions

- The existing provider-liveness signal (`.koan-active` / `get_execution_state`, issue
  #2086) is the correct and available source of truth for "a mission is executing"; it
  already distinguishes working/stalled from idle/zombie.
- The local fallback formatter produces acceptable (if less polished) notification text;
  mission-completion notifications — the most important ones — arrive *after* the mission,
  when AI formatting is available again.
- Retrying an empty/throttled chat call after a short backoff is likely to succeed because
  contention is transient; a lighter context further reduces the chance of a repeat empty
  response.
- The chat lane's existing single-in-flight back-pressure is adequate; this feature does
  not change lane concurrency.
