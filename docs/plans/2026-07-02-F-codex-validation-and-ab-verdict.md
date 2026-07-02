<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Codex validation and A/B inject-probe verdict

**Date:** 2026-07-02
**Branch:** `codex/inject-probe`
**Codex implementation commit:** `92e3cd9f90c190dbc19ed2d81b3c26b74cf68d30`
**Handoff:** `fact_906_18be6539037ab9f8`

## Bottom line

Use the Codex probe as the base. It is committed on current `main`, has real
socket-response validation, and stamps readiness into the hook-facing
`session_probe` envelope plus the persisted `rally-start` payload next to
`capability_level`.

Claude's probe is directionally correct and has clear degradation wording, but
it is still uncommitted in `build-loop.worktrees/inject-probe` and only logs
readiness from the shell hook. If grafting anything, graft the explicit
`should_use_handoff()` helper/doctrine wording, not the connect-only socket
liveness check.

## RCA re-derivation

### Lane A - addressed handoffs stayed open

Confirmed. ARP closes a handoff only when a later `resolve`, `receipt`, or
`artifact` references the handoff event id and passes target correlation
(`agent-rally-point/crates/rally-cli/src/store.rs:1739-1752`). Open handoffs are
projected by excluding only handoffs that satisfy that closer predicate
(`store.rs:1963-1966`). `rally ack` writes `decision/coordination:ack`
(`lib.rs:11227-11260`), so it cannot close a handoff. The build-loop fix in
`scripts/rally_point/lifecycle.py` now calls `rally say receipt --ref`.

### Lane C - worktree teardown orphaned file claims

Confirmed. `collapse_run.py::_remove_worktree` previously removed the worktree
without releasing file-scoped Rally claims. The fix releases claims after a
successful removal and also on idempotent "already gone" cleanup
(`scripts/collapse_run.py:228-247`). The path-boundary filter is correct:
exact `file:<wt>` or prefix `file:<wt>/`, not bare substring matching
(`collapse_run.py:152-170`). ARP has expired-lease logic in Rust
(`claim_authority.rs`, `reaper.rs`), but the installed 0.1.3 CLI rejects
`rally room --reap-stale`; surgical `rally say release --ref` is the right
build-loop-side path.

Residual caveat: the implementation is hardcoded to `_RALLY_TOOL =
"claude_code"`. That is fine for Claude-owned dispatch teardown, but should be
made host/tool-aware before Codex or another host uses the same teardown path.

### Lane E-a - stale on-PATH Rally binary was not visible

Confirmed. `binary_fetch.py` now exposes the pinned version via `--print-pin`,
and `hooks/session-start-rally-point.sh` compares that to `rally version` on
PATH in a fail-open warning block. That would have surfaced the observed
version drift without blocking session start.

### Lane E-b - discovery preferred stale sibling builds

Confirmed. `discovery_bridge._rally_binary_candidates` now orders candidates as
env override, cached pinned binary, PATH, then sibling dev builds. That keeps
the pin authoritative while preserving `AGENT_RALLY_BINARY` for local ARP
development.

### Lane E-c - worktree hook path used a non-authoritative parse

Confirmed. `install_git_hooks.py::_git_hooks_dir` now asks git directly via
`git rev-parse --git-path hooks`. That is the correct source of truth for
linked worktrees and `core.hooksPath`; the manual `.git` parse is now only a
fallback.

## Independent opinion on the five fixes

1. **A is correct.** Receipt is the right close primitive for "handoff acted
   on"; using `resolve` for every addressed handoff would overstate completion
   semantics.
2. **C is correct with a host-parity follow-up.** Best-effort release is safer
   than trying to repurpose unavailable/native reaper CLI paths, but the
   hardcoded tool id should not become a cross-host contract.
3. **E-a is correct.** Warning-only is the right failure mode for startup hooks.
4. **E-b is correct.** Pin/cache before sibling build fixes production drift;
   env override keeps intentional local development possible.
5. **E-c is correct.** Git should own hook path resolution.

## Independent opinion on design specs D/E

**D - session identity and liveness cascade:** right direction, but sequence is
critical. Unify on the ARP session id and make the lease per-runtime-fresh
before wiring child actors. A cascade keyed on a different id space will
entrench the current split. `child_alive = min(own, parent_session_alive)` is
sound when parent state comes from an explicit stop/tombstone or a policy-backed
stale verdict; do not treat transient parent `Unknown` as dead. The headless
carve-out must be an explicit registration field (`independent_lifetime=true`)
with tests for CI/launchd/detached cases.

**E - inject portability:** detect plus degrade to handoffs is the right first
fix. Do not pin/fetch ptyd now; there is no pinned ptyd release asset/sha256
surface to fetch. Add ARP-level `inject_available`/`recommended_backend` later
so agents stop duplicating host checks.

## Codex implementation

Files:
- `scripts/rally_point/inject_readiness.py`
- `scripts/rally_point/test_inject_readiness.py`
- `scripts/rally_point/session_probe.py`
- `scripts/rally_point/test_session_probe.py`
- generated architecture artifacts updated by the commit hook

Behavior:
- Reports exactly `{tmux, ptyd_socket_live, ptyd_bin, inject_available,
  recommended_backend}`.
- Uses stdlib only.
- Does not start tmux, ptyd, or any daemon.
- Treats ptyd socket as live only if it answers `pane.list`.
- Emits readiness in the `session_probe --json` envelope and in the persisted
  `rally-start` payload beside `capability_level`.

Validation:
- `uv run --with pytest pytest scripts/rally_point/test_inject_readiness.py scripts/rally_point/test_session_probe.py -q`
  - `35 passed`
- `bash docs/plans/2026-07-02-rally-lifecycle-validate.sh`
  - `SUMMARY: PASS=12 FAIL=0 WARN=0`
  - `pytest suite green (302 passed)`
- Live host probe:
  - normal PATH: `inject_available=true`, `recommended_backend=tmux`
  - `PATH=/usr/bin:/bin`: `inject_available=false`, `recommended_backend=handoff`
- `python3 scripts/rally_point/session_probe.py --workdir . --tool codex --mode hook --json`
  - includes `capability_level=full`
  - includes `inject_readiness.inject_available=true`

## Claude implementation

Status:
- Worktree `build-loop.worktrees/inject-probe`
- Branch `claude/inject-probe`
- Base commit `d2fa420`
- Dirty/uncommitted files:
  - `hooks/session-start-rally-point.sh`
  - `scripts/rally_point/inject_readiness.py`
  - `scripts/rally_point/test_inject_readiness.py`

Validation:
- `python3 -m unittest scripts.rally_point.test_inject_readiness`
  - `9 tests OK`
- `uv run --with pytest pytest scripts/rally_point/test_inject_readiness.py -q`
  - `9 passed`
- `bash docs/plans/2026-07-02-rally-lifecycle-validate.sh`
  - `SUMMARY: PASS=12 FAIL=0 WARN=0`
  - `pytest suite green (305 passed)`
- Live host probe:
  - normal PATH: `inject_available=true`, `recommended_backend=tmux`
  - `PATH=/usr/bin:/bin`: `inject_available=false`, `recommended_backend=handoff`
- `session_probe --json` on Claude worktree did not include `capability_level`
  or `inject_readiness`; readiness is only echoed from the shell hook.

## A/B verdict

| Criterion | Codex | Claude | Verdict |
|---|---|---|---|
| Correctness | Requires ptyd `pane.list` response; stamps session envelope/payload | Connect-only ptyd socket check; hook stderr only | Codex |
| Fresh-laptop degradation | Passes exact `PATH=/usr/bin:/bin` sim | Passes exact `PATH=/usr/bin:/bin` sim | Tie |
| Cost | One PATH check pair plus optional bounded socket roundtrip | One PATH check pair plus bounded socket connect | Tie; Codex cost is still acceptable |
| Clarity | Shorter module, stronger persisted integration | Clearer prose and `should_use_handoff()` helper | Claude for prose/helper |
| Merge readiness | Committed on current `main` tip | Uncommitted, older base | Codex |

Final recommendation: keep Codex as the merge base. Optionally graft Claude's
`should_use_handoff()` helper and doctrine comments after merge, but preserve
Codex's response-based ptyd socket liveness and `session_probe` payload stamp.
