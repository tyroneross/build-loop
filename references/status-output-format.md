<!-- Canonical: how build-loop reports findings, status, and recommendations to a human.
     Adopted 2026-08-18 after three rounds of correction from Tyrone in one session:
     (1) every item needs its consequence, not just its fact;
     (2) headings are action-verb phrases, not sentences with trailing explainers;
     (3) plain words, not internal vocabulary.
     Companion to references/output-style.md (sentence-level) and the style-calibrator
     "Leverage Stack" profile (message logic). This file governs the SHAPE of a status
     block; those govern the sentences inside it. Reference this file, do not restate it. -->

# Status & findings output format

Use this shape for every finding, open item, status update, and recommendation.
One block per item. No preamble, no wrap-up paragraph.

## The block

```
[Action-verb phrase naming the specific thing, with real numbers/names/dates inline]
Why this matters: what breaks, for whom, and when — in one sentence.
 - [Specifics: name every affected item individually, with its own detail]
 - [The fix: what needs to happen, stated as an action]
 - [The ask: EITHER "I've started this and will update you when done"
                OR "I need your approval to do this" — never leave it implicit]
```

## Rules

**Heading is a phrase, not a sentence.**
Action verb + the concrete noun doing or receiving the action. Put real values
inline — model names, counts, dates, file names. No trailing em-dash explainer,
no clause that restates the point.

- ✅ `2 Groq models shut down on 2026-08-16`
- ✅ `Unpushed commits keep the fixes off your users' machines`
- ✅ `Seven broken tests hide the next real break`
- ❌ `A deliberate expiry alarm on your Groq model facts fired 4 days ago — it's a staleness timer, not a bug.`
- ❌ `Groq catalog issue`
- ❌ `There are some test failures that need attention`

Make the noun that carries the stake the subject. `failures hide the next break`,
not `the suite has failures`.

**Second line states the consequence, not a restatement.**
Name what breaks, who it breaks for, and when. It must be falsifiable and
specific. If you cannot write this line, the item probably does not belong in
the response — that is the test for cutting it.

- ✅ `Work quietly goes to the wrong model instead of erroring.`
- ✅ `The people installing your plugin are still hitting every problem we fixed.`
- ❌ `This could cause problems down the line.`
- ❌ `This is important to address.`

**Plain words, not jargon — in the heading and the body.**
Say the thing, not its label. Internal vocabulary reads as precise while
carrying no meaning. Keep a technical term only when the reader must type it
(a filename, a flag, an env var, a command). Otherwise translate it.

| don't say | say |
|---|---|
| private slugs | project names |
| push to origin | get it onto your users' machines |
| resolvers disagree | two lookups pick different models |
| regression | the next real break |
| public-boundary issue | published in your public repo |
| portability fixes | name what they stop breaking |
| T3/T4 routing | which model does the work |

**Name every specific individually.**
"2 models affected" is a headline, not information. List each one with its own
date, replacement, or detail so the reader can act per-item.

**Always close with a decision or a status.**
Every block ends knowing what happens next and who does it. Two forms only:
- `I've started this and will update you when done.`
- `I need your approval to do this.` — and say why approval is needed.

Never end on a finding with no disposition.

## Accuracy

State only what you verified, and say how. If a fact is assumed or unchecked,
mark it. If you previously said something wrong, correct it plainly in one
sentence and move on — do not bury it and do not dwell on it.

Do not inflate impact to make an item sound worth reporting. If the real impact
is low, say it is low.

## Worked example

```
2 Groq models shut down on 2026-08-16, two days ago
Anything still pointing at them fails outright, and the catalog still lists
them as live, so the next person to read it picks a dead model.

 - llama-3.1-8b-instant — shut down 2026-08-16, replacement openai/gpt-oss-20b
 - llama-3.3-70b-versatile — shut down 2026-08-16, replacements
   openai/gpt-oss-120b and qwen/qwen3.6-27b
 - Both were free + developer plan only; enterprise spend unaffected
 - To fix: re-read Groq's official docs, update all 15 entries, mark those 2
   retired, and move the re-check date forward
 - I need your approval — this needs live doc lookups, and I won't move the
   date without doing them
```
