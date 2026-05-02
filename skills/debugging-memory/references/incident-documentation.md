# Incident Documentation Guide

Complete guide to documenting debugging incidents for effective future retrieval.

## Incident Structure

Each incident contains these fields:

### Identification

```json
{
  "incident_id": "INC_20241225_143052_abc1",
  "timestamp": 1735135852000,
  "session_id": "SESSION_1735135800000_xyz"
}
```

- **incident_id**: Auto-generated, format `INC_YYYYMMDD_HHMMSS_random`
- **timestamp**: Unix timestamp of when incident was stored
- **session_id**: Links to the debugging session that produced this fix

### Symptom

```json
{
  "symptom": "Search results not displaying after typing in the search box",
  "symptom_type": "ui"
}
```

- **symptom**: User-facing description of what went wrong
- **symptom_type**: Category (ui, api, logic, crash, performance, build)

**Writing effective symptoms:**
- Describe observable behavior
- Include context (what action triggered it)
- Avoid implementation details
- Keep it searchable

### Root Cause

```json
{
  "root_cause": {
    "description": "The debounce hook was resetting the search query on every keystroke because the dependency array included a reference-unstable callback. Each render created a new callback reference, triggering the debounce reset.",
    "file": "src/hooks/useDebounce.ts",
    "line_range": [15, 28],
    "code_snippet": "const debouncedValue = useMemo(() => {...}, [value, callback])",
    "category": "react-hooks",
    "confidence": 0.95
  }
}
```

- **description**: Detailed technical explanation (minimum 50 characters)
- **file**: Primary file where the bug existed
- **line_range**: Affected line numbers
- **code_snippet**: Relevant code (helps future matching)
- **category**: Technical category (react-hooks, api, config, dependency, logic)
- **confidence**: How certain the diagnosis is (0.0-1.0)

**Root cause quality checklist:**
- Explains WHY the bug occurred, not just WHERE
- Includes enough detail to understand without reading the code
- References specific code patterns or concepts
- Assigns appropriate confidence based on certainty

### Fix

```json
{
  "fix": {
    "approach": "Wrapped the callback in useCallback to stabilize the reference, then updated the debounce hook's dependency array to only include the memoized callback.",
    "changes": [
      {
        "file": "src/hooks/useDebounce.ts",
        "lines_changed": 8,
        "change_type": "modify",
        "summary": "Added useCallback wrapper and updated dependencies"
      },
      {
        "file": "src/components/SearchBar.tsx",
        "lines_changed": 3,
        "change_type": "modify",
        "summary": "Memoized the search handler callback"
      }
    ],
    "pattern_used": null,
    "time_to_fix": 25
  }
}
```

- **approach**: High-level description of the solution
- **changes**: Array of file changes with details
- **pattern_used**: Pattern ID if an existing pattern was applied
- **time_to_fix**: Minutes spent (helps estimate similar bugs)

### Verification

```json
{
  "verification": {
    "status": "verified",
    "regression_tests_passed": true,
    "user_journey_tested": true,
    "tests_run": ["search.test.ts", "debounce.test.ts"],
    "success_criteria_met": true
  }
}
```

- **status**: verified | partial | unverified
- **regression_tests_passed**: Existing tests still pass
- **user_journey_tested**: Manual testing of the affected feature
- **tests_run**: Specific test files executed
- **success_criteria_met**: Bug no longer reproducible

### Quality Gates

```json
{
  "quality_gates": {
    "guardian_validated": true,
    "tested_e2e": false,
    "tested_from_ui": true,
    "security_reviewed": false,
    "architect_reviewed": false
  }
}
```

Optional validation checkpoints for higher-stakes fixes.

### Tags

```json
{
  "tags": ["react", "hooks", "useMemo", "debounce", "search"],
  "files_changed": ["src/hooks/useDebounce.ts", "src/components/SearchBar.tsx"],
  "agent_used": "coder"
}
```

- **tags**: Searchable keywords for categorization
- **files_changed**: All modified files
- **agent_used**: Which Claude agent fixed this

## Quality Scoring

The memory system calculates a quality score (0-100%) based on:

| Component | Weight | Criteria |
|-----------|--------|----------|
| Root Cause | 30% | Description length, confidence, file reference |
| Fix | 30% | Approach detail, change documentation |
| Verification | 20% | Status, tests run, criteria met |
| Documentation | 20% | Tags, files listed, agent noted |

**Quality targets:**
- 75%+: Excellent - highly reliable for future matching
- 50-74%: Good - useful but may need verification
- <50%: Fair - stored but lower priority in results

## How Claude Code Stores Incidents

Claude Code should **directly write JSON files** to store incidents. No CLI command needed.

### Step-by-Step

**1. Create directory (if needed):**
```bash
mkdir -p .claude/memory/incidents
```

**2. Generate incident ID:**
```
INC_YYYYMMDD_HHMMSS_xxxx
```
Example: `INC_20241231_143052_a7b2` where:
- `20241231` = date (Dec 31, 2024)
- `143052` = time (14:30:52)
- `a7b2` = 4 random alphanumeric chars

**3. Write JSON file:**
```
.claude/memory/incidents/INC_20241231_143052_a7b2.json
```

**4. Include required fields:**
```json
{
  "incident_id": "INC_20241231_143052_a7b2",
  "timestamp": 1735654252000,
  "symptom": "Description of the bug",
  "root_cause": {
    "description": "Technical explanation",
    "file": "path/to/file.ts",
    "category": "logic",
    "confidence": 0.85
  },
  "fix": {
    "approach": "How it was fixed",
    "changes": []
  },
  "tags": ["searchable", "keywords"],
  "quality_score": 0.75
}
```

### When to Store

- **After fixing a bug** - Immediately document while context is fresh
- **After debugging session** - Even if fix wasn't found, document the investigation
- **After pattern identified** - If you notice recurring issues

### Workflow Example

```
1. Bug reported: "Search not working"
2. Search memory via `debugger search` MCP tool: "search not working"
3. Found match? → Apply fix
   No match? → Investigate and fix
4. Store via `debugger store` MCP tool (or write JSON to .claude/memory/incidents/INC_xxx.json)
5. Future searches will find this incident
```

## Storage Modes

### Local Mode (default)

Incidents stored in project's `.claude/memory/incidents/`:
- Project-specific context
- Doesn't pollute other projects
- Best for project-specific bugs

### Shared Mode

Incidents stored in `~/.claude-code-debugger/incidents/`:
- Cross-project learning
- Common patterns accessible everywhere
- Best for reusable solutions

Set via environment:
```bash
export CLAUDE_MEMORY_MODE=shared
```

## Retrieval Strategy

When searching for matches, the system:

1. **Pattern match first** (90% confidence threshold)
2. **Incident search** (70% confidence threshold)
3. **Keyword similarity** using Jaccard scoring
4. **Temporal weighting** - prefer recent incidents (90-day window)

Higher quality incidents rank higher in results.

## Interactive Documentation

Use interactive mode for guided documentation:

```typescript
import { storeIncident } from '@tyroneross/claude-code-debugger';

await storeIncident(incident, {
  interactive: true,
  validate_schema: true
});
```

The system prompts for:
- Missing required fields
- Short descriptions that need expansion
- Verification status confirmation
- Tag suggestions based on symptom
