---
name: build-loop:security-scan
description: "Run before any push OR any deployment, during Phase 2 planning, or whenever an agent wants a security pass. Executes a deterministic, model-independent OWASP scanner (scripts/security_scan.py) over the repo — secrets in source and logs, SQL/command/eval injection, broken object-level authorization, missing or fail-open auth guards, privileged keys in client bundles, session/token hygiene, CORS, mass assignment, unbounded or ungated AI tool calls — and maps each finding to OWASP Web/LLM/Agentic IDs. Changed files get every check; the rest of the tree gets an advisory sweep. The judgment layer (business-rule authz, RAG tenant design, agent goal-drift) escalates to the security-reviewer agent + the security-methodology canon."
version: 0.2.0
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Security Scan

The deterministic, **model-independent** complement to the two existing security artifacts:
- `agents/security-reviewer.md` — the LLM judgment grader (Fable-pinned; unavailable when Fable is down).
- `skills/security-methodology/` — the OWASP/NIST/ATLAS canon, knowledge-only ("performs no scans").

This skill RUNS: a stdlib-only Python scanner, no model, no network, no Fable dependency. It exists because of a **named, observed failure** — a GitHub OAuth `access_token` was logged to `console.log` in middleware, shipped, and went unnoticed across five commits. Root cause: detection was gated on a judgment flag (`riskSurfaceChange`) + a single model-pinned agent, with **no always-on deterministic backstop**. Secret-in-logs is a greppable class; it should be caught on every push regardless of any flag.

## When to run

1. **Before any feature push (always-on gate).** `scripts/hooks/pre_bash_dispatch.sh` routes `git push` through the scanner and HARD-BLOCKS the push on HIGH+ findings (mirrors the commit auditor). Invoke it explicitly too when pushing outside a build-loop project.
2. **Before any DEPLOYMENT (always-on gate).** `git push` is only one way code reaches users. The same hook classifies the command with `deployment_policy.is_deploy_like()` and full-scans before `vercel deploy`, `wrangler deploy`, `wrangler pages deploy`, `flyctl deploy`, `railway up`, `render deploy`, `supabase functions deploy`, `eas submit`, `fastlane`, `kubectl apply`, `helm`, `terraform apply`, `pulumi up`, `gcloud`/`aws`/`sam`/`cdk` deploys, `gh workflow run`, `gh release create`, and `npm publish`. A deploy ships the whole tree, so this path never uses `--diff` — every file gets every check.
3. **During Phase 2 planning.** Run it over the files/area you're about to change to surface existing security debt before adding to it.
4. **Whenever any agent wants a security pass.** It's a plain script — any orchestrator or agent can call it (agent-callable; not a user-facing slash command, so `user-invocable: false`).

## Coverage model — deep on the change, sweep everywhere else

`--diff` alone scans only what changed, which leaves the rest of the app unchecked on every ship. `--spot-check` closes that without making the gate unusable:

- **Changed files → every check, fully enforced.** A HIGH here blocks.
- **Unchanged files → the high-confidence subset** (secrets, secret-in-logs, injection, object-level authz, auth guards, client-exposed keys, token hygiene, CORS), tagged `scope: "spot"` and reported as **advisory**. Only a CRITICAL among them blocks.

The asymmetry is deliberate. Blocking a ship on a stranger's years-old MEDIUM is how a gate becomes something people route around; a CRITICAL in any file is a reason not to ship regardless of who wrote it. The absence-of-control checks (rate limiting, headers, prompt hygiene, mass assignment) are excluded from the sweep — they fire on almost every older file and would bury the signal.

The pre-push hook passes `--spot-check` automatically. The report header names the sweep and the summary separates advisory from blocking counts, so widened scope is visible rather than implied.

## Run it

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/security_scan.py" --path <repo> [--fail-on {low,medium,high,critical}] [--json] [--diff <ref>] [--spot-check] [--exclude <glob>]
```

- **Exit 0** = nothing at/above threshold · **Exit 1** = found something at/above threshold (this is what gates the pre-push hook).
- Default threshold is **HIGH**. `--json` emits machine output.
- **`--diff <ref>`** (opt-in) scopes the scan to files changed in `<ref>..HEAD` — scan what's being pushed, not the whole tree, so pre-existing unrelated debt doesn't block an unrelated push. Fail-safe: a bad ref / non-git path falls back to a full scan (never scans less than intended); an empty range scans nothing (exit 0). Delta discovery uses `git diff --name-only -z --relative` so non-ASCII/quoted filenames and subdirectory `--path` roots are handled correctly; a belt-and-braces guard also full-scans if the delta named changed files but the walk matched none. The pre-push hook derives `<ref>` from the upstream tracking branch (`@{u}`), and applies it **only to a plain current-branch → tracking push**: any refspec (`origin main:release`), non-tracking remote (`git push backup main`), or whole-repo flag (`--mirror`/`--all`/`--tags`) omits `--diff` and full-scans (fail-safe: never scan less than intended). No upstream → whole-repo scan.
  - **Scope limitation (working-tree vs pushed blob):** `--diff` scopes to the *files named* in `<ref>..HEAD` but reads each file's **current working-tree content**, not the exact pushed blobs. A secret committed then removed later in the same range, or dirty-edited out before the push, escapes. This is shared with the pre-delta whole-tree gate (not a delta-mode regression). A future follow-up could scan pushed blobs directly (`git diff <ref>..HEAD -U0` / per-commit `git show`); tracked as backlog, not yet implemented.
- **`--exclude <glob>`** (opt-in, repeatable) skips any file whose repo-relative path matches the fnmatch glob, in both full and `--diff` mode. The hook reads these from `.build-loop/config.json` → `securityScan.excludeGlobs` (best-effort; absent = no-op). The report always names the active globs and the count of files they removed, and a bare `*`/`**` (or a glob removing >50% of candidates) emits a stderr warning — an over-broad glob cannot silently bypass the whole scan unnoticed.
- With neither `--diff` nor `--exclude`, behavior is unchanged (whole-tree, git-tracked files).
- Suppress a *confirmed* false positive with an inline `// nosec: <reason>` (JS/TS) or `# nosec: <reason>` (Python/shell) on the flagged line.

## What it catches (DET layer — the greppable 80/20)

Checks A–G live in `scripts/security_scan.py`; the API and AI-boundary checks H–N live in `scripts/security_checks_api.py`. Shared primitives are in `scripts/security_common.py`.

### Content classes (A–G)

| Check | Severity | OWASP |
|---|---|---|
| A · Hardcoded provider keys / PEM private keys / `SECRET=…literal`; git-tracked `.env`/`.dev.vars` | HIGH | A07 / LLM06 |
| B · **Secret-in-logs** — `console/print` of a token/secret var or a token-labeled response body | HIGH | A09 / LLM06 |
| C · SQL built via string interpolation; `eval`/`new Function`; `child_process.exec`+concat; `shell=True`; `innerHTML=`/`dangerouslySetInnerHTML` with a var | HIGH | A03 / LLM05 / ASI05 |
| D · `fetch`/`requests` with a non-constant URL (SSRF) | MEDIUM | A10 |
| E · Public POST/PUT/PATCH/DELETE endpoint that emails/writes-DB with no rate-limit keyword | MEDIUM | A06 / LLM10 |
| F · Missing `_headers`/CSP at the project level | LOW | A02 |
| G · User/tool input concatenated into a `*prompt`/`system` var; wildcard tool perms (`tools:["*"]`) | MEDIUM | LLM01 / LLM06 / ASI02 |

### Authorization and boundary classes (H–N)

| Check | Severity | OWASP |
|---|---|---|
| H · **Broken object-level authorization** — handler queries the data store with a request-supplied id and no owner/tenant predicate. CRITICAL when the handler mutates | CRITICAL / HIGH | A01 |
| I · Mutating route with no auth reference at all; **fail-open env comparison** (`token !== process.env.X` with no assertion that `X` is set — unset env means the check passes for everyone) | CRITICAL / HIGH | A01 / A07 |
| J · Privileged key behind a client-exposed prefix (`NEXT_PUBLIC_`, `VITE_`, `EXPO_PUBLIC_`, …) — service-role keys, provider API keys, signing secrets. Publishable/anon/DSN identifiers are allowlisted | CRITICAL / HIGH | A07 / LLM06 |
| K · JWT `alg: none`, decode-without-verify, `verify_signature: False`; session cookie missing `httpOnly`/`secure`/`sameSite`; auth token in `localStorage` | CRITICAL–MEDIUM | A02 / A07 |
| L · `Access-Control-Allow-Origin: *` or reflected origin, HIGH when paired with `Allow-Credentials: true` | HIGH / MEDIUM | A05 / A01 |
| M · Request body spread into a DB write with no schema validation (mass assignment — caller sets `role`, `tenant_id`, `is_admin`); mutating route reading a body with no validation library present | HIGH / MEDIUM | A03 / A04 |
| N · Model call with no output-token cap (HIGH when also untimed inside a loop); model output into an interpreter/shell/SQL/HTML sink; vector retrieval with no tenant/ACL filter; model-proposed tool calls dispatched with no authorization layer | HIGH / MEDIUM | LLM02 / LLM04 / LLM08 / ASI02 / ASI05 / ASI06 |

Every H–N check requires **two** signals before it emits: a handler or sink match *and* the absence of the corresponding control. A gate that hard-blocks deploys cannot afford a noisy check, so single-signal heuristics are excluded even where they would catch more.

## What it does NOT catch — escalate to the JUDGE layer

The scanner grades structure, not intent. It sees that a query has an owner predicate; it cannot see whether that predicate is the *right* one. Escalate for: business-rule and workflow authorization, property-level field permissions, whether a tenant boundary is correctly designed, RAG corpus partitioning, tool-permission scope versus actual task need, agent goal-drift, approval gates before destructive actions, and supply-chain trust. Load `Skill("build-loop:security-methodology")`, and when the change crosses a security boundary dispatch the `security-reviewer` agent. Scanner findings and agent findings both cite the same `references/cross-source-matrix.md` rows, so they compose into one report.

## Interpreting findings

- **CRITICAL** → blocks in every scope, including a spot finding in a file you did not touch. These are the shapes where shipping is the wrong move regardless of authorship: an unscoped mutating query, a fail-open auth guard, a service-role key in the client bundle, `alg: none`.
- **HIGH** → fix before push (the gate blocks) when the finding is in a changed file. Advisory when it comes from the spot sweep. If it's a genuine false positive, annotate with `// nosec: <reason>`; if you must ship anyway, `BUILD_LOOP_HOOKS=off` bypasses the gate for that command (use sparingly, it's logged in the diff intent).
- **MEDIUM / LOW** → advisory. Route to `.build-loop/backlog/` rather than blocking. Rate-limiting and headers gaps live here.

Findings carry `scope: "deep" | "spot"` in JSON output, and `--json` `summary` reports `spot_total` and `blocking_total` alongside the severity counts.

Sources for every ID: `skills/security-methodology/references/owasp-{web,llm,agentic}-top-10.md` (current as of OWASP Web 2025 RC1, LLM v2.0 2025, Agentic 2026). The scanner is the enforcement arm; the methodology skill is the citation trail.
