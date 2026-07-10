---
name: build-loop:security-scan
description: "Run before any feature push, during Phase 2 planning, or whenever an agent wants a security pass. Executes a deterministic, model-independent OWASP scanner (scripts/security_scan.py) over the repo — catches the common greppable classes: secrets in source, secrets/tokens in logs, SQL/command/eval injection, public mutating endpoints without rate limiting, missing security headers, prompt-injection sinks — and maps each finding to OWASP Web/LLM/Agentic IDs. The judgment layer (authz logic, tenant scoping, tool-permission scope, agent goal-drift) escalates to the security-reviewer agent + the security-methodology canon."
version: 0.1.0
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
2. **During Phase 2 planning.** Run it over the files/area you're about to change to surface existing security debt before adding to it.
3. **Whenever any agent wants a security pass.** It's a plain script — any orchestrator or agent can call it (agent-callable; not a user-facing slash command, so `user-invocable: false`).

## Run it

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/security_scan.py" --path <repo> [--fail-on {low,medium,high,critical}] [--json] [--diff <ref>] [--exclude <glob>]
```

- **Exit 0** = nothing at/above threshold · **Exit 1** = found something at/above threshold (this is what gates the pre-push hook).
- Default threshold is **HIGH**. `--json` emits machine output.
- **`--diff <ref>`** (opt-in) scopes the scan to files changed in `<ref>..HEAD` — scan what's being pushed, not the whole tree, so pre-existing unrelated debt doesn't block an unrelated push. Fail-safe: a bad ref / non-git path falls back to a full scan (never scans less than intended); an empty range scans nothing (exit 0). Delta discovery uses `git diff --name-only -z --relative` so non-ASCII/quoted filenames and subdirectory `--path` roots are handled correctly; a belt-and-braces guard also full-scans if the delta named changed files but the walk matched none. The pre-push hook derives `<ref>` from the upstream tracking branch (`@{u}`), and applies it **only to a plain current-branch → tracking push**: any refspec (`origin main:release`), non-tracking remote (`git push backup main`), or whole-repo flag (`--mirror`/`--all`/`--tags`) omits `--diff` and full-scans (fail-safe: never scan less than intended). No upstream → whole-repo scan.
  - **Scope limitation (working-tree vs pushed blob):** `--diff` scopes to the *files named* in `<ref>..HEAD` but reads each file's **current working-tree content**, not the exact pushed blobs. A secret committed then removed later in the same range, or dirty-edited out before the push, escapes. This is shared with the pre-delta whole-tree gate (not a delta-mode regression). A future follow-up could scan pushed blobs directly (`git diff <ref>..HEAD -U0` / per-commit `git show`); tracked as backlog, not yet implemented.
- **`--exclude <glob>`** (opt-in, repeatable) skips any file whose repo-relative path matches the fnmatch glob, in both full and `--diff` mode. The hook reads these from `.build-loop/config.json` → `securityScan.excludeGlobs` (best-effort; absent = no-op). The report always names the active globs and the count of files they removed, and a bare `*`/`**` (or a glob removing >50% of candidates) emits a stderr warning — an over-broad glob cannot silently bypass the whole scan unnoticed.
- With neither `--diff` nor `--exclude`, behavior is unchanged (whole-tree, git-tracked files).
- Suppress a *confirmed* false positive with an inline `// nosec: <reason>` (JS/TS) or `# nosec: <reason>` (Python/shell) on the flagged line.

## What it catches (DET layer — the greppable 80/20)

| Check | Severity | OWASP |
|---|---|---|
| Hardcoded provider keys / PEM private keys / `SECRET=…literal`; git-tracked `.env`/`.dev.vars` | HIGH | A07 / LLM06 |
| **Secret-in-logs** — `console/print` of a token/secret var or a token-labeled response body | HIGH | A09 / LLM06 |
| SQL built via string interpolation; `eval`/`new Function`; `child_process.exec`+concat; `shell=True`; `innerHTML=`/`dangerouslySetInnerHTML` with a var | HIGH | A03 / LLM05 / ASI05 |
| `fetch`/`requests` with a non-constant URL (SSRF) | MEDIUM | A10 |
| Public POST/PUT/PATCH/DELETE endpoint that emails/writes-DB with no rate-limit keyword | MEDIUM | A06 / LLM10 |
| Missing `_headers`/CSP at the project level | LOW | A02 |
| User/tool input concatenated into a `*prompt`/`system` var; wildcard tool perms (`tools:["*"]`) | MEDIUM | LLM01 / LLM06 / ASI02 |

## What it does NOT catch — escalate to the JUDGE layer

The scanner is the deterministic 80/20. For the judgment risks — data-ownership/authz logic, RAG/tenant scoping, tool-permission scope vs task need, agent goal-drift, approval gates before destructive actions, supply-chain trust — load `Skill("build-loop:security-methodology")`, and when the change crosses a security boundary dispatch the `security-reviewer` agent. Scanner findings and agent findings both cite the same `references/cross-source-matrix.md` rows, so they compose into one report.

## Interpreting findings

- **HIGH** → fix before push (the gate blocks). If it's a genuine false positive, annotate with `// nosec: <reason>`; if you must ship anyway, `BUILD_LOOP_HOOKS=off` bypasses the gate for that command (use sparingly, it's logged in the diff intent).
- **MEDIUM / LOW** → advisory. Route to `.build-loop/backlog/` rather than blocking. Rate-limiting and headers gaps live here.

Sources for every ID: `skills/security-methodology/references/owasp-{web,llm,agentic}-top-10.md` (current as of OWASP Web 2025 RC1, LLM v2.0 2025, Agentic 2026). The scanner is the enforcement arm; the methodology skill is the citation trail.
