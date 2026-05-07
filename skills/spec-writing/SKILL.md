---
name: spec-writing
description: Write a build-loop-compatible plan/spec. Walks an 8-item checklist before drafting; runs plan-critic on output. Triggers when build-loop Phase 2 starts OR when the user says "write a plan", "write a spec", "draft a plan for X", "spec out a feature".
version: 0.1.0
user-invocable: true
---

# spec-writing

A skill that walks an 8-item completeness checklist before producing a build-loop-compatible plan markdown, then verifies the output with both a deterministic checker and an adversarial critic before returning.

## When to use

**Invoke when any of these are true:**

- Build-loop Phase 2 starts and no plan file exists yet (`.build-loop/plan.md` is absent).
- User says "write a plan", "write a spec", "draft a plan for X", "spec out a feature", "plan this out".
- A plan already exists but a Review or Iterate step found a spec-completeness gap (auth, rate-limit, input validation, discoverability missing).

**Do NOT invoke for:**

- Trivial fixes (single-file edits, <20 lines, no new endpoint, no architectural boundary crossing). Just fix it.
- Pure Q&A or conversational clarifications.
- Resuming an existing build when a valid `.build-loop/plan.md` already exists and passed `plan-verify` on the previous run.

---

## The 8-Item Checklist

Walk every item before writing a single line of the plan body. For each item, record the answer (or "N/A with reason") inline in a `<!-- checklist -->` HTML comment block at the top of the plan file so the critic can verify it.

### Item 1 — Auth guard utility

**Prompt:** Name the auth guard utility used by similar endpoints (e.g., `requireAuth` from `lib/api-auth-guard.ts`). Reject "match existing pattern" without naming it.

**How to check:**

```bash
grep -r "requireAuth\|getUserIdFromSession\|getServerSession\|withAuth\|verifyToken" \
  lib/ app/api/ src/ --include="*.ts" --include="*.tsx" -l | head -10
```

Pick the most-used pattern (count occurrences with `grep -rc`). Name the function AND the file it lives in. If the feature has no server routes, write "N/A: client-only feature, no server routes."

---

### Item 2 — External API contracts

**Prompt:** Check official docs to verify input/output contracts of external APIs (rate limits, max payload size, error codes) before specifying their use.

**How to check:**

1. List every third-party API call the plan will introduce (search `fetch(`, `axios.`, `openai.`, `anthropic.`, SDK client calls).
2. For each: use Context7 MCP (`resolve-library-id` → `query-docs`) or WebSearch for the current official docs. Note the rate limit, max payload, and error codes you'll need to handle.
3. If no external APIs are in scope, write "N/A: no new external API calls."

---

### Item 3 — Rate-limit acceptance criterion

**Prompt:** Add a rate-limit acceptance criterion any time a paid external API call is in scope (e.g., 10/hour per user for OpenAI TTS).

**How to check:**

```bash
grep -r "openai\.\|anthropic\.\|stripe\.\|sendgrid\.\|twilio\." \
  app/ src/ lib/ --include="*.ts" --include="*.tsx" -l | head -10
```

If the plan introduces any paid API call, the F-criteria table must include a row like:
`Rate-limit | Max N calls/user/hour to <API> | Pass if no 429 under load test`

If no paid API calls, write "N/A."

---

### Item 4 — Discoverability surfaces

**Prompt:** Specify discoverability surfaces for UI features: nav entry path, empty-state CTA copy, first-run hint placement.

**How to check:**

1. If the plan adds a UI feature (new page, new section, new action), answer:
   - Where does the user navigate to find it? (e.g., "Settings → Notifications → New tab")
   - What does the empty state show? (headline + CTA copy, verbatim or a brief spec)
   - Is there a first-run hint, tooltip, or onboarding step?
2. If no UI surface, write "N/A: API/backend only."

---

### Item 5 — Server/client boundary mechanism

**Prompt:** Name the server/client boundary mechanism (e.g., `*-shared.ts` for types + `import 'server-only'` in accessor).

**How to check:**

```bash
grep -r "import 'server-only'\|import \"server-only\"\|use client\|use server" \
  app/ src/ lib/ --include="*.ts" --include="*.tsx" -l | head -10
```

Name the convention the repo uses. If the plan adds server-side data fetching, name which file gets `import 'server-only'` and which file exports shared types. If pure client-side, write "N/A."

---

### Item 6 — Concurrency mechanism per write path

**Prompt:** Specify the concurrency mechanism per write path (e.g., Prisma upsert on unique index, optimistic lock, DB transaction).

**How to check:**

```bash
grep -r "upsert\|createOrUpdate\|transaction\|BEGIN\|COMMIT\|optimisticLock\|version:" \
  app/ src/ lib/ prisma/ --include="*.ts" --include="*.tsx" --include="*.sql" -l | head -10
```

For each new write endpoint in the plan: name whether it uses `upsert`, a DB transaction, or an optimistic lock. If the endpoint is read-only, write "N/A: read-only."

---

### Item 7 — Observability events

**Prompt:** List observability events to emit (e.g., structured log on TTS call with userId + char count; metric for daily TTS spend).

**How to check:**

```bash
grep -r "console\.log\|logger\.\|structuredLog\|emit\|track\|metric\|posthog\|analytics" \
  app/ src/ lib/ --include="*.ts" --include="*.tsx" -l | head -10
```

For each major operation in the plan (API call, job, user action with side effects): name one structured log event to emit and what fields it carries. Minimum: `userId`, operation name, outcome. For paid API calls also include cost/usage metric. If the plan has no side effects, write "N/A."

---

### Item 8 — Input validation at route handler entry

**Prompt:** Validate user input at the route handler entry (e.g., Zod schema on POST body before calling business logic).

**How to check:**

```bash
grep -r "z\.object\|z\.string\|safeParse\|zod\|joi\|yup\|validate(" \
  app/api/ src/api/ --include="*.ts" --include="*.tsx" -l | head -10
```

For each new POST/PUT/PATCH route: name the validation library and schema file. Example: `Zod schema at lib/validators/podcast.ts, called at top of POST handler before any DB access`. If no new routes, write "N/A."

---

## Plan Output Template

After the checklist is complete, write the plan to `docs/plans/<feature-slug>.md` using this structure:

```markdown
# Plan: <Feature Name>

<!-- checklist
Item 1 — Auth guard: <answer>
Item 2 — External APIs: <answer>
Item 3 — Rate-limit criterion: <answer>
Item 4 — Discoverability: <answer>
Item 5 — Server/client boundary: <answer>
Item 6 — Concurrency: <answer>
Item 7 — Observability: <answer>
Item 8 — Input validation: <answer>
-->

## Goal

<One paragraph. What changes, why, what user value it delivers.>

## Locked Decisions

<Table or bullets of decisions already made (stack, API choice, DB schema). Do not re-litigate these in the plan.>

## Scope

<What IS in scope. Then a hard "Out of scope" subsection.>

### Out of scope

<Explicit list. Prevents scope creep during Execute.>

## Six-Commit Table

| # | Commit subject | Files owned | Depends on |
|---|----------------|-------------|------------|
| 1 | feat(...): ... | ... | — |
| 2 | feat(...): ... | ... | C1 |
...

## F-Criteria (functional)

| Criterion | Pass condition | Grader |
|-----------|---------------|--------|
| Auth      | 401 on unauthenticated request | curl test |
| Rate-limit | No 429 under N req/min per user | load test |
...

## Q-Criteria (quality)

| Criterion | Pass condition | Grader |
|-----------|---------------|--------|
| TypeScript | `tsc --noEmit` exits 0 | CI |
| Lint | `eslint` exits 0 on changed files | CI |
...

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| ...  | ...        | ...        |

## Out of Scope

<Mirror of Scope §Out of scope — keeps it visible at the bottom too.>
```

---

## Self-Critic Step

After writing the plan, run both verifiers before returning. Attempt up to 3 fix-and-recheck cycles.

### Step A — Deterministic verifier

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/scripts/check_checklist.py \
  --plan docs/plans/<feature-slug>.md --json
```

Exit 0 = checklist complete. Exit 1 = items missing — revise the plan's checklist block and re-run.

Also run the existing plan-verify script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_verify.py \
  docs/plans/<feature-slug>.md --repo "$PWD" --json
```

Both must exit 0 before proceeding to Step B.

### Step B — Adversarial critic

Dispatch the `plan-critic` agent with the plan file and the JSON from Step A:

```
Agent(subagent_type="build-loop:plan-critic", ...)
```

The critic's output has `strong_checkpoint_count` (its name for WARN findings that require plan revision). Wait for return. If `strong_checkpoint_count > 0`, revise the plan to address each finding, then re-run Step A.

### Convergence

| Attempt | Action |
|---------|--------|
| 1 | Run A + B. If clean, return. |
| 2 | Fix all flagged items. Re-run A + B. If clean, return. |
| 3 | Fix remaining items. Re-run A + B. If clean, return. |
| After 3 | Return the plan with a `FAILED-TO-CONVERGE` header listing both verifier outputs. Let the orchestrator decide. |

---

## Output Convention

1. Write the plan to `docs/plans/<feature-slug>.md` (create `docs/plans/` if it doesn't exist).
2. Commit on a docs-only commit with subject `docs(plans): draft <feature-slug> spec` BEFORE any implementation branches are cut.
3. Copy the plan path into `.build-loop/plan.md` (symlink or copy — copy is fine) so Phase 3 Execute picks it up.

Return the plan path, the checklist answers, and the final verifier JSON to the caller.
