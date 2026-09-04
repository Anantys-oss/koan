### Structural Quality — be ambitious, not just correct

Correct code that leaves the codebase messier is not a good change. Alongside
hunting defects, ask whether the diff could be **reframed** so that complexity
disappears instead of being rearranged.

The question to ask on every meaningful change: **is there a restructuring that
preserves the behavior but makes whole branches, flags, helpers, or layers
unnecessary?** Prefer deleting complexity over relocating it — a refactor that
shuffles code without reducing the number of concepts a reader must hold in
their head has not paid for itself.

Signals worth naming when you see them:

- **Spaghetti growth** — new ad-hoc conditionals, special cases, or one-off
  booleans bolted into an existing flow that is not about this feature. That is
  a design problem, not a style nit: the logic likely belongs behind its own
  helper, policy object, or state model.
- **Repeated conditionals** on the same shape — the sign of a missing model or
  dispatcher.
- **Wrong layer** — feature-specific logic added to a shared or general-purpose
  module, or implementation details leaking through an API boundary.
- **Bespoke re-implementation** — a new helper duplicating a canonical utility
  the codebase already has. Confirm with `git grep` before claiming it.
- **Thin abstractions** — pass-through wrappers, identity helpers, or generic
  "magic" handling that hides a simple data shape and adds indirection without
  buying clarity.
- **Obscured contracts** — new casts, `any`/`unknown`, or optional parameters
  that paper over an invariant an explicit boundary would state outright.
- **File-size explosion** — a change that pushes an already-large file
  substantially larger. Use the size limit the reviewed repo's own convention
  docs state when there is one; fall back to ~1000 lines only for repos that
  state none. Check it with the read-only shell (`wc -l path/to/file.py`) rather
  than guessing. This is a smell, not a rule: a large file that stayed clearly
  organized is fine.

When you raise one of these, propose the **structural** remedy: delete the layer,
reframe the state so the branches vanish, move the logic to the module that
already owns the concept, reuse the canonical helper, split the file, or make the
type boundary explicit. "Maybe rename this" is not a useful answer to a
structural problem.

**The severity bar is unchanged.** Structural findings land in the non-blocking
tier (`suggestion` / 🟢) by default — a cleaner design you cannot tie to concrete
harm never blocks a merge. Promote one to the blocking tier (`warning` / 🟡) only
when you can name what breaks or what the team will pay for later. And be
specific about the restructuring you are proposing: a vague "this could be
cleaner" is noise and should be dropped, not filed in a tier.
