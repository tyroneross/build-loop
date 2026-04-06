# LLM Eval Reference

Loaded on demand during Phase 2 (grader design) and Phase 5 (validation).

## Grading Hierarchy

1. **Code-based** (preferred): fast, deterministic, cheap. Test pass/fail, lint clean, build succeeds, schema validation.
2. **LLM-as-judge**: for nuanced criteria code can't check. Binary pass/fail only.
3. **Human**: last resort. Only for calibrating automated graders.

## LLM-as-Judge Rules

- **Binary pass/fail only**. No Likert scales. Categorical decisions are more reliable.
- **One evaluator per dimension**. No multi-dimension God Evaluator.
- **Judge reasons, then decides**. Think in thinking tags, output only pass/fail.
- **Use the running Claude instance as judge**. No external API calls.

## Judge Prompt Template

```
You are evaluating whether code meets a specific criterion.

<criterion>
{criterion_description}
</criterion>

<pass_condition>
{what_constitutes_a_pass}
</pass_condition>

<evidence>
{code_output_or_screenshot_or_test_result}
</evidence>

Think through your evaluation in <thinking> tags.
Then output exactly one word: PASS or FAIL.
```

## Code-Based Grader Patterns

```bash
npm test 2>&1; echo "EXIT:$?"              # pass if EXIT:0
npm run lint 2>&1; echo "EXIT:$?"           # pass if EXIT:0
npx tsc --noEmit 2>&1; echo "EXIT:$?"       # pass if EXIT:0
npm run build 2>&1; echo "EXIT:$?"          # pass if EXIT:0
```

## Scorecard Format

```markdown
## Scorecard: [feature] — [date]

| # | Criterion | Method | Result | Evidence |
|---|-----------|--------|--------|----------|
| 1 | Tests pass | code | ✅ PASS | exit 0, 47/47 passing |
| 2 | Goal met | llm-judge | ❌ FAIL | Judge: missing X |

**Overall**: N/M PASS | **Iteration**: 1 of 5 | **Action**: Fix criterion 2
```

## Designing Good Criteria

Bad: "Code quality is good" → Good: "No lint errors, all types resolve, no `any` types outside explicit escape hatches"
