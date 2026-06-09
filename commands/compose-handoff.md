---
description: "Compose a complete build-loop handoff from current run state. Optional --launch starts a fresh session in the stable checkout with the handoff injected."
argument-hint: "[--launch] [--workdir DIR]"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

{{#if ARGUMENTS}}
{{#contains ARGUMENTS "--launch"}}

## Step 1 — Compose the handoff doc

Run from the STABLE checkout (not a worktree):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff --workdir "$PWD" --output .build-loop/handoff-latest.md
```

Report the sources used and any warnings from stderr.

## Step 2 — Identify the stable checkout path

Run:
```bash
git worktree list --porcelain | grep "^worktree" | head -1 | awk '{print $2}'
```

This is the canonical repo root. The new session MUST open there, not in any `.build-loop/worktrees/` sub-path (those may be GC'd).

## Step 3 — Launch a fresh session with handoff injected

Determine the host. Then:

**Claude Code host** — use `claude --print` in a new terminal:
```bash
HANDOFF=$(cat .build-loop/handoff-latest.md)
echo "RESUME_FROM_HANDOFF: The following handoff document captures the current build-loop run state. Use it to resume work.\n\n${HANDOFF}" | claude --print
```
If the trust prompt fires on the new session, type `y` and press Enter.

**Codex host** — pass the doc as context:
```bash
codex --context "$(cat .build-loop/handoff-latest.md)" "Resume from the attached handoff document."
```

**Unknown / unsupported host** — print the path and instructions:
```
Handoff doc written to: .build-loop/handoff-latest.md
To resume: open a fresh session at <stable-checkout-path>, share the handoff doc as your opening message, and load the build-loop:build-loop skill.
```

In all cases: exit 0 (degrade gracefully — the doc is the deliverable, launch is a convenience).

{{else}}

## Compose the handoff document

Run:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff --workdir "$PWD"
```

Present the full output as your response. Also report:
- Sources used (from the `sources` field if `--json` was used)
- Any warnings

{{/contains}}
{{else}}

## Compose the handoff document

Run:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff --workdir "$PWD"
```

Present the full output. Also note:
- What sources were found vs missing
- Any errors/warnings

To launch a fresh session after handoff, re-run with `--launch`:
```
/build-loop:compose-handoff --launch
```

{{/if}}

---

## Reference

**Emit to stdout (default)**
```
/build-loop:compose-handoff
```

**Write to file**
```
/build-loop:compose-handoff --workdir /path/to/repo
```
(Use `--output` flag in the script directly for file output.)

**Emit + launch fresh session**
```
/build-loop:compose-handoff --launch
```

**JSON envelope (for programmatic use)**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff --workdir "$PWD" --json
```
Returns `{document, sources, errors, ts}`.

See `skills/handoff/SKILL.md` for the full 8-section template, source table, and host-agnostic design notes.
