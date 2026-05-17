# Build-Loop

Build-Loop is a Claude Code plugin that runs multi-file code changes through a disciplined, cost-tiered development loop and refuses to ship unverified output.

## The loop runs in five phases, plus an optional sixth

1. **Assess:** Maps the current state of the project, defines the goal in one concrete sentence, and sets 3–5 pass/fail scoring criteria before any code is written.
2. **Plan:** Breaks the work into dependency-ordered tasks with exact file paths and flags which groups are safe to execute in parallel.
3. **Execute:** Dispatches parallel Sonnet subagents for independent file groups, each with minimal context and a clear integration contract.
4. **Review:** Runs six ordered sub-steps (critic → validate → optimize → fact-check → simplify → report) as a single gated exit point; failures route to Iterate.
5. **Iterate:** Diagnoses the root cause of each failed criterion, applies a targeted fix, and re-validates, with a hard stop at five iterations.
6. **Learn** *(optional)*: Detects recurring patterns across past runs and auto-drafts experimental skills with A/B tracking, auto-promoting on metric wins when enabled.

## Model tiering drives roughly 4× cost efficiency

- **Opus 4.7** at boundaries: planning, final sign-off, ambiguity resolution
- **Sonnet 4.6** inside: code execution, adversarial critic, fact-checking
- **Haiku 4.5** for pattern-matching: mock-data scanning, recurring-pattern detection

## Guardrails block unverified output from reaching production

Three gates run in parallel during Review and must pass before deploy:

- **Fact-check:** every rendered metric traces to a real source
- **Mock-data scan:** production paths contain no lorem ipsum, `Math.random()` in display code, or placeholder values
- **Architecture check:** no circular dependencies, layer violations, or direct frontend-to-DB access (when NavGator is installed)

A PostToolUse hook blocks `git push`, `npm publish`, `vercel deploy`, and `gh release` until the fact-check gate has completed.

## Use it for multi-file work; skip it for quick edits

- **Use:** refactors, new features, migrations, cross-cutting changes
- **Skip:** single-file edits, config tweaks, fixes under ~20 lines
