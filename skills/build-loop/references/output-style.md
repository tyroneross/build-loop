# Output Style Contract — User-Facing Terminal Output

Final user-facing output (Phase 4 Review-G report, phase status lines printed to the operator's terminal) must be **clear, direct, concise, and free of internal jargon**. This contract is enforced — `scripts/report_lint.py` runs on the draft before the user sees it, and Review-G auto-revises on findings (warn-and-self-heal, never a hard halt).

It extends — does not duplicate — the existing guidance:

- `CLAUDE.md` § "Concise output" — say only what the user needs to decide or act
- `~/.claude/CLAUDE.md` § "Intentional word choice" — every line must transmit information the reader does not already have
- `~/.claude/CLAUDE.md` § "Reporting Work" — pyramid-principle headline + verification line + impact-by-size

Scope: **user-facing output only.** Internal agent-to-agent envelopes (subagent return JSON, judge-decisions, run records, MECE briefs) are structured data and stay as-is — they exist for machines, not the human.

## The five rules

1. **Headline = one plain full sentence** stating what changed. First non-blank line. Not a noun phrase, not a telegraph fragment, not a heading.
2. **Bulleted concrete artifacts.** Below the headline: commit hashes, file paths, issue paths. Concrete things the user can grep, open, or `git show`.
3. **Substance bullets.** What the change captures or does, in plain language. Optional when the artifacts alone are self-explanatory.
4. **Validation line, explicit.** Name the exact command, method, or observer that verified the work, with a status marker:
   - `✅ Verified by <method>` — ran the script, passing test, curl response, IBR scan, demo
   - `⚠️ Untested — <what couldn't be verified and why>`
   - `❓ Uncertain — <what's assumed and what would close it>`
5. **Plain language.** No jargon (see blocklist below). No contrastive pivot (`not X — it's Y`, `isn't X, it's Y`, `not just X but Y`) — state the point directly.

## Jargon blocklist (user-facing only)

These tokens are fine in internal envelopes; they must be translated to plain language in user-facing output:

| Internal token | Plain-language translation |
|---|---|
| `GAP-1`, `GAP-N` | Name the actual gap (e.g. "missing auditor dispatch") |
| `auditor_status: not-run:parent-must-dispatch` | "Auditor not run — parent owes it" |
| `auditor_status: ran:dispatched-agent` | "Auditor ran" or omit if obvious |
| `sub-step G`, `sub-step F` | "Final report step", "Auto-resolve step", or omit |
| `MECE` | "ownership split", "one owner per file" |
| `envelope` | "return data", "result" |
| `scope=build` | "full build review" or omit |
| `suggest_correction` / `yay` / `nay` / `look_again` | "approve", "reject", "needs change", "needs another look" |
| `Phase 4G` / `Phase 5` | "final review", "iterate step" |
| `state.json.runs[]` | "run record" |
| Internal phase codenames (C-HEAL, C-RCA, C-FLOW…) | Describe the behavior, not the code |

## Good — the exemplar (Codex output, captured 2026-06-04)

```
- ad1ac79a memory(build-loop): capture native ax ui follow-up
- Issue: dev/git-folder/build-loop-memory/projects/build-loop/issues/bl-native-ax-light-ui-gap-check.md
- Backlog row added to .../backlog.md

It captures:
- skills/native-ax-driver/ as the home.
- IBR as the primary/full verifier, native AX as the lighter gap/fill layer.
- Blockers: clean main/fresh worktree and recovered AX/screencapture.

Validation passed: python3 scripts/validate_memory_store.py --strict.
```

Why this is good: plain-sentence headline implied by the commit subject, bulleted concrete artifacts with paths and a commit hash, an "It captures:" substance block, and an explicit validation line naming the exact command. Scannable, direct, no jargon.

## Bad — verbose + jargon (rewritten Good)

**Bad (jargon + filler):**

```
Phase 4G Sub-step G completed for the auditor dispatch ladder. The auditor_status
field was changed to ran:dispatched-agent based on the GAP-1 contract; the build
scope=build review-completeness gate passed. We've now wired the MECE envelope
through the build-orchestrator so it propagates to the runs[] entry. Suggested
correction verdict from independent-auditor: yay. Want me to push?
```

**Good (rewrite):**

```
Auditor now runs on every build commit; the gap where nested orchestrators
skipped it is closed.

- Commit: 7e54621 fix(audit): close nested-orchestrator audit gap
- Files: agents/build-orchestrator.md, scripts/audit_before_commit.py
- Run record: .build-loop/state.json runs[] entry build-2026-06-07-...

It captures:
- Nested orchestrators now hand the audit back to the dispatching parent.
- Auditor's approve verdict recorded in .build-loop/judge-decisions.json.

✅ Verified by python3 scripts/test_audit_before_commit.py — 14 passed.
```

## What stays internal (do not lint)

These are structured data for machines, not user-facing prose:

- Subagent return envelopes (JSON return values, `status: blocked | partial`, etc.)
- `state.json` writes
- `.build-loop/judge-decisions.json` entries
- MECE brief packets between orchestrator and implementers
- Rally Point post bodies on internal channels (peer-to-peer coordination)
- `auditor_status` and other machine fields embedded in run records

The lint targets ONLY the final user-facing report markdown + phase status lines printed to the operator's terminal.

## Enforcement summary

- `scripts/report_lint.py <draft.md> --json` runs on every Phase 4G draft
- Findings are WARN (advisory): the orchestrator self-heals by revising the draft once, then emits it
- Never hard-blocks a run — worst case is one revision pass
- The structural lint (`scripts/build_report_lint.py`) keeps running in parallel; it is orthogonal (parallel_batch / merge_plan / evidence triplet shape)
