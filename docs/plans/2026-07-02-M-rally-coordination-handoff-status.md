<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# HANDOFF: Rally coordination work — latest, pending, expected outcomes

**For:** a fresh terminal/agent (Claude or Codex) picking up the Rally coordination program. Self-contained.
**Date:** 2026-07-02 · **Author:** claude_code · **Live backlog:** `rally backlog list` in `agent-rally-point` (canonical room; 8 items seeded via the I-3 bus with `--target/--status/--expected-by`).
**Canonical room rule:** when working the agent-rally-point repo and its room is available, coordinate THERE; build-loop room is fallback only during an ARP outage.

---

## LATEST — shipped & verified (done)

**ARP `v0.1.4` released** (tag `v0.1.4`, HEAD `6fbbd95`, 4 public binary assets published, not draft). This closes the ledger-corruption saga:
- **Root cause eliminated:** old binaries allocated seq from the derived `facts.db` row-COUNT, not the canonical segment MAX; with a seq gap (June write-drop) COUNT<MAX, so every rebuild made an old binary collide at the tail. NOT concurrency (an flock already serializes writers; ARP is multi-writer by design). Trigger was our own hand-repair (cache delete → rebuild → counter reset).
- **Fix (committed + released):** `4e575c3` canonical high-water allocator; `0abaf54` last-line dup-gate (loud error, not silent dup) + fingerprinted fast path (order-independent) + 2 regression tests (full suite **407 green**, CI hermetic-green).
- **Plan/status bus (I-3) SHIPPED** (`63788b7`): `rally backlog add/update --target/--status/--expected-by`; `rally next` surfaces targeted plan items as an actionable `update_plan_status` obligation → peers forecast ETAs, plan requests can't sit unconsumed. (This doc's backlog dogfoods it.)
- **Fleet reinstalled** to `0.1.4+6fbbd95` (both `~/.local/bin/rally` + `~/.cargo/bin/rally` carry canonical-max + dup-gate). Ledger healthy (`max_seq` ~3078).

**build-loop side (already on `origin/main`):** rally-lifecycle fixes lanes A/C/E; inject-readiness probe (`should_use_handoff` degrade-to-handoff); design/runbook docs `2026-07-02-{C-RCA, D-identity, E-inject, H-ownership, I-engagement, J-ledger-runbook, K-plan-status-bus, L-allocator-rootcause, M-this}.md`; memory (RCA lesson, rally-013 ops gotcha, ADR-0095).

**Coordination invariant program (3 legs):** I-1 checkout-ownership (H, designed) · I-2 engagement-readiness (I, designed) · **I-3 plan/status bus (K, SHIPPED)**.

---

## PENDING — the backlog (owner · status · expected outcome)

| id | owner | status | Expected outcome |
|---|---|---|---|
| `release-verify-v0.1.4` | claude_code | in_progress | Confirm release-commit CI green + `release.yml` published (✅ 4 assets, not draft). Reinstall on any OTHER machines — binary-install lag is the recurring systemic cause. |
| `plugin-manifest-icon-fix` | codex | blocked | Manifests reference `./assets/app-icon-rally-point-v5.png` which doesn't exist (only v2/v3/v4). Create v5 or repoint to v4, then commit the 5 manifests + REUSE.toml + assets **together**. (Skipped in the v0.1.4 takeover.) |
| `h-checkout-ownership` | codex | planned | I-1: `rally check before-write` keys conflict on **stable session id** (not tool label, `check.rs:128`) + checkout scope + verdict vocab (ok/block/warn/exempt) + `handoff_command`. WARN-first. Plan `H-*`. |
| `i2-engagement-arp` | codex | planned | `rally check engagement` → `ready\|not-ready(reason)`; expose watcher/drain state. Plan `I-*`. |
| `i2-engagement-buildloop` | claude_code | planned | `--start-watch` at SessionStart + semantic lint + runtime preflight + closeout gate (undrained actionable `rally next` degrades closeout). Activation-proof tests. Depends on `i2-engagement-arp`. |
| `identity-d-cutover` | codex | planned | Unify to one session id + fresh-per-runtime lease + endpoint-lineage re-attach + `independent_lifetime` + subagent id derivation + liveness cascade. On `identity-wiring` branch (partial). **Schedule-don't-ship; id-unify first.** Plan `D-*`. |
| `bus-status-validation` | claude_code | open | Validate `--status` strings on add/update (today `--status wip` is silently accepted then drops off the obligation radar). ~5 lines. |
| `ledger-segment-checkpoint` | codex | open | Commit `.rally/log` segments 2026-06-11..07-02 in a dedicated `chore(rally)` commit at a quiet moment (repo convention). Never inside a code commit; `2026-07-02.jsonl` is live-append incl. repair lines. |

**Sequencing:** `release-verify` (now) → `plugin-manifest` (unblock) → `h-checkout-ownership` → `i2-engagement-arp` → `i2-engagement-buildloop`; `identity-d-cutover` is the deliberate long-horizon cutover (id-unify prerequisite for full I-1/I-2 robustness); `bus-status-validation` + `ledger-segment-checkpoint` are quick/chore fast-follows. B (ghost-decay) already shipped in v0.1.3.

---

## HOW TO PICK UP
1. `cd agent-rally-point && rally whoami --tool <you> --json` → `rally enter` → `rally next --tool <you> --json`. Verify `rally version` = `0.1.4+...` (reinstall from the v0.1.4 tag if older — binary skew is the corruption cause).
2. `rally backlog list --json` for the live board (status/ETA per item). Pick an item you own; `rally backlog update --id <id> --status in_progress`.
3. Read the matching `docs/plans/2026-07-02-*.md` in build-loop for the design/spec.
4. Verify any claimed output read-only against source/bytes before agreeing (this session's discipline). Bounded waits + fallback — never wait forever on a peer; route critical work to a resource you control (e.g. Fable) if a peer is slow.

## DO-NOT (hard-won this session)
- Don't hand-repair the ledger before deploying the fixed binary — the repair (cache delete) re-arms corruption on any old binary. On v0.1.4 with the dup-gate this now fails LOUD instead of silently corrupting.
- Don't `git add -A` in ARP — 81M bundles / live ledger / backups sit untracked (now gitignored, but stage explicit paths).
- Don't fold I-2 (engagement) into H (ownership) — separate release blockers.
- Don't merge `identity-wiring` reactively — deliberate cutover.
