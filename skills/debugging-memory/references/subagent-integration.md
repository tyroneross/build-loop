# Subagent Integration Guide

This guide explains how to integrate claude-code-debugger with custom subagents, third-party agents, and multi-agent workflows.

## Overview

The debugging memory system works best when all agents in a workflow can access it. However, not all subagents have the necessary tool access. This guide covers:

1. How to give subagents direct access
2. How to proxy debugging memory for restricted agents
3. How to coordinate multiple debugging agents
4. How to handle limitations transparently

## Direct Integration

### Requirements

For a subagent to directly access debugging memory, it needs **one of**:

| Access Method | When to Use | Tools Needed |
|---------------|-------------|--------------|
| **MCP tools** (preferred) | Agent runs within Claude Code plugin context | MCP tool access |
| **CLI fallback** | Agent only has Bash, no MCP access | `Bash` |
| **Proxy** | Agent has no direct access | None (parent pre-queries) |

### Direct Integration via MCP (Preferred)

When the debugger plugin is installed, agents can call MCP tools directly:

- `debugger search` — search past incidents by symptom
- `debugger detail` — get full incident details
- `debugger store` — store new incidents
- `debugger patterns` — view extracted patterns

### CLI Fallback for Restricted Agents

For agents that only have Bash access (no MCP), use CLI commands:

```yaml
---
name: my-debugging-agent
description: Custom debugging agent with memory access
tools: ["Bash", "Read", "Grep"]
---
```

Add this to the agent's system prompt:

```markdown
## Debugging Memory

This project uses claude-code-debugger for debugging memory.

**Before investigating any bug:**
\`\`\`bash
npx @tyroneross/claude-code-debugger debug "<symptom description>"
\`\`\`

**Interpret results:**
- High confidence (>70%): Apply documented fix directly
- Medium confidence (40-70%): Use as investigation starting point
- Low/no match (<40%): Investigate fresh, document afterward

**After fixing a bug:**
Write incident JSON to `.claude/memory/incidents/INC_YYYYMMDD_HHMMSS_xxxx.json`

This ensures fixes are remembered for future similar issues.
```

> Note: CLI is the correct approach here — these agents lack MCP tool access.

## Proxy Integration

When subagents cannot directly access debugging memory (no Bash tool, sandboxed, external MCP), use proxy integration.

### Pre-Query Pattern

```
1. Parent agent searches memory
2. Parent includes results in subagent prompt
3. Subagent investigates with context
4. Parent stores any new findings
```

### Implementation

**Step 1: Search before spawning**

Use the `debugger search` MCP tool with the symptom description. If MCP is unavailable, fall back to CLI:

```bash
npx @tyroneross/claude-code-debugger debug "user login failing with 401"
```

**Step 2: Format context for subagent**

```markdown
DEBUGGING MEMORY CONTEXT:

Similar incidents found (2):
1. INC_20241215_auth_401 (confidence: 0.82)
   - Symptom: 401 errors after token refresh
   - Root cause: Token expiry check using wrong timezone
   - Fix: Use UTC for all token comparisons

2. INC_20241201_login_fail (confidence: 0.65)
   - Symptom: Intermittent login failures
   - Root cause: Race condition in session creation
   - Fix: Add mutex lock around session write

Relevant pattern: PTN_auth_token_issues
- Common causes: timezone mismatch, clock skew, token format changes
- Recommended: Check token generation and validation code paths
```

**Step 3: Include in subagent prompt**

```
Investigate the 401 error on user login.

[DEBUGGING MEMORY CONTEXT from above]

Given this prior knowledge, focus your investigation on:
1. Token handling differences from past incidents
2. Any recent changes to auth code
3. New causes not covered by past incidents
```

**Step 4: Store new findings**

After subagent completes, if new root cause discovered, write incident JSON:

```bash
mkdir -p .claude/memory/incidents
# Then write JSON file: .claude/memory/incidents/INC_YYYYMMDD_HHMMSS_xxxx.json
```

See `skills/debugging-memory/examples/incident-example.json` for full schema.

## Multi-Agent Coordination

### Parallel Assessment Pattern

When multiple domain-specific agents investigate simultaneously:

```
┌─────────────────────────────────────────────────────┐
│                 Parent Agent                         │
│  1. Search memory once                              │
│  2. Spawn assessors with relevant context           │
└──────────┬──────────┬──────────┬──────────┬────────┘
           │          │          │          │
           ▼          ▼          ▼          ▼
      ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
      │Database│ │Frontend│ │  API   │ │ Perf   │
      │Assessor│ │Assessor│ │Assessor│ │Assessor│
      └────┬───┘ └────┬───┘ └────┬───┘ └────┬───┘
           │          │          │          │
           └──────────┴──────────┴──────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │   Aggregate Results    │
              │   Store Unified Fix    │
              └───────────────────────┘
```

### Context Distribution

Give each assessor only relevant context:

```javascript
// Pseudo-code for context distribution
const memoryResults = searchMemory(symptom);

const dbContext = memoryResults.filter(i =>
  i.tags.some(t => ['database', 'prisma', 'sql', 'query'].includes(t))
);

const apiContext = memoryResults.filter(i =>
  i.tags.some(t => ['api', 'endpoint', 'auth', 'middleware'].includes(t))
);

// Spawn with filtered context
spawnAgent('database-assessor', { context: dbContext });
spawnAgent('api-assessor', { context: apiContext });
```

### Result Aggregation

After all assessors complete:

1. Rank by confidence score
2. Identify consensus (multiple agents pointing to same area)
3. Note conflicts for user decision
4. Store unified incident with attribution

```json
{
  "symptom": "App slow and returning wrong search results",
  "diagnosis": {
    "primary": {
      "domain": "database",
      "confidence": 0.85,
      "assessor": "database-assessor",
      "cause": "Missing index on search column"
    },
    "secondary": {
      "domain": "api",
      "confidence": 0.45,
      "assessor": "api-assessor",
      "cause": "Possible caching issue"
    }
  },
  "recommended_sequence": [
    "Add index to search column",
    "Monitor performance",
    "Investigate caching if still slow"
  ]
}
```

## Handling Limitations

### Detection

Identify when debugging memory cannot be fully utilized:

| Scenario | Detection | Action |
|----------|-----------|--------|
| No Bash tool | Check agent tool list | Use proxy pattern |
| External MCP | Agent uses MCP tools | Pre-query and inject |
| Third-party agent | Not in project agents/ | Inform user |
| Sandboxed | Environment restrictions | Pre-query and inject |

### User Communication Templates

**When using proxy integration:**
```
Note: I'm searching debugging memory before spawning the subagent,
as it doesn't have direct access. Found [X] relevant incidents
that I'll include in its investigation context.
```

**When no integration possible:**
```
Note: The [agent-name] agent cannot access this project's debugging
memory. I recommend running `/debugger "<symptom>"` first to check
for similar past issues before proceeding.
```

**When third-party agent used:**
```
Note: This third-party debugging tool operates independently of your
project's debugging memory. After it completes, consider documenting
any new findings with `/debugger-scan` to capture this session.
```

## Best Practices

1. **Always pre-query for restricted agents** - Don't let knowledge gaps form

2. **Include confidence scores** - Help subagents calibrate their investigation depth

3. **Attribute findings** - Track which agent discovered what for learning

4. **Store unified incidents** - One incident per bug, not one per agent

5. **Be transparent** - Always inform users when memory access is limited

6. **Use tags consistently** - Enable effective context filtering

7. **Document proxy patterns** - Note in incidents when proxy integration was used

## Example: Complete Multi-Agent Flow

```markdown
User: "The checkout is broken - showing wrong totals and timing out"

Claude Code:
1. Searches memory via `debugger search` MCP tool: "checkout wrong totals timeout"
   - Found: INC_20241210_checkout_math (confidence: 0.72)
   - Found: PTN_timeout_database (confidence: 0.58)

2. Spawns parallel assessors:
   - database-assessor: receives timeout pattern context
   - api-assessor: receives both contexts
   - frontend-assessor: receives math incident context

3. Informs user:
   "Checking debugging memory... Found 1 similar incident and 1 relevant pattern.
   Running parallel assessment across database, API, and frontend domains."

4. Aggregates results:
   - database-assessor: 0.80 confidence - slow cart total query
   - api-assessor: 0.45 confidence - possible middleware timeout
   - frontend-assessor: 0.70 confidence - floating point rounding

5. Reports:
   "Assessment complete. Primary issue: database query for cart totals
   (0.80 confidence). Secondary: frontend rounding display (0.70).
   This matches past incident INC_20241210_checkout_math.

   Recommended fix sequence:
   1. Optimize cart total query (add index)
   2. Fix frontend decimal display
   3. Increase API timeout as buffer"

6. After fix, stores unified incident with all findings
```
