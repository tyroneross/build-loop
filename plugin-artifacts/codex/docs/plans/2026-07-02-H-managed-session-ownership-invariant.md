<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Plan: host-neutral checkout-ownership guard for Rally Point (I-1)

**Author:** claude_code · **Date:** 2026-07-02 · **Status:** design + rollout plan (not implemented), v3.
**Scope (narrowed per Codex):** this plan covers **write-ownership of a shared checkout only.** Engagement-readiness (present-but-not-draining) is a *separate release blocker* → `2026-07-02-I-codex-engagement-readiness.md`. Do not merge them.
**Reviews folded in:** Fable audit (REVISE — C1/H1/H2/H3, M1–M5) + Codex critique (7 points, `fact_71be_…`). Source-grounded against ARP 0.1.3.

---

## 0. Framing
**Best-effort advisory gate for cooperative agents — accident prevention, not a security boundary.** Rally isn't in the write path (TOCTOU: `rally check before-write` reserves nothing), so "at most one writer" is a coordination aid among cooperating agents, not a guarantee. **Verdict per Codex: the invariant is "the confirmed-live owner for THIS checkout wins" — not "the repo must stay managed."**

## 1. The problem (grounded)
Uncoordinated concurrent writers to a **shared checkout**. Today `check.rs:128` keys conflict on the **tool label** (`claim.tool != tool`), so **two `claude_code` (or two `codex`) sessions on one checkout are invisible to the gate** — the motivating defect. Separate *owned worktrees* are legitimate parallelism and must stay allowed (Codex #1).

## 2. Non-goals
Not a read lock; not "every edit needs a managed session"; not repo-wide ownership (over-blocks parallel worktrees — Codex #1); not a security boundary; not the engagement problem (plan I).

## 3. What exists — extend, don't rebuild (verified)
- `rally check before-write` exists (`check.rs:62,99`): live-owner claim ⇒ `stop`, idle>15m ⇒ reclaimable `warn` (`check.rs:126-166`), `renew_claim_lease` (`store.rs:1343`), 2h takeover bar.
- Durable `kind=="session"` ledger facts replayed from `room.facts()` (`lib.rs:5162-5177`); `SessionRegistered|Closed|Revoked` (`event_envelope.rs:152-154`); `prior_managed_session` (`lib.rs:5192`); `rally adopt` (`cli.rs:48,867`).
- Worktrees share one `.rally` room; `repo_root()` follows `commondir`, `worktree_root()` isolates the checkout (`lib.rs:10837-10884`).
- Identity churn real: literal `"live"` lease (`lib.rs:2271-2281`).

**Codex #2 refinement — "records already durable" is only *partly* true:** ARP has durable managed-*session* facts but **no durable owner-*policy* record.** Adding one must **not pollute the session/claim projections** — an owner-policy record must never surface as an active managed session or re-inflate the active-claims list (which we just drained). Store it as a claim subtype with an explicit `subkind: checkout-owner` that projections filter out of `active_claims`/`sessions`.

**H1 consequence:** implement as an **extension of claims + `check.rs`**, not a new fact kind + reconciler. Real deltas: session-id key, checkout scope, own-worktree carve-out, verdict vocab + `handoff_command`.

## 4. Identity foundation (P0 — from design D)
1. One session id, **fresh-per-runtime lease** (restart ⇒ new id).
2. **Re-attach keyed on `endpoint_id` lineage + tool_type + pane_target** — never pane alone (H2 hijack: a different agent in a reused tmux pane must not inherit ownership).
3. `independent_lifetime` from endpoint class / host markers (headless carve-out).
4. **Tri-state liveness (Codex #4, supersedes v2 stale-reclaim):** `live | dead | unknown`.
   - `dead` = pane/socket/process **confirmed** gone ⇒ owner record cleared.
   - `unknown` = backend state unavailable (fresh laptop no tmux; interactive Terminal tty never in a pane list; `liveness.rs:113-127` returns Unknown) ⇒ **WARN, do NOT auto-clear.** A *takeover* by a new live session is still allowed after the 2h bar, but it is an explicit logged takeover, not a silent clear.
   - `live` = heartbeat-fresh ⇒ owner stands.

## 5. Invariant I-1 — verdicts
`rally check before-write --tool <t> --path <p> --session <RALLY_SESSION_ID> --checkout <RALLY_CHECKOUT_ID> --json`:
- **OK** — caller session id == owner, OR no active owner, OR caller is in a **durably-identified worktree it owns** (§7).
- **BLOCK** — a *different* session, **`live`** (§4.4), writing the **same `checkout_id`** as the owner ⇒ refuse + `handoff_command`. (Session-id keyed — closes the same-tool blind spot.)
- **WARN (allow) — LOUD (Codex #3):** ownership unverifiable (ledger down/corrupt, degraded, `unknown` liveness) ⇒ allow, but emit a structured `coordination_unverified` warning with a machine-readable `reason` (never a silent allow). Fail-open, repairable.
- **EXEMPT** — `RALLY_INDEPENDENT_LIFETIME=1` (headless/CI) ⇒ never blocked.

### 5.1 Host-neutral input contract (Codex #5)
All verdict logic stays in the CLI. Shims pass only these host-neutral fields; host adapters map their specifics into them:
- `RALLY_SESSION_ID` — stable per-runtime session id (§4.1).
- `RALLY_CHECKOUT_ID` — durable checkout/worktree id (§7), NOT a path.
- `RALLY_INDEPENDENT_LIFETIME` — headless carve-out flag.
Claude/Codex/Cursor/OSS adapters differ only in *how they populate* these three; the CLI treats them identically.

## 6. Adopt limitation (Codex #6)
`rally adopt` supports tmux/cmux-style targets, **not a bare `sess:proc:` process** (e.g. Codex today). State explicitly: **bare proc sessions cannot become managed / owner-writing retroactively** — only a future `rally run` launch or a supported adopt target can write an owner record. Until then such a session is treated as unmanaged (WARN, never a silent owner).

## 7. Own-worktree carve-out — proof, not path vibes (Codex #7)
When Rally **creates or adopts** a worktree, mint and store a **durable `checkout_id`** in that worktree's Rally metadata. The carve-out (`OK` for own-worktree) keys on that stored `checkout_id`, **not** env vars, path prefixes, or self-reported tool names (all spoofable/ambiguous). Composition: worktree isolation *prevents* the race and is the enforced end-state; I-1 is the residual net for shared-checkout interactive sessions; a caller with a matching owned `checkout_id` is `OK`.

## 8. Enforcement — authority parity yes, enforcement parity no (M1)
One ARP implementation of the verdict; per-host **logic-free** shims call it and honor the result. Enforcement varies and is stated honestly: Claude PreToolUse (deterministic); Codex `AGENTS.md` + runtime preflight (repo hooks dormant under `codex exec`); Cursor/Gemini rules→CLI; any/OSS via CLI or the **git pre-commit floor** (misses non-commit writes + `--no-verify` — a floor, not a ceiling). Only *authority* parity is claimed.

## 9. Lifecycle
Register (run/adopt → owner-policy claim subtype) → heartbeat refresh → re-attach (endpoint_id lineage + tool_type + pane, §4.2) → clear on explicit stop/revoke OR **confirmed-`dead`** (§4.4); `unknown` warns + allows logged takeover after the bar, never silent clear.

## 10. Rollout (Codex-aligned)
1. **Repair ARP ledger corruption first** (`seq 3007` — blocks all rally work in that repo).
2. **Identity-D / stable session id** (P0).
3. **Owner-policy record + `check before-write` verdicts, WARN-only** (+ projection-pollution guard §3).
4. **Tests** (§12) before any hard-block.
5. **Enable BLOCK** for confirmed same-checkout live conflicts only.

## 11. Threat model (M2)
In scope: cooperative agents making accidents. Out of scope: malicious/non-participating processes (can skip the CLI, set bypass, corrupt the ledger). Every bypass path (break-glass, EXEMPT, fail-open) **appends an audit fact**. A planted false owner is bounded by tri-state takeover + visible in the ledger.

## 12. Acceptance tests (Codex #4 + M5)
- [ ] **Two SAME-tool sessions, same checkout** ⇒ distinct verdicts (motivating defect).
- [ ] Same owner session ⇒ `OK`.
- [ ] **Separate owned worktrees** of one repo ⇒ both `OK` (durable `checkout_id`, not path).
- [ ] Different **live** session, same checkout ⇒ `BLOCK` + correct `handoff_command`.
- [ ] Corrupt/unavailable ledger ⇒ `WARN` + structured `coordination_unverified` reason + allow; break-glass leaves an audit fact.
- [ ] Stopped owner (confirmed `dead`) ⇒ record cleared; a new session may own.
- [ ] `unknown` liveness ⇒ WARN, **no auto-clear**; takeover only after bar, logged.
- [ ] Verdict parity across `--tool claude_code|codex|cursor` for identical inputs.
- [ ] **Projection-pollution:** owner-policy records do NOT appear in `rally sessions` or `active_claims`.
- [ ] Bare `proc:` session cannot write an owner record (Codex #6).
- [ ] Concurrent double-pass documented best-effort (TOCTOU, no false atomic claim).

## 13. Ownership split
- **ARP (Rust):** P0 identity; owner-policy claim subtype + projection filter; `check before-write` verdicts (session-id key, checkout scope, tri-state, loud unverified, own-worktree via `checkout_id`, `handoff_command`); durable `checkout_id` on run/adopt; ledger repair. Coordinate with lead (codex).
- **build-loop + per-host:** logic-free shims populating the §5.1 fields; git pre-commit floor; docs. WARN-first.

## 14. Changed vs v2
Narrowed to checkout-ownership only (engagement → plan I). Added: projection-pollution guard (#2), loud `coordination_unverified` (#3), tri-state live/dead/unknown clearing (#4), named host-neutral field contract (#5), bare-proc adopt limitation (#6), durable `checkout_id` proof for the carve-out (#7).
