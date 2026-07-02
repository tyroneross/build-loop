<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# RCA: Rally lifecycle cleanup — independent root-cause analysis

**Author:** claude_code · **Date:** 2026-07-02 · **Method:** 5 parallel read-only investigation lanes (Sonnet), synthesized on Opus.
**Feeds:** the build-loop-owned fixes below go through `/build-loop:run`; the ARP-owned items are handed to Codex (room lead) for independent assessment.
**Companion:** `2026-07-02-C-rally-lifecycle-cleanup-PICKUP.md` (the hypothesis this RCA tests) · `RALLY-VERSION-MISMATCH-ASSESSMENT-2026-07-01.md`.

---

## Bottom line

The doc's five problems split cleanly by **repo owner**. Two of the doc's framings were factually wrong, and the split changes what a single build-loop run can actually fix:

| # | Problem | True root cause | Owner | Buildable in build-loop run? |
|---|---------|-----------------|-------|------------------------------|
| A | Handoffs never resolve | build-loop never calls the (already-correct) `rally say receipt/resolve` closing primitive | **build-loop** | ✅ yes |
| C | Stale claims (152 active / 148 expired / 84 dead-worktree) | Wiring gap: worktree teardown makes zero rally calls; the tested reaper only fires on explicit `--reap-stale` | **build-loop** (+ safe one-time op) | ✅ yes |
| E | Version-staleness recurrence | No on-PATH-vs-pin guard; discovery ranks stale sibling above pinned cache; worktree hook path computed wrong | **build-loop** | ✅ yes |
| B | Ghost squads never decay | ARP `liveness.rs::is_live()` requires all-4 signals → sparse squads verdict `Unknown` → fail-open forever; drop path unreachable | **ARP (Rust)** | ❌ separate repo → Codex |
| D | Duplicate-active-squad-id | ARP `enter` dedup keyed on tool-label string, no session awareness (self re-entry warns); + a deliberate identity cutover | **ARP (Rust)** | ❌ separate repo → Codex |

**Corrections to the pickup doc:**
1. **"ACK ≠ resolve" is a red herring.** There are *two* same-named `ack` mechanisms (ARP's global rules-ack; build-loop's inbox read-cursor); neither is meant to close a handoff. ARP's projection is correct and the closing primitive (`receipt`/`resolve`) already exists — it's simply never called. So doc option (a) "resolve-on-ACK" is *wrong* (it would conflate read vs resolved); option (b) build-loop closeout is right.
2. **Ghost decay is not a build-loop fix.** The doc lists it as a cleanup item; the real fix is in ARP Rust (`liveness.rs`), and *no CLI path can remove a squad ghost today* — not even manually — until `is_live()` is relaxed.

---

## Lane detail (symptom → mechanism → root cause → escaped-control → lever)

### A. ACK→resolve lifecycle — OWNER: build-loop ✅
- **Symptom (verified):** 2–3 open handoffs targeted at `claude_code`; direct `facts.db` query shows no `resolve`/`receipt`/`artifact` fact references their `event_id`. Per ARP's own rules they are *correctly* still open.
- **Mechanism:** `handoff_is_closed` (`crates/rally-cli/src/store.rs:1748-1752`) closes a handoff only via a later `Resolve|Receipt|Artifact` fact whose `ref_id` + target match (`store.rs:1725-1745`). `rally ack` appends a `decision` fact `subject:"coordination:ack"` (`store.rs:322`) — unrelated. build-loop's `inbox.py` `ack` only advances a local JSON cursor.
- **Root cause:** design gap — no automated path calls `rally say receipt --ref <event_id>` after a handoff is addressed. `lifecycle.py` does session/log hygiene only.
- **Escaped control:** two same-named "ack" mechanisms mask that a third, correct one exists.
- **Lever (build-loop):** run-closeout step that emits `rally say --tool claude_code receipt --ref <handoff_event_id>` for handoffs addressed this run. Confidence ✅ high.

### C. Stale-claim ledger + lease expiry — OWNER: build-loop ✅ (+ safe one-time op)
- **Symptom (verified):** 152 active claims; 148 expired-lease; 84 `file:.claude/worktrees/agent-*` whose folders no longer exist (`.claude/worktrees/` empty on disk). ~4 possibly-live (not session-cross-checked).
- **Mechanism:** `is_active_claim_fact` (`claim_authority.rs:64-69`) = fact-kind only; `lease_expires_at` captured as display evidence (`:116-127`) but never consulted on read. The lease-honoring path (`expired_claims()` `:190-211`, fail-closed) + actuator (`reaper.rs::run_reap_stale_in_room`) exist but fire only on explicit `rally room --reap-stale [--apply]` (`cli.rs:1252`). build-loop `collapse_run.py::_remove_worktree` deletes the worktree with **zero** `rally` calls → orphaned claims.
- **Root cause:** wiring gap — claim release not coupled to worktree teardown; reaper never scheduled.
- **Escaped control:** two controls each assumed the other covered it; build-loop's `presence.reap_stale` (presence files only, not fact-store claims) created false confidence.
- **Levers:** (1) **one-time safe cleanup now:** `rally room --reap-stale --apply` — existing tested actuator, fail-closed, never touches an unexpired-lease/live-peer claim (dry-run first). (2) **durable (build-loop):** call reap/release at end of `collapse_run.py::_remove_worktree`. ARP: nothing needed. Confidence ✅ high (⚠️ the ~4 live count unverified — dry-run confirms preserved set before apply).

### E. Version staleness + discovery + hook path — OWNER: build-loop ✅
- **Symptom (verified):** PATH `rally` = 0.1.3+7e33d5a (matches pin); sibling `target/release/rally` = 0.1.2 (stale) and would win discovery today; cached pinned = correct.
- **Mechanism:** `discovery_bridge.py::_rally_binary_candidates()` (`:285-333`) orders sibling `target/{release,debug}` **before** PATH and the pinned cache (added last, `:321-332`); `rust_rally_binary()` returns first candidate passing a *help-text surface check only* (`:349-370`) — no version comparison. `install_git_hooks.py::_git_hooks_dir()` (`:91-116`) manually parses `gitdir:` and appends `/hooks`, producing `.git/worktrees/<name>/hooks` — a path git never reads (real git resolves worktree hooks to shared `.git/hooks`).
- **Root cause:** (a) no on-PATH/sibling-vs-pin staleness guard (`version_matches_pin()` exists but only checked on the already-cached file); (b) discovery preference order wrong + worktree hook path wrong.
- **Escaped control:** surface check validates subcommand presence, not version; no hook/Phase-1 step diffs discovered version vs `PINNED_VERSION`.
- **Levers (build-loop):** (a) add staleness WARN to `hooks/session-start-rally-point.sh` (fires every session; fail-open); (b) reorder `_rally_binary_candidates()` so PATH/pinned precede sibling `target/*`, or version-gate siblings; (c) replace manual parse with `git -C <repo> rev-parse --git-path hooks`. Confidence ✅ high (live-reproduced).

### B. Ghost squad decay — OWNER: ARP (Rust) ❌ hand to Codex
- **Symptom (verified):** 18 squads, 15 weeks-stale ghosts (last_seen 2026-05-31…06-12), all `status:"idle"`, none live.
- **Mechanism:** `f3053e1` added `is_live()` (`liveness.rs`, `store.rs:2025-2160`): squad is `Stale` (dropped) only if all four signals (heartbeat, inject/ack, code-progress, plan) are `Some` AND past the adaptive window. Any `None` → `Unknown` → fail-open kept forever (`liveness.rs:105-114`, `store.rs:2130`). `code_progress_age` needs two presence facts with differing `branch_head_sha` — a bar one-shot/probe/demo squads never clear.
- **Root cause:** the drop path is *mathematically unreachable* for sparse-signal squads.
- **Escaped control:** the 8 decay unit tests all populate 4 signals; none model the dominant 2-present/2-absent shape.
- **Lever (ARP):** in `liveness.rs::is_live()`, relax to "heartbeat present + ≥2-of-4 present, all present ones stale" (keep the invariant: any fresh signal ⇒ alive). No squad-prune CLI exists, so this is the *only* path to remove ghosts. Confidence ✅ high.

### D. Identity duplication — OWNER: ARP (Rust) ❌ hand to Codex
- **Symptom (verified):** `facts.db` seq 4009 `risk: duplicate-active-squad-id: claude_code`. 393 facts `tool:claude_code` vs 7 legacy `tool:claude` (distinct historical label, not this bug).
- **Mechanism:** `lib.rs:1112-1116` gates duplicate-entry on tool-label string equality (`s.tool == tool && s.status=="active"`), never compares `from_session_id`. Surrounding facts share one `from_session_id` → the agent re-entered under its own active label and collided with *itself*.
- **Root cause:** `enter` dedup is tool-label-keyed, zero session awareness. The `claude`/`claude_code` split is a dead legacy label — **build-loop side is already clean** (`build_loop_id.py`/`session_probe.py` take `tool` as a param, no hardcoded "claude").
- **Escaped control:** identity-wiring (`58a1a26`+4, tip `fdaff327`) rewires claim/presence/gate authority to session_id but **never touches `lib.rs:1112`** — the cutover doesn't fix this symptom.
- **Two-part lever (ARP):**
  - **(a) interim, shippable independently:** patch `lib.rs:1112-1140` to compare incoming `session_id` vs `squads[].session_id` before warning — self re-entry no-ops, true peer collision still warns.
  - **(b) DELIBERATE cutover — SCHEDULE, DON'T SHIP:** identity-wiring branch, 5 chunks ~800 LOC (claim_authority.rs/store.rs/lib.rs), tool→session authority keys. Merge checklist: rebase onto main → re-run 416 lib tests → verify JSON back-compat (additive + serde-default) → re-sync build-loop bridge scripts vs new Squad/RoomSnapshot fields → regenerate `.rally/manifest.json` → coordinate fleet-wide re-enter. Confidence ✅ high on symptom/mechanism.

---

## Execution split

**build-loop `/build-loop:run` (this session, isolated worktree):** A + C-durable + E-a + E-b + E-c, each with a colocated test. Plus the one-time safe `rally room --reap-stale --apply` (dry-run → apply) to clear the 148/84 backlog.

**Handed to Codex (room lead, ARP repo):** B (`liveness.rs` decay relaxation) + D-a (`lib.rs` session-aware dedup) as shippable; D-b (identity-wiring cutover) as a *scheduled* item with the checklist above. Codex is also running an independent RCA pass against this same brief.

**Do-not (carried from pickup):** don't mass-reap without the fail-closed guard (the CLI reaper already honors it); don't merge identity-wiring reactively; don't commit into the live build-loop checkout from a background/headless agent (use a worktree).
