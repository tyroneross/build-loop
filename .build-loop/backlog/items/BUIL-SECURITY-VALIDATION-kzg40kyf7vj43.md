---
id: BUIL-SECURITY-VALIDATION-kzg40kyf7vj43
schema_version: 1
title: Make full security scan fixture-aware and deduplicate generated mirrors
status: open
priority: P2
type: debt
area: security-validation
entities: [security_scan, security-fixtures, codex-artifact, CSP]
gated: none
provenance:
  source: autonomy-dashboard repair validation
  ref: commit-parent:0dbbf05
evidence: [scripts/security_scan.py, scripts/test_security_scan.py, scripts/test_security_checks_api.py, hooks/hooks.json]
supersedes: null
superseded_by: null
created: 2026-08-08
updated: 2026-08-08
review_by: 2026-08-22
owner: security-review
---

## Context
The 2026-08-08 full scan returned 213 non-blocking findings: 146 shell-tool declarations, 22 prompt-concatenation matches, 44 fixture/generated-mirror matches, and one generic missing-static-CSP match. No finding touched the dashboard repair and no high/critical finding existed.

## Acceptance
- Runtime findings and intentional test fixtures are classified separately.
- Generated Codex mirrors do not double-count a canonical source finding.
- Server-emitted CSP headers satisfy the scanner's CSP control.
- Intentional shell exposure names its prompt-injection and autonomy controls.

## Notes
Acceptance: classify runtime versus intentional fixture examples; collapse source/artifact duplicates; recognize CSP emitted by the Python dashboard server; document intentional shell exposure and its autonomy/pre-tool controls; keep true runtime findings visible.
