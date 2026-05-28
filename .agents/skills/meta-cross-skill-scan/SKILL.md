---
name: meta-cross-skill-scan
status: active-functional
description: |
  Scan PreferenceStore across all 19 domains; surface tokens with strong accepted-signal in ≥3 domains but absent in others; emit CrossSkillFinding for human review.

  Triggers — invoke this skill when the user says any of:
  - "find cross-skill patterns"
  - "哪些 token 在多个 domain 都被 accept"
  - "scan for meta-skill transfer candidates"

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (`judge-evaluate`) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.38
omni_hub:
  layer: functional
  display_name: "Meta Cross-Skill Scan"
  status: active
  version: 0.1.0
  entrypoint: "operation:meta_cross_skill_scan"
  risk_level: L0
  composes:
    - judge-evaluate
  required_permissions: []
  tags:
    - functional
    - orchestrator
---

<!-- omni-skill-stub: v0.38 -->

# Meta Cross-Skill Scan

Scan PreferenceStore across all 19 domains; surface tokens with strong accepted-signal in ≥3 domains but absent in others; emit CrossSkillFinding for human review.

## What it composes

- `judge-evaluate` (foundation)

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli meta-cross-skill-scan [--help]
```

## Hard rules

- Functional skills MAY orchestrate multiple foundation calls.  They do
  NOT bypass the Proposal[T] gate for any mutating step.
- Trigger phrases are the user-visible product surface; tune via
  PreferenceStore + ``harness-compile-skill --functional`` (v0.40).



---

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.38 -->`` marker line to
opt out of regeneration._
