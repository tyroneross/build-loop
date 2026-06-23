---
name: build-loop:debugging-store
description: Store a debugging incident after fixing a bug — writes build-loop's native local incident record and optionally mirrors to standalone Coding Debugger. Build-loop's native incident storage; canonical source has no discrete SKILL.md (the §"Incident Documentation" section of debugging-memory).
version: 0.1.0
user-invocable: false
source: claude-code-debugger/skills/debugging-memory/SKILL.md
source_hash: 5c4ee5ada781107e7def92abeca4d51fc0efc61700f7cf43e948da34f4c0681d
source_section: "Incident Documentation"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Debugging Incident Storage

Persist a fixed bug to debugging memory so future builds can recognize recurrence. Native to build-loop; initially adapted from the §"Incident Documentation" workflow in the debugger lineage.

> **Divergence note**: the standalone debugger does not ship a discrete `store` SKILL.md. Build-loop keeps storage as a native skill because Review-F needs deterministic local persistence even when no MCP server exists.

## When to Activate

- Phase 4 Review-F Report: for each Review-B/Iterate failure newly resolved this build, store the incident
- After any `build-loop:debug-loop` run that produced a verified fix
- User asks "save this fix", "remember this bug"

## Native Path — Build-Loop Incident Note

Write one incident note per resolved failure:

```bash
mkdir -p .build-loop/issues
```

Path:

```text
.build-loop/issues/YYYY-MM-DD-<short-slug>.md
```

Template:

```markdown
# <one-line symptom>

**Symptom**: <error string, failing command, or observed behavior>
**Root cause**: <technical cause plus first controllable system cause>
**Fix**: <what changed and why>
**Verification**: <commands, tests, or observed proof>
**Files**: <paths touched>
**Tags**: build-loop, <project>, <layer>, <framework>, <symptom-type>
**RCA framework**: <5 Whys | causal tree | fishbone | Kepner-Tregoe | differential diagnosis | falsification>
```

**Required**: `symptom`, `root_cause`, `fix`, `verification`. Everything else improves future retrieval.

## Optional Mirror — Coding Debugger MCP

If standalone Coding Debugger is installed and available, mirror the same incident:

```
mcp__plugin_coding_debugger__store({
  symptom: "user-facing description (≤200 chars, preserves error type/file/key phrase)",
  root_cause: "technical explanation of why",
  fix: "what was changed",
  category: "logic|config|dependency|performance|react-hooks",
  tags: ["build-loop", "<project>", "<layer>", "<framework>", "<symptom-type>"],
  files_changed: ["path/to/file1.ts", "path/to/file2.ts"],
  file: "path/to/primary/problematic/file.ts"
})
```

Mirror failure is not a build failure. Report it as "local incident stored; Coding Debugger mirror unavailable."

## Tag Discipline

Always include:
- `"build-loop"` — distinguishes build-orchestrator origins from manual `/build-loop:debug` runs
- Project name (lowercase, slugified)
- Layer (`frontend`, `backend`, `database`, `infra`, `external`)

Add as relevant:
- Technology: `react`, `typescript`, `python`, `api`, `prisma`
- Category: `logic`, `config`, `dependency`, `performance`
- Symptom type: `crash`, `render`, `timeout`, `validation`

## Quality Score Targets

The memory system scores stored incidents:
- Root cause depth (30%)
- Fix documentation completeness (30%)
- Verification status (20%)
- Tags and metadata (20%)

Target 75%+. Score below 75% means future searches won't surface this incident reliably — pad the description and tags before storing.

## JSON Compatibility Fallback

If a downstream process requires JSON, write a compatibility copy after the native note:

**Step 1: Generate incident ID**
```
INC_<CATEGORY>_YYYYMMDD_HHMMSS_xxxx
```
where `xxxx` is 4 random alphanumeric characters. Example: `INC_API_20260403_143052_a7b2`.

**Step 2: Ensure directory exists**
```bash
mkdir -p .build-loop/debugging/incidents
```

**Step 3: Write the JSON**
```json
{
  "incident_id": "INC_API_20260403_143052_a7b2",
  "timestamp": 1735654252000,
  "symptom": "User-facing description of the bug",
  "root_cause": {
    "description": "Technical explanation",
    "file": "path/to/problematic/file.ts",
    "category": "logic|config|dependency|performance|react-hooks",
    "confidence": 0.85
  },
  "fix": {
    "approach": "What was done",
    "changes": [
      { "file": "path/to/file.ts", "lines_changed": 10, "change_type": "modify|add|delete", "summary": "..." }
    ]
  },
  "verification": {
    "status": "verified|unverified",
    "regression_tests_passed": true,
    "success_criteria_met": true
  },
  "tags": ["build-loop", "project-name", "layer", "category"],
  "files_changed": ["list/of/all/files.ts"],
  "quality_score": 0.75
}
```

Write to `.build-loop/debugging/incidents/<incident_id>.json`. Flag `debugger JSON compatibility copy written` in Review-F only if another workflow requested JSON.

## After Storing

1. Confirm the local note path.
2. If standalone Coding Debugger supplied the prior incident, invoke `outcome({incident_id, result: "worked"|"failed"|"modified", notes})` to train its verdict classifier.
3. If the note lacks verification, enrich it before ending Review-F.

## Sibling Skills

- `build-loop:debugging-memory` — search memory before debugging
- `build-loop:debugging-assess` — parallel domain assessment
- `build-loop:debug-loop` — full iterative debugging that produces the incident this skill stores

*Source: adapted from the debugger incident-documentation workflow and maintained as a build-loop-native skill. Drift-checked by `build-loop:sync-skills`.*
