# Pattern Extraction Guide

Patterns are reusable solution templates automatically extracted from similar incidents.

## What Are Patterns?

When 3+ incidents share similar symptoms and fixes, the memory system can extract a pattern. Patterns represent proven solutions with higher confidence than individual incidents.

**Pattern vs Incident:**
| Aspect | Incident | Pattern |
|--------|----------|---------|
| Source | Single debugging session | Multiple similar incidents |
| Confidence | Variable (0.5-0.95) | High (0.9+) |
| Reusability | Context-specific | Generalized |
| Priority | Lower in search | First checked |

## Pattern Structure

```json
{
  "pattern_id": "PTN_REACT_HOOKS_DEPENDENCY",
  "name": "React Hook Dependency Array Fix",
  "description": "Fixes issues caused by unstable references in React hook dependency arrays",
  "detection_signature": [
    "infinite render",
    "useEffect loop",
    "useMemo dependency",
    "useCallback",
    "re-render",
    "reference unstable"
  ],
  "applicable_to": ["coder", "refactorer"],
  "solution_template": "1. Identify the hook with unstable dependencies\n2. Wrap callbacks in useCallback\n3. Wrap computed values in useMemo\n4. Update dependency arrays with stable references",
  "code_example": "// Before: unstable callback\nuseEffect(() => {...}, [callback]);\n\n// After: stable callback\nconst stableCallback = useCallback(callback, [deps]);\nuseEffect(() => {...}, [stableCallback]);",
  "tags": ["react", "hooks", "performance", "useEffect", "useMemo", "useCallback"],
  "related_patterns": ["PTN_REACT_STATE_BATCHING"],
  "success_rate": 0.92,
  "usage_history": {
    "total_uses": 15,
    "successful_uses": 14,
    "by_agent": {"coder": 12, "refactorer": 3},
    "recent_incidents": ["INC_20241220_...", "INC_20241218_..."]
  },
  "caveats": [
    "May not apply if the callback intentionally needs fresh closure",
    "Consider whether memoization overhead is worth it for simple callbacks"
  ]
}
```

## Automatic Extraction

Patterns are extracted automatically when conditions are met:

### Extraction Criteria

1. **Minimum incidents**: 3+ similar incidents required
2. **Similarity threshold**: 70%+ symptom similarity
3. **Fix consistency**: Similar approaches across incidents
4. **Success rate**: Fixes verified as working

### Triggering Extraction

Use the `debugger patterns` MCP tool to preview and extract patterns.

CLI alternative (if MCP unavailable):

```bash
# Preview potential patterns
npx @tyroneross/claude-code-debugger patterns

# Extract and store
npx @tyroneross/claude-code-debugger patterns --extract
```

Or programmatically:

```typescript
import { extractPatterns } from '@tyroneross/claude-code-debugger';

const patterns = await extractPatterns({
  min_incidents: 3,
  min_similarity: 0.7,
  auto_store: true
});
```

## Pattern Matching

During debugging, patterns are checked first:

1. System extracts keywords from symptom
2. Matches against `detection_signature` of all patterns
3. Patterns with 90%+ match are presented first
4. If no pattern matches, falls back to incident search

## Using Patterns

When a pattern matches your bug:

### 1. Review the Pattern

Check:
- `description`: Does it match your situation?
- `solution_template`: Understand the approach
- `caveats`: Note any limitations

### 2. Apply the Solution

Follow the `solution_template` steps:
- Adapt for your specific codebase
- Use `code_example` as reference
- Check `related_patterns` for additional context

### 3. Verify

- Confirm the fix works
- Run relevant tests
- The incident still gets stored, linked to the pattern

## Pattern Quality

Patterns track their own reliability:

- **success_rate**: Percentage of successful applications
- **usage_history**: How often used, by which agents
- **recent_incidents**: Links to incidents that used this pattern

Low success rate patterns may indicate:
- Over-generalized pattern
- Context-dependent solution
- Need for refinement or splitting

## Creating Patterns Manually

While automatic extraction is preferred, create patterns manually for well-understood solutions:

```typescript
import { storePattern } from '@tyroneross/claude-code-debugger';

await storePattern({
  pattern_id: 'PTN_CATEGORY_NAME',
  name: 'Human Readable Name',
  description: 'What this pattern solves',
  detection_signature: ['keyword1', 'keyword2'],
  applicable_to: ['coder'],
  solution_template: 'Step-by-step approach',
  tags: ['tag1', 'tag2'],
  success_rate: 1.0,
  usage_history: {
    total_uses: 0,
    successful_uses: 0,
    by_agent: {},
    recent_incidents: []
  }
});
```

## Best Practices

1. **Let extraction happen naturally** - Don't force patterns from few incidents
2. **Review extracted patterns** - Ensure they generalize well
3. **Update caveats** - Add warnings when patterns fail
4. **Monitor success rates** - Refine patterns with low success
5. **Split over-broad patterns** - If a pattern covers too many cases, split it
