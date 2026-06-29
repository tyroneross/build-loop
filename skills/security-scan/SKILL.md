---
name: build-loop:security-scan
description: "Run before any feature push, during Phase 2 planning, or whenever an agent wants a security pass. Executes a deterministic, model-independent OWASP scanner (scripts/security_scan.py) over the repo — catches the common greppable classes: secrets in source, secrets/tokens in logs, SQL/command/eval injection, public mutating endpoints without rate limiting, missing security headers, prompt-injection sinks — and maps each finding to OWASP Web/LLM/Agentic IDs. The judgment layer (authz logic, tenant scoping, tool-permission scope, agent goal-drift) escalates to the security-reviewer agent + the security-methodology canon."
version: 0.1.0
user-invocable: true
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
3. **Whenever any agent wants a security pass.** It's `user-invocable` and a plain script — any orchestrator or agent can call it.

## Run it

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/security_scan.py" --path <repo> [--fail-on {low,medium,high,critical}] [--json]
```

- **Exit 0** = nothing at/above threshold · **Exit 1** = found something at/above threshold (this is what gates the pre-push hook).
- Default threshold is **HIGH**. `--json` emits machine output.
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
