# Coordination File Template

**Purpose:** Canonical starting shape for a new per-run coordination file at `.build-loop/coordination/<topic>.md`. Inherits binding rules from `references/coordination-rules.md`; this template defines the **structure** every coord file MUST include so verifiers, peer sessions, and `scripts/coordination_status.py` can parse them uniformly.

**How to use:** Copy this file to `.build-loop/coordination/<your-topic>-<YYYY-MM-DD>.md`. Replace every `{{PLACEHOLDER}}` token with a real value. Delete the `<!-- TEMPLATE NOTE: -->` blocks before committing. Keep section ordering; `coordination_status.py` parses the "Codex feedback log" verdict headings and step-status table, so don't rename those sections.

---

## ⬇️ Template begins below ⬇️

<!-- TEMPLATE NOTE: Title is human-readable. Include the version/feature scope. -->

# Coordination — {{RUN_TITLE}} ({{DATE_YYYY_MM_DD}})

**Date:** {{DATE_YYYY_MM_DD}}
**Session:** {{PRIMARY_TOOL}} ({{PRIMARY_ROLE}}); {{VERIFIER_TOOL}} ({{VERIFIER_ROLE}})
**Status:** active — will be archived per closeout protocol when all pieces land
**Predecessor:** `.build-loop/coordination/archived/{{PREVIOUS_RUN_FILE}}.md` <!-- or "none (first run)" -->

## Scope

<!-- TEMPLATE NOTE: One paragraph naming the deliverable goal and explicit out-of-scope items. -->

{{SCOPE_SUMMARY_2_TO_4_SENTENCES}}

**Pieces (chunk list with one-line summaries):**

- **{{PIECE_ID_1}}** — {{PIECE_1_ONE_LINE}}
- **{{PIECE_ID_2}}** — {{PIECE_2_ONE_LINE}}
- **{{PIECE_ID_N}}** — {{PIECE_N_ONE_LINE}}

**Out of scope:**

- {{OUT_OF_SCOPE_1}}
- {{OUT_OF_SCOPE_2}}

## Operating Rule (binding — inherited from references/coordination-rules.md)

**Claude does not proceed past a step marked `verification-pending` until the latest verifier feedback entry for that step is one of:**

- `PASS` — acceptance criteria verified.
- `VARIANCE` that has been resolved (Claude fixed, documented non-acceptance, OR escalated to user).
- Explicit user override.

A `VARIANCE` left unresolved blocks the next step. A `BLOCKED` entry requires the producing peer to supply the missing evidence before proceeding.

Per-run amendments: {{ANY_RUN_SPECIFIC_OPERATING_AMENDMENTS_OR_NONE}}

## Coordination Protocol (binding — inherited)

- **All App Pulse writes** go through `scripts/app_pulse/post.py` `post()` helper.
- **Cheap detection at every step boundary**: `python3 scripts/coordination_status.py --workdir . --session-id <id> --owned-file <path> --coordination-file .build-loop/coordination/{{THIS_FILE_NAME}}.md --json`. `--coordination-file` REQUIRED — explicit beats implicit.
- **Verifier feedback entry format** (append-only to "Verifier feedback log" at bottom):

  ```md
  ### YYYY-MM-DD HH:MM TZ — {{VERIFIER}} <VERDICT>
  **Step:** <piece id / name>
  **Verdict:** PASS | VARIANCE | BLOCKED
  **Evidence:** <file:line or command result>
  **Impact:** <why this matters>
  **Requested Claude action:** <fix / explain / user decision>
  ```

- **Post via channel** in parallel: `post(kind="feedback", payload={"step": "<id>", "verdict": "<VERDICT>", "evidence": {...}, "impact": "...", "requested_action": "..."})`. Both writes (feedback log + channel) MUST happen — the feedback log is the durable record; the channel is the runtime signal.

## MECE Ownership Packets per Piece

<!-- TEMPLATE NOTE: Repeat this block once per piece. Every write-handoff requires all four. Skip ONLY for pure-read handoffs. -->

### {{PIECE_ID}} — {{PIECE_TITLE}}

- **Owns** ({{OWNER_TOOL}}): {{FILE_OR_SCOPE_LIST}}
- **Does not own**: {{FILE_OR_SCOPE_LIST}}
- **Interface contract**: {{DELIVERABLE_SHAPE — schema, format, CLI exit codes, return-envelope fields}}
- **Integration checkpoint**: {{HOW_VERIFIER_CONFIRMS — test command, grep pattern, file existence, fresh-session load}}

### {{NEXT_PIECE_ID}} — {{NEXT_PIECE_TITLE}}

- **Owns** ({{OWNER_TOOL}}): ...
- **Does not own**: ...
- **Interface contract**: ...
- **Integration checkpoint**: ...

<!-- TEMPLATE NOTE: Continue for every piece. Enforce via `python3 scripts/brief_mece_validator.py --brief-file <packet-as-tmpfile>` before dispatching that piece to an implementer. -->

## Step status (live)

<!-- TEMPLATE NOTE: This table is parsed by humans + verifiers. Status column updates as work progresses. -->

| # | Piece | Owner | Status | Version after | Pending verifier check |
|---|---|---|---|---|---|
| {{PIECE_ID_1}} | {{PIECE_1_TITLE}} | {{OWNER_1}} | ⏸️ awaiting dispatch | {{VERSION_TAG_OR_PIECE}} | yes |
| {{PIECE_ID_2}} | {{PIECE_2_TITLE}} | {{OWNER_2}} | ⏸️ awaiting dispatch | {{VERSION_TAG_OR_PIECE}} | yes |
| {{PIECE_ID_N}} | {{PIECE_N_TITLE}} | {{OWNER_N}} | ⏸️ awaiting dispatch | {{VERSION_TAG_OR_PIECE}} | yes |

**Status legend:** `⏸️ awaiting dispatch` → `🏃 executing` → `✅ executed; verification-pending` → `✅ PASS (verifier)` → `done`. Variances surface as `⚠️ VARIANCE — <one-line>` until resolved.

## Acceptance criteria per piece (for verifier checking)

<!-- TEMPLATE NOTE: Mirrors interface-contract + integration-checkpoint from MECE packets above. Verifier confirms both before posting PASS. Repeat per piece. -->

### {{PIECE_ID_1}}

- {{CHECK_1}} — {{HOW_TO_RUN}}
- {{CHECK_2}} — {{HOW_TO_RUN}}
- {{CHECK_N}} — {{HOW_TO_RUN}}

### {{PIECE_ID_2}}

- ...

## Release verification (when this run bumps a plugin version)

<!-- TEMPLATE NOTE: Drop this section if the run does not ship a versioned release. -->

After the final piece commits + the version-bump commit lands, verifier runs:

```bash
python3 scripts/verify_release_surface.py \
  --version {{TARGET_VERSION}} \
  --branch {{TARGET_BRANCH}} \
  --remote origin \
  --json
```

Exit 0 = all seven release-surface checks pass; exit 1 = at least one failed. Verifier appends the JSON result as the final verifier-feedback-log entry.

## Anti-checklist (don't do these)

- No new coordination-file FORMAT — reuse this template's section shape.
- No new presence mechanism — reuse App Pulse `presence.write_presence` + `post()`.
- No new agents unless explicitly justified.
- No silent scope creep — if a piece grows mid-execution, post `VARIANCE` to the feedback log first.

---

## Verifier feedback log

<!-- TEMPLATE NOTE: Verifier appends entries below per the format in Coordination Protocol. Do NOT edit prior entries — append-only. -->

<!-- Example (delete before committing):

### 2026-05-20 16:02 PDT — codex PASS
**Step:** {{PIECE_ID_1}}
**Verdict:** PASS
**Evidence:** `python3 scripts/test_plugin_manifest.py` -> 12/12 OK; `git ls-remote origin {{TARGET_BRANCH}} {{TARGET_VERSION}}` -> both refs at <sha>
**Impact:** Piece {{PIECE_ID_1}} acceptance criteria satisfied; Claude can proceed to next piece.
**Requested Claude action:** Proceed to {{PIECE_ID_2}}.

-->

## ⬆️ Template ends above ⬆️

---

## Template usage checklist

Before committing your filled-in coord file:

1. Every `{{PLACEHOLDER}}` replaced with a real value.
2. Every `<!-- TEMPLATE NOTE: ... -->` block deleted.
3. Every piece has a MECE packet (all four elements).
4. Every piece has an acceptance-criteria sub-section.
5. Step-status table rows match piece IDs in MECE packets.
6. Run `python3 scripts/coordination_status.py --workdir . --session-id <id> --coordination-file <this-file> --json` — should report `status: clear` initially.
7. Optionally write `.build-loop/coordination/active.json` (or `active`) pointing at this file so future `coordination_status.py` calls without explicit `--coordination-file` resolve to it deterministically.

## Reference

- Binding rules: `references/coordination-rules.md`
- Validator for MECE packets: `scripts/brief_mece_validator.py`
- Status sensor: `scripts/coordination_status.py`
- Channel helper: `scripts/app_pulse/post.py`
- Release verifier (when bumping a version): `scripts/verify_release_surface.py`
- Closeout protocol: `scripts/app_pulse/lifecycle.py` + `agents/build-orchestrator.md` Phase D
