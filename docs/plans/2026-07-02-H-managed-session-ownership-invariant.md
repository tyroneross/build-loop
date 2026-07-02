<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Plan: host-neutral coordination invariants for Rally Point (ownership + engagement)

**Author:** claude_code · **Date:** 2026-07-02 · **Status:** design + rollout plan (not implemented), v2.
**Reviews folded in:** Fable adversarial audit (verdict REVISE — C1/H1/H2/H3 + M1–M5) and Codex critique (`fact_71be_…` — dormant-engagement / adjacent invariant). Both source-grounded against ARP 0.1.3.
**Synthesizes:** `2026-07-02-D-session-identity-liveness-design.md`, `-E-inject-fresh-laptop-portability.md`, Codex's managed-required proposal.
**Owner of authoritative core:** ARP (Rust). Consumers: per-host logic-free shims. Audience: any Rally-speaking agent — Claude Code, Codex, Cursor, Gemini, OSS/CI.

---

## 0. Framing correction (from the audit)
This is a **best-effort advisory coordination gate for cooperative agents — accident prevention, not a security boundary.** Rally is not in the write path, so it cannot *guarantee* "at most one writer" (TOCTOU: `rally check before-write` reserves nothing). Language of "invariant/hard-block" is scoped to *positively-confirmed conflicts among cooperating agents*; a malicious or non-participating process is out of scope (it already has write access and can do worse directly). See §11 Threat model.

## 1. Two distinct problems (both grounded)
The original draft conflated these; the reviews separate them.

- **P-OWN — uncoordinated concurrent writers to a shared checkout.** Today's `check.rs:128` keys conflict on the **tool label** (`claim.tool != tool`), so **two `claude_code` (or two `codex`) sessions on one checkout are invisible to the gate** — the actual motivating defect. Visibility ≠ managed: an agent can be `sess:proc:…#live` yet uncoordinated.
- **P-ENG — dormant engagement (Codex's finding).** An agent can be *present* in the room yet have **no watcher/worker draining handoffs** — so addressed work is never picked up (this is exactly why the first Codex handoff sat unread; repo hooks are documented dormant under `codex exec`). Ownership does not detect this.

These need **two invariants**, both host-neutral, sharing the identity foundation (§4).

## 2. Non-goals
- Not a read/visibility lock. Not "every edit needs a managed session." Not a scheduler/wake system. Not a security boundary (§0). Not a replacement for git worktree isolation — it composes with it (§7).

## 3. What already exists (verified — do NOT rebuild)
The audit confirmed against source; the plan **extends**, not invents:
- `rally check before-write` exists (`check.rs:62,99`), returns `allow` + findings (`stop|warn|info`), strict exit 4. Live-owner claim ⇒ `stop`; idle owner (>15m) ⇒ reclaimable `warn` (`check.rs:126-166`). Lease renew via `renew_claim_lease` (`store.rs:1343`); 2h takeover bar.
- Session records are **durable ledger facts** (`kind=="session"`), replayed from `room.facts()` (`lib.rs:5162-5177`); `rally sessions` reads the ledger + a liveness probe (NOT live tmux); reap is explicit `--reap` only. `SessionRegistered|Closed|Revoked` at `event_envelope.rs:152-154`; `prior_managed_session` at `lib.rs:5192`; `rally adopt` at `cli.rs:48,867`.
- Worktrees share ONE `.rally` room — `repo_root()` follows `commondir` to the main checkout (`lib.rs:10837-10884`); `worktree_root()` distinguishes the checkout. So checkout-vs-repo detection lives entirely in the CLI.
- Identity churn is real: lease token is the literal `"live"` (`lib.rs:2271-2281`) ⇒ restart reuses the id, contradicting `mint()`'s fresh-lease docstring (`session_identity.rs:244-249`); build-loop uses a random presence id (`session_probe.py:~91-110`).

**Consequence (H1):** the genuine deltas for P-OWN are small and belong as an **extension of claims + `check.rs`**, not a new fact kind: (a) key conflicts on **stable session id**, not tool label; (b) a **checkout-scoped** claim auto-written at session start, heartbeat-renewed; (c) the own-worktree carve-out; (d) a verdict vocabulary + `handoff_command` in the output. This deletes the earlier "new non-expiring fact kind + reconciler."

## 4. Identity foundation (P0 — shared prerequisite, from design D)
Both invariants need stable identity first. Required:
1. **One session id**, **fresh-per-runtime lease** (restart ⇒ new id), unifying the Rust endpoint id + build-loop presence id.
2. **Re-attach keyed on `endpoint_id` LINEAGE + `tool_type` + `pane_target`** — never `pane_target` alone (H2: else a different agent launched in a reused tmux pane inherits ownership). `endpoint_id` is stable across leases (`session_identity.rs`); use it as the continuity key.
3. **`independent_lifetime` flag** from endpoint class (Cloud/Managed) or host markers (`BUILD_LOOP_WORKTREE_ISOLATED`, launchd/CI) — headless carve-out.
4. **Staleness → reclaim, not permanence (H3).** A crashed *interactive* owner (a Terminal.app tty never appears in any pane list) or a fresh-laptop owner (no tmux/ptyd; `liveness.rs:113-127` returns `Unknown`) must not leave a permanently un-clearable record. Rule: heartbeat-stale beyond the existing 2h takeover bar ⇒ **reclaimable + GC-eligible** (soft-lease semantics, reusing `check.rs` staleness — reinforces H1).

## 5. Invariant I-1 — write ownership (P-OWN)
Extend `rally check before-write --tool <t> --path <p> --session <stable-id> --json` to return:
- **OK** — caller's **stable session id** == owner's, OR no active owner, OR caller is in a **dedicated worktree it owns** (own-worktree carve-out, §7).
- **BLOCK** — a *different* stable session, confirmed live (heartbeat-fresh), writing the **same checkout** as the owner ⇒ refuse + emit `handoff_command`. (Keyed on session id, not tool label — closes the same-tool blind spot.)
- **WARN (allow)** — ownership unverifiable (ledger down/corrupt/absent, degraded capability, ambiguous identity, owner heartbeat-stale) ⇒ allow + advise. Fail-open.
- **EXEMPT** — caller `independent_lifetime` (headless/CI) ⇒ never blocked.

Ownership record = a **checkout-scoped claim** (scope = worktree/checkout root, NOT `repo_id` — C1/M4: `repo_id` "stable non-path id" doesn't exist in source and the `.rally` room already IS the repo scope), session-start-written, heartbeat-renewed, reclaimable when stale. Repo-level **lead** stays a separate, non-blocking concept.

## 6. Invariant I-2 — engagement readiness (P-ENG, Codex)
`engaged(session) = presence ∧ stable_identity ∧ (watcher ∨ managed_worker) ∧ next_action_drain`. Purpose: an agent that is present but cannot/does not drain handoffs is **not** coordination-ready; work routed to it would rot.
- **Detection (ARP):** extend `rally check before-complete` (exists, `cli.rs`) / a new `rally check engagement --tool <t> --json` returning `ready | not-ready(reason)` where reason ∈ {no-watcher, unstable-identity, undrained-actionable}.
- **Enforcement points (host shims):** (a) SessionStart: start a watcher (`--start-watch`) or register a managed worker; (b) **closeout gate** — if `coordination_status` shows peers / unread inbox / `rally next` actionable AND no watcher/managed-worker ran AND no explicit manual drain completed ⇒ closeout **degrades/fails** (WARN-first), telling the agent to drain or start a watcher. This directly prevents the "handoff sat unread" failure.
- **Codex specifics:** `--start-watch` on Codex SessionStart; a **semantic lint + runtime preflight** because repo hooks are dormant under `codex exec` (can't rely on the hook firing).

## 7. Composition with worktree isolation (own-worktree carve-out — sound per audit)
- Worktree isolation *prevents* the race (each committing writer gets its own checkout); I-1 *detects+coordinates* when a checkout is genuinely shared.
- Caller in a **dedicated worktree it owns** ⇒ OK even if unmanaged (separate HEAD/index; git refuses the same branch in two worktrees; residual overlap = ordinary merge conflict). This is **not** a bypass — worktree isolation is the enforced desired end state; I-1 is the residual net for interactive shared-checkout sessions. Detection lives in the CLI via `worktree_root()` (shim passes cwd/path only).

## 8. Enforcement — host-neutral (authority parity yes, enforcement parity no — M1)
- **Authority (one ARP implementation):** the verdicts above. Identical result for any `--tool`. This is the only place logic lives.
- **Enforcement is host-varied and must be stated honestly:**
  | Host | Shim (logic-free; calls CLI, honors verdict) | Gap |
  |---|---|---|
  | Claude Code | PreToolUse(Edit/Write) hook — deterministic | — |
  | Codex | `AGENTS.md` before-edit + `--start-watch` + runtime preflight (hooks dormant under `codex exec`) | model-honor unless preflight runs |
  | Cursor / Gemini | rules/hook → CLI | model-honor |
  | any / OSS / CI | `rally check before-write` in edit path, or git **pre-commit** floor | non-commit writes + `--no-verify` |
- **The git pre-commit floor is a floor, not a ceiling:** it misses non-commit clobbers, is bypassable via `--no-verify`, and `.git/hooks` is unversioned / `core.hooksPath`-overridable. Enforcement parity is therefore **not** claimed; only *authority* parity is. Hosts without deterministic hooks get post-hoc audit + the closeout gate (§6).

## 9. Lifecycle (I-1)
Register (run/adopt, session-start claim) → refresh (heartbeat) → re-attach on reconnect (**endpoint_id lineage + tool_type + pane_target**, §4.2) → clear on explicit stop/revoke OR heartbeat-stale-beyond-takeover-bar GC (§4.4). Transient `Unknown`/quiet never clears; stale-beyond-bar reclaims.

## 10. Rollout phases
- **P0 — Identity foundation (D):** unify id + fresh lease + endpoint_id-lineage re-attach + `independent_lifetime` + staleness-reclaim. *ARP. Blocks all.*
- **P1 — I-1 WARN-only:** session-id-keyed, checkout-scoped claim + extended `check before-write` verdicts; consumers WARN only. Ship git pre-commit floor + Claude/Codex shims. *ARP + hosts.*
- **P2 — I-1 BLOCK on confirmed conflict:** WARN→BLOCK only for confirmed-live same-checkout different-session; fail-open + break-glass retained. *Requires ledger integrity (repair seq 3007 + corruption-resistant append) — else unsafe.*
- **P3 — I-2 engagement:** `check engagement` + `--start-watch` + closeout gate (WARN-first), Codex runtime preflight.
- **P4 — Broaden hosts:** Cursor/Gemini/OSS shims + docs.

## 11. Threat model (M2)
- **In scope:** cooperative agents making *accidents* (two sessions, stale identity, dormant engagement). Accident prevention.
- **Out of scope:** a malicious/non-participating process — it can skip the CLI, set `RALLY_OWNERSHIP_BYPASS=1`, self-declare `independent_lifetime`, or corrupt the ledger (fail-open then allows). Such a peer already has write access; this gate is not a defense against it.
- **Requirement:** every bypass path (break-glass env, EXEMPT, fail-open-on-corrupt) **appends an audit fact** so it is visible after the fact. A peer planting a false owner to DoS a shared checkout is bounded by the reclaim/GC timer (§4.4) and is visible in the ledger.

## 12. Acceptance criteria (expanded per M5)
- [ ] **Two SAME-tool sessions on one checkout get distinct verdicts** (the motivating defect — keyed on session id, not tool label).
- [ ] Verdict parity across `--tool claude_code|codex|cursor` for identical state (authority parity).
- [ ] Ledger unavailable/corrupt ⇒ `warn`+allow (fail-open); break-glass bypass works AND leaves an audit fact.
- [ ] Headless/CI (`independent_lifetime`) ⇒ `exempt`.
- [ ] Different live session, **same checkout** ⇒ `block` + correct `handoff_command`; caller in **own worktree** ⇒ `ok`.
- [ ] Owner restart (new lease) **re-attaches** via endpoint_id lineage + tool_type; a *different* agent in a **reused pane** does NOT inherit ownership (H2).
- [ ] Crashed interactive/no-backend owner ⇒ record becomes reclaimable + GC-eligible after the takeover bar (H3), not permanent.
- [ ] Concurrency: two writers passing `check` simultaneously is documented best-effort (no false "atomic" claim) (M3).
- [ ] Hook-absent / `git commit --no-verify` behavior is defined (post-hoc audit or explicitly accepted gap) (M1).
- [ ] **I-2:** a present session with no watcher + actionable `rally next` ⇒ closeout degrades/fails until watcher started or drain done.

## 13. Risks & mitigations
| Risk | Mitigation |
|---|---|
| Fail-closed lockout on corrupt ledger (live seq 3007) | Fail-open + break-glass + audit fact (§0,§11) |
| Self-expiring / never-clearing ownership | Soft-lease reclaim on staleness, reuse check.rs (§4.4,H1) |
| Pane-reuse ownership hijack | endpoint_id lineage + tool_type re-attach (§4.2,H2) |
| Redundant with worktree isolation | own-worktree = OK; block only same-checkout (§7) |
| Host enforcement divergence | authority parity only; honest §8 table + closeout gate |
| Over-trust of an advisory gate | explicit threat model + best-effort framing (§0,§11) |
| Dormant engagement (present, no drain) | I-2 engagement invariant + closeout gate (§6) |

## 14. Ownership split
- **ARP (Rust):** P0 identity; extend `check before-write` (I-1 verdicts, session-id key, checkout scope, own-worktree, handoff_command); `check engagement` (I-2); staleness-reclaim/GC; ledger-integrity + seq-3007 repair. Coordinate with lead (codex).
- **build-loop + per-host:** logic-free shims (Claude PreToolUse, Codex AGENTS.md + `--start-watch` + runtime preflight, Cursor, universal git pre-commit floor), `independent_lifetime` feeding, closeout-gate wiring, docs. WARN-first.

## 15. Deleted vs v1 (audit-driven)
Removed the new non-expiring fact kind + standalone reconciler (H1 — re-based on claims + `check.rs`); removed `repo_id` as authority key (C1/M4 — checkout scope, room is repo scope); downgraded "hard invariant" to best-effort advisory (M3). Added: I-2 engagement (Codex), threat model (M2), endpoint-lineage re-attach (H2), staleness-reclaim (H3), honest enforcement-parity table (M1), expanded ACs (M5).
