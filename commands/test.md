---
description: "(Advanced override — `/build-loop:run` auto-routes here on 'test plugin'/'validate plugin' language; use this to force the mode.) Run build-loop's plugin-tests static-analysis suite against the current repo (skill resolution, manifest, MCP, triggers, bridges, agent surfaces, cache pruning)"
allowed-tools: Bash, Read
argument-hint: "[--strict] [<test-name>]"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

Load the `build-loop:plugin-tests` skill.

{{#if ARGUMENTS}}
Args: `{{ARGUMENTS}}`

If `<test-name>` is one of `skill-resolution`, `plugin-manifest`, `mcp-registration`, `trigger-phrases`, `bridge-preflight`, `agent-surface-policy`, `cache-prune`, run only that single script. Otherwise treat the args as flags for the runner.

Example:
- `/build-loop:test skill-resolution` — run only the namesake-collision test
- `/build-loop:test --strict` — exit non-zero on any soft warning (CI gate)
{{else}}
Run the full plugin-test suite from the repo root:

```bash
for t in scripts/test_skill_resolution.py scripts/test_plugin_manifest.py \
         scripts/test_mcp_registration.py scripts/test_trigger_phrases.py \
         scripts/test_bridge_preflight.py scripts/test_agent_surface_policy.py \
         scripts/test_prune_plugin_cache.py scripts/test_prune_codex_plugin_cache.py; do
  echo "=== $(basename $t) ==="
  python3 "$t" || EXIT=1
done
exit ${EXIT:-0}
```

Surface failures with the script name + test name + the assertion message. On any failure, suggest the fix path documented in the test's docstring.
{{/if}}
