<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Agent Role Taxonomy

This reference answers two recurring coordination questions:

1. Who is lead?
2. Is a "coder", "backend", or "database" entity an agent, a skill, or a task role?

Use this doc with `references/model-tier-mapping.md`: this file defines
responsibility boundaries, while model-tiering defines the model class used for
each role.

## Binding Rule

The lead is the session holding the current valid Rally Point leadership lease.
It is not a hardcoded tool name, default UI label, or mock value. If no valid
lease exists, the first active orchestrator that claims the lease becomes lead
until it transfers, relinquishes, or expires.

```bash
python3 scripts/agent_rally.py lead status --workdir "$PWD" --json
```

When the displayed lead conflicts with the live lease, trust the live lease and
file the display as a Rally Point bug.

## Roles

| Role | Current surface | Default tier | Owns | Must not own |
|---|---|---:|---|---|
| Human/operator | User prompt and explicit gates | n/a | Goal, production push approval, irreversible delete approval, major user-impact decisions | Routine commit cadence, reversible implementation choices |
| Lead orchestrator | `agents/build-orchestrator.md`; Rally Point leadership lease | Thinking | Run plan, phase transitions, ownership partition, dispatch, final judgment, commits, release/report | Pretending peer handoffs are approval, ignoring stale/off-task peers |
| Peer host/session | Rally Point presence/inbox/handoff | Host-selected | Its claimed lane, verdicts, handoffs, review requests | Files claimed by another active peer, silent lead takeover |
| Coder subagent | `agents/implementer.md` | Code | Bounded implementation against owned files and a written spec | Architecture expansion, git staging/commit/push, cross-lane cleanup |
| Domain assessor | `agents/database-assessor.md`, `api-assessor.md`, `frontend-assessor.md`, `performance-assessor.md` | Code or Thinking by complexity | Diagnosis and evidence in one domain | Shipping code by default; use implementer for the fix |
| Architecture/scope specialist | `architecture-scout`, `scope-auditor`, architecture skills | Thinking | Blast-radius tracing, caller impact, scope gaps | Applying the fix after finding the gap |
| Reviewer/auditor | `independent-auditor`, `security-reviewer`, `plan-critic`, `fix-critique`, `synthesis-critic` | Code or Thinking by rubric/judgment mix | Read-only challenge, risk ranking, verdicts | Mutating code under review |
| UI/design specialist | `design-contract-specialist`, `ui-validator`, `ui-design` skill | Code or Thinking by task | UI contract, visual validation, design direction | Business logic outside the UI contract |
| Retrospective/learning agent | `retrospective-synthesizer`, `recurring-pattern-detector`, `self-improvement-architect` | Pattern to Thinking | Pattern extraction, proposals, lessons | Silent promotion of new enforcement |
| Skill | `skills/*/SKILL.md` | n/a | Procedural guidance, routing rules, references, scripts | Acting as a live worker or owner |
| Script/tool | `scripts/*` | n/a | Deterministic checks, writes, summaries | Making LLM judgment calls unless explicitly wrapped by an agent |

## Coder vs Backend/Database Agent

Build-loop already has a dedicated coder subagent: `implementer`. Do not add a
separate generic `coder` agent unless evidence shows agents or users repeatedly
miss that `implementer` is the coding role.

For backend/database work:

1. Use the domain assessor when the problem is diagnostic or cross-layer:
   `database-assessor`, `api-assessor`, `performance-assessor`, or
   `assessment-orchestrator`.
2. Use `implementer` when the fix is scoped to owned files and the "what" is
   decided.
3. Escalate to `scope-auditor` or `architecture-scout` when a persistence,
   schema, API, or data-contract boundary might widen the blast radius.

This keeps roles MECE: assessors diagnose, implementer edits, reviewers judge,
and the lead orchestrator commits.

## Lead Responsibilities

The lead orchestrator must:

- Claim or verify the leadership lease at Phase 1 and renew it at phase starts.
- Keep Rally Point status/watch active when peers, inbox items, or an active
  coord file exist.
- Write task heartbeats for long-running work and pass `--task-ref` into
  status/watch.
- Assign exactly one owner per file/chunk before dispatch.
- Dispatch subagents by role, not by vague labels like "backend agent" unless
  the role maps to one of the surfaces above.
- Remain the single writer to `.git/`; subagents return envelopes, not commits.
- Decide routine reversible choices under `references/leadership.md`.

## Peer Responsibilities

Every peer host/session must:

- Use a stable `tool` id (`claude_code`, `codex`, `cursor`, etc.).
- Publish presence and lane ownership before mutating shared files.
- Read direct and broadcast inbox messages before acting on a handoff preview.
- Post verdicts or handoffs through Rally Point, not only terminal prose.
- Use `heartbeat --task-ref` during long-running tasks so other sessions can
  distinguish "process alive" from "still on task".

## When To Add A New Agent

Add a new agent only when all are true:

1. The task repeats across builds.
2. The task has a stable input/output envelope.
3. The responsibility is not already owned by one row above.
4. A skill or script cannot express the behavior cleanly.
5. Verification can check the output before the orchestrator accepts it.

If the need is "teach agents how to do X", add or update a skill/reference. If
the need is "perform X repeatedly with a bounded contract", add an agent. If
the need is "compute X deterministically", add a script.
