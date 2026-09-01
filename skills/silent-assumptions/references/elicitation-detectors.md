<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# The nine detectors, with the real row each was derived from

Every example below is a genuine row from the reference register
(`.build-loop/decisions/2026-09-01-rosslabs-mockup-audit/` in `ross-labs-astro`),
which the user ruled on. Where he overrode the default, that is recorded — it is
the strongest available evidence that the detector found something real rather
than something tidy.

The detectors work on your trajectory, not on your memory of it. Before running
them, assemble the concrete record: files read, commands run, tools called,
figures reported, subagents briefed, fixes landed. Then take each detector to
that list. A detector you cannot point at a specific trajectory entry for did not
fire; do not write a row for it.

---

## 1. `ambiguous-term` — a word with more than one defensible referent

**Scan for:** every noun and adjective in the request that could resolve two
ways. `latest`, `the main page`, `production`, `the tests`, `recent`, `broken`,
`the config`. Resolve each one out loud and check whether a different resolution
was equally defensible.

**Worked row — `latest`, leverage `high`, still unruled.**
"I read 'latest mockups' as most recently edited, not most recently chosen."
File modification time picked an 11-file batch from Aug 31. The gallery's own
selection record (`.mockup-gallery/selected.json`) pointed at April 2026 picks.
Two records disagreed and the agent silently trusted one.

**Why it is high leverage:** the whole audit ran on those files. If the referent
was wrong, every finding describes the wrong artifact.

**The tell:** two sources of truth existed and you consulted one. When a repo
carries an explicit selection, pin, or lockfile, timestamp recency is a *second*
answer, not the answer.

---

## 2. `scope-narrowed` — you did N of M and did not say so

**Scan for:** every count you executed against the count available. Files read
vs files present. Viewports tested vs profiles shipped. Routes, samples, date
ranges, log lines, test cases. Write both numbers down; the gap is the row.

**Worked row — `viewports`, leverage `med`, user overrode to "add tablet".**
"I tested two viewports and called that the risk envelope." Scanned at iPhone 14
and desktop 1440 on the assumption that narrowest-phone and standard-desktop
bracket the failure modes between them. Evidence names what was skipped:
`ipad-air`, `ipad-pro-11`, `iphone-14-pro-max`, `pixel-7`. Tablet widths are
exactly where column counts and sticky headers break.

**The tell:** you described a subset with a word that implies the whole —
"the mockups", "the viewports", "the tests". Say N of M, or write the row.

---

## 3. `rule-applied-or-waived` — a standard invoked, or passed over

**Scan for:** every project rule, standing instruction, or memory you acted on,
and every one that applied and you did not. Both directions are silent calls.

**Worked row — `deadbtn`, leverage `high`, user overrode to "expected in a
mockup", note: "just fix design do not wire buttons if a mockup".**
"I called the dead buttons real defects, though your own rule exempts mockups."
The no-fake-buttons rule was applied as if these were shipping pages; the user's
own instructions explicitly exempt declared mockups and prototypes. Evidence
names the exact selectors and sizes.

**Why the note matters more than the pick:** the user's note is a standing
policy for all future mockup work, not a comment on these three buttons.

**The tell:** you enforced a rule without checking its exemptions, or skipped
one because it felt inapplicable. Cite the rule's own carve-outs.

---

## 4. `tool-output-as-truth` — a tool's model of importance became yours

**Scan for:** every tool whose severity, ranking, score, or verdict you passed
through unchanged. Then, separately, every scan that returned nothing.

**Worked row — `severity`, leverage `med`, user overrode to "re-rank by user
impact".** "I accepted the tool's severity ranking instead of ranking by user
harm." IBR labelled no-handler as error and hick-choice-count as warning; the
agent reported those labels. But they rank by rule type, not by how badly a user
is blocked. On mobile the dead menu button removes the only navigation
affordance on the page, and the tool graded it like any other unwired control.

**The second half, easily missed:** a scan that found nothing is not a clean
result. Reading silence as a pass is itself a silent call, and it is the one
that hides broken instruments. In this same session a contrast rule returned
`null` when it could not measure, and null read as "no failures".

**The tell:** your output inherits a vocabulary you did not choose.

---

## 5. `number-wrong-basis` — a figure whose inputs are assumed

**Scan for:** every number you reported. Name its inputs one at a time and mark
each measured or assumed. One assumed input contaminates the figure.

**Worked row — `whitebg`, leverage `high`, user overrode to "re-measure now by
hand".** "I reported zero contrast failures using a number I know is partly
wrong." Ratios were computed for 29–34 elements per page; where an element had a
transparent background the agent assumed the page was white. Page 09 contains a
near-black container, so text inside it was measured against the wrong
background. Evidence quotes the tool's own semantic warning: page luminance
1.000, container luminance 0.003.

**The tell:** you know the caveat and reported the number anyway, with the caveat
in a different paragraph. If the basis is wrong, the row belongs here even when
you disclosed the gap in general terms — this agent had flagged coverage, but
not *this specific inaccuracy*.

---

## 6. `invented-context` — you filled a field no source supplied

**Scan for:** every audience, persona, goal, threshold, deadline, priority, or
success criterion in your output. Trace each to a source. Anything with no
source, you invented.

**Worked row — `audience`, leverage `high`, user overrode to "panel infers it
from the page".** "I invented the audience the panel is reviewing for." The
agent told a five-persona panel the audience was technical practitioners,
AI-curious professionals, and people evaluating the user's credibility. The user
never defined an audience. Evidence quotes the dispatch verbatim.

**The user's note is the real payload:** "let them discover and don't lead them"
— a standing instruction about how to brief every future panel.

**The tell:** you briefed a subagent. Everything in a brief that did not come
from the user is invented context, and it propagates before anyone can check it.
This is why invented context is almost always `high`.

---

## 7. `assumed-workflow` — you optimised for a workflow that may not exist

**Scan for:** every optimisation target you chose. Precision vs recall. Speed vs
thoroughness. Strict vs lenient. Fail-closed vs fail-open. Each implies a
workflow. Name it and ask whether the user has it.

**Worked row — `recall`, leverage `high`, user overrode to "recall first".**
"I first optimised for precision, and you corrected me to recall." The agent
briefed a build to avoid false positives, assuming a CI gate where noise is
expensive. The user's note: there is no gate; he uses the tool to improve UI by
hand and would rather catch issues than miss them. Under human triage, silence
costs far more than noise.

**This row is the reason the skill exists.** The assumption had already shaped a
code fix's acceptance criteria before it was ever stated out loud.

**The tell:** you can name the cost you were minimising but not the process that
makes it costly.

---

## 8. `static-for-dynamic` — you inspected at rest something that has behaviour

**Scan for:** everything you looked at but did not operate. A page not clicked.
An API described from its schema, not called. A script read, not run. A form not
submitted. A keyboard path not walked.

**Worked row — `static`, leverage `high`, user overrode to "full interaction
session".** "I scanned the pages at rest and never operated them." Evidence
enumerates precisely what went untested: keyboard tab order, the declared
`:focus-visible` ring, `prefers-reduced-motion` behaviour, a `<details>`
disclosure, every hover state.

**The tell:** your finding describes what is rendered, and the user's question
was whether someone can use it. Those are different questions.

---

## 9. `root-cause-not-swept` — you fixed the instances and not the pattern

**Scan for:** every fix you landed. For each, ask whether you searched for other
instances of the same shape.

**Worked row — `pattern`, leverage `high`, user overrode to "audit every rule
for silent skips".** "I fixed the four defects I found and did not check whether
the pattern repeats." The defect shape was a rule returning null instead of
reporting that it could not measure. Evidence names the unaudited files carrying
the same shape: three other presets and the built-in run-all path. A third
instance was later found by someone else.

**The tell:** your fix list matches your discovery list exactly. That is a
coincidence worth one grep.

---

## Calibration: what this session produced

20 rows from one audit: 8 `high`, 8 `med`, 4 `low`. The user ruled on 14 and
overrode 12 of those, writing notes on 8. Six rows were left unruled, one of them
`high` (`latest`).

Two numbers worth carrying:

- **A 60% override rate** means the defaults were defensible but frequently not
  what he wanted. That is the normal case, not a failure — it is the argument
  for the register.
- **8 notes on 14 rulings.** More than half the rulings carried free text, and
  several were standing policy rather than a comment on that row. A design that
  captured only the pick would have lost most of the instruction.

## Anti-patterns that make a register worthless

- **Restating the brief.** "I audited the mockups because you asked me to audit
  the mockups" is not a silent assumption. Filter B exists for this.
- **A row with no consequence.** If you cannot say what breaks, for whom, and
  when, delete it. Filter A exists for this.
- **Vague evidence.** "The scan showed issues" is not evidence. A path, a
  selector, a line number, a PID, a count, or command output is.
- **Binary options on a non-binary call.** Three of the rows the user overrode
  had a third option, and he picked the third twice.
- **Leverage rated by difficulty.** The hardest call in a session is often
  `low`. Rate by what consumed it.
