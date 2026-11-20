# Build principles: think before building, check after

Owner rule (2026-11-20): engineering principles shape the code from the start and are checked at the end. At the start they guide; at the end they verify. Code Audit's `code-principles` skill is the source when installed; the questions below are the self-sufficient fallback and name the matching Code Audit rule.

## Before building: think about these

Answer only the questions the change touches, one line each, in the plan's `## Build Principles` section. Pass the answers to every implementer brief. These are prompts for judgment, not a gate.

1. **Reuse first.** What existing store, path, tool, function or type already does part of this? Extend it. Add a new database, service, file format or helper only when the existing one cannot do the job, and say why. (Code Audit G2, P08)
2. **One source of truth.** Does any value you are about to write already live somewhere: config, manifest, database, environment, a sibling constant? Read it from there instead of copying it. (frozen values and path length)
3. **Shared core, thin adapters.** When two variants exist (two LLM providers, two hosts, two platforms), keep everything common in one path and put only the differences in a small adapter per variant. Add the adapter for a real second variant, not a guessed one. (P09; ports and adapters)
4. **Shortest path.** Count the hops from request to result. Each layer must name what it buys: isolation, a real alternative, a stable contract, or a simpler whole. Drop pass-through wrappers. (G1, G2, P13)
5. **Callers' contract.** Who calls this, and what must they keep getting: inputs, outputs, errors, ordering, timing? (P07, P10)
6. **Failure and partial success.** If this fails halfway, what state is left, who can act on it, and what is the safe next step? Retry at one layer only. (P14 to P19, B3)
7. **Identity.** Does a key, id or file name come from a name, title or role that people can repeat or edit? Use an issued id. (section H)
8. **Numbers and limits.** Where does each timeout, threshold, size or formula come from? Use an observation or a stated assumption, and check boundary inputs in a script. (timeouts, E10)
9. **Effort matches risk.** What does a failure cost? Size tests to it, with at least one test on the real path, not only fakes. (P04, E4)

## After building: check against them

Review sub-step A gives the reviewer the plan's `## Build Principles` answers with the diff. The reviewer applies Code Audit `code-principles` to the diff when it is installed, loading only the sections the change triggers, and otherwise checks the diff against the nine questions above. Every finding names the question or rule it breaks, the location and the smallest fix. Findings go to Iterate like any other review finding.
