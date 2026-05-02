# Debugger Detail

Load full details for a specific incident or pattern from debugging memory.

## Usage

```
/debugger-detail <ID>
```

Where `<ID>` is an incident ID (INC_*) or pattern ID (PTN_*).

## What to do

1. Run the CLI command to load the full data:
   ```bash
   npx @tyroneross/claude-code-debugger detail $ARGUMENTS
   ```

2. Present the result to the user in a readable format:
   - For incidents: show symptom, root cause, fix approach, file changes, verification status
   - For patterns: show detection signature, solution template, success rate, usage history

3. If the ID is from a progressive search result, explain how this detail relates to the user's current issue.

## Examples

- `/debugger-detail INC_API_20260215_143022_a1b2` — loads full incident
- `/debugger-detail PTN_REACT_HOOKS` — loads full pattern
