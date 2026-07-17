# Feature Specification: Dedicated Chat Process

**Feature Branch**: `koan.atoomic/chat-process-1084`

**Created**: 2026-07-17

**Status**: Draft

**Input**: User description: "Dedicated chat channel/process to prevent Claude API contention during missions (issue #1084)."

## Overview

When Kōan is executing a mission, the assistant's Telegram chat can go silent: the
human sends a message and gets back "⚠️ I didn't get a response — please try again."
The cause is contention for the single Claude quota. While a mission runs, up to three
callers invoke the CLI at once — the mission itself, the chat handler, and the cosmetic
formatting of outbox notifications. The chat call, being the smallest and most
time-sensitive, is the one that loses: it returns empty or times out.

This feature makes the human ↔ agent chat channel resilient by giving it its own
autonomous execution path that keeps answering even under heavy mission load, and by
removing the lowest-value competing caller (outbox formatting) while a mission is active.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Chat stays responsive while a mission runs (Priority: P1)

The human is chatting with Kōan on Telegram while the agent is busy executing a
long-running mission. Every message the human sends receives a real, personality-shaped
reply, in order, without the "I didn't get a response" failure — the mission continues
uninterrupted in the background.

**Why this priority**: This is the entire point of issue #1084 — the observable bug is
"chat dies during missions." Delivering this story alone resolves the reported problem
and is a viable MVP.

**Independent Test**: Start a mission, then send several chat messages from Telegram.
Verify each gets a coherent reply and the failure message never appears, while the
mission runs to completion.

**Acceptance Scenarios**:

1. **Given** a mission is actively running, **When** the human sends a chat message,
   **Then** the human receives a normal Kōan reply and the mission is unaffected.
2. **Given** a mission is running, **When** the human sends three chat messages in
   quick succession, **Then** all three are answered in the order they were sent
   (none are dropped or rejected as "busy").
3. **Given** no mission is running, **When** the human chats, **Then** chat behaves
   exactly as before (same personality, tools, history, and typing indicator).

---

### User Story 2 - Personality edits take effect without a restart (Priority: P2)

An operator edits the assistant's personality (`soul.md`) or the memory summary while
the system is live. The next chat reply reflects the edit, with no need to restart any
process.

**Why this priority**: A dedicated long-lived chat path could easily cache personality
context and serve stale replies indefinitely (the primary defect found in the earlier
attempt, PR #1088). Getting this right is required for the dedicated path to be a true
drop-in for the inline handler, but it is a correctness refinement on top of P1.

**Independent Test**: Send a chat message, edit `soul.md`, send another chat message,
and confirm the second reply reflects the edited personality without any restart.

**Acceptance Scenarios**:

1. **Given** the chat path is serving replies, **When** `soul.md` is edited, **Then**
   the next reply uses the updated personality.
2. **Given** the chat path is serving replies, **When** the memory summary changes,
   **Then** subsequent replies reflect the new summary.

---

### User Story 3 - Chat path degrades gracefully and is operationally visible (Priority: P3)

If the dedicated chat path is not available (not started, crashed, or disabled), chat
still works via the existing inline behavior — the human notices no difference. Operators
can see, start, stop, and read logs for the dedicated path with the same commands they
use for the other long-running components.

**Why this priority**: Resilience and operability harden the feature for production but
are not required to demonstrate the core fix.

**Independent Test**: With the dedicated path stopped, send a chat message and confirm a
normal reply. Then confirm the standard start/stop/status/logs commands account for the
dedicated path.

**Acceptance Scenarios**:

1. **Given** the dedicated chat path is not running, **When** the human chats, **Then**
   the message is still answered through the inline fallback.
2. **Given** the dedicated chat path dies after a message is accepted but before it is
   answered, **When** the human's message would otherwise be lost, **Then** the system
   detects the loss of the path and answers via the inline fallback instead.
3. **Given** an operator runs the standard status/logs commands, **When** the dedicated
   chat path is running, **Then** it appears alongside the other components.

---

### Edge Cases

- **Malformed queued message**: If the chat inbox accumulates an unparseable entry
  (e.g. a partial write from a crash), it must not wedge the queue or be re-processed
  forever — it is discarded on the next read.
- **Rapid bursts**: Multiple messages arriving faster than they can be answered are
  queued and answered in arrival order (FIFO), never rejected with a hard "busy" bounce.
- **Empty / timed-out model call**: A blank or timed-out reply triggers the same
  lite-context retry the inline handler uses; only after retry exhaustion does the human
  see a soft failure message.
- **Mission ends mid-conversation**: Once the mission finishes, outbox notifications
  resume their normal polished formatting; in-flight chat is unaffected.
- **Both paths momentarily present**: A message must never be answered twice — exactly
  one path (dedicated or inline fallback) handles any given message.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Chat messages MUST be answerable by an execution path that is independent
  of the mission runner, so a running mission cannot starve chat of a reply.
- **FR-002**: When the dedicated chat path is available, incoming chat messages MUST be
  handed to it; when it is not available, the system MUST fall back to the existing
  inline handling so chat always works.
- **FR-003**: The dedicated chat path MUST have full behavioral parity with the inline
  handler: prompt-injection scanning of the incoming message, saving both the human's
  message and the assistant's reply to conversation history, showing a typing indicator,
  and the same lite-context retry behavior (identical retry semantics and tool/scope
  constraints) on empty or timed-out replies.
- **FR-004**: Personality and memory context (soul, summary) used for chat replies MUST
  be read fresh so that edits are reflected without restarting the chat path.
- **FR-005**: Queued chat messages MUST be processed in arrival order (FIFO); the system
  MUST NOT reject a new message solely because a previous one is still being answered.
- **FR-006**: While a mission is active, outbox notification formatting MUST skip the
  Claude-powered formatting step and use the instant local fallback formatter, to remove
  that competing caller; once no mission is active, polished formatting resumes.
- **FR-007**: The check for "is a mission currently active" and the identifier for the
  chat message queue MUST each be defined in exactly one place and reused everywhere
  (no duplicated definitions that can drift).
- **FR-008**: The dedicated chat path MUST be managed like the other long-running
  components: a single exclusive instance, and coverage by the standard
  start / stop / status / logs / make-target tooling.
- **FR-009**: A malformed or unparseable queued message MUST NOT be reprocessed
  indefinitely and MUST NOT block processing of subsequent valid messages.
- **FR-010**: Any given chat message MUST be answered at most once across the dedicated
  and fallback paths.
- **FR-011**: The dedicated chat path MUST shut down cleanly on a termination signal,
  finishing any in-progress reply before exiting.

### Key Entities

- **Chat message queue**: An ordered, append-only handoff of pending human chat messages
  from the bridge to the dedicated chat path, drained in FIFO order and cleared as it is
  read.
- **Chat reply cycle**: The single shared unit of work — scan, record, build context,
  invoke model with retry, deliver, record reply — reused identically by the dedicated
  path and the inline fallback so their behavior cannot diverge.
- **Mission-active signal**: The single authoritative indicator of whether a mission is
  currently executing, consulted by both the chat path and outbox formatting.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With a mission running, 100% of chat messages sent over a normal session
  receive a substantive reply (0 occurrences of the "I didn't get a response" failure
  attributable to contention, excluding genuine quota exhaustion or provider outage).
- **SC-002**: A personality/summary edit is reflected in the very next chat reply with
  no restart of any component.
- **SC-003**: A burst of N chat messages during a mission yields N replies delivered in
  arrival order, with none rejected as "busy."
- **SC-004**: When the dedicated chat path is unavailable, chat replies are still
  delivered with no user-visible change in behavior.
- **SC-005**: Behavior for the inline (no-mission) chat path is unchanged from today —
  same personality, tools, history, retry, and typing indicator.

## Assumptions

- The reported failure is caused by concurrent Claude CLI callers competing for one
  quota, not by a network or Telegram-API fault; removing/relocating competing callers
  addresses the reported symptom.
- File-based handoff between components (the established inter-process mechanism in this
  system) is an acceptable transport for chat messages; sub-second added latency is
  negligible next to the multi-second model call.
- Chat is not project-scoped; a single working context for chat replies (as today) is
  acceptable, and multi-project routing of chat is out of scope.
- The dedicated chat path is an internal architectural component; end users interact only
  through Telegram and are not expected to manage it directly beyond the standard
  operator tooling.
- Slash-commands and mission classification remain handled by the bridge as today; only
  free-form chat is relocated to the dedicated path.
