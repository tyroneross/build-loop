# Dispatch brief

`# CONFIG: v1.0 | T2 | agent | Agent/Tooling | SCORE: 22/25 [A:4|C:5|Cs:5|D:4|Cp:4]`

Fill this in **before** launching any agent or loop. An agent that was never told where to
report cannot report, and that is not a behaviour you can fix afterwards by asking it to
try harder.

Copy the block, replace every `<...>`, delete nothing. Five fields and five sections.

---

## The frontmatter — every field required

```yaml
---
goal:            <the observable condition that ends this — never a count>
max_iterations:  <hard cap; terminates even if the goal is never reachable>
report_primary:  <rally | commit | the-content-itself | <named channel>>
report_backup:   <path other agents already check>
durable:         <path that survives a clone, or the literal word: none>
---
```

`durable: none` is a valid answer. **Omitting the field is not.** The difference matters:
one is a decision you made, the other is a decision nobody made. `.build-loop/` is
gitignored, so a report written only there dies with the machine — that is the failure this
field exists to force a choice about.

---

## 1. GOAL — what ends this

State the accomplishment as a condition **the agent can check itself**.

> ✅ `docs/architecture/index.json regenerates byte-identically twice, and CI is green on main`
> ❌ `improve the architecture index` — nothing here is checkable
> ❌ `run 30 times` — that is a bound, not a goal

## 2. WHEN DONE

What to produce, and where it goes. Name the artifact, not the activity.

- Produce: `<the concrete thing — a commit, a markdown report, a passing gate>`
- Send to: `<report_primary>`
- Also write: `<report_backup>`, and `<durable>` if the output must outlive the session

## 3. WHEN STUCK OR BLOCKED

**Blocked is a terminal state, not a failure to hide.** An agent that keeps trying past its
bound produces less than one that stops and says why. Name which kind, because they route
differently:

| kind | who unblocks it | what the agent does |
|---|---|---|
| missing credential or secret | the user | stop, report, do not retry |
| waiting on another agent | that agent | post to `report_primary`, keep working the parts you own |
| a decision only the user can make | the user | stop, state the options and your recommendation |
| the goal condition may be unsatisfiable | the dispatcher | stop, report the evidence |

That last row is not theoretical. A watcher polled `! pgrep -f "vitest run ..."` where the
pattern matched the watcher's **own** argv, so the condition could never become true. It
ran two days. `max_iterations` is what would have ended it.

## 4. ITERATION BOUND

`max_iterations` terminates the loop **even when the goal is never met**, because a goal
condition can be unsatisfiable and the agent cannot always tell. Reaching the cap is a
reportable outcome, not a silent stop — say what was achieved and what remains.

## 5. COMMS — primary and backup

**Primary** is the live channel: a rally post, a commit, or producing the content itself.
**Backup** is a file at a path other agents already read, used when the primary fails or no
peer is listening.

Conventions already in use here — pick one, do not invent a new path:

| path | read by |
|---|---|
| `.build-loop/followup/` | the orchestrator's next iterate pass |
| `.build-loop/briefs/` | a dispatching parent |
| rally `inbox/<tool>.jsonl`, `inbox/all.jsonl` | addressed peer / broadcast |
| `build-loop-memory/projects/<slug>/` | **any future session — the only durable one** |

Write the backup **even when the primary succeeds** if the output must outlive the session.
A rally post is live coordination; it is not a record.

---

## Worked example

```yaml
---
goal:            navgator CI is green on all three jobs on main
max_iterations:  5
report_primary:  rally
report_backup:   .build-loop/followup/
durable:         build-loop-memory/projects/navgator/handoffs/
---
```

**Done** → produce a commit per fixed layer plus a markdown summary naming each failure and
its root cause. Post the summary to rally; write it to `.build-loop/followup/`; copy to
`build-loop-memory/projects/navgator/handoffs/` because the next session needs it.

**Blocked** → if a fix needs a credential (an npm token, a repo setting), stop and report;
do not mint credentials. If a job stays red for reasons outside this repo — an upstream API
returning 503 — report it as an external blocker with the evidence, and do not weaken the
gate to get green.

**Cap** → at 5 iterations, stop and report which layers were fixed and which remain, with
the failing output for each. Four stacked defects were found in this repo where each masked
the next, so "the first one is fixed" is not the same as done.
