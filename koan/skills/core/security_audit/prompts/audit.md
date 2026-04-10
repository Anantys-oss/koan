You are performing a **security audit** of the **{PROJECT_NAME}** project. Your goal is to find exploitable security vulnerabilities — the kind that would warrant a CVE, a security advisory, or an urgent fix. Produce a structured report that will be used to create individual GitHub issues.

{EXTRA_CONTEXT}

## Instructions

### Phase 1 — Reconnaissance

1. **Read the project's CLAUDE.md** (if it exists) for architecture overview, tech stack, dependencies, and deployment model.
2. **Explore the directory structure**: Use Glob to map the project layout — source directories, config files, build files, dependency manifests, Docker/CI files.
3. **Identify the attack surface**: entry points where untrusted data enters the system:
   - HTTP/API endpoints, request handlers, route definitions
   - CLI argument parsing, environment variable reads
   - File uploads, user-supplied paths, template rendering
   - Database queries, ORM calls, raw SQL
   - External service calls, webhook handlers
   - Deserialization points (JSON, YAML, pickle, XML)
   - Authentication and session management
4. **Read recent git history**: Use `git log --oneline -20` to check for recent security-related changes.

### Phase 2 — Vulnerability Analysis

Systematically examine each attack surface area. For each, trace the data flow from input to dangerous operation. Focus on these vulnerability classes:

#### A. Injection Vulnerabilities
- **SQL injection**: Raw SQL with string interpolation, unsanitized ORM filters, dynamic table/column names
- **Command injection**: `os.system()`, `subprocess` with `shell=True`, backtick execution, unsanitized args passed to shell commands
- **Server-Side Template Injection (SSTI)**: User input rendered in templates without escaping, dynamic template compilation from user data
- **XSS (Cross-Site Scripting)**: Reflected or stored user input rendered without escaping in HTML, JavaScript, or SVG contexts
- **LDAP/XPath/Header injection**: Unsanitized input in LDAP queries, XPath expressions, or HTTP headers

#### B. Authentication & Authorization Flaws
- Missing or bypassable authentication on sensitive endpoints
- Broken access control: horizontal privilege escalation (accessing other users' data), vertical escalation (admin functions without role check)
- Insecure session management: predictable tokens, missing expiry, no invalidation on logout
- Hardcoded credentials, API keys, or secrets in source code
- Weak password hashing (MD5, SHA1, no salt, low iteration count)

#### C. Secrets & Credential Exposure
- API keys, tokens, passwords committed to source code or config files
- Secrets in logs, error messages, or stack traces
- `.env` files, private keys, or certificates in the repository
- Insufficient `.gitignore` coverage for sensitive files
- Secrets passed via URL query parameters (logged by proxies/browsers)

#### D. Path Traversal & File System Attacks
- User-controlled file paths without sanitization (`../../../etc/passwd`)
- Unrestricted file upload (type, size, destination)
- Symlink attacks, race conditions in file operations (TOCTOU)
- Temporary file creation with predictable names

#### E. Server-Side Request Forgery (SSRF)
- User-controlled URLs fetched server-side without validation
- DNS rebinding vulnerabilities
- Cloud metadata endpoint access (`169.254.169.254`)

#### F. Insecure Deserialization
- Untrusted data passed to `pickle.loads()`, `yaml.load()` (without SafeLoader), `eval()`, `exec()`
- JSON deserialization into executable objects
- XML External Entity (XXE) processing

#### G. Cryptographic Weaknesses
- Use of broken algorithms (MD5, SHA1 for security, DES, RC4)
- Hardcoded encryption keys or IVs
- Missing TLS certificate validation
- Insecure random number generation for security tokens (`random` instead of `secrets`)
- Improper use of cryptographic primitives (ECB mode, no authentication on encryption)

#### H. Race Conditions with Security Impact
- TOCTOU (Time-of-Check-Time-of-Use) on authorization checks
- Double-spend or replay vulnerabilities in financial/token operations
- Concurrent access to shared state without proper locking in security-critical paths

#### I. Dependency & Supply Chain Risks
- Known vulnerable dependency versions (check manifest files against known CVEs if version is obviously outdated)
- Unpinned dependencies that could be hijacked
- Typosquatting risks in dependency names

#### J. Configuration & Deployment Security
- Debug mode enabled in production configuration
- CORS misconfiguration (overly permissive origins)
- Missing security headers (CSP, HSTS, X-Frame-Options)
- Exposed admin panels, debug endpoints, or internal APIs
- Docker running as root, overly permissive container capabilities

#### K. Memory Safety & Native Boundary Vulnerabilities
- **Buffer overflows / out-of-bounds access**: writes or reads beyond allocated memory, unsafe copies, unchecked buffer lengths, fixed-size buffers, stack/heap corruption
- **Integer overflow / underflow / truncation**: attacker-controlled sizes, offsets, or counts that wrap and lead to undersized allocations or incorrect bounds checks
- **Use-after-free / double free / uninitialized memory**: lifetime bugs in native code, extensions, FFI bindings, C/C++/Rust unsafe blocks, or third-party native modules
- **Unsafe parsing of untrusted binary data**: images, archives, compressed formats, protocol frames, file metadata, custom serialization formats
- **Native boundary trust issues**: Python/Node/Perl/Ruby code passing untrusted data into C/C++ libraries, shell tools, or unsafe system APIs without validating size, encoding, or structure

When auditing projects that include native code, FFI bindings, image/file parsers, compression, archive extraction, custom protocol handling, or unsafe blocks, explicitly trace attacker-controlled length/offset values to memory operations. Treat memory corruption bugs as critical when they could lead to denial of service, information disclosure, or remote code execution.

#### L. Request Handling, Cross-Origin, and Browser Abuse
- **CSRF** on state-changing endpoints that rely on cookies or ambient browser credentials
- **Open redirect** and unvalidated forwards that can aid phishing, token leakage, or auth bypass chains
- **HTTP request smuggling / header parsing inconsistencies** where reverse proxy and app disagree on message boundaries or trusted headers
- **Host header / proxy trust issues** leading to poisoned links, cache poisoning, SSRF pivots, or auth bypass

#### M. Denial of Service & Resource Exhaustion
- Missing rate limiting, anti-automation, or abuse controls on expensive endpoints
- Unbounded file upload, decompression, parsing, regex, pagination, or recursive processing
- Hash-collision, regex backtracking, zip bombs, image bombs, and oversized JSON/XML payloads
- Expensive auth flows or report/export endpoints that can be abused for CPU, memory, disk, or queue exhaustion

### Phase 3 — Produce Findings

For EACH finding, produce a block in this exact format. Use `---FINDING---` as separator between findings:

```
---FINDING---
TITLE: Security: <concise one-line summary>
SEVERITY: <critical|high|medium|low>
CATEGORY: <injection|auth|secrets|path_traversal|ssrf|deserialization|crypto|race_condition|dependency|config|memory_safety|request_handling|dos>
LOCATION: <file_path:line_range>
PROBLEM: <2-3 sentences explaining the vulnerability and how it could be exploited>
WHY: <1-2 sentences on the real-world impact — data breach, RCE, privilege escalation, etc.>
SUGGESTED_FIX: <Concrete remediation steps. Include a brief code sketch if helpful.>
EFFORT: <small|medium|large>
```

### Severity Guide

- **critical**: Remote Code Execution (RCE), SQL injection, authentication bypass, unrestricted file read/write, deserialization of untrusted data leading to code execution
- **high**: Stored XSS, SSRF, privilege escalation, hardcoded secrets/credentials, path traversal to sensitive files, broken access control
- **medium**: Reflected XSS, CSRF, information disclosure, weak cryptography, insecure session management, missing security headers
- **low**: Minor information leakage, verbose error messages, missing best practices with limited exploitability

**Prioritization rule**: Focus on **critical** and **high** severity findings first. Only report medium/low if fewer than {MAX_ISSUES} critical+high issues exist.

### Effort Guide

- **small**: < 30 minutes, single file, straightforward fix (e.g., add parameterized query, remove hardcoded secret)
- **medium**: 1-2 hours, possibly multiple files, requires design thought (e.g., implement CSRF protection, add auth middleware)
- **large**: Half day+, cross-cutting change, may need migration (e.g., replace auth system, implement rate limiting)

## Rules

- **Read-only.** Do not modify any files. This is a pure analysis task.
- **Be specific.** Always include exact file paths and line numbers. Show the vulnerable code snippet.
- **Be exploitable.** Each finding must describe a realistic attack scenario, not just a theoretical weakness.
- **Quality over quantity.** Report at most {MAX_ISSUES} findings. Focus on the most critical and exploitable issues.
- **No false positives.** Only report issues where you can trace the data flow from untrusted input to dangerous operation. If you're unsure, verify by reading the code path.
- **Each finding must be self-contained.** A developer should be able to understand the vulnerability, assess the risk, and fix it from the issue alone.
- **Use the exact separator format** (`---FINDING---`) so findings can be parsed programmatically.
- **Do not report**: style issues, code quality concerns, non-security tech debt, or optimization opportunities. This is a security audit, not a code review.
