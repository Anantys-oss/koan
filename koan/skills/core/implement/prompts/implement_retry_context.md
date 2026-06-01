# Escalated Retry — Bias Toward Practical Progress

The previous implementation attempt did not produce a viable committed result.

You must make a second implementation pass with a strong bias toward practical delivery.

---

## Core Objective

Deliver the simplest correct implementation that satisfies the plan while remaining consistent with existing codebase patterns.

Progress is expected, but correctness and sound judgment take priority over forcing speculative changes.

---

## Execution Rules

### 1. Prepare the Working Branch

- Create the feature branch immediately if it does not already exist.

### 2. Prefer Simplicity

Choose the simplest implementation that:

- satisfies the plan,
- follows established project conventions,
- minimizes unnecessary complexity,
- avoids introducing new abstractions unless clearly required.

Do not over-engineer.

### 3. Resolve Ambiguity Pragmatically

When requirements are underspecified:

- infer intent from surrounding implementation patterns,
- follow local codebase conventions,
- choose the most conservative reasonable interpretation.

Minor ambiguity is not a blocker.

---

## Blocker Resolution Framework

When implementation friction appears, do not stop immediately.

### Step 1 — Validate the Blocker

Confirm whether the issue is real.

### Step 2 — Simplify

If the original approach is blocked:

- reduce implementation scope,
- remove unnecessary moving parts,
- simplify dependencies,
- prefer incremental adaptation over redesign.

### Step 3 — Find the Nearest Viable Alternative

If the planned approach cannot be completed directly:

- select the closest safe implementation,
- preserve behavioral intent,
- minimize architectural deviation.

---

## Escalation Threshold

Escalate only if implementation would require:

- violating core architecture,
- introducing unsupported assumptions that risk correctness,
- creating behavior inconsistent with established system patterns,
- making changes that cannot be reasonably validated.

Do **not** escalate for:

- minor uncertainty,
- local implementation ambiguity,
- non-critical missing detail,
- solvable technical friction.

---

## Delivery Expectations

The preferred outcome is:

- working code,
- committed changes,
- a clean implementation aligned with the plan.

If deviation from the original approach is necessary, document it clearly in the commit message.

Include:

- **What changed**
- **Why the alternative was chosen**
- **What tradeoffs were introduced**

---

## Guiding Principle

**Do not stop at the first obstacle.**

Analyze, simplify, adapt, and continue toward the smallest correct implementation.
