<!-- Canonical: how build-loop reports findings, status, and recommendations to a human.
     Adopted 2026-08-18 after four rounds of correction in one session. The progression
     is preserved in §7 on purpose — each failed attempt fixed a different level of the
     Leverage Stack while the sentence stayed unusable, which is the lesson.
     Companion to references/output-style.md (sentence level) and the style-calibrator
     "Leverage Stack" profile (message logic). This file governs the SHAPE of a status
     block; those govern the sentences inside it. Reference this file, do not restate it. -->

# Status & findings output format

## 1. Who this is for, and when it applies

You are an agent emitting a **user-facing report**: findings, open items, status
updates, recommendations. Your reader is deciding what to do next, not reading for
comprehension.

**Applies to:** Phase 4 reports, review findings, queue items, handoff documents,
commit-worthy summaries, any list of open work, any recommendation.

**Does NOT apply to:**
- Structured internal envelopes (subagent JSON returns, run records, judge
  decisions, MECE briefs). Those exist for machines and are unconstrained.
- Conversational replies, single-answer questions, and status one-liners. A
  question that wants a sentence gets a sentence.
- Code, commit bodies, and test names, which have their own conventions.

If you are unsure whether a response is a status report, ask: *does the reader have
to decide or act because of this?* If yes, use this format.

## 2. The governing test — write for a cold reader

**If a new agent with zero context read only this line, would it know what to do?**

Not "is it short." Not "is it plain." Is it *actionable by someone who was not in
the room*. A sentence fails when the reader cannot tell whether to remember it,
decide something, act, or ignore it.

This is the rule. Everything in §4 is a consequence of it — a block that satisfies
every rule in §4 and still fails this test is wrong, and the rules are what yield.

## 3. Three things every sentence carries

| element | what it means | good | bad |
|---|---|---|---|
| **Actor — who** | a named person or system | `We`, `you`, `the guard`, `an installed user` | `two lookups`, `the resolvers`, `it` |
| **Object — what** | concrete enough to build or verify | `one code path per model tier` | `different models`, `some issues` |
| **Modality — what kind of statement** | requirement / fact / done / decision owed | `We need X`, `Today we have Y`, `I've done Z`, `Should we A or B?` | anything the reader must infer |

**If you cannot name the actor, you do not yet understand the finding.** Go back to
the code before you write the line.

## 4. The block

```
[Action-verb phrase naming the specific thing, real numbers/names/dates inline]
[One sentence: what breaks, for whom, when.]
 - [Every affected item, named individually, with its own detail]
 - [The fix, stated as an action]
 - [The ask: "I've started this and will update you when done"
          OR "I need your approval to do this" + why]
```

One block per item. No preamble, no wrap-up paragraph.

### 4.1 Heading is a phrase, not a sentence

Action verb + the concrete noun doing or receiving the action, real values inline.
No trailing em-dash explainer. The noun carrying the stake is the subject.

- ✅ `2 Groq models shut down on 2026-08-16`
- ✅ `Unpushed commits keep the fixes off your users' machines`
- ✅ `Seven broken tests hide the next real break`
- ❌ `A deliberate expiry alarm on your Groq model facts fired 4 days ago — it's a staleness timer, not a bug.` (sentence + explainer)
- ❌ `Groq catalog issue` (no verb, no stake)
- ❌ `The suite has failures` (container as subject, not the stake)

### 4.2 Second line is the consequence, not a restatement

What breaks, for whom, when. Falsifiable and specific.

- ✅ `Work quietly goes to the wrong model instead of erroring.`
- ✅ `The people installing your plugin are still hitting every problem we fixed.`
- ❌ `This could cause problems down the line.`
- ❌ `This is important to address.`

**This line is a relevance filter, not decoration.** If you cannot write it, the
item does not belong in the response. That is the test for cutting.

### 4.3 Shape: target state first, then the gap

State what should be true, then what is true today. Diagnosis-first framing —
explaining how the defect works — reads as precision and transfers no decision.
Mechanism belongs in the fix or the spec; it goes up top ONLY when it changes which
option the reader picks.

### 4.4 Plain words, not jargon

The test is not "is this technical." It is: **could a competent engineer who has
never seen this project understand it?**

**KEEP** universal vocabulary — translating it adds nothing and reads as
condescending: `push to origin`, `push to main`, `merge`, `rebase`, `stash`,
`pull request`, `regression`, `race condition`, `cache`, `timeout`, `schema`,
`dependency`, plus filenames, flags, env vars, and anything the reader will type.

**TRANSLATE** project-internal vocabulary, whose meaning lives only in this codebase:

| internal | plain |
|---|---|
| `private slugs` | the short names of your private projects |
| `T3/T4 routing` | which model does the work |
| `the ratchet baseline` | the list of already-known problems the check ignores |
| `public-boundary issue` | published in your public repo |

Plain words alone are not enough. A plainer sentence that still fails §2 is not an
improvement — see the progression in §7.

### 4.5 Name every specific individually

`2 models affected` is a headline, not information. List each with its own date,
replacement, or detail so the reader can act per item.

### 4.6 Always close with a decision or a status

Two forms only:
- `I've started this and will update you when done.`
- `I need your approval to do this.` — and say why approval is needed.

Never end a finding with no disposition.

## 5. Accuracy

State only what you verified, and say how. Mark anything assumed or unchecked.
If you previously said something wrong, correct it in one plain sentence and move
on — do not bury it, do not dwell on it.

Do not inflate impact to justify reporting something. **If the real impact is low,
say it is low.** A reader who catches you inflating once discounts everything after.

## 6. Before you send — verify each block

Run this check on every block. Any "no" means rewrite, not ship.

1. Could an agent with zero context act on the heading alone?
2. Does every sentence name an actor, a specific object, and its modality?
3. Is the second line a consequence, or a restatement in different words?
4. Does the heading lead with the target state rather than the diagnosis?
5. Is every remaining technical term one the reader will actually type?
6. Does the block end with a decision or a status?
7. Would you be comfortable if this line were quoted back with no surrounding text?

## 7. Worked progression — why the near-misses fail

Real sequence from the session that produced this file. Each attempt fixed a
different level and the sentence stayed unusable, because the defect was in message
logic the whole time.

**Original (jargon):** *"The resolvers disagree on T3/T4."*
Project-internal vocabulary; meaningless outside this codebase.

**Attempt 1 — FAILS:** *"Two lookups pick different models."*
Plainer, and no more useful. No actor, no target, no modality. The reader asks "why
would they?" and has nothing to do next.

**Attempt 2 — ALSO FAILS:** *"Ask for the same tier two different ways and you get
two different models back — one path sorts by release date, the other by capability
rank."*
Accurate, and still wrong. Diagnosis-first: it explains the internals, so the reader
now understands the bug and still has to work out what to do about it.

**PASSES:**
```
We need one code path per model tier
Today we have 2+ paths, with inconsistent results
```
Actor (`we`), specific object (`one code path per model tier`), modality (`need` =
requirement). Target state first, gap second. Longer than attempt 1 — length was
never the goal.

## 8. Worked example — a full block

```
2 Groq models shut down on 2026-08-16
Anything still pointing at them fails outright, and the catalog lists them as
live, so the next person to read it picks a dead model.

 - llama-3.1-8b-instant — shut down 2026-08-16, replacement openai/gpt-oss-20b
 - llama-3.3-70b-versatile — shut down 2026-08-16, replacements
   openai/gpt-oss-120b and qwen/qwen3.6-27b
 - Both were free + developer plan only; enterprise spend unaffected
 - To fix: re-read Groq's official docs, update all 15 entries, mark those 2
   retired, and move the re-check date forward
 - I need your approval — this needs live doc lookups, and I will not move the
   date without doing them
```

## 9. Escape hatches

- **Nothing to report.** Say so in one line. Do not manufacture blocks to fill a
  report.
- **You cannot name the modality.** That means you do not know whether it is a
  requirement, a fact, or a decision. Find out before writing, or state the
  uncertainty explicitly as the finding: *"I do not know whether X is intended
  behaviour or a defect; deciding needs Y."*
- **The item is genuinely trivial.** One line, no block. Format overhead on a typo
  is noise.
- **A rule fights the cold-read test.** §2 wins. Say which rule you broke and why.
