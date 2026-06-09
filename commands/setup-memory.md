---
description: "Set up or verify the build-loop central memory store (build-loop-memory). Guided and idempotent — copies only the packaged public seed, never overwrites your content. Safe to re-run."
argument-hint: "[--check]"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

{{#if ARGUMENTS}}
{{#contains ARGUMENTS "--check"}}
Run `python3 "${CLAUDE_PLUGIN_ROOT:-$PWD}/scripts/install_memory.py" --check` and report the memory store status: which template files and which lanes (`indexes/`, `projects/`) exist vs are missing. Do not write anything.
{{else}}
Run `python3 "${CLAUDE_PLUGIN_ROOT:-$PWD}/scripts/install_memory.py" --guided` and report what was seeded vs already present, then surface the installer's printed next steps to the user.
{{/contains}}
{{else}}
Run `python3 "${CLAUDE_PLUGIN_ROOT:-$PWD}/scripts/install_memory.py" --guided` and report what was seeded vs already present, then surface the installer's printed next steps to the user.
{{/if}}

---

## Reference

**Guided setup (idempotent — public seed only, no personal content):**
```
/build-loop:setup-memory
```

**Status check (no writes):**
```
/build-loop:setup-memory --check
```

The store lives at `memory_store_root()`. Resolution: an env override (`$BUILD_LOOP_MEMORY_STORE_ROOT` / `$BUILD_LOOP_MEMORY_ROOT` / `$AGENT_MEMORY_ROOT`), else a pre-existing legacy `~/dev/git-folder/build-loop-memory` if it is already on disk, else the neutral fresh-install default `~/.build-loop-memory`. On a fresh machine it is also bootstrapped automatically on session start by `hooks/session-start-memory.sh` when entirely absent; this command is the manual / repair path and the way to see what's present. Research packets, project lessons, decisions, and debugging memory all persist under this store.
