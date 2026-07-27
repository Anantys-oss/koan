# Quickstart / Validation: Config-driven mission hooks

Prerequisites: a working koan checkout, `KOAN_ROOT` set, `make setup` done.

## 1. Unit tests (primary validation)

```bash
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest \
  koan/tests/test_project_koan.py \
  koan/tests/test_mission_hooks.py \
  koan/tests/test_config.py \
  koan/tests/test_projects_config.py \
  koan/tests/test_mission_executor.py \
  koan/tests/test_mission_runner.py -v
```

Expected: resolver precedence/caps/fail-safe pass; executor honors gate, ordering,
timeout, status env, and failure isolation; call sites invoke the executor with the
right (mission_type, phase, success).

## 2. Resolver by hand

```bash
mkdir -p /tmp/hookrepo/.koan
cat > /tmp/hookrepo/.koan/config.yaml <<'YAML'
default:
  pre_hooks: ["echo default-pre"]
review:
  pre_hooks: ["echo review-pre-1", "echo review-pre-2"]
  post_hooks: ["echo review-post"]
YAML

KOAN_ROOT=/tmp/test-koan .venv/bin/python -c "
from app.project_koan import get_mission_hooks as g
print('review/pre  =', g('/tmp/hookrepo', 'review', 'pre'))   # both review-pre-*
print('review/post =', g('/tmp/hookrepo', 'review', 'post'))  # review-post
print('plan/pre    =', g('/tmp/hookrepo', 'plan', 'pre'))     # falls back to default-pre
print('plan/post   =', g('/tmp/hookrepo', 'plan', 'post'))    # [] (no default post)
print('none/pre    =', g('/tmp/hookrepo', '', 'pre'))         # default-pre
"
```

## 3. Gate off by default (security)

```bash
KOAN_ROOT=/tmp/test-koan .venv/bin/python -c "
from app.config import is_mission_hooks_enabled
print('enabled by default =', is_mission_hooks_enabled())   # False
"
# With the gate off, run_pre_hooks/run_post_hooks must execute ZERO commands.
```

## 4. Executor smoke (gate on)

```bash
# Enable in instance/config.yaml:  mission_hooks: { enabled: true }
KOAN_ROOT=/tmp/test-koan .venv/bin/python -c "
from app import mission_hooks
mission_hooks.run_pre_hooks('/tmp/hookrepo', 'anyproj', 'review')
mission_hooks.run_post_hooks('/tmp/hookrepo', 'anyproj', 'review', success=False)
"
# Expect: review-pre-1/2 then, for post, KOAN_MISSION_STATUS=failure visible to the command.
# A command that exits non-zero or sleeps past the timeout is logged but does not raise.
```

## 5. End-to-end (optional, live-ish)

With the gate enabled for a test project and `review.pre_hooks: ["touch
/tmp/hookrepo/pre.flag"]` / `post_hooks: ["touch /tmp/hookrepo/post.flag"]`, run a
`/review <pr-url>` mission and confirm both flag files appear (`make logs` shows the
`[mission_hooks]` lines). With the gate disabled, confirm no flag files appear and a
"skipped (not enabled)" line is logged.

## Acceptance mapping

- SC-001/SC-006 → steps 1–2 (precedence, ordering, replace-not-merge).
- SC-002 → step 3 (default-off gate).
- SC-003 → step 4 (`KOAN_MISSION_STATUS`).
- SC-004 → step 4 (timeout/non-zero isolation) + unit tests.
- SC-005 → gate-off / no-config no-op (steps 1, 3).
