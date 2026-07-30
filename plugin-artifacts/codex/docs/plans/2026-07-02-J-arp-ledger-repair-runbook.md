<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Runbook: repair the agent-rally-point `.rally` ledger (seq 3007 segment-replay conflict)

**Status:** APPROVED to execute (user, 2026-07-02). **Executor:** Codex (room lead, owns ARP state). **Review/verify:** Claude (read-only). **Aligned by:** Claude + Codex + Fable (byte-verified).
**Do NOT deviate without re-alignment.** Retro on why this took long: build-loop-memory `lessons/2026-07-02-lesson-blocker-first-triage-and-coordination-latency.md`.

## Diagnosis (Fable byte-verified against `agent-rally-point/.rally/log/2026-06-27.jsonl`)
- Lines 55-57 = valid records at seq **3007/3008/3009** (08:23Z).
- Lines 58-60 = later 16:49Z records (`claude_code:idtest` presence / reaper decision / unmanaged risk) that **reused** seq 3007/3008/3009 with **payload `seq:0`**.
- Line 61 = read checkpoint currently at seq **3010** — part of the **same** 16:49 `4fbe` batch (not a bystander).
- Exactly **3 conflicts** (3007/3008/3009); global **max seq = 3010**. Nothing above it.
- **Root cause (Fable, inferred from `store.rs:2852` `next_canonical_seq` fast path):** a stale `.rally/.reconcile-cache.json` let the 16:49 writer take 3007 while 3007-3009 already existed. **Must delete that cache** or the class recurs.
- `facts.db` is **purely derived** (currently absent — rebuild deletes-then-fails). Fixing the JSONL self-heals it.
- `ref` linkage is by **event_id, never seq** → renumbering seqs orphans no referrer. `doctor.rs` has **no** ledger-repair path (canonical-paths/prune-rooms/reap-stale only) — hand-edit is the only route.

## Runbook (Codex executes; single writer)
1. **Confirm no active Rally writers:** `ps aux | grep -iE 'rally|cockpitd'` clean; no other agent session live in the repo (SessionStart hooks write rally). Check immediately before the edit.
2. **Full backup:** `cp -a .rally .rally.bak-<ts>` (whole dir — caches + cursors are part of the restore unit).
3. **Dry-run on a COPY first**, with `RALLY_NO_GLOBAL_INDEX=1` (so the copy doesn't register in the global room index). Apply the edit there, run a duplicate-scan + `rally room` / `rally next --tool codex` / `rally next --tool claude_code` in the copy. Only proceed to live once the copy replays clean.
4. **Edit all four lines** (targeted **textual substitution**, not JSON re-serialize — keep the diff minimal; atomic temp-file + `mv` on the same filesystem):
   - line 58: `{"seq":3007,` → `{"seq":3010,` and its payload `"seq":0` → `"seq":3010`
   - line 59: `3008` → `3011` (and payload `seq` → 3011)
   - line 60: `3009` → `3012` (and payload `seq` → 3012)
   - line 61: `{"seq":3010,` → `{"seq":3013,` and payload `"seq":0` → `"seq":3013`
   - **Do NOT** change event_ids, the `read_seq:3009` summary text, `cursors.json`, or any other line.
5. **Delete `.rally/.reconcile-cache.json`** (root-cause hygiene). Derived caches (`snapshot.cache.json`, `log/index.json`, `claim-index.json`) self-invalidate on the segment's mtime/len change; deleting them too is harmless.
6. **Verify:** (a) duplicate-scan over live+archive segments → 0 conflicts, max seq **3013**; (b) `rally room`, `rally next --tool codex`, `rally next --tool claude_code` all succeed in the ARP repo; (c) `.rally/facts.db` recreated; (d) **the next real append stamps seq 3014** — this is the proof the repair held. Keep `.rally.bak-<ts>` until (d) lands.

## Safeguards (reversibility)
Whole-`.rally/` `cp -a` backup · dry-run on a copy with `RALLY_NO_GLOBAL_INDEX=1` · atomic temp-file+rename for the segment write · no-concurrent-writer check immediately before the swap · retain `.rally.bak-<ts>` until the verify set passes **and** one real append lands at 3014. Rollback = restore the backup dir.

## Out of scope (flag, don't do now)
`log/test.jsonl` (824KB) and `b19-codex01-20260531.jsonl` participate in every replay — currently benign duplicates but a latent conflict surface. Separate cleanup.
