<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 7: Fact Check & Mock Scan — Detailed Guidance

Loaded on demand when entering Phase 7.

## Gate A: Fact Checker

| Check | What to Do |
|-------|-----------|
| **Rendered data** | Any %, $, score, count, assessment in UI — trace to data source. If a number appears on screen, where does it come from? |
| **Claims in code/comments** | Assertions about performance, accuracy, coverage — verified or marked ⚠️ UNVERIFIED |
| **Plan/spec claims** | What was "achieved" — cross-check against actual eval results from Phase 5 |
| **Extreme language** | Flag "always", "never", "100%", "guaranteed", "impossible", "all", "none" in code, UI copy, error messages, docs. Replace with accurate qualified language unless genuinely absolute |
| **Assessment integrity** | App displays quality scores, risk levels, health indicators? Verify scoring logic exists and produces the displayed value. No hardcoded "95%" without backing logic |
| **Source traceability** | Every rendered metric: data source → transformation → display. Missing link = flag it |

## Gate B: Mock And Privacy Data Scanner

Run this as the `mock-scanner` pass in the Review-D parallel dispatch.

Scan production code paths for:
- Hardcoded fake data in display paths (names, emails, addresses, phone numbers, prices)
- Placeholder text (lorem ipsum, "TODO", "FIXME") in rendered output
- Fake metrics: hardcoded percentages, scores, or counts not derived from real computation
- Mock API responses left in production code (not test files)
- Fake semantic search, recommendation, chart, metric, summary, or comparison responses where users expect real data to make decisions
- `faker` library or `Math.random()` generating user-facing data
- Seed/fixture data rendering outside dev/test environments
- Commented-out real implementations replaced by stubs
- API keys, private keys, bearer/OAuth tokens, connection strings, passwords, `.env` values, and credential assignments in public surfaces
- Absolute local path references such as `/Users/<name>/`, `/home/<name>/`, `C:\Users\<name>\`, plugin cache paths, local session paths, private vault/wiki paths, and machine-specific temp/build paths
- Persona/profile exports, customer/user lists, resumes, calendars, private notes, transcripts, hostnames, session IDs, Rally runtime logs, worktree bundles, and other personal or machine-specific data that should not ship publicly

**Scope**: Production code paths and public release/package surfaces. Test files, fixtures, dev-only code, and clearly synthetic documentation examples are excluded.

## Resolution

- Blocking issues (fake data rendered to users, data supporting user decisions, or private data shipping in public surfaces) -> route back to Phase 5 (Iterate). Do not halt the run; the orchestrator should invoke the appropriate implementer, auditor, or specialist agent to fix the issue and then re-run validation.
- Warnings (TODO in comments, minor language issues) → include in Review-F report

Prefer `.gitignore` plus untracking for runtime/generated files, archive or private-store relocation over deletion for useful evidence, and redaction/scrubbing over removing useful public documentation.
