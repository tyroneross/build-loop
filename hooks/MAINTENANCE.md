# Lifecycle and explicit maintenance

Claude and Codex startup restore provenance, scoped architecture state, coordination and pending closeout context. Startup does not install Git hooks, prune executable caches, load models, bootstrap memory or run full worktree/lesson scans. Safety policy and before-write checks remain registered.

The existing maintenance entry points remain available when their workflows need them:

- Initial setup or explicit reconciliation: `python3 scripts/install_git_hooks.py --install --repo /absolute/repo`, `python3 scripts/artifact_guard.py --install-hook`, `python3 scripts/rally_point/install_git_hook.py --repo /absolute/repo`.
- Memory setup: `python3 scripts/install_memory.py`. Existing content is preserved.
- First retrieval use: the retrieval client owns backend availability; `bash hooks/session-start-retrieval.sh` remains an explicit warmup action. Concurrent launches use one PID-file transaction. Missing backends remain unavailable rather than being downloaded.
- Worktree assessment: `bash hooks/session-start-worktree-gc.sh` reports only. It does not authorize disposal.
- Pending closeout: `python3 scripts/closeout_baton.py --workdir /absolute/repo`. One writer owns reconciliation. Partial/error results retain the baton; a completion receipt names the exact generation. Reminders are retained until the workflow resolves them, not consumed merely because a hook printed them.
- Extension review: `python3 scripts/extensions_pending_count.py` and the existing proposal-review workflow.
- Cache maintenance: `python3 scripts/prune_plugin_cache.py --help` describes preview/apply. No cache action runs automatically at launch. Host trust belongs to the native review flow; these scripts do not grant trust.

The script names beginning `session-start-` remain callable for compatibility, but removed handlers are absent from both lifecycle manifests. Other models can call these deterministic commands directly. Automatic events are supported only where a host has a verified adapter; model choice does not change the JSON payload contract.
