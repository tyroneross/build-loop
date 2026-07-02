<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Plan: engagement-readiness invariant (I-2) — present ≠ engaged

**Author:** claude_code (from Codex's critique `fact_71be_…`) · **Date:** 2026-07-02 · **Status:** design plan (not implemented).
**Separate release blocker from ownership (`2026-07-02-H-*`), per Codex.** H solves "two writers in one checkout." This solves "an agent is *visible* but not *engaged*" — it never drains handoffs after a turn ends. Different failure class, owner, and tests; do NOT fold into H as a sub-bullet.

---

## 1. Problem (observed, this session)
Codex was present in the room (`sess:proc:…#live`) but had **no watcher/worker draining handoffs**, so the first handoff to it sat unread until a human relayed it. Root: **repo hooks are documented dormant under `codex exec`**, so a SessionStart watcher never started. Presence and even a stable identity are insufficient — coordination requires something that keeps consuming `rally next` after the agent's turn ends.

## 2. Invariant
```
engaged(session) = presence
                 ∧ stable_identity
                 ∧ (watcher ∨ managed_worker)
                 ∧ next_action_drain
```
A session missing any conjunct is **not** coordination-ready; handoffs routed to it will rot. This is orthogonal to write-ownership (H): an agent can own a checkout yet be disengaged, or be engaged without owning anything.

## 3. Detection (ARP, host-neutral)
Add `rally check engagement --tool <t> --session <id> --json` → `ready | not-ready(reason)`, `reason ∈ {no-watcher, unstable-identity, undrained-actionable, presence-stale}`. Reuse existing signals: presence file + heartbeat, the identity work (design D), and `rally next` actionability. Verdict logic lives in ARP; host shims pass only the §5.1-H host-neutral fields.

## 4. Minimum build-loop fix (Codex's concrete list)
1. **`--start-watch` at SessionStart** — add to `.codex/hooks.json` SessionStart (and the equivalent for other hosts) so a watcher/worker starts with the session.
2. **Semantic lint/test** — fails if Codex `session_probe.py` is wired **without** `--start-watch` or an explicit alternate worker. Prevents silently shipping a disengaged config.
3. **Runtime preflight** — if peers / unread inbox / coordination files exist, require a watcher, a managed worker, OR a completed manual drain **before closeout**. Because repo hooks are dormant under `codex exec`, this must run at runtime, not rely on the hook firing.
4. **Closeout gate** — if `coordination_status` shows peers/inbox/coordination-files AND `rally next` is actionable AND nothing drained it ⇒ closeout **degrades/fails** (WARN-first) with an explicit "start a watcher or drain now" instruction.
5. **Document repo-hook dormancy** + require a global/manual fallback (don't assume the repo hook runs).

## 5. Acceptance tests — prove real ACTIVATION, not helper behavior
Per the "built ≠ wired" rule — assert the watcher actually starts and closeout actually gates, not just that a helper function exists:
- [ ] The SessionStart hook command **includes/starts the watcher** (assert the wired command, not a unit stub).
- [ ] `session_probe.py --start-watch` **creates watcher metadata** (assert the artifact/PID/lease exists).
- [ ] Semantic lint **fails** when `session_probe.py` is wired without `--start-watch`/alternate worker (mutation-style: break the wiring → lint red).
- [ ] Dormant-hook / stripped / fresh-laptop path ⇒ **degrades to explicit manual-drain requirement** (not silent success).
- [ ] With actionable `rally next` and no drain ⇒ **closeout blocks/degrades**; after a drain (or watcher start) ⇒ closeout proceeds.

## 6. Rollout
1. Depends on identity-D (stable id) for the `stable_identity` conjunct.
2. `rally check engagement` (ARP) + host-neutral fields.
3. build-loop fix §4 (WARN-first): `--start-watch`, lint, runtime preflight, closeout gate.
4. Acceptance tests §5 (activation-proof).
5. Promote closeout WARN→fail once stable.

## 7. Ownership split
- **ARP (Rust):** `rally check engagement` verdict; expose watcher/drain state.
- **build-loop + per-host:** `.codex/hooks.json` `--start-watch`; `session_probe.py` watcher wiring; semantic lint + runtime preflight; closeout-gate wiring; docs. Codex owns its host adapter; build-loop owns the lint/preflight/gate.

## 8. Relationship to H
Adjacent, not nested. H = *may this session write this checkout?* I-2 = *is this session actually going to keep consuming the work routed to it?* Both consume design-D identity; both are host-neutral via the ARP CLI + logic-free shims; both ship WARN-first. Track and release them separately.
