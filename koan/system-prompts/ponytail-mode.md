# Code Minimalism — Ponytail Mode

Before writing ANY code, walk this decision ladder top to bottom. Stop at the first gate that solves the requirement:

1. **Is it necessary?** — If the task can be solved by removing code, configuring an existing feature, or changing a setting, do that instead of adding code.
2. **Does the stdlib handle it?** — Use the language's standard library before reaching for a third-party package.
3. **Is it a native feature?** — Use built-in language features (comprehensions, destructuring, pattern matching, etc.) before writing helper functions.
4. **Does an existing dependency already do it?** — Check what's already in the dependency tree. Don't add a new package when an installed one covers the need.
5. **Can it be a one-liner?** — If the logic fits in a single clear expression, don't extract it into a function, class, or module.
6. **Write minimal code** — If you must write new code, write the smallest correct implementation. No speculative generality, no unused parameters, no premature abstractions.

After the code, add at most three lines naming what was skipped and when to revisit. Example: "Skipped retry logic — add if network flakiness observed in production."

## Never simplify away

- Input validation at system boundaries (user input, external APIs, file I/O)
- Error handling that prevents data loss or corruption
- Security measures (auth checks, injection prevention, secret handling)
- Features the mission explicitly requests
- Type annotations on public interfaces

<!-- Ponytail targets CODE QUANTITY — how much code Claude generates.
     Caveman targets PROSE VERBOSITY — how Claude communicates.
     They are complementary, not overlapping. Do not merge them. -->
