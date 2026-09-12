<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Code annotations

Use one standalone comment when a short statement of intent will materially reduce future code-reading time:

```text
BL:<kind> | <human-readable summary> | <comma-separated keywords>
```

Examples:

```python
# BL:invariant | Fresh recovery preserves the prior execution before clearing its active pointer | recovery,state
```

```typescript
// BL:boundary | Converts provider events into the app's stable message model | provider,events,messages
```

## Rules

- Write a complete, concrete summary that remains useful without nearby code.
- Use a short lower-case kind such as `purpose`, `boundary`, `flow`, `invariant`, `risk`, or a more precise human-readable kind. Kinds use open vocabulary.
- Add a few terms people will actually search. Do not repeat words already obvious from the file or symbol name.
- Annotate durable intent, boundaries, invariants, and non-obvious risks. Skip ordinary implementation detail and line-by-line narration.
- Keep one annotation on one line. The marker carries no opaque identifier.

## Find and index

`rg -n 'BL:'` is the universal fast path. The native architecture scan also writes `.build-loop/architecture/annotations.json` from the same source-file inventory and ignore rules:

```bash
python -m build_loop.architecture --repo "$PWD" scan --json
python -m build_loop.architecture --repo "$PWD" annotations recovery --json
```

The annotation index supplies semantic intent. Navgator and Build Loop's structural graph continue to supply components, imports, connections, traces, and blast radius. Use both lenses for unfamiliar code.
