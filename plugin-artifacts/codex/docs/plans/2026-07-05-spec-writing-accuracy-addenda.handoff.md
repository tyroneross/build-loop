# Handoff: Spec-Writing Accuracy Addenda

When implementing F-01, read `skills/spec-writing/scripts/check_checklist.py`, `tests/test_check_checklist.py`, and satisfy T-01 with `uv run pytest tests/test_check_checklist.py`.

When implementing F-02, read `skills/spec-writing/SKILL.md`, `skills/build-loop/references/phase-2-plan.md`, and satisfy T-02 with `python3 skills/spec-writing/scripts/check_checklist.py --plan docs/plans/2026-07-05-spec-writing-accuracy-addenda.md --json --quiet`.

Keep all wording domain-neutral. Do not encode TruePace, ADHD, or planning-app-specific behavior in Build Loop source files.
