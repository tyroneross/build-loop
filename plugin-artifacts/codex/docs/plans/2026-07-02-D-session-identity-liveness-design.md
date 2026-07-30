<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Design: session-keyed identity + liveness cascade

**Author:** claude_code · **Date:** 2026-07-02 · **Status:** design spec (not implemented) · grounded in a read-only source map of both repos + the `identity-wiring` branch.
**Relationship:** extends the ARP `identity-wiring` branch (58a1a26…tip fdaff327). That branch is a claim/authority/attribution rekey; this spec adds the three things it does NOT cover: subagent id derivation, liveness cascade, headless carve-out. **Schedule-don't-ship** (identity-model cutover) — ARP is the primary owner (Codex lead).

## Target model (user intent)
1. Each terminal has a unique session id.
2. Orchestrator agent id derived from that session id.
3. Subagents/sub-subagents get ids derived from the terminal session.
4. Liveness cascade: terminal/session ends ⇒ its agents inferred unavailable, without individually reaping each.
5. Carve-out: headless/deployed agents (CI, launchd, detached) have independent lifetime — not cascade-killed.

## Current state (verified, file:line)
- **Three disconnected id schemes.** (a) Rust endpoint-derived `sess:term:<host>:<uuid>#live` (`session_identity.rs:259`, minted `lib.rs:2281`) — but on `main` largely dead code, wired only into `whoami`. (b) The id build-loop actually runs on: **random** `<tool>-<hex16>-<utcstamp>` presence id (`session_probe.py:78`), drives presence/roster/liveness. (c) Run id `bl-<ts>-<tool>-<6d>` (`build_loop_id.py:118`) stores a session id (= the random one).
- **`#live` is a literal lease token, not a lifecycle state.** No `#stopped/#dead`. Endpoint is stable + lease constant ⇒ **restart on the same terminal reuses the same session id** (contradicts the module's own "fresh lease" intent). identity-wiring changes lease to `live:<tool>` — tool-distinct but still constant, still not per-restart-fresh.
- **Authority keyed on tool-label** (`claude_code`) on `main` → the observed `claude` vs `claude_code` self-conflict. `from_session_id` exists but is mostly unstamped on `main`.
- **Subagents have no rally identity.** Rust `actor_id` slot exists (`session_identity.rs:230`) but is never populated; Python presence has `parent`/`spawned` fields (`presence.py:187`) + a roster tree (`roster.py`) but **no production writer feeds them** (`hooks.py:344` passes neither). No parent→child derivation anywhere.
- **Lineage (`--run/--step`, `rally dag`) is task-causation only** (`dag.rs`), not agent identity, not tied to liveness.
- **Liveness is per-actor independent, no cascade.** 4-signal `is_live` (`liveness.rs:113`); reaper's only parent binding is OS parent-PID (`reapable(verdict, parent_alive)` where `parent_alive` = launching *process* alive). identity-wiring's squad projection passes `parent=None` — no cascade.
- **Carve-out signals exist but are unwired.** ARP classifies Cloud/Managed endpoints distinctly (`session_identity.rs:374`); build-loop has `BUILD_LOOP_WORKTREE_ISOLATED`/launchd markers — but neither reaches a liveness decision.

## Target design (build ON identity-wiring, five decisions)
1. **Unify to one session id.** Make the Rust endpoint-derived id the single source of truth; build-loop's presence + run id adopt it (retire the random `<tool>-<hex>-<ts>`). **Make the lease per-runtime-fresh** (uuid minted at process start) so "restart = new session" — a prerequisite for "session ended" to mean anything. *(ARP mint + build-loop adopt.)*
2. **Session lifecycle state.** `enter` → `#live:<runtime-uuid>`; process-exit / `rally stop` → a `session stopped` tombstone. Managed sessions already emit this (`lib.rs:5127`); extend to interactive terminals via a stop hook + heartbeat-staleness fallback. Now "terminal ended" is a detectable event, not just staleness. *(ARP.)*
3. **Subagent id derivation (#3).** Each dispatched agent's `actor_id = <session_id>/<agent-slug>-<n>`; the Agent-dispatch path registers it in the roster with `parent=<session_id>`. build-loop's sub-subagent ban ⇒ depth is 2 (session → agent). The `parent`/`spawned` roster fields already exist — this wires the dormant feeder. *(ARP derivation primitive + build-loop dispatch-time registration.)*
4. **Liveness cascade (#4).** `child_liveness = min(own_signals, parent_session_alive)`. When the parent session's tombstone lands (or it goes stale), every actor with `parent=<session_id>` is inferred dead in one projection step — no per-child reap. This is exactly the `reapable(verdict, parent_alive)` hook that today passes `None`; feed it the parent-session liveness. *(ARP liveness/reaper; depends on #3.)*
5. **Headless carve-out (#5).** Register agents with `independent_lifetime=true` when the endpoint is Cloud/Managed OR `BUILD_LOOP_WORKTREE_ISOLATED=1` OR a launchd/CI marker is present; such agents are EXEMPT from the parent-session cascade (they live/die by own signals + own OS parent). Wire the existing-but-disconnected signals into this flag. *(ARP consumes the flag; build-loop feeds its markers.)*

## Ownership + sequencing
| Step | Owner | Depends on |
|---|---|---|
| 1 unify id + fresh lease | ARP (mint) + build-loop (adopt) | — |
| 2 session lifecycle tombstone | ARP | 1 |
| 3 actor_id + roster registration | ARP primitive + build-loop dispatch wiring | 1 |
| 4 liveness cascade | ARP | 3 |
| 5 headless carve-out | ARP consume + build-loop feed | 4 |

**Deepest risk:** the two-scheme id divergence. A cascade keyed on the Rust session cannot find Python-registered children until (1) unifies the id spaces. Do #1 first or nothing else composes.

**Recommendation:** hand the ARP core (1,2,4,5 + the #3 primitive) to Codex as an `identity-wiring` v2 scope. build-loop lands the bridge adoption (presence/run-id source from the unified id; dispatch-time subagent registration; feed carve-out markers) AFTER the ARP id unifies. No build-loop-side piece is safely shippable before #1 — attempting subagent registration on today's random id just entrenches the divergence.
