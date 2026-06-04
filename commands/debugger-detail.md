---
description: "Load full build-loop incident or pattern details from native debugging memory"
allowed-tools: Read, Grep
argument-hint: "<INC_* or PTN_* id>"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Debugger Detail

Load full details for a specific incident or pattern from debugging memory.

## Usage

```
/debugger-detail <ID>
```

Where `<ID>` is an incident ID (INC_*) or pattern ID (PTN_*).

## What to do

1. Search `.build-loop/issues/` for the requested incident or pattern ID.

2. Present the result to the user in a readable format:
   - For incidents: show symptom, root cause, fix approach, file changes, verification status
   - For patterns: show detection signature, solution template, success rate, usage history

3. If the ID is from a progressive search result, explain how this detail relates to the user's current issue.

## Examples

- `/debugger-detail INC_API_20260215_143022_a1b2` — loads full incident
- `/debugger-detail PTN_REACT_HOOKS` — loads full pattern
