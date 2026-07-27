# Security

* [Mission Hooks Security](mission-hooks.md) - Threat model and safe-use guidance for repo-config-driven pre/post shell hooks (.koan/config.yaml pre_hooks/post_hooks): why they execute arbitrary code from the target repo, the default-off operator opt-in gate and per-project override that contain the risk, and the audit surface.
* [Prompt Guard](prompt-guard.md) - Documents `prompt_guard.py`'s input-side defenses against prompt injection in missions and its configuration/complementary defenses (outbox scanner, data fencing, memory scanning).
* [Security Review](security-review.md) - Documents the automated post-mission security review that scans diffs for dangerous patterns, scores risk, optionally blocks auto-merge, and logs an audit trail.
* [Threat Model: Agent Disalignment Risk](threat-model-agent-disalignment.md) - A threat-model analysis of the blast radius if Koan's autonomous agent becomes disaligned, covering attack surface, exfiltration vectors, protections, and recommended mitigations.
