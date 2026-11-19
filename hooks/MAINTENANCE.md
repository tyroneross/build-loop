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

Build Loop does not register its old prompt classifier or its Skill denial hook.
The obsolete `route-guard.sh` and `route_guard.py` implementations have been removed.
The host's routing instructions choose the workflow. The old hooks shared one
verdict file across sessions in a checkout and could deny explicitly requested
brainstorming using a different session's prompt. No safety or commit gate
depends on that verdict.

## SessionStart: one registration with bounded recovery

`hooks.json` and `codex.json` each register one SessionStart command, generated
by `scripts/session_start_hook_command.py` (`--write` / `--check`). A fresh
`bin/native/bl` handles the event. Missing, stale or explicitly disabled binaries
use the generator's recovery-only `--fallback` mode: tool-state first, then the
other four legacy commands concurrently, each retaining its original timeout.
One host command emits one merged JSON response in either mode. The total host
budget is eight seconds. Cancellation terminates active foreground groups;
normal completion preserves deliberately detached workers.

The five additional skip-only shell registrations are removed. Legacy shell
implementations remain recovery paths and parity oracles. Their removal requires
a replacement for missing/stale/off operation. The native trust check reads
recorded grants and command fingerprints directly but never writes Codex grants.
Codex must review the changed registration; this change grants no trust.

Architecture edit observers run on successful PostToolUse Edit/Write events. The historical `pre-edit-architecture.sh` filename remains for direct-call compatibility; it no longer runs before the write.

## Registration inventory (2026-11-15)

> Historical for Codex: since 2026-11-17 `hooks/codex.json` and `.codex/hooks.json`
> are empty manifests (Build Loop ships no automatic Codex hooks; AGENTS.md "Codex
> wiring"). The X and R columns below record what those surfaces held before; only
> the C (Claude) rows are live.

Inventory baseline: `1f951408`. `hooks/hooks.json` is the Claude plugin surface;
`.codex-plugin/plugin.json` selects `hooks/codex.json` for Codex. The repository
`.codex/hooks.json` is a separate, trusted project configuration: it must work
without an installed plugin. Identical purposes across these surfaces do not
prove an entry can be deleted; Codex can load both surfaces together.

The table covers registered responsibilities. C = Claude plugin, X = Codex plugin,
R = repository Codex. The five C/X startup responsibilities share one registration;
R retains its project-only entries. Current totals are 13 C, 12 X and 7 R handlers
(32 total, down from 44). Paths below include internal substeps as well as host
entry points; their consumers remain part of the runtime dependency boundary.

| Event / matcher | Surface | Purpose and source | Execution and decision |
|---|---|---|---|
| SessionStart / all | C, X | Restore combined context: `bl hook session-start`; generator `scripts/session_start_hook_command.py` | Keep native entry. Rust orchestration still calls Python and shell; it is not interpreter-free. |
| SessionStart / all | C, X, R | Provenance: `hooks/session-start-tool-state.sh` | Keep. C/X skip shell when native is fresh; R directly uses shell/Python. |
| SessionStart / all | C, X | Architecture freshness: `hooks/session-start-architecture.sh` | Keep fallback. Native and shell start Python architecture maintenance. |
| SessionStart / all | C, X | Coordination restore/watch: `hooks/session-start-rally-point.sh` | Keep fallback. Native path still invokes Python Rally helpers. |
| SessionStart / all | C, X, R | Pending closeout context: `hooks/closeout.sh session-start` → `scripts/stop_closeout.py` | Keep. C/X native path still uses Python closeout. |
| SessionStart / all | C, X | Hook trust warning: `hooks/session-start-codex-hook-trust.sh` → `scripts/codex_hook_trust_check.py` | Keep supported failure guidance, not automatic trust approval. Fresh native SessionStart reads recorded grants and fingerprints directly; shell recovery still uses Python. |
| SessionStart / all | R | Codex presence/watch: `scripts/rally_point/session_probe.py` | Keep project-only fallback; Python. Plugin coexistence requires identity/idempotency verification. |
| PreToolUse / Edit\|Write | C, X | Before-write coordination: `hooks/pre-edit-rally-point.sh` | Keep. Fresh native fast path, supported shell/Python handoff. |
| PreToolUse / Bash | C, X | Same coordination hook for shell writes | Keep separate matcher; not a duplicate Edit/Write event. |
| PreToolUse / Bash | C, X | Credential, autonomy, dependency and commit gates: `scripts/hooks/pre_bash_dispatch.sh` | Keep. Native bounded supervision; the existing dispatcher and Python policy rules remain the single policy authority. Missing/stale/off binaries use the bounded Perl supervisor. |
| PreToolUse / all | C, X | Trace start: generated `scripts/trace_hook_command.py` command | Keep. Native UTF-8 trace parsing, redaction and run attribution. Python recovery is launcher-only when native dispatch is unavailable; events never hand off from Rust. |
| PostToolUse / Edit\|Write | C, X | Invalidate/refresh after successful edits: `hooks/pre-edit-architecture.sh` | Keep. Historical name supports direct calls; shell/Python worker. |
| PostToolUse / Bash | C, X | Reconcile successful pushes: `hooks/post-push-closeout.sh` | Keep. Shell/Python target parsing and closeout; not every Bash call is a push. |
| PostToolUse / all | C, X | Trace end | Keep. Native UTF-8 trace parsing, redaction and run attribution. Python recovery is launcher-only when native dispatch is unavailable; events never hand off from Rust. |
| PostToolUseFailure / all | C | Trace error | Keep Claude event. Codex manifest intentionally lacks this unsupported event. |
| Stop / all | C, X | Completed-run entry and criteria: `scripts/hooks/stop_finalize.sh` | Keep. Shell/Python; report-phase and session checks prevent closing active runs. |
| Stop / all | C, X, R | Turn closeout: `hooks/closeout.sh stop` → `scripts/stop_closeout.py` | Keep. Distinct from run-entry finalizer. |
| Stop / all (retired) | C, X | Tracked-dirt advisory: `scripts/commit_state_check.py` | Removed automatic registration. Keep explicit CLI: no session/phase ownership or mandatory denial; repository dirt can belong to a live peer. |
| Stop / all | C, X, R | Shared transcript consumers: `hooks/stop-transcript-sweep.sh all` | Keep one registration per surface. One bounded worker feeds decisions, corrections, findings, traces and cost ledger. Python consumers remain. |
| Stop / all | R | Release session coordination: `scripts/agent_rally.py stop` | Keep existing per-turn coordination behavior; Python identity helper also runs. |
| SessionEnd / all | C, X, R | Retrospective/miner: `scripts/hooks/session_end_retro_sweep.py` | Keep at session end. Removed R's incorrect Stop registration; worker retains Codex transcript discovery when no explicit path exists. |

### Removal evidence and preserved behavior

The project Codex retrospective registration was the confirmed lifecycle defect:
`Stop` is a turn boundary, while the worker documents session-end processing and
can discover a real Codex transcript even with stdin closed. Running it each turn
therefore was neither dead nor a harmless no-op. Its registration now uses
`SessionEnd`, matching both plugin manifests. No worker was deleted. The separate
Stop transcript sweep has different consumers and stays registered.

The five legacy startup implementations are active recovery paths when the binary is
missing, stale, or `BUILD_LOOP_NATIVE_HOOKS=off`. Do not apply the earlier removal
recipe until distribution and native coverage replace those contracts. Likewise,
unregistered scripts may be explicit maintenance commands or parity oracles;
absence from these manifests is not removal evidence.

### Host and native-runtime coverage limits

`bin/build-loop-install.js` accepts only `all|claude|codex`, and `all` expands to
Claude and Codex. `scripts/host_capabilities.py` additionally recognizes Gemini,
OpenCode and Cursor for wake/resume capabilities; this does **not** install or
prove hook adapters for them. No Gemini, Cursor, OpenCode, Copilot or Antigravity
hook registration is established by these three manifests. Host-neutral skills
and explicit commands are distinct from automatic hook delivery.

The native core is not an interpreter-free replacement for every Build Loop
service. Credential/command policy, closeout/Learn, transcript maintenance and
cold/legacy Rally operations retain their existing owners. Native startup no
longer requires Python or Perl globally: unavailable workers degrade independently
while native provenance and trust context remain available. A stale/missing/off
binary uses one bounded recovery registration.

Native trace accepts UTF-8 host events bounded to 64 MiB and 256 container levels.
Finite numeric fingerprints, supported timestamps, run/session ownership, redaction,
shared locks and rotation stay native. Invalid JSON/encoding, non-string identity
metadata and non-finite content safely produce no record;
`BL_TRACE_EXIT=1` exposes static reasons without payload text. This is an explicit
coverage limit, not evidence that those events never occurred. Standalone native
trace does not require Python or a Python source marker. Unusable timestamps retain
the event at observation time with a null run ID and the static opt-in reason
`timestamp_unavailable`; they never confer run membership. Saved state accepts one
leading UTF-8 BOM, with the same byte/depth bounds; event input does not accept a BOM.

Model-independent operation requires host adapters to normalize event/tool names,
stdin, transcript locators, root variables and response envelopes before invoking
the common runtime. A model name must not select a lifecycle contract. Pin fixture
contracts for each supported host and verify real host delivery separately.

Pre-change `rosslabs-lsp compat --section agent-hooks` and `hooks doctor` completed:
0 errors, 4 warnings, 10 informational findings. The records support Codex
SessionEnd and distinguish turn Stop, but report installed-version drift and
`live_verified false`. This source inventory and regression test do not establish
installed-host acceptance. The Codex trust review must cover changed commands or
registration positions; this change does not approve them automatically.

The automatic commit-state advisory was also retired from both plugin Stop
surfaces. `scripts/commit_state_check.py:164-201` checks only whether state or
optimize storage exists before reporting repository-wide tracked dirt. It does
not establish completed work or session ownership, never denies, and can report
a peer's unfinished files. Explicit CLI checks remain supported; orchestrator
finalization owns scoped commit verification. No performance measurement is
claimed for this registration removal.

## Retired AppPulse forwarding aliases

The unregistered, forwarding-only AppPulse hook names were removed after checking
tracked source and hidden host configurations for consumers. Use the canonical
entry points instead:

- `hooks/pre-edit-apppulse.sh` → `hooks/pre-edit-rally-point.sh`
- `hooks/session-start-apppulse.sh` → `hooks/session-start-rally-point.sh`
- `hooks/test_apppulse_hooks.sh` → `hooks/test_rally_point_hooks.sh`

The canonical implementations and registered coordination behavior remain.
