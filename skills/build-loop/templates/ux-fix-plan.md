---
id: {id}
dimension: {dimension}
severity: {severity}
label: {label}
architecture_impact: {architecture_impact}
files_touched:
{files_touched_yaml}
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# UX Fix Plan: {label}

**Hint**: {hint}

## Evidence

{evidence}

## Proposed fix

{proposed_fix}

## Rollback

```bash
{rollback}
```

## Notes

- `architecture_impact: true` means this fix introduces a new component, data
  flow, navigation graph, schema migration, or auth provider change. The
  orchestrator must surface it in Review-F for explicit user confirmation
  before Iterate dequeues it.
- `architecture_impact: false` entries are auto-fixable in the next Iterate
  cycle, parallelized when independent (`files_touched` disjoint).
- `severity: minor` entries never reach this template — they live in the
  Review-F report only.
