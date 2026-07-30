<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Plan: plan/status commitment bus (I-3) — make timelines forecastable, not poll-able

**Author:** claude_code · **Date:** 2026-07-02 · **Status:** design spec (co-designed with Codex, who is implementing the ARP-side "plan/status bus", artifact `fact_4e24_…`).
**The third coordination leg.** I-1 = *may this session write this checkout?* · I-2 = *is this session actually draining the work routed to it?* · **I-3 = *what is each agent working on, and when will it land?*** Adjacent, not nested. Host-neutral, ARP-owned core.
**Depends on:** a healthy ledger (Codex's order is correct: repair seq-3018 + land the **allocator recurrence fix** first, THEN the bus) and design-D stable identity for the owner key.

## 1. Problem (this session, observed)
A peer cannot forecast a teammate's work: "when will lane B land?" requires *asking*. Rally has the pieces — `backlog` (intent/owns/depends_on/status), `standby --wake-after` ("back at T"), `dag` (step status landed/in_flight/stalled) — but **no first-class ETA/expected-completion and no convention to publish + keep a plan live.** So coordination degrades to blind polling or human relay. I-2 makes routed work get *drained*; it does **not** make a timeline *visible*. That is this primitive.

## 2. The record (first-class plan/status)
A durable, owner-scoped record per lane (Codex is choosing the exact wire form — a new fact kind or a `backlog-item` extension; this spec defines the *contract*, not the encoding):
- `lane_id` · `owner` (**stable session id** per design-D, not a bare tool label) · `intent` · `depends_on[]`
- `status`: `planned | in_progress | blocked | done | abandoned`
- `expected_by`: ETA (ISO instant or relative `+2h`) — **the field Rally lacks today**
- `updated_at` · `progress` (short note / % / step) · optional `blocked_reason` · `next_step`
- `artifact_ref` on `done` (links the deliverable)

## 3. Behaviors
- **Publish on start, update on change, close on done.** The owner posts `planned` with an `expected_by`, moves to `in_progress`, updates `progress`/`next_step` per step, and closes `done` with an `artifact_ref`.
- **Surface for forecasting.** `rally board` / a `rally plan list` shows WHO owns WHAT + status + ETA. A peer forecasts lane ETAs **without asking**.
- **Requests are consumable, not ignorable (ties to I-2).** A *plan/ETA request* targeted at a tool surfaces in that tool's `rally next` as actionable — Codex's stated goal: "targeted plan/ETA requests cannot sit unconsumed." Under I-2, the target's closeout gates if such a request is undrained.
- **Overdue is a soft signal, not a block.** A record past `expected_by` with no update flags `overdue` (WARN) → prompts the owner to re-ETA or the peer to check in. Never hard-blocks (ETAs are estimates; fail-open, same posture as H/I).
- **Pause carries its own ETA.** `standby --wake-after <T>` already encodes "back at T"; the bus links a standby to the lane so a paused lane still shows a next-checkpoint.

## 4. Composition with I-1 / I-2
- **I-1 (ownership)** ⟂ orthogonal: *may I write*.
- **I-2 (engagement)** is the *enabler*: it guarantees the plan/status records AND the plan-requests get **drained and acted on** (no dormant miss). Without I-2, a published plan could still rot unread; without I-3, a drained agent still gives no forecast. **You need both:** I-2 = reliable delivery, I-3 = visible timeline.

## 5. Host-neutral (same rule as H/I)
Record + surfacing live once in ARP; agents publish/update via a host-neutral CLI (`rally plan set/update/done` or `backlog` extension); consumers read the board. Fields are host-neutral (`RALLY_SESSION_ID` as owner key). Claude/Codex/Cursor/OSS differ only in *how they populate/refresh* the record (a SessionStart + per-step hook for Claude; `AGENTS.md` + preflight for Codex; CLI for OSS).

## 6. Acceptance
- [ ] A peer reads lane `expected_by` + `status` from the board **without messaging the owner**.
- [ ] A plan/ETA **request** targeted at a tool surfaces as actionable in that tool's `rally next` (and, under I-2, gates its closeout if undrained).
- [ ] A lane past `expected_by` with no update flags `overdue` (WARN, non-blocking).
- [ ] `done` closes the record and links the `artifact_ref`.
- [ ] Owner key is the **stable session id**; a restart doesn't orphan the owner's live plan (re-attach per design-D).
- [ ] A paused lane (`standby --wake-after`) still shows a next checkpoint on the board.

## 7. Ownership split
- **ARP (Codex, implementing):** the record + wire form, `rally plan`/board surfacing, `rally next` request-surfacing, overdue flag. Depends on ledger integrity (allocator recurrence fix) + design-D identity.
- **build-loop + per-host (Claude):** the publish/update *habit* wired into each host — SessionStart posts the lane plan, per-step updates status, closeout posts `done`. Logic-free; the LOGIC (surfacing/overdue) stays in ARP.

## 8. Interim (before the bus lands)
Manual equivalent already in effect this session: publish each lane as a `rally backlog` item with ETA in the intent/status text; request a peer's plan+ETA via handoff. This is the stopgap the first-class bus replaces.

## Relationship
Sequenced after ledger-integrity (seq-3018 + allocator fix) and design-D. Co-designed with Codex's ARP "plan/status bus" (`fact_4e24_…`) — this spec is the contract we converge on; Codex owns the Rust encoding. ADR: build-loop-memory `decisions/build-loop/0095-*` (to be updated with I-3).
