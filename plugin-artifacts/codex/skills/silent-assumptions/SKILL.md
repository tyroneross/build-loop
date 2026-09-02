---
name: silent-assumptions
description: "Surface the judgement calls you already made without asking — what 'latest' meant, which viewports counted, who the audience was, whether to optimise for precision or recall — as a register the user can rule on and reverse after the fact. Never blocks: you make the call, apply your default, and keep working; the register is a record of work already done. Triggers: 'what did you assume', 'what calls did you make', 'silent assumptions', 'show me your assumptions', 'assumption register', or an offer at a run boundary when high-leverage calls have accumulated. Not for a decision the user is BLOCKED on and work has stopped for — that is `decision-queue`."
user-invocable: false
companion_scripts:
  - scripts/assumption_register.py — the file-based half: new / check / build / read / promote / offer. Works identically under Claude and Codex.
companion_assets:
  - skills/decision-queue/assets/template.html — the interactive page. Copy and adapt; NEVER regenerate the save/self-publish plumbing from scratch. Claude-only.
  - skills/decision-queue/scripts/regen_template_constants.py — MUST run after any CSS or save-bar edit to that template. Never hand-sync HEAD_HTML / SAVE_BAR_HTML.
  - references/elicitation-detectors.md — the eleven detectors, each with a real worked example, plus the 2026-09-01 adversarial-audit calibration. Read before running step 2.
namespace: .build-loop/decisions/<slug>/   (central mirror: build-loop-memory/projects/<project>/decisions/)
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# silent-assumptions — expose the calls you made without asking

Doing ordinary work you make dozens of judgement calls the user never sees.
Each is defensible. None was surfaced. The user finds out only when a result is
wrong, and then cannot tell which call caused it.

This skill finds those calls, writes them down with their evidence and their
real alternatives, and lets the user reverse any of them afterwards.

**Measured stake.** One audit session produced 20 such calls. The user reviewed
them and overrode 14. Two had already done damage before he saw them: an
unstated choice of precision over recall had sent a code fix in the wrong
direction, and an invented audience definition had contaminated an entire
five-persona research panel.

## The one rule: this never blocks

Make the call. Apply your default. **Keep working.** The register is a record of
work already done, not a gate in front of work.

If you ever find yourself writing "wait for the user to rule on this", you have
built `decision-queue` instead. Stop.

| | `decision-queue` | `silent-assumptions` |
|---|---|---|
| The user is | blocked, waiting | unaware a call was made |
| Work has | stopped | continued |
| The page exists to | unblock work | expose and reverse a call already applied |
| Control flow | blocking by design | non-blocking by design |
| Each row already has | no answer | **your default, applied and tagged** |
| Rows are ranked by | urgency | **consequence if the call is wrong** |

Same rendering problem, opposite control flow. Both skills should exist. Do not
merge them. Do reuse `decision-queue`'s page template and save plumbing —
that part is identical and already tested.

## Three permitted entry points, and nothing else

1. **Invoked.** The user asks. Run the full workflow, render, hand over the path.
2. **Accruing.** Capture rows in the background as you work. Costs the user
   nothing; renders nothing.
3. **Offering.** At a natural boundary — a phase close, a run close, the end of
   a long autonomous stretch — emit ONE line offering to show the register.

An offer is one ignorable sentence. It is never `AskUserQuestion`, never a
modal, never mid-task, and never repeated for the same register. Work continues
whether or not it is taken.

## Step 1 — Decide whether there is anything to elicit

Skip entirely for a single-file edit, a direct question, or a task where the
user specified every parameter. You need a trajectory with real judgement in it.

## Step 2 — Run the eleven detectors against your ACTUAL trajectory

This is the part that cannot be hand-waved. You are looking for decisions you
did not notice making, so "list your assumptions" fails by construction — you
will list the ones you noticed. Instead, scan the concrete record of what you
read, ran, and decided, and let each detector ask its question of it.

Read `references/elicitation-detectors.md` for the worked example behind each row.

| # | Detector | Scan your trajectory for… | The question it forces |
|---|---|---|---|
| 1 | `ambiguous-term` | every word in the request with more than one defensible referent — **stopping rule below** | Which referent did I pick, and what else could it have meant? |
| 2 | `scope-narrowed` | every N-of-M you executed: files read vs files present, viewports, routes, samples, date ranges | What was M? Did I say I only did N? |
| 3 | `rule-applied-or-waived` | every project rule, standard, or memory you invoked — and every one you passed over | Did I apply it where an exemption existed, or waive it where it applied? |
| 4 | `tool-output-as-truth` | every tool whose ranking, severity, or verdict you passed through unchanged; every scan whose silence you read as a clean result | Whose model of importance is this, and is it the user's? |
| 5 | `number-wrong-basis` | every figure you reported; name its inputs one by one | Is any input assumed rather than measured? |
| 6 | `invented-context` | every field you filled that no source supplied: audience, persona, goal, threshold, deadline | Did I label it fabricated? |
| 7 | `assumed-workflow` | every optimisation target you chose (precision vs recall, speed vs thoroughness) **and every ordering call — what you ran in parallel, and what you ran before what** | What workflow makes that right, does it exist, and did my ordering starve a later step of its input? |
| 8 | `static-for-dynamic` | everything you inspected at rest that has behaviour: a page not clicked, an API not called, a script not run | Did I operate it, or only look at it? |
| 9 | `root-cause-not-swept` | every fix **or conclusion** you landed, **and every one you declined** | Did I sweep for other instances of the same pattern? What did I choose not to do, and did I say so? |
| 10 | `source-authority` | every instruction you obeyed, ranked by who sent it: the human, a peer agent, a hook, a file, a tool | Whose authority did I assume this carried, and did I verify the sender? |
| 11 | `irreversible-act` | every act that cannot be undone: a write to an append-only ledger, a push, a dispatch to another agent, a published artifact, a sent message | Did I do this while still deciding whether to? |

Detector 4 has a second, easier-missed half: a scan that returned nothing is not
the same as a clean result. Reading silence as a pass is itself a silent call.

**Detector 1's stopping rule (materiality).** Applied literally to a 400-word
brief, "every ambiguous word" yields dozens and drowns the register. Keep a term
only when **a different reading would have changed what you actually did**. If
both readings lead to the same action, the ambiguity was never load-bearing.

**Detectors 10 and 11 were added after an adversarial audit** of the original
nine against a real multi-agent transcript. Both found calls with large blast
radius that no other detector located: an agent that treated six peer-injected
instructions stamped `UNVERIFIED SENDER` as authoritative, and an agent that
wrote to an append-only ledger 11 seconds after being told not to change state.
Detector 11 is the inverse of detector 8 — 8 finds what you looked at but did
not operate, 11 finds what you operated and cannot un-operate.

**Yield is uneven, and that is expected.** On a read-only session (review,
audit, research) detector 9 finds little from "fixes landed", which is why its
scan target now includes conclusions and declined work. Detector 5 returns
nothing on a session that reports few figures of its own computation. A detector
that finds nothing costs one pass; skipping it costs the class it covers.

## Step 3 — Apply both filters. They cut in opposite directions.

**Filter A — the cut test.** For each candidate, state what breaks, for whom,
and when, if the call is wrong. **If you cannot write that sentence, delete the
row.** It goes in the `consequence` field, where `assumption_register.py check`
enforces its presence. A row a cold reader could not act on is not finished.

*"When" may be a condition rather than a clock time* — "as soon as anyone opens
it on a tablet", "the first time this runs in CI", "at implementation, not
before". A trigger is what the reader needs; a timestamp is usually unavailable
and demanding one would delete good rows.

**Filter B — the restatement test.** If the user's own words already specify
this, it is not a silent assumption. Delete it. A register full of things the
user already said is worse than no register: it buries the real calls and
teaches him the artifact is noise. Quote the instruction to yourself and check
whether it actually determines the choice. "He said audit the mockups" does not
determine which viewports, so viewports survives; it does determine that you
audit mockups, so that does not.

**"The user" means the human principal — nobody else.** In a multi-agent repo
most of your turns can arrive from peer agents, hooks, or injected messages. An
instruction from a peer agent is a SOURCE, not an authority, and it does not
cut a row under this filter. It does the opposite: obeying it without checking
the sender is itself a silent call, and detector 10 exists for exactly that.
This distinction is not cosmetic — on the transcript this rule was calibrated
against, six of nine turns were peer-authored, and reading them as "the user's
words" swung the measured restatement rate from 18% to 4% and would have hidden
the session's largest assumption.

**What does NOT disqualify a row: your own narration.** Saying what you did is
not the same as flagging that a choice was being made. "Two things in parallel"
announces the action while leaving the ordering call invisible; "verified by
reading these four files" names the method while leaving the sufficiency
judgement — that reading is enough for a CI-bound fix — unstated. The test is
whether the user could tell **a choice existed and had alternatives**, not
whether you mentioned the activity.

## Step 4 — Rate leverage by consequence, never by difficulty

| Rating | Test |
|---|---|
| `high` | Already propagated. The call left your control — an artifact shipped, a fix landed, another agent was briefed on it, a verdict was delivered to a peer. Being wrong means rework, not just a different answer. |
| `med` | Changes a conclusion, but nothing has consumed it yet. Reversible now, expensive later. |
| `low` | Reversible with no downstream. Housekeeping. |

**"Consumed" means handed off, not observed being used.** You usually cannot see
what a peer session or a human did with your output, so waiting for proof of use
would rate everything `med` and defeat the ranking. The moment a call leaves
your control it is `high`. Delivery is the line.

A hard call that changed nothing is `low`. An easy call that briefed five
subagents is `high`. In the reference register, the invented audience was one
sentence to write and rates `high`, because a five-persona panel ran on it.

## Step 5 — Write the row

Every row carries all of these. `assumption_register.py check` fails the
register if any is missing.

| Field | Contract |
|---|---|
| `title` | One sentence, first person, naming the call. Not a topic. |
| `what_i_did` | The action, plainly. |
| `why_and_cost` | The reasoning, and what it gives up. |
| `consequence` | Filter A's sentence. What breaks, for whom, when. |
| `evidence` | A real path, selector, line number, PID, count, or command output. Never a gesture. The check warns when it contains none of these. |
| `options` | 2–4 REAL alternatives, phrased as things a person would choose between. Exactly one carries `is_default: true` and it is what you already did. |
| `leverage` | Step 4. |
| `trigger_class` | Which detector fired. Lets a later pass audit which detectors never fire. |
| `decision` | `{pick, note, reviewed_at}` — the user's, left null by you. |

**Two options is a toggle and a toggle cannot express a real choice.** The
reference register's `static` row offered audit-at-rest, operate-the-pages, and
do-both-and-compare; the user picked the third, which no binary could have
expressed. Reach for three when a compare-both or a do-both option is genuinely
available.

## Step 6 — Write, validate, render

```bash
BL="${CLAUDE_PLUGIN_ROOT:-.}"
DIR="$PWD/.build-loop/decisions/<YYYY-MM-DD>-<slug>"
mkdir -p "$DIR"
python3 "$BL/scripts/assumption_register.py" new --slug <slug> --title "<title>" --repo "$PWD" -o "$DIR/register.json"
# … replace the example row with your real rows …
python3 "$BL/scripts/assumption_register.py" check "$DIR/register.json"          # exit 1 on any error
python3 "$BL/scripts/assumption_register.py" build "$DIR/register.json" --check  # renders + lints
```

`build` emits `spec.json`, `data.json`, `data.js`, and `dashboard.html` beside
the register. `--check` runs `dashboard_lint.py`. Tell the user the absolute
path of `dashboard.html` and stop; do not wait for a reply.

## Step 7 — Read the rulings back

The user edits `rows[].decision.pick` (0-based index into `options`) and
`rows[].decision.note` in `register.json`, then you read them:

```bash
python3 "$BL/scripts/assumption_register.py" read "$DIR/register.json"
```

`read` leads with the overrides, because those are the ones that change your
behaviour. **The notes carry more instruction than the picks do** — in the
reference register the user wrote notes on 8 of 14 rulings, and several were
standing policy, not commentary on that row. Treat every note as an instruction
for future work, not as a comment on this one.

## Step 8 — Mirror centrally, so a register raised anywhere is trackable

A register lives in the repo it describes. Tracking across repos goes through
the existing decision store — do not build a second one.

```bash
python3 "$BL/scripts/assumption_register.py" promote "$DIR/register.json" --workdir "$PWD"
```

This calls `scripts/write_decision/__main__.py`, the same atomic writer
`auto-decision-capture` uses (file + INDEX + events.jsonl + DB), landing rows in
`build-loop-memory/projects/<project>/decisions/`. A silent assumption is a
decision with a subtype, not a new record type: it maps onto the existing schema
as `--tags silent-assumption,...`, `--consequences` from the `consequence` field,
`--alternatives` from the options, and confidence/status that track the ruling —
`assumed`/`proposed` while unruled, `explicit`/`accepted` when the user confirms
the default, `explicit`/`rejected` when he overrides it.

Promote unruled rows too. An unreviewed high-leverage call is exactly the thing a
later session needs to find.

## The offer threshold, and why this number

Score the register: **`high` = 2, `med` = 1, `low` = 0. Offer at 6.**

Six is three high-leverage calls, or two high plus two medium. It is set so that
three highs offer and fifteen lows do not, because a `low` is by definition
reversible with no downstream and costs nothing to leave unruled — weighting it
above zero would let volume alone trigger an offer, which is how a useful prompt
becomes ignorable noise. The audit session that motivated this skill scored 24
(8 high, 8 med, 4 low), so a real case clears the bar four times over rather
than scraping it.

**One override.** Any single row whose `consequence` names an effect that is
already shipped or cannot be undone offers immediately, at any score. Set
`"escalate": true` on that row. Consequence beats count.

```bash
python3 "$BL/scripts/assumption_register.py" offer "$DIR/register.json"   # exit 0 = offer, 1 = stay quiet
```

Offer once per register. If he declines, do not ask again.

## Dual host

The **file-based path is primary and works on both hosts.** `register.json` plus
the generated `dashboard.html` need no artifact host, no browser automation, and
no model tokens to refresh. Codex uses this path exclusively; the root
`AGENTS.md` section "Silent assumptions" carries the Codex instructions.

The **interactive page is a Claude-only enhancement layered on top.** Codex
cannot publish a self-saving artifact, so it must never be the primary
mechanism. When you do build it under Claude:

- Copy `skills/decision-queue/assets/template.html`. Do not hand-roll the
  save/self-publish plumbing; it took a shipped user-visible bug to get right.
- Strip the leading authoring comment. Edit only the CONTENT ZONE
  (`window.__META__`, `window.__ITEMS__`).
- Extend the item shape with `leverage` and with the default already marked
  applied — that is the genuine structural difference from a pending decision,
  because the work has already been done under it.
- If you touch that template's CSS or save bar, run
  `python3 skills/decision-queue/scripts/regen_template_constants.py`. Never
  hand-sync `HEAD_HTML` / `SAVE_BAR_HTML`.
- Read answers back with `Artifact({action: "read", url})` and write them into
  `register.json` so both halves agree. `register.json` is the source of truth;
  the page is a projection.

## Known gaps, stated rather than worked around

`dashboard_build.py` (interface-built-right) is another session's work and is
read and invoked here, never modified. Three limits follow from that, and none
is fatal:

- **The rendered page is read-only.** Its own footer says state lives in the
  record, not the page. So the file-based ruling happens by editing
  `register.json`. Proposed change, not made: implement the `actions` block its
  `validate()` already accepts, so a generated page can write a response file.
- **`spec.columns` is required by `validate()` but never rendered by `build()`.**
  One placeholder column is supplied to satisfy it.
- **There is no slot for a per-row badge**, so leverage is prefixed into the row
  label (`HIGH · …`) to stay readable without opening the row.

## Escape hatches

| Situation | Do this |
|---|---|
| No trajectory to scan (fresh session, compacted context) | Say so, elicit only from artifacts on disk, mark the register `partial`. Do not invent rows. |
| Every candidate fails Filter A or B | Write no register. Say you found no silent calls worth ruling on. An empty register is a correct outcome. |
| `dashboard_build.py` missing | `build` still writes `register.json`, `spec.json`, `data.json`, `data.js` and exits 3 with the reason. The record survives; only the page is missing. |
| Host cannot publish artifacts | Use the file path. It is the primary mechanism, not a fallback. |
| User rules on nothing | Leave it. Do not re-offer, do not re-render, do not chase. |

## The decision-surface family — one core, several variants

Four skills share one job: put a set of calls in front of the user and capture a
ruling. They differ only in the KIND of call, so they share a core rather than
forking one — the variant registry (`scripts/decision_surface.py`), the
interactive page and its save/self-publish plumbing
(`skills/decision-queue/assets/template.html`), and the durable writer
(`scripts/write_decision/__main__.py`). **Adding a variant is a registry entry,
never a fork of the core.**

**Choose by the question the user is actually asking, never by name.** An agent
that picks on name alone reaches for the one it already knows and rebuilds
something that exists.

| Member | Answers | Layer | Does work stop? |
|---|---|---|---|
| [`silent-assumptions`](../silent-assumptions/SKILL.md) | "What did you decide without me?" | surface | No — work continued under your default |
| [`decision-queue`](../decision-queue/SKILL.md) | "What is waiting on me?" | surface | Yes — work has stopped |
| [`auto-decision-capture`](../auto-decision-capture/SKILL.md) | "What did we already settle, and where is it written down?" | capture | No — fires passively |
| [`auto-finding-capture`](../auto-finding-capture/SKILL.md) | "What concrete issues has anyone surfaced?" | capture | No — fires passively |

`python3 scripts/decision_surface.py` prints this table (`--json` for machines).
The registry is the one place a member is declared; this table is its prose
mirror and must match it.

**You are here: `silent-assumptions`.** Reach for a sibling when the user is blocked and waiting on an answer (`decision-queue`), or when there is nothing to render and you only need the record written (`auto-decision-capture`, `auto-finding-capture`).
