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

- [ ] **Step 1: Write the failing test** — Describe what to test and show the test code:
  ```python
  def test_specific_behavior():
      result = function(input)
      assert result == expected
  ```
- [ ] **Step 2: Implement the change** — Show the key code changes (not just descriptions):
  ```python
  def function(input):
      return expected
  ```
- [ ] **Step 3: Verify** — Exact command and expected outcome:
  ```bash
  pytest tests/path/test_file.py::test_name -v
  # Expected: PASS
  ```
- [ ] **Step 4: Commit** — Conventional commit message for this step.

**Done when**: Acceptance criteria (how to know this phase is complete).

#### Guidelines for steps

- Each step should be one action (2-5 minutes of work for an engineer).
- Steps that change code MUST include the actual code, not just descriptions.
- Test steps MUST show the test function, not "add appropriate tests."
- Verification steps MUST include the exact command and expected outcome.
- Follow the test-first pattern: write failing test → implement → verify → commit.
- When a phase has no testable behavior (e.g., config changes, docs), skip the test step but keep verify.