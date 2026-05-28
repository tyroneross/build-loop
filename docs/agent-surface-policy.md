# Agent Surface Policy

Build Loop exposes a small public entrypoint set and keeps implementation
helpers internal.

## Public Entry Points

These are the only skills that should appear as user-facing Build Loop choices:

- `build-loop` — main multi-step code workflow
- `debug-loop` — root-cause debugging workflow
- `optimize` — metric-driven optimization
- `research` — repo-grounded pre-build research
- `knowledge` — durable decisions and lessons

## Host Rules

Codex and ChatGPT use `.codex-plugin/plugin.json`, which points at
`./codex-skills`. That directory contains wrapper skills for the public
entrypoints only. The full `./skills` tree still ships in the package for
internal references.

Claude Code keeps `.claude-plugin/plugin.json` pointed at `./skills` because
commands and orchestrator agents load internal skills by qualified name. Helper
skills must set `user-invocable: false`; the public entrypoints set
`user-invocable: true`.

Cursor and other AGENTS.md-style tools should treat `AGENTS.md` plus this file
as the routing contract. Start from the public entrypoints above. Read helper
files under `skills/` only when the active entrypoint, a command, or an
orchestrator instruction explicitly references them.

## Cache Hygiene

Plugin cache pruning is explicit and auditable:

```bash
python3 scripts/prune_plugin_cache.py --source . --apply
```

The command keeps the current host manifest version and deletes older verified
cache directories for the same plugin. It checks both `.codex-plugin/plugin.json`
and `.claude-plugin/plugin.json` by default. Use `--host codex` or
`--host claude` for a single host.
