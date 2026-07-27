# Contract — Configuration keys

New/extended keys under `config.yaml`, each resolved through the existing
`_get_config_with_overrides(<key>, defaults)` convention (per-instance value, per-project override
via `projects.yaml`). All are **backward-compatible and fail-open**: absent config → the documented
default, which for the default review path preserves today's behavior.

## `review_consistency` (NEW) — getter `get_review_consistency_config(project_name="")`

```yaml
review_consistency:
  reuse_enabled: true      # reproduce prior review verbatim when head+base SHA & request signature match (FR-001)
  freeze_enabled: true     # re-review freeze: suppress first-time non-critical findings on unchanged code (FR-003)
```

- `reuse_enabled=false` → always re-derive (still reconciles). `freeze_enabled=false` → no freeze
  (pre-feature re-review behavior). Both default `true`.

## `review_discovery` (NEW) — getter `get_review_discovery_config(project_name="")`

```yaml
review_discovery:
  enabled: false           # OFF by default (SC-008): default review is byte-identical to today
```

- When `true`, the `{@include review-comprehensive-discovery}` partial is added to the review
  prompt; participates in the reuse request-signature (FR-016). Fixed perspective set; whole-mode
  toggle only (no per-perspective keys) — clarification decision.

## `review_triage` (EXTEND existing `get_review_triage_config`)

```yaml
review_triage:
  important_bar: strict    # how strict the yellow "Important" bar is (FR-014); default tightens vs today
  pre_existing_label: "[Pre-Existing Issue]"   # prefix text (FR-027/FR-028)
  deferred_label: "[Deferred]"                 # prefix text (FR-034)
```

## `review_dispositions` (NEW) — getter `get_review_dispositions_config(project_name="")`

```yaml
review_dispositions:
  enabled: true            # honor human PR-comment dispositions on re-review (FR-031)
  honor_critical: true     # dismissals apply to critical too (FR-035; clarification Q7 = full authority)
  min_role: any            # whose comments count: any|author|write (FR-032; clarification Q6 = any).
                           # `any` is the chosen default; `author`/`write` are the optional future
                           # tightening knob (spec Assumptions) — NOT the default.
```

## Invariants (tested)

- With `review_discovery.enabled=false` (default) **and** no other keys set, the review prompt and
  output are byte-identical to pre-feature behavior (SC-008).
- Each getter returns the documented defaults when the key is absent, and merges a per-project
  override on top (matches the ~20 existing `get_review_*_config` helpers).
- Unknown/garbled values fail open to the safe default (never raise into the review pipeline).
