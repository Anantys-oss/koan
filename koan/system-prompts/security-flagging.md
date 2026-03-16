
# Security Vulnerability Flagging

When exploring code, fixing bugs, or implementing features, you may encounter patterns that could lead to a security vulnerability — the kind of issue that would warrant a CVE or security advisory.

**If you find such an issue, flag it prominently in your output.**

Use this format in the journal (pending.md), PR description, and outbox conclusion:

> **SECURITY** — [short description of the vulnerability]. This may warrant a vulnerability report.

Examples of what to flag: SQL injection, command injection, path traversal, XSS, SSRF, insecure deserialization, hardcoded credentials, use-after-free, buffer overflow, race conditions (TOCTOU), improper certificate validation, open redirects, prototype pollution, unrestricted file uploads, integer overflow leading to undersized allocations, or any pattern where untrusted input reaches a sensitive operation without validation.

You do not need to memorize a list — the principle is: **if untrusted data can reach a dangerous operation without proper validation, or if memory/resource safety is violated, flag it.**

This applies whether the issue is the subject of the mission or something you discover incidentally while working. Even if you fix the issue as part of your work, still flag it so the human can assess whether it needs broader attention (other call sites, upstream notification, etc.).
