### Review Checklist

Use the following checklist to guide your review. Check each item *if applicable* to the
files in the diff — skip items that don't apply to the changes under review.

**Security**
- Check for SQL/command injection, shell interpolation of user input
- Check for hardcoded secrets, API keys, or credentials
- Check for unsafe deserialization (`pickle.loads`, `yaml.load` without `SafeLoader`)
- Check for path traversal (unsanitized user input in file paths)
- Check for missing input validation at system boundaries (API endpoints, CLI args)

**Error Handling**
- Check for bare `except:` or `except Exception` that swallows errors silently
- Check for missing cleanup in error paths (unclosed files, unreleased locks)
- Check for resource leaks (sockets, file handles, database connections)
- Check for error messages that expose internal details to end users

**Performance**
- Check for N+1 queries or repeated I/O in loops
- Check for unbounded collections that grow without limit
- Check for missing pagination on list endpoints or queries
- Check for unnecessary copies of large data structures

**Testing**
- Check for untested code branches introduced by the changes
- Check for missing edge case coverage (empty input, boundary values, None)
- Check for test isolation issues (shared state, order-dependent tests)
- Check for tests that read or inspect actual source code to verify code presence/absence:
{@include test-guidance}

**Production Readiness** (apply when changes affect deployment, data, or public interfaces)
- Check for backward-incompatible changes to public APIs, configs, or data formats
- Check for missing migration strategy when schema or state format changes
- Check for changes that could break existing callers or consumers

**Python-specific** (apply only when Python files are in the diff)
- Check for mutable default arguments (`def f(x=[])`)
- Check for `is` vs `==` misuse with literals
- Check for unsafe `eval()`/`exec()` usage
- Check for missing `with` statement for resource management