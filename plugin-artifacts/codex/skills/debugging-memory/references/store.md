<!-- PROVENANCE: op=store reference for build-loop:debugging-memory. Native core refreshed from @tyroneross/claude-code-debugger v1.9.0 at 74cc2cc96ce7c212a81d41b85143dc1fc9094bc3 on 2026-08-25. -->

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Native Debugging Incident Storage

Persist verified fixes to the same `.claude/memory/` store searched by Build Loop's native debugger.

## Required payload

Create a temporary JSON file with:

```json
{
  "symptom": "Exact user-visible failure or error",
  "root_cause": {
    "description": "Technical cause and first controllable cause",
    "category": "logic",
    "confidence": 0.95
  },
  "fix": "What changed and why",
  "verification": "verified",
  "tags": ["build-loop", "project", "backend", "typescript"],
  "files_changed": ["path/to/file.ts"]
}
```

Then run:

```bash
node "${CLAUDE_PLUGIN_ROOT}/bin/build-loop-debugger.js" store \
  --input /path/to/incident.json --workdir "$PWD"
```

The command validates and writes `.claude/memory/incidents/<incident-id>.json`, then updates the JSONL and keyword indexes in the same memory root. Delete the temporary input after a successful store.

## Quality requirements

- Preserve the exact symptom and error class.
- Explain the root cause, not only the failing line.
- Record the implemented fix and changed files.
- Use `verified` only when the reproduction and relevant regression tests pass.
- Include `build-loop`, project, layer, technology, and symptom tags when applicable.

Target a quality score of at least 75%. Do not store speculative diagnoses as verified incidents.

## After storing

1. Confirm `file_path` exists in the JSON response.
2. Search the symptom again and confirm the new incident is discoverable.
3. Keep `.build-loop/issues/` for unresolved/executable work; resolved history belongs only in the structured debugger store.
