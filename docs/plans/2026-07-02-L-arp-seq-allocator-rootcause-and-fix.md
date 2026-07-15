<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# ARP seq-allocator root cause + fix + stabilization (mixed-binary, NOT concurrency)

**DURABLE FIX SHIPPED via Claude takeover — ✅ v0.1.4 released.** Codex committed the canonical-max allocator (4e575c3); Claude took over the close-out (user-directed): committed the plan/status bus (I-3, 63788b7), added the last-line dup gate + fingerprinted fast path (0abaf54, +2 tests, full suite 407 green), gitignore hygiene (93c873a), and tagged v0.1.4 (6fbbd95) — all CI-green hermetically. Fleet reinstalled to 0.1.4+6fbbd95 (canonical-max + dup-gate). Skipped: plugin-manifest icon WIP (references a nonexistent v5 PNG — left for Codex). Remaining invariant lanes (I-2 engagement, H checkout-ownership, identity-D cutover) are future work.

**STABILIZATION EXECUTED 2026-07-02 (Claude, user-approved) — ✅ VERIFIED.** Fleet reinstalled (all 3 rally binaries = f1d3466, canonical_max_seq allocator; old copies backed up). Dups 3038/39/40 renumbered to 3041/43; .reconcile-cache.json deleted; whole-.rally backup at `.rally.bak-20260702T133828Z`. Verified: 0 dups, rally room/next OK, append probe got seq 3044 (canonical max+1, NOT count+1) and survives replay. Ledger healthy (max_seq 3045). REMAINING (Codex): commit the working-tree fix, add the last-line dup gate, tag v0.1.4, land the version-skew guard.

**Status:** diagnosis Fable-verified (byte + source + git + `strings`). Executor: Codex (ARP repo). Claude: review/verify. **Supersedes the J runbook's "just hand-repair" — hand-repair alone re-corrupts.**

## Root cause (verified)
- **Concurrency is safe.** Every allocate+append runs under an exclusive cross-process flock (`acquire_room_mutation_lock`, `store.rs:649-668`, since `e050922`). Multi-writer scale is intact; concurrency is NOT the cause. Colliding pairs are **same-tool, ~10 min apart** — not simultaneous.
- **The bug:** released binaries allocate `fact.seq = facts.db` row auto-increment (HEAD `store.rs:1007`, since `26de1c6`). `facts.db` is *derived*; after a rebuild its counter = **distinct-seq COUNT, not MAX**. This ledger has a 3-seq gap at 2481-2483 (the 2026-06-10/11 write-drop): **count=3037, max=3040**. So after any rebuild the old binary stamps its next appends `3038/3039/3040` → collide with the live tail. Deterministic; no race.
- **New vs pre-existing:** **latent/pre-existing, masked.** The reconcile sidecar self-advanced its counts +1 per append without rescanning, hiding the divergence Jun 11→Jul 1 (no rebuild → no collision).
- **The trigger = our own hand-repair.** Repair deletes `.reconcile-cache.json` → forces full scan → count≠max → rebuild → counter resets to max−3 → next old-binary batch collides. **The repair arms the corruption.** Proven twice (3007-3009, then 3038-3040).
- **Mixed fleet is the source:** PATH `~/.local/bin/rally` `0.1.3+7e33d5a` = OLD count allocator (writes `payload.seq:0`, `strings … | grep -c canonical_max_seq` = 0). `./target/debug/rally` `0.1.3+f1d3466` (built from the dirty tree) = HAS the fix (grep = 1). Hooks prefer `target/debug` only at repo-root cwd → mixed.
- **Convergence:** hand-repair **never** converges while any old binary writes — the 3-seq deficit is invariant; each repair re-collides at max−2..max.

## The current dups (all in `.rally/log/2026-07-02.jsonl`; lines 28/29/30 are the old-binary copies with `payload.seq:0`)
`seq 3038` (line 25 backlog-item vs line 28 read) · `3039` (26 wake vs 29 wake) · `3040` (27 read vs 30 handoff). Keep 25-27; renumber 28-30.

## Durable fix (Codex — ARP)
1. **Commit the working-tree allocator fix** (`next_canonical_seq` = canonical max+1 under the existing flock; `store.rs:2852`, called from `append_fact:988` after reconcile). This IS the scale-to-thousands invariant — atomic allocate+append under LOCK_EX; **no single-writer needed.**
2. **Last-line dup gate (defense-in-depth):** before `append_segment_line`, if `allocated_seq <= last_seq` → hard-error. Turns silent corruption into a loud failure (~5 lines).
3. **Fingerprint-check the fast path** (`next_canonical_seq:2853-2858`) so the invariant doesn't depend on call order.
4. **Tag v0.1.4, reinstall EVERY binary** (`~/.local/bin/rally`, `~/.cargo/bin/rally`) + land the on-PATH version-skew guard (build-loop lane E-a already ships one) — binary-install lag is the recurring systemic cause (2nd incident of this class).

## Immediate stabilization (state-changing → needs approval; ORDER MATTERS)
1. **FIRST eliminate old binaries from the write path:** commit fix → `cargo build` → `cp target/debug/rally ~/.local/bin/rally`; verify `strings ~/.local/bin/rally | grep -c canonical_max_seq` ≥ 1. **Do this BEFORE touching the ledger** or the repair re-corrupts.
2. **Renumber the 3 dup lines** (28/29/30) in `2026-07-02.jsonl`: 3038→3041, 3039→3042, 3040→3043 — set **both** outer `"seq"` and payload `"seq"` (leaving payload 0 makes db max under-report → permanent rebuild loop).
3. **Delete `.rally/.reconcile-cache.json`** (facts.db already absent; first op rebuilds cleanly once dups are gone).
4. **Verify set:** (a) dup-scan (per-seq distinct event_ids over `log/*.jsonl`+archive) → 0 dups, max=3043, distinct=3040; (b) `rally room` ×2 exit 0; (c) **append probe:** post one fact via the NEW binary → `seq==3044` AND `payload.seq==3044` AND it **survives a forced replay** (`rally room` again) — persistence-through-rebuild is the success criterion; (d) no `payload.seq:0` in new appends.

## Interim operating rule (until PATH reinstall lands)
**Single-BINARY, not single-writer.** Keep concurrent multi-agent writes (the flock makes them safe once allocation is canonical-max). Everyone sets `RALLY_BIN=<agent-rally-point-checkout>/target/debug/rally` (hooks honor it, `hooks/rally-coordination-hook.sh:126`) and uses that absolute path for direct calls, until step 1 completes (minutes). Any call that hits old `~/.local/bin/rally` re-corrupts on the next append batch.

## Confidence
Allocator mechanism, lock coverage, dup provenance, binary attribution, count/max math — ✅ verified. The 2481-2483 gap = the June write-drop, and the repair-cache-deletion being the Jul-1 trigger — high-confidence inferred (TAG:INFERRED; consistent with both observed incidents).
