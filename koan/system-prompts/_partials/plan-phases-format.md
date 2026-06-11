### File Map

Before defining phases, map out which files will be created or modified and what each one is responsible for. This is where decomposition decisions get locked in.

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `exact/path/to/new_file.py` | One-line description |
| Modify | `exact/path/to/existing.py` | What changes and why |
| Test   | `tests/exact/path/test_file.py` | What it covers |

Design units with clear boundaries. Files that change together should live together. Follow the codebase's existing file organization patterns.

### Implementation Phases

Break the work into numbered **phases**. Each phase should be a self-contained unit of work that can be implemented and reviewed independently.

For each phase, use this format:

#### Phase N: Short descriptive title

**Files**: List the exact files from the File Map touched in this phase.

- [ ] **Step 1: Write the failing test** — Describe what to test, then show the test code:
  <details><summary>Test code</summary>

  ```python
  def test_specific_behavior():
      result = function(input)
      assert result == expected
  ```

  </details>
- [ ] **Step 2: Implement the change** — Describe the change, then show the key code:
  <details><summary>Implementation</summary>

  ```python
  def function(input):
      return expected
  ```

  </details>
- [ ] **Step 3: Verify** — Exact command and expected outcome:
  <details><summary>Command</summary>

  ```bash
  pytest tests/path/test_file.py::test_name -v
  # Expected: PASS
  ```

  </details>
- [ ] **Step 4: Commit** — Conventional commit message for this step.

**Done when**: Acceptance criteria (how to know this phase is complete).

#### Wrapping code in collapsible blocks

Every code block in a step MUST be wrapped in a `<details>` element with a short `<summary>` label, exactly as shown above. This keeps the plan scannable — a reader sees the step descriptions and checkboxes first, and expands the code only when they need it. Rules for the wrapping:

- Put a **blank line after** the `<summary>` line and a **blank line before** the closing `</details>` — GitHub will not render the fenced code otherwise.
- Indent the `<details>`, code fence, and `</details>` to stay inside the checkbox list item (2 spaces under the `- [ ]`).
- Keep the `<summary>` label short and descriptive (e.g., `Test code`, `Implementation`, `Command`, `Migration`).
- The step's one-line description stays **outside** the `<details>` (always visible); only the code goes inside.

#### Guidelines for steps

- Each step should be one action (2-5 minutes of work for an engineer).
- Steps that change code MUST include the actual code (inside a `<details>` block), not just descriptions.
- Test steps MUST show the test function, not "add appropriate tests."
- Verification steps MUST include the exact command and expected outcome.
- Follow the test-first pattern: write failing test → implement → verify → commit.
- When a phase has no testable behavior (e.g., config changes, docs), skip the test step but keep verify.