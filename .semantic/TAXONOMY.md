---
type: taxonomy
status: active
schema_version: 2
last_updated: 2026-05-04
namespace: episodic-memory-framework
---

# Controlled Vocabulary — Repo-Local Episodic Memory

Canonical vocabulary for the four-memory-types framework
(`.episodic/`, `.semantic/`, `.procedural/`). Everything that filters,
indexes, or validates content reads from here. New terms require either
(a) a manual edit to this file or (b) a `proposed:` prefix at the call
site (proposed terms surface in review and are promoted manually).

The framework follows the design at
`~/dev/research/topics/repo-episodic-memory-framework/repo-episodic-memory-framework.md`.
Section references below cite that document as `(design §N)`.

## 1. Decision tags (design §3, §10)

Decision frontmatter has a `tags:` list (multiple tags allowed) and a
`primary_tag:` (single value, used together with `entity:` as the
topic-identity key for overwrite matching).

**Canonical decision tags** — `tags:` and `primary_tag:` MUST draw from
this set (or use `proposed:` prefix on `tags:` only, never on
`primary_tag:`):

- `architecture` — component boundaries, module split, data flow shape
- `data` — schema, persistence, migration, source-of-truth
- `ui` — visual design, layout, component patterns, navigation
- `infra` — deploy, hosting, CI/CD, runtime topology
- `tooling` — build tools, scripts, dev workflow
- `process` — team practice, review cadence, release ritual
- `security` — auth, authz, secrets, threat surface
- `performance` — latency, throughput, bundle/memory budget
- `testing` — test framework, fixtures, coverage policy

**Proposed-tag pattern**: an author may use `tags: [proposed:new-tag]`
when no canonical tag fits. Validator accepts the prefix; review
promotes. `primary_tag:` does NOT accept `proposed:` — topic identity
must be stable.

## 2. Event kinds for `.episodic/events.jsonl` (design §5)

Closed enum. Writers emit one of:

- `run_completed` — build-loop run finished successfully
- `run_failed` — build-loop run terminated with failure
- `decision_proposed` — decision file written with `status: proposed`
- `decision_accepted` — decision file written with `status: accepted`
- `decision_superseded` — prior decision moved to `_history/`
- `decision_revoked` — decision marked rejected without replacement
- `issue_opened` — new issue file in `.episodic/issues/`
- `issue_closed` — issue marked resolved/closed
- `library_added` — manifest gained a dep
- `library_bumped` — manifest version change
- `library_removed` — manifest lost a dep
- `architecture_component_added` — NavGator detected new component
- `architecture_component_removed` — NavGator detected removed component
- `manual_intervention` — human paused/redirected the loop
- `escalation` — sub-agent failed and was re-spawned at higher tier

## 3. Confidence levels (design §10)

Strict total ordering, lowest to highest:

`assumed < inferred < confirmed < explicit`

- `explicit` — direct user statement ("use pytest"). Manual MADR
  authoring also defaults here.
- `confirmed` — proposed-and-accepted action that landed (Claude
  proposed, user accepted, code shipped).
- `inferred` — Claude pattern-matched a position from textual context
  without a direct statement.
- `assumed` — drawn from prior memory or precedent without a current-
  session signal.

**Overwrite rules** (enforced by `scripts/write_decision.py`):

- New same-topic decision (same `primary_tag` + `entity`) with strictly
  higher confidence → auto-supersedes prior. Prior moves to
  `.episodic/decisions/_history/<id>-v<N>.md`.
- New same-topic decision with equal confidence → requires explicit
  `--supersedes <id>` flag (writer exits 1 otherwise with a helpful
  error).
- New same-topic decision with lower confidence → writer exits 1
  always; lower confidence cannot displace higher.
- `--supersedes <id>` bypasses confidence comparison; the user has
  explicitly directed.

## 4. Decision status (frontmatter `status:`)

- `proposed` — written, not yet accepted by user/orchestrator
- `accepted` — current truth
- `superseded` — replaced by a newer decision (frontmatter
  `superseded_by:` points to replacement)
- `rejected` — explicitly revoked without replacement

## 5. Issue status (frontmatter `status:`)

- `open` — under investigation
- `closed` — resolved (with closing note in body)
- `resolved` — fixed (alias for closed; teams may distinguish)

## 6. Source attribution (frontmatter `source:`)

Closed enum:

- `manual` — human authored the MADR
- `auto-explicit` — auto-capture skill, direct user marker (Phase 3)
- `auto-confirmed` — auto-capture skill, accepted action (Phase 3)
- `auto-inferred` — auto-capture skill, pattern-matched position (Phase 3)
- `auto-assumed` — auto-capture skill, drawn from prior memory (Phase 3)
- `migration` — bulk migration script (e.g. feedback.md → MADR)
- `orchestrator` — build-loop orchestrator wrote it

## 7. Frontmatter required fields (decisions)

Required (v1 base):
- `id` — zero-padded 4-digit decimal, allocated by `write_decision.py`
- `slug` — kebab-case, derived from title
- `title` — full sentence
- `type` — one of `decision | issue | research`
- `status` — see §4
- `confidence` — see §3
- `date` — `YYYY-MM-DD`
- `tags` — list, vocab from §1
- `primary_tag` — single value, vocab from §1 (no `proposed:`)
- `entity` — string, the subject the decision is about
  (e.g. `build-loop`, `auth-flow`, `chart-pipeline`)
- `source` — see §6

Required (v2; defaults applied by writer — see §9):
- `project` — repo-scoped namespace
- `tool` — authoring tool (closed enum)
- `model` — free-form model ID
- `task_category` — closed enum
- `author` — free-form author identifier

Optional:
- `related_runs` — list of run_ids
- `related_decisions` — list of decision IDs
- `supersedes` — single decision ID this replaces
- `superseded_by` — single decision ID that replaces this
- `bookmark_snapshot_id` — provenance for auto-captured decisions
- `captured_turn_excerpt` — first ~200 chars of triggering user turn
- `last_validated` — null | ISO date
- `last_accessed` — null | ISO date
- `files_touched` — list of repo-relative paths
- `closing_commit` — null | git SHA

## 8. Procedural memory frontmatter (design §14)

Required for entries under `.procedural/<name>/procedure.md`:

- `name` — slug, matches directory name
- `trigger` — symptom phrase or error substring
- `domains` — list (e.g., `["ios", "notifications"]`)
- `confidence` — `high | medium | low`
- `created` — `YYYY-MM-DD`
- `last_applied` — `YYYY-MM-DD` or null
- `incident_count` — integer
- `depends_on` — list of `{symbol, min_version, last_verified}`
- `invalidation_signal` — string description (or null)

## 9. v2 metadata fields (added 2026-05-04, design §15)

Frontmatter v2 adds nine fields to every decision and mirrors them onto
`events.jsonl` lines and `semantic_facts.metadata`. Defaults are applied
by `scripts/write_decision.py` at write time so callers without new args
still produce valid frontmatter. The validator requires all of
`project`, `tool`, `model`, `task_category`, `author` after defaults are
applied; the remaining four (`last_validated`, `last_accessed`,
`files_touched`, `closing_commit`) are optional.

### 9.1 `project` — string

Repo-scoped namespace, separate from `entity` (which targets a module
inside the project). Default: derived from `entity` prefix before `:`
(e.g. `build-loop:foo` → `build-loop`), else basename of
`$CLAUDE_PROJECT_DIR`, else `unknown`. Examples: `build-loop`,
`speaksavvy`, `atomize-ai`.

### 9.2 `tool` — closed enum

The agentic tool that authored the entry. Closed enum:

- `claude-code` — Anthropic Claude Code CLI (default for in-session captures)
- `codex` — OpenAI Codex CLI / GPT-5.x runs
- `cursor` — Cursor IDE
- `aider` — Aider CLI
- `goose` — Block Goose
- `manual` — human-authored MADR
- `migration` — bulk-migration script (one-shot data imports)
- `unknown` — escape hatch for retroactive backfill

### 9.3 `model` — free-form string

Model ID such as `claude-opus-4-7`, `claude-sonnet-4-6`, `gpt-5.4`,
`qwen3:8b-q4_K_M`. Free-form (not enum) so new models do not require a
TAXONOMY edit. Convention: lowercase with hyphens, vendor prefix where
ambiguous. `unknown` and `migration` are reserved for backfill.

### 9.4 `task_category` — closed enum

Closed enum, drives metadata-filter retrieval:

- `feature` — new capability
- `bugfix` — defect repair
- `refactor` — internal restructuring without behavior change
- `research` — investigation / analysis
- `docs` — documentation
- `migration` — schema or data move
- `experiment` — exploratory / spike
- `config` — settings, env, runtime config
- `unknown` — escape hatch (default for skill-driven captures that lack a clear signal)

### 9.5 `author` — string

Free-form author identifier. Default: `$USER` env var. Reserved values:
`auto` for skill-driven captures.

### 9.6 `last_validated` — null | ISO date

Timestamp of last user re-validation via `/knowledge:review`. Used by
`detect_decision_rot.py`. Null until first validation.

### 9.7 `last_accessed` — null | ISO date

Bumped by `recall.py` whenever a decision ranks in the top-K returned.
Optional; informational only. Used by future staleness scoring.

### 9.8 `files_touched` — list of repo-relative paths

Paths the decision applies to. Populated automatically by
`write_decision.py` from `git diff --name-only HEAD~1 HEAD` when a recent
commit is detected, or explicitly via `--files-touched a,b,c`. Default:
empty list.

### 9.9 `closing_commit` — null | git SHA

The commit that closed/landed the decision. Set manually via
`supersede_decision.py` or by a future post-commit hook. Default: null.

## 10. No sensitivity-keyword filter (per user direction, design §10)

Earlier drafts proposed a sensitivity-keyword exclusion list for
auto-capture. User direction: do NOT filter on sensitivity keywords.
The four-confidence + topic-identity supersession model is the only
gate. This section exists to record the explicit decision so future
maintainers do not reintroduce the filter without a fresh discussion.
