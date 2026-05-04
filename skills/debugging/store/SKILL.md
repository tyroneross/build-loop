---
name: build-loop:debugging-store
description: Store a debugging incident after fixing a bug — writes to the bundled debugger MCP store tool with required fields and optional manual JSON fallback. Build-loop's native incident storage; canonical source has no discrete SKILL.md (the §"Incident Documentation" section of debugging-memory).
version: 0.1.0
user-invocable: false
source: claude-code-debugger/skills/debugging-memory/SKILL.md
source_hash: 484cd20dfe7fc6f345e508738a54fc6ba9750dca1efa9dbe26c6d57e5ba8f46e
source_section: "Incident Documentation"
---

# Debugging Incident Storage

Persist a fixed bug to debugging memory so future builds get a `KNOWN_FIX` verdict on recurrence. Native to build-loop — content adapted from the §"Incident Documentation" section of `claude-code-debugger/skills/debugging-memory/SKILL.md`.

> **Divergence note**: claude-code-debugger does not ship a discrete `store` SKILL.md. Storage is described inline in `debugging-memory/SKILL.md` and exposed as the `store` MCP tool. This skill encodes the workflow as a build-loop-native skill and points the source hash at the canonical file.

## When to Activate

- Phase 4 Review-F Report: for each Review-B/Iterate failure newly resolved this build, store the incident
- After any `build-loop:debugging-debug-loop` run that produced a verified fix
- User asks "save this fix", "remember this bug"

## Preferred Path — `store` MCP Tool

```
mcp__plugin_build-loop-debugger__store({
  symptom: "user-facing description (≤200 chars, preserves error type/file/key phrase)",
  root_cause: "technical explanation of why",
  fix: "what was changed",
  category: "logic|config|dependency|performance|react-hooks",
  tags: ["build-loop", "<project>", "<layer>", "<framework>", "<symptom-type>"],
  files_changed: ["path/to/file1.ts", "path/to/file2.ts"],
  file: "path/to/primary/problematic/file.ts"
})
```

**Required**: `symptom`, `root_cause`, `fix`. Everything else improves future retrieval.

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

## Manual JSON Fallback (MCP unavailable)

If the bundled debugger MCP server fails to start, write the incident JSON directly:

**Step 1: Generate incident ID**
```
INC_<CATEGORY>_YYYYMMDD_HHMMSS_xxxx
```
where `xxxx` is 4 random alphanumeric characters. Example: `INC_API_20260403_143052_a7b2`.

**Step 2: Ensure directory exists**
```bash
mkdir -p .claude-code-debugger/memory/incidents
```

(Build-loop bundles the debugger; the storage path is the standalone debugger plugin's directory, kept compatible so memory survives a future un-bundling.)

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

Write to `.claude-code-debugger/memory/incidents/<incident_id>.json`. Flag `⚠️ debugger MCP unavailable — wrote incident manually` in Review-F report.

## After Storing

1. Confirm: "Incident stored as `<incident_id>` (quality score: <score>)"
2. If `outcome` MCP is needed (a prior incident's fix was applied this build), invoke it now: `outcome({incident_id, result: "worked"|"failed"|"modified", notes})` to train the verdict classifier
3. If quality score < 0.75, suggest enriching tags or root-cause description before next build

## Sibling Skills

- `build-loop:debugging-memory` — search memory before debugging
- `build-loop:debugging-assess` — parallel domain assessment
- `build-loop:debugging-debug-loop` — full iterative debugging that produces the incident this skill stores

*Source: §"Incident Documentation" of `claude-code-debugger/skills/debugging-memory/SKILL.md`. Drift-checked by `build-loop:sync-skills`.*
