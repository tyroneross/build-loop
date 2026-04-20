---
name: build-loop:promote-experiment
description: Promote an auto-promoted experimental skill or agent from project-local `.build-loop/skills/active/` into the build-loop plugin repo, making it available across every project. Requires user confirmation and opens a PR on the plugin repo for review.
argument-hint: <artifact-name>
---

# /build-loop:promote-experiment <name>

Promote a proven experimental artifact into the build-loop plugin itself. This is the manual, user-approved counterpart to Phase 9's auto-promote — which stays local to the project. Cross-project promotion modifies the plugin repo and affects every user, so it is never automatic.

## Prerequisites

1. The artifact must exist at `.build-loop/skills/active/<name>/SKILL.md` (already auto-promoted within the project), OR at `.build-loop/skills/experimental/<name>/SKILL.md` (user is fast-tracking without waiting for A/B completion — require explicit confirmation).

2. The build-loop plugin repo must be writable. Check:
   ```bash
   BUILD_LOOP_REPO="${CLAUDE_PLUGIN_ROOT:-$HOME/Desktop/git-folder/build-loop}"
   [ -d "$BUILD_LOOP_REPO/.git" ] && [ -w "$BUILD_LOOP_REPO/skills" ]
   ```
   If false, abort with "build-loop plugin repo not writable at $BUILD_LOOP_REPO".

3. The user must supply `<name>` as an argument. If missing, list current candidates:
   ```bash
   ls .build-loop/skills/active/ 2>/dev/null
   ls .build-loop/skills/experimental/ 2>/dev/null
   ```

## Steps

### 1. Read the evidence

Collect:
- The artifact's SKILL.md content
- Its experiments jsonl (`.build-loop/experiments/<name>.jsonl`)
- All `applied` entries across this project (and, if a global index exists at `~/.build-loop/experiments/<name>.jsonl`, append those too)
- Any `auto_promote` or `extend_sample` decisions from `.build-loop/experiments/decisions.jsonl`

Compute:
- Total applied runs (across projects)
- Aggregate win rate vs baseline
- Any regression signals (reversed auto-promotes, discard attempts)

### 2. Synthesize a promotion dossier

Build a markdown summary for the user:

```markdown
## Promotion dossier: <name>

**Type**: skill | agent
**Auto-promoted in this project**: <date>, after N runs
**Cross-project evidence**: P projects, Q applied runs, R wins, S regressions
**Aggregate metric delta**: +X% vs baseline (target was +Y%)
**SKILL.md length**: N lines
**Dependencies declared**: (none | list)
**Last modified**: <date>

**What the skill does** (from description frontmatter):
<one line>

**Recommended promotion target**:
- Name on promote: build-loop:<name>  (or rename to: build-loop:<suggested-name>)
- Target path: skills/<name>/SKILL.md in the plugin repo
- Triggers on: <extracted trigger phrases>
```

### 3. Ask the user

Use `AskUserQuestion`:

```
Question: "Promote '<name>' into the build-loop plugin repo?"
Header: "Promote"
Options:
  - "Promote as drafted" — copy SKILL.md verbatim, open PR
  - "Promote with rename" — user provides new name, then copy + PR
  - "Review first" — output the dossier but do not copy (user decides after reading)
  - Cancel (implicit via AskUserQuestion "Other")
```

### 4. Execute

If user approved promotion:

1. Determine target path: `$BUILD_LOOP_REPO/skills/<final-name>/SKILL.md` (plus any references/ subdirectory from source).
2. If target already exists:
   - Abort and ask user to choose a new name, OR confirm overwrite.
3. Create a feature branch on the plugin repo: `feat/promote-<name>-from-<project-slug>`.
4. Copy files. Edit frontmatter: remove `experimental: true`, `promoted_at`, `promoted_from_project`; add `promoted_at: <ISO>`, `promoted_from_project: <project-name>`, `original_baseline: <N>`, `aggregate_delta: <percent>`.
5. Update the plugin's `skills/build-loop/SKILL.md` capability routing table to reference the new skill if it fits an existing row, OR add a new row (ask the user which).
6. Commit:
   ```
   feat(skills): promote <name> from Phase 9 self-improvement

   Originated as auto-drafted experimental skill in <project>.
   Aggregate track record: P projects, Q runs, +X% vs baseline
   (target was +Y%). SKILL.md copied verbatim with promotion
   metadata added to frontmatter.

   Evidence log: .build-loop/experiments/<name>.jsonl (N entries)
   Signed off by Opus 4.7 on <auto-promote date>.
   ```
7. Push the branch and open a PR with the promotion dossier as the body.

### 5. Record in project

Append to `.build-loop/experiments/decisions.jsonl`:

```jsonl
{"event": "cross_project_promote", "date": "ISO", "name": "<name>", "plugin_pr_url": "<url>", "aggregate_evidence": { runs, projects, delta }}
```

Do not delete the local artifact — it stays in `.build-loop/skills/active/<name>/`. The plugin version will shadow it once installed; the local copy is retained as the historical source.

## Safety rails

- **Never auto-invoke**. Phase 9 auto-promote stops at the project boundary. This command is the only path to the plugin repo.
- **Require user confirmation**. AskUserQuestion is mandatory.
- **Always open a PR**, never commit to main directly. Plugin maintainers review before merge.
- **Preserve provenance**. Frontmatter records the source project, baseline, and delta. If the promoted skill regresses in a cross-project context, the track record makes rollback decisions clear.
- **Never overwrite without confirmation**. If a skill with the same name exists, abort and let the user rename or approve overwrite.

## Related

- Phase 9 auto-promote (project-local): `skills/self-improve/SKILL.md`
- User removal override: `.build-loop/skills/.demoted` file or `rm -rf .build-loop/skills/active/<name>/`
- Global cross-project evidence index: `~/.build-loop/experiments/<name>.jsonl` (appended on every applied run across projects)
