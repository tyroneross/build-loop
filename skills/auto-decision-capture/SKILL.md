---
name: auto-decision-capture
description: Project-scoped skill for proactive in-session decision capture. Loads automatically when `.episodic/` is present in the repo. Provides Claude the signal taxonomy, confidence ladder, overwrite rules, and the three extraction prompts (SPO triplet / MADR-aligned / batch consolidation) so substantive decisions land in `.episodic/decisions/` without manual triggering. Use when the user makes a substantive choice, confirms a proposal, or implies a constraint with textual evidence.
when_to_use: |
  - User issues a direct verbal marker ("let's go with X", "ship it", "use Y")
  - Claude offered a choice menu and the user picked one
  - User accepts a proposal Claude wrote (action-confirmation)
  - Topic shifts past a proposal that stood without objection (continuation)
  - User states a project-scoped constraint, preference, or convention
  - Topic-coherent inference can be drawn with quotable evidence (tier 3)
namespace: .episodic/decisions/ (at repo root)
companion_scripts:
  - scripts/write_decision.py — atomic writer (file + INDEX + events.jsonl + DB)
  - scripts/scan_transcript_for_decisions.py — Stop-hook batch sweep (tier 3)
  - scripts/supersede_decision.py — explicit replacement
  - scripts/revoke_decision.py — explicit withdrawal
  - scripts/recall.py — "have we decided this before?" lookup
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross | SPDX-License-Identifier: Apache-2.0 -->

# auto-decision-capture — proactive decision capture during sessions

This skill makes Claude an active participant in maintaining the project's
decision log. The user does not have to remember to write decisions, and
does not have to invoke a slash command. When evidence is in the
conversation, Claude writes; when evidence is weak, the end-of-session
Stop hook batches inferences for review.

## Core principle: signal-based, NOT turn-counted

There is no "wait N turns" rule. The threshold is **"is there textual
evidence?"**, not "have we waited long enough?". Every capture must
trace to a quote, an action, or a topic transition. No speculation
without a concrete signal.

## Signal taxonomy and confidence ladder

| Signal | Confidence | Write target | Write timing | Example |
|---|---|---|---|---|
| **Direct verbal marker (user)** | `explicit` | trusted (`.episodic/decisions/`) | immediately on user turn | "let's go with X" / "ship it" / "use Y" |
| **Choice converter** | `explicit` | trusted | immediately | Claude offered A/B/C; user said "B" |
| **Implementation declarative (agent)** | `explicit` | trusted | when stated in agent reasoning | "I'll use X for Y", "going with X over Y because Z", "implementing via X". For build-orchestrator subagents specifically — task-execution language IS a decision signal. |
| **Tradeoff statement** | `explicit` | trusted | when "X over Y because Z" appears | "extend YAML parser instead of changing test expectations" / "use X (Apache-2.0) over Y (GPL-3) because licensing" |
| **Threshold/parameter declaration with rationale** | `explicit` | trusted | when a numeric/named value is bound to reasoning | "cosine 0.85 because design ref §12 cites Mem0/Zep production consensus" / "default budget 25s leaves headroom on cold qwen3:8b" |
| **Default-value selection** | `confirmed` | trusted | when a default is declared | "default: `claude-opus-4-7` (current top model)" / "fall back to ollama on MLX failure" |
| **Action-confirmation (user)** | `confirmed` | trusted | after action accepted | Claude proposed → wrote code → user accepted without reverting |
| **Continuation pattern** | `confirmed` | trusted | when topic shifts past decision | proposal stood while conversation moved on |
| **Topic-coherent inference** | `inferred` | trusted, **overwriteable** | aggressive — written during session | Claude pattern-matched a position; user did not object but did not endorse |
| **Pure pattern-match from prior conversation** | `assumed` | trusted, **overwriteable** | aggressive — written during session | inferred from past decisions or memory |

**Note on agent-style language (build-orchestrator subagents, implementer subagents).** Task-execution language ("I'll add X", "extending Y", "default to Z") is just as much a decision signal as user-conversational language ("let's use Y"). Earlier versions of this skill under-captured agent decisions because the patterns were tuned only for user-Claude back-and-forth. The three middle rows above (Implementation declarative / Tradeoff / Threshold) are the explicit fix. **If you are reasoning as a subagent and you make any of those moves, capture it.**

**Tier 1 + 2 (explicit + confirmed) live captures during the session.**
Use `scripts/write_decision.py` directly with `--confidence explicit` or
`--confidence confirmed`. Set `--source auto-explicit` or
`--source auto-confirmed` so the capture is distinguishable from manual
human entries.

**Tier 3 + 4 (inferred + assumed) batch sweeps at session end** via the
Stop hook. The hook runs `scripts/scan_transcript_for_decisions.py`,
which calls a local LLM (`qwen3:8b-q4_K_M`) with Prompt C below. Tier 3
results land in `.episodic/decisions/_review/` for user promotion,
NOT in the trusted set.

You can also write tier-3 captures aggressively in-session if a
topic-coherent inference is strong. Old `inferred` of the same
`primary_tag + entity` is overwritten by the next signal — see
"Overwrite rules" below.

## Overwrite rules

Written into `write_decision.py`'s topic-identity supersession path
(`primary_tag + entity` is the topic key):

- Higher-confidence auto-supersedes lower (prior moves to `_history/<id>-vN.md`)
- Equal-confidence requires `--supersedes <id>` flag (refuse otherwise)
- Lower-confidence cannot displace higher (refused with exit 1)
- `inferred` and `assumed` captures are overwriteable by any newer signal of
  equal or higher confidence on the same topic

This is the safety mechanism that lets you capture aggressively without
poisoning the trusted set: a wrong inferred capture is corrected by the
next contradicting signal.

## What gets captured (positive evidence required)

- Decisions: choice + rationale + alternatives that were on the table + provenance
- Project-scoped constraints, preferences, conventions stated as durable
- Tooling/library/architecture choices with rationale

## What does NOT get captured

- Brainstorming that did not converge
- Questions the user asked Claude
- One-off corrections to a single response (these are feedback, not decisions)
- Anything Claude "thinks the user would want" without textual evidence
- Sensitive or volatile content where the user has not signaled durability

**No sensitivity-keyword filter.** Per project direction, capture any
decision the user makes regardless of topic. The user can revoke any
capture with `scripts/revoke_decision.py --id <id> --reason ...`.

## Tag selection — controlled vocabulary

Tags come from `.semantic/TAXONOMY.md`. New tags get the `proposed:`
prefix (e.g. `proposed:streaming-llm`), surfaced for promotion in the
next Phase 1 Assess. Do not invent tags outside this vocabulary —
vocabulary mismatch is the dominant retrieval failure mode.

## How to capture (live, during the session)

```bash
python3 scripts/write_decision.py \
  --workdir "$PWD" \
  --title "<one-line title>" \
  --decision "<one-sentence decision>" \
  --context "<1-3 sentences>" \
  --alternatives "<options considered>" \
  --consequences "<1-3 bullets>" \
  --tags "tooling,testing" \
  --primary-tag "testing" \
  --entity "<scope: project name or module>" \
  --confidence explicit \
  --source auto-explicit \
  --captured-turn-excerpt "<≤200 chars from the user's turn>" \
  --tool claude-code \
  --model claude-opus-4-7 \
  --task-category bugfix \
  --author auto
```

### v2 metadata fields (skill-driven captures)

Frontmatter v2 (added 2026-05-04, design §15) attaches five required
fields and four optional fields to every decision. The writer applies
sensible defaults so legacy callers still work, but skill-driven
captures should set these explicitly when the signal is clear:

| Field | Skill default | Example | When to override |
|---|---|---|---|
| `--tool` | `claude-code` (Stop hook fires inside Claude Code) | `claude-code` | If the conversation references another tool authoring the decision (rare) |
| `--model` | `claude-opus-4-7` (or `$CLAUDE_MODEL`) | `claude-sonnet-4-6` | When you know the active sub-agent's model |
| `--author` | `auto` (skill-driven captures) | `auto` | Always `auto` for in-session skill writes |
| `--project` | derived from entity prefix or `$CLAUDE_PROJECT_DIR` | `build-loop` | Almost never; the default is right |
| `--task-category` | `unknown` if no signal | `bugfix`, `feature`, `refactor`, `research`, `docs`, `migration`, `experiment`, `config` | **Set explicitly** whenever the conversational signal is clear (user said "fix this bug" → `bugfix`; "add this feature" → `feature`; "investigate why X" → `research`) |
| `--files-touched` | `[]` (or `--infer-files-touched` for git diff) | `src/auth/session.ts,src/auth/middleware.ts` | When the decision is bound to a specific file set |

**Inferring `task_category` from the conversation** — pick the strongest
signal:

- "fix the bug where…" / "this error…" / "broken since…" → `bugfix`
- "add the X feature" / "build a new…" / "implement…" → `feature`
- "clean up…" / "extract…" / "rename…" without behavior change → `refactor`
- "investigate / understand / what's the best way" → `research`
- "update the docs" / "README" / "changelog" → `docs`
- "migrate from X to Y" / "schema change" → `migration`
- "try / spike / experiment" → `experiment`
- env vars / settings / runtime config → `config`
- everything else → `unknown` (let the user override later via `/knowledge:review`)

### v3 metadata fields (skill-driven captures, design §16)

Frontmatter v3 (added 2026-05-04) adds seven more fields. The writer fills
sensible defaults, but skill-driven captures should set the three that
benefit from conversational signal:

| Field | Skill default | When to override |
|---|---|---|
| `--confidence-source` | `user_statement` if confidence=`explicit` AND a verbal marker / quote is captured; else `ai_inference` | Set explicitly whenever you can attribute the assertion. `user_statement` for direct quotes ("let's go with X"); `ai_inference` for topic-coherent inferences; `tool_extraction` if the value came from a deterministic tool output rather than the conversation |
| `--domain` | `unknown` | **Set explicitly** when the conversational scope is clear: `ui` for visual / interaction work, `api` for endpoint contracts, `data` for storage layer, `search` for retrieval / recall / indexing, `auth` for identity / permissions, `build` for compilation / packaging / CI, `infra` for deploy / hosting / runtime, `tooling` for dev workflow / CLI / linters, `docs` for documentation, `test` for test-suite work, `meta` for cross-cutting / process / architecture |
| `--goal` | `unknown` | **Set explicitly** when the user's framing makes intent clear: `user-value` for end-user-visible improvements, `reliability` for bug fixes / robustness, `performance` for speed / cost / efficiency, `security` for hardening, `dev-velocity` for developer-experience gains, `maintainability` for refactor / readability, `compliance` for regulated requirements, `learning` for spike / explore |
| `--confirmation-count` | `0` | Almost never override at write time — the bumper hook (future) increments this on successful action |
| `--valid-until` | `null` | Set when the decision is bound to a date — quarterly OKR, vendor contract end, model deprecation |
| `--causal-parent-id` | `null` | Set when this decision is downstream of a specific prior decision (use the 4-digit id) |
| `--embedding-model-version` | `$EMBED_MODEL` env or `mxbai-embed-large-v1` | Almost never override; the writer reads the active backend automatically |

**Examples — `domain` and `goal` from conversational context:**

| User said | `domain` | `goal` |
|---|---|---|
| "fix this bug in the search retrieval" | `search` | `reliability` |
| "speed up the homepage loader" | `ui` | `performance` |
| "rotate the API keys before Friday" | `auth` | `security` |
| "rewrite the auth middleware to be testable" | `auth` | `maintainability` |
| "add a CSV export to the dashboard" | `ui` | `user-value` |
| "investigate which queue library to use" | `infra` | `learning` |
| "update the CONTRIBUTING.md" | `docs` | `dev-velocity` |
| "tighten the lint rules" | `tooling` | `maintainability` |

When in doubt, leave them as `unknown` rather than guess — the user can
backfill via `/knowledge:review`.

**`confidence_source` — when to set what:**

- Direct verbal marker (the user typed it) → `user_statement`
- Choice converter (Claude offered A/B/C, user picked one) → `user_statement`
- Action confirmation (user did the thing Claude proposed) → `user_statement`
- Topic-coherent inference (no direct quote, but the topic stood) → `ai_inference`
- Pulled from external memory / migration import / vector-DB sync → `external_import`
- Deterministic tool output (e.g., `git log`, `grep`) → `tool_extraction`
- Genuinely cannot tell → `unknown`

Output is the new 4-digit decision ID on stdout (e.g. `0042`). Errors go
to stderr.

For supersession (a new decision replaces an old one):
```bash
python3 scripts/supersede_decision.py \
  --old-id 0042 \
  --new-decision "..." \
  --new-title "..." \
  --tags "..." --primary-tag "..." --entity "..." \
  --confidence explicit \
  --rationale "Why we changed our mind"
```

For revocation (the captured decision was wrong, no replacement):
```bash
python3 scripts/revoke_decision.py \
  --id 0042 \
  --reason "user clarified this was venting, not a decision"
```

For lookup before writing (avoid duplicates):
```bash
python3 scripts/recall.py --query "test framework"
```

## Extraction prompts (inline reference)

These three prompts are the canonical extraction templates from
`~/dev/research/topics/repo-episodic-memory-framework/repo-episodic-memory-framework.md` §12.
Use them whenever you need to extract structured decisions from
unstructured conversation. Keep temperature low (0.1–0.3).

### Prompt A — Subject-Predicate-Object triplet extraction

```
Extract facts from the user's conversational decisions. Output JSON array.
Each fact: subject (who/what), predicate (relationship), object (value).

Example:
  Input: "We'll use pytest for testing from now on."
  Output: [{"subject":"this_project","predicate":"testing_framework",
           "object":"pytest","confidence":"explicit",
           "entity_type":"tool","tags":["tooling","testing"]}]

Rules:
- confidence: explicit | confirmed | inferred | assumed
- entity_type: tool | process | library | constraint | preference | architecture
- tags: from controlled vocabulary {{taxonomy_tags}}
- Do NOT infer beyond textual evidence.
```

### Prompt B — Decision + rationale (MADR-aligned, single decision)

```
Extract a decision the user made or implied. Output JSON object.

Schema: {decision_title, context, alternatives_considered[],
         chosen, rationale, confidence, primary_tag}

Rules:
- rationale MUST have textual evidence; otherwise mark "inferred"
- If user did not explicitly choose, confidence is "inferred" at best
- primary_tag is singular; pick most relevant
```

### Prompt C — End-of-session batch consolidation (tier 3 sweep)

```
Scan the conversation transcript. Identify 2–4 decisions the user made
implicitly. For each, output:

[{decision, evidence (exact quote or turn range),
  confidence: inferred|assumed,
  may_be_superseded_by: null or prior decision ID}]

Existing decisions: {{prior_decisions_summary}}

Rules:
- Only capture if there is textual signal. No speculation.
- Flag supersessions for human review.
```

## Dedup thresholds (when comparing against prior captures)

| Cosine similarity | Same (subject, predicate)? | Action |
|---|---|---|
| ≥ 0.90 | yes, near-identical object | IGNORE (already known) |
| 0.85–0.90 | yes, slight phrasing variation | MERGE (use supersession) |
| 0.75–0.85 | related but different predicates | INSERT (keep both) |
| < 0.75 | unrelated | INSERT |

`write_decision.py` handles the topic-identity match (primary_tag + entity);
the cosine threshold matters for the end-of-session batch sweep
(`scripts/scan_transcript_for_decisions.py` consults `semantic_facts`
before writing).

## End-of-session sweep — automatic via Stop hook

The Stop hook in `hooks/hooks.json` invokes
`scripts/scan_transcript_for_decisions.py` against
`$CLAUDE_TRANSCRIPT_PATH`. The script:

1. Reads the transcript JSONL
2. Calls `qwen3:8b-q4_K_M` with Prompt C (above)
3. For each candidate: dedups against `semantic_facts` (≥0.85 = SKIP)
4. Tier 1+2 (explicit+confirmed) → `write_decision.py` → trusted
5. Tier 3+4 (inferred+assumed) → `.episodic/decisions/_review/` quarantine

If ollama is unreachable, the hook logs a no-op and exits 0 — never
fails the session.

### Per-session opt-out

Create `.episodic/.no-capture` to skip the auto-capture sweep for the
current session. The script exits 0 immediately on startup with a log
line. Remove the file when you want the sweep back on:

```bash
touch .episodic/.no-capture     # disable for this session
rm .episodic/.no-capture        # re-enable
```

This is a per-repo flag; it does not affect other projects.

### Hardening contract (Stop-hook safety)

The script self-imposes guardrails so the Stop hook never disrupts a
coding session:

- **Wall-clock budget** — default 25s, override with env `SCAN_BUDGET_S`.
  Checked before the LLM call and between writes. On overrun the script
  logs `budget exceeded` and exits 0 with whatever was already written.
  The hook timeout (60s) is a backstop; the budget should always fire
  first.
- **Single-flight lock** — `fcntl.flock` on `/tmp/build-loop-scan.lock`
  (override with `--lock-file`). A second concurrent invocation exits 0
  immediately with a log line. Prevents contention when sessions end
  close together.
- **Output suppression** — the hook command redirects stdout and stderr
  to `/dev/null`. The durable record is the log file at
  `${XDG_STATE_HOME}/build-loop/scan.log` (default
  `~/.local/state/build-loop/scan.log`). Override with `--log-file`. The
  log file auto-rotates when it exceeds 10 MB (last 1 MB kept).

## Confidence floor at retrieval

`write_decision.py`'s INDEX regenerator filters by `confidence >= confirmed`
by default. Tier 3 entries written aggressively in-session do appear in
the trusted directory but are filtered out of the default INDEX view.
Phase 1 Assess loads only `explicit + confirmed` unless you explicitly
search at lower confidence.

## When in doubt

1. If the user's turn contains a clear verbal marker → capture as `explicit`
2. If the user accepted Claude's proposal and the topic moved on → capture as `confirmed`
3. If you can quote evidence but the user did not endorse → capture as `inferred`
4. If you can only pattern-match prior context → either skip, or capture as `assumed` knowing it will be overwritten

When unsure between two confidence levels, pick the lower. The system
self-heals as more signal arrives.

## When the user runs `/knowledge:review`, surface…

The review surface (loaded by `build-loop:knowledge-review`, backed by
`scripts/knowledge_review.py`) shows four sections of decisions and
procedures awaiting human attention:

1. **Review queue** — tier-3 / inferred captures sitting in
   `.episodic/decisions/_review/` (written there by either the Stop-hook
   batch sweep or in-session aggressive `inferred` captures from this
   skill). User promotes or dismisses each.
2. **Decision rot** — accepted decisions where `last_validated` (or
   `date` if absent) is older than 90 days. User marks-validated,
   supersedes, or revokes. Long-running decisions accumulate rot until
   touched.
3. **Open conflicts** — `fact_conflicts` rows (resolved=FALSE) created
   when `consolidate_memory.py` detected two semantic facts with the
   same `(subject, predicate, confidence)` but different `object`. User
   resolves by superseding one.
4. **Stale procedures** — procedure files whose `depends_on` symbols
   are no longer present in the codebase (run by
   `procedural_governance.py --mode validate-symbols`).

**Cross-reference**: when this skill writes aggressively at tier 3
(`inferred`) and the same `primary_tag + entity` keeps getting
overwritten, that's a signal the user's intent is unstable. The
review queue surfaces it; the user takes the explicit decision and
pins it. The overwrite path then stops firing.

Consolidation (`scripts/consolidate_memory.py`) is the related batch
script for promoting `.semantic/_candidates.jsonl` entries into
`semantic_facts`.
