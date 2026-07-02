<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Codex handoff: validate + independent opinion + A/B build + test (Rally lifecycle work)

**From:** claude_code · **To:** codex · **Date:** 2026-07-02 · **Repo:** `/Users/tyroneross/dev/git-folder/build-loop` (branch `main`)
**Purpose:** Codex independently (1) VALIDATES what Claude shipped, (2) gives an INDEPENDENT OPINION on the RCA + designs, (3) BUILDS its own inject-readiness probe for an A/B comparison + dogfood, (4) TESTS both. Do NOT trust this doc's claims — re-derive them.

---

## 0. What Claude shipped (verify each; don't take on faith)

**Committed to `main`** (`git log --oneline 4e425d4..HEAD`):
- `41bdf48` lane A — `resolve_addressed_handoffs()` in `scripts/rally_point/lifecycle.py` (+ test)
- `47acc45` lane C — release orphaned claims on worktree teardown in `scripts/collapse_run.py` (+ test)
- `79f9e01` lane E-a — on-PATH-vs-pin version guard in `hooks/session-start-rally-point.sh` + `binary_fetch.py --print-pin` (+ tests)
- `32e95b5` lane E-b — discovery order (env→pinned→PATH→sibling) in `scripts/rally_point/discovery_bridge.py` (+ test)
- `195ad83` lane E-c — `_git_hooks_dir` via `git rev-parse --git-path hooks` in `scripts/install_git_hooks.py` (+ test)
- `c207dce` / `d9f2849` — RCA + 2 design specs (docs).

**Operational (already applied to the live room):** one-time release of 149 expired claims (168→18 active); 3 handoffs closed via `rally say receipt --ref`.

**Claimed RCA root causes (RE-DERIVE against source):**
| Lane | Root cause claim | Where to check |
|---|---|---|
| A | handoffs never closed because nothing calls `rally say receipt/resolve --ref`; `ack` is a rules-ack, not a resolver | ARP `store.rs:1725-1752,1963-1966`; `lib.rs:860-862` |
| C | `collapse_run.py::_remove_worktree` made zero rally calls → orphaned claims; ARP lease-honoring exists but only via `rally room --reap-stale` which is NOT on 0.1.3 | ARP `claim_authority.rs:64-127,190-211`, `reaper.rs`; installed `rally room --help` |
| E-a | no on-PATH-vs-pin guard existed | `binary_fetch.py:109`; `hooks/session-start-rally-point.sh` |
| E-b | discovery ranked stale sibling above pinned cache | `discovery_bridge.py:285-333,349-370` |
| E-c | manual gitdir parse → per-worktree hooks path git never reads | `install_git_hooks.py:91-116`; `git -C <wt> rev-parse --git-path hooks` |

**Also handed to you separately (ARP/Rust lanes, do not need re-handoff):** B (`liveness.rs::is_live()` all-4-signal fail-open → ghosts never decay), D-a (`lib.rs:1112` tool-label dedup, session-unaware), D-b (identity-wiring cutover, schedule-only), and **CRITICAL: the agent-rally-point repo's own `.rally` ledger is corrupted — `segment replay conflict at seq 3007` (`store.rs:3056`); every rally command fails there.** Design specs: `docs/plans/2026-07-02-D-session-identity-liveness-design.md`, `-E-inject-fresh-laptop-portability.md`.

---

## 1. TASK — Validate (independent)

Run the validation harness (below) from a clean checkout of `main`:
```
bash docs/plans/2026-07-02-rally-lifecycle-validate.sh
```
Expected: all fix-presence checks PASS, full suite green (~296+ tests), live-binary invariants confirmed. Then independently re-derive each RCA root cause from the source file:line above and confirm/refute. Report any divergence.

## 2. TASK — Independent opinion

Critique, don't rubber-stamp:
- **Fixes:** Is lane C's path-boundary scope match correct (it must release claims under the removed worktree but never a sibling sharing a name prefix — see `test_collapse_run_claim_release.py::test_sibling_worktree_sharing_a_name_prefix_is_not_released`)? Is lane A's `receipt` the right closer vs `resolve` (semantic difference: receipt=acknowledged-received, resolve=work-done)? Is E-b's reorder safe for the sibling-dev-build workflow?
- **Designs (D, E):** Is the session-id unification (retire build-loop's random presence id in favor of the ARP endpoint id) the right call? Is the liveness-cascade `child_alive = min(own, parent_session_alive)` sound, and is the headless carve-out complete? For E: is "detect + degrade to handoffs" the right minimum, or should ptyd be pinned+fetched now?
- Name anything Claude got wrong or missed.

## 3. TASK — Independent A/B build (inject-readiness probe)

Build YOUR OWN implementation, on branch `codex/inject-probe` (worktree-isolated), WITHOUT reading Claude's version first (Claude builds `claude/inject-probe` in parallel). Shared acceptance criteria so the two are comparable:
- A function/CLI that reports pane-backend readiness: `{tmux: bool, ptyd_socket_live: bool, ptyd_bin: bool, inject_available: bool, recommended_backend: "tmux"|"ptyd"|"handoff"}`.
- Cheap, **fail-open**, **no always-on process** (honor the user's no-idle-compute constraint).
- Wire into the session-start rally preflight (stamp result next to `capability_level`); build-loop-side only (no ARP/Rust change).
- Degradation doctrine: `inject_available == false` ⇒ agents route via `rally say handoff`, never `rally inject`.
- Colocated test INCLUDING a fresh-laptop simulation (PATH stripped of tmux+ptyd, no live socket) ⇒ `inject_available=false, recommended_backend="handoff"`.

## 4. TASK — Test both / dogfood / compare

- Run both probes on this box (tmux+ptyd present) → expect `inject_available=true`.
- Simulate fresh laptop: `env PATH=/usr/bin:/bin <probe>` (no tmux/ptyd) → expect `false` + `handoff`.
- Compare: correctness on both cases, cost (subprocess count / latency), clarity, and fit to the degradation doctrine. Produce a short comparison verdict (which to keep, or the best merge of both).

## 5. Report back
Post via rally from the repo:
```
rally say receipt --tool codex --ref <this-handoff-event-id> --subject 'validation + A/B complete' --json
rally say artifact --tool codex --subject 'Codex inject-probe + validation verdict' --uri <branch-or-doc> --evidence '<results>' --json
```
Include: validation PASS/FAIL per lane, independent-opinion divergences, your probe branch, and the A/B comparison verdict. Keep official git author human/service-owned (record agent involvement in the commit body only).
