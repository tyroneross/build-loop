# test_trigger_phrases.py

**Purpose:** Assert that every skill's `description:` field in YAML frontmatter contains the trigger phrases the orchestrator expects to see, so the runtime's skill-resolution heuristic actually fires when a user types those phrases.

## What problem does this solve?

Claude Code resolves which skill to load partly by matching the user's request against each available skill's `description`. If a skill's description doesn't mention the phrase the user is likely to type, the skill won't auto-trigger. The user has to fall back to invoking the skill by qualified name (`Skill("plugin:name")`), which most users don't know to do.

This is a category of silent failure. The skill is registered correctly, the description is well-written prose, but the description doesn't include the canonical trigger words. There's no diagnostic — the skill simply doesn't appear in the runtime's selection set when it should.

A second category: descriptions that are too long. Claude Code truncates descriptions over a threshold (currently around 1024 characters in the matcher's effective window). A long description that buries trigger phrases past the truncation point silently degrades match quality.

This test guards both. It maintains a curated `(skill_name, expected_trigger_phrase)` list, and asserts that each phrase actually appears in the named skill's description. It also asserts every description is under the maximum length.

## How it works (algorithm)

The test module ships a hardcoded `EXPECTED_TRIGGERS` mapping from skill name to the set of phrases that skill must mention:

```python
EXPECTED_TRIGGERS = {
    "build-loop": {"build", "implement", "create", "ship", "wire"},
    "optimize": {"run optimization", "make this faster", "speed up", "reduce"},
    "research": {"research", "investigate", "compare options"},
    "plan-verify": {"plan", "verify"},
    "debugging-memory": {"debug", "fix", "why is this failing"},
    "logging-tracer": {"add logging", "add tracing", "no logs"},
    ...
}
```

For each entry, the test:
1. Locates the skill at `skills/<name>/SKILL.md`.
2. Parses the YAML frontmatter and extracts `description:`.
3. Asserts each expected phrase appears (case-insensitive) in the description.
4. Asserts the description length is under the max threshold.

A separate test class asserts every skill on disk has a non-empty `description:` (catches the brand-new-skill-shipped-without-description failure mode).

## Inputs and outputs

- **Inputs:** all `skills/<name>/SKILL.md` files in the repo.
- **Outputs:**
  - stdout: unittest output. Failures name the skill, the missing phrase, and a snippet of the current description for context.
  - exit code: 0 if all pass; non-zero on the first hard failure.

## Worked example

```bash
python3 scripts/test_trigger_phrases.py
```

Output:

```
test_descriptions_under_max (...) ... ok
test_every_expected_trigger_is_covered (...) ... ok
test_every_skill_has_description (...) ... ok
test_no_unexpected_drift (...) ... ok

----------------------------------------------------------------------
Ran 4 tests in 0.007s

OK
```

When the `optimize` skill's description was rewritten and the phrase "speed up" was accidentally deleted:

```
AssertionError: skill 'optimize' missing expected trigger phrase 'speed up'.
Current description: "Metric-driven optimization with DOE by default. Triggers on 'run optimization', 'optimize this', 'make this faster', 'reduce <metric>'..."
```

The fix is either to add the missing phrase back to the description (preferred — the curated list reflects what users actually type) or, if the phrase is genuinely no longer relevant, to update `EXPECTED_TRIGGERS` with a comment justifying the change.

## Edge cases and known limits

- **Curated list, not derived:** `EXPECTED_TRIGGERS` is hand-maintained based on user behavior observation, not derived from the descriptions themselves. The whole point is to assert that the descriptions match the user behavior, not to assert they're internally consistent. This means adding a new skill requires adding its expected triggers to the test; otherwise the test silently passes for that skill.
- **Case-insensitive substring match:** "Run Optimization" and "run optimization" are equivalent; "optimisation" (British spelling) is distinct. If a description uses a synonym, that's a different phrase and the curated list must include both.
- **Description length:** the threshold is set conservatively (1024 chars). The actual runtime threshold may be larger, but staying well under it provides headroom for description tweaks without retesting.
- **Frontmatter parsing:** uses a small inline parser, not PyYAML, so unusual frontmatter quoting (multiline strings, anchors) may not parse correctly. In practice all build-loop SKILL.md files use the simple `key: value` form.

## Verification / how do we know it works

The test caught two regressions during build-loop 0.7.x → 0.8.x:
1. The `authentication` skill's description was rewritten to focus on Better Auth and lost the "Google OAuth" phrase that users actually type.
2. The `mcp-builder` skill's description got too long after listing supported runtimes; truncation pushed "MCP server" past the matcher's window.

In both cases the test failed before merge, the description was edited, and the test passed.

## Related files

- `skills/<name>/SKILL.md` — the files under test
- `skills/plugin-tests/SKILL.md` — describes when this test runs
- `agents/build-orchestrator.md` §Phase 4 Review-B — auto-dispatch
