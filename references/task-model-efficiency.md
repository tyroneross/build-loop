<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Task-specific model efficiency

Use the lowest-resource route that meets the task's acceptance contract and required verification. Sufficient substance and appropriate formatting define quality; decoration, redundant prose and extra implementation earn no credit. Include retries, context, skill loading, model review and repair in resource comparisons. Keep latency within the task's needs; do not pay for extra polish the user did not request.

Benchmark Lab owns tests, interpretation, and versioned routing receipts. The study is `prompt-model-benchmark-lab/docs/benchmarks/2026-09-09-short-model-efficiency-study.md`; its agent responsibilities are in `docs/benchmarks/agents.md`. This reference contains briefing guidance and untested exploration candidates. It does not change model taxonomy/resolver behavior or override verification-role floors.

## How to brief the candidates

| Candidate | Start with | Include in brief | Verify / escalate |
|---|---|---|---|
| Luna | Small extraction, synthesis, reusable calculation scripts and other bounded tasks with cheap executable checks | Exact sources/files, relevant skill excerpt, concrete output, numerical/schema requirements; request execution where math matters | Execute oracle and inspect readback. After a material failure, diagnose once and revise or escalate to Terra if unresolved. Broader reasoning is an experiment, not assumed impossible. |
| Haiku | Short grounded writing, structured ingestion, tool workflows and typed content for document renderers | Required facts, source IDs, unknown/conflicting evidence handling, minimum format, actual tool names/schema | Check facts against sources and actual tool/output state; render documents. Escalate unresolved cross-source reasoning or integration to Sonnet. |
| Terra | Scoped scripts, SQL, graph/vector queries, UI changes and routine integration | Owned files, task acceptance, relevant guidance, test/readback command and return artifact | Run code/query/UI flow and inspect result; escalate persistent ambiguity or coupled failures through the existing resolver. |
| Sonnet | Bounded implementation, synthesis and artifact work where it is a useful comparator or fallback | Same concise outcome contract; preserve constraints and use a deterministic renderer when appropriate | Verify behavior and content. Do not assume it is a gold answer or that more prose means higher quality. |
| Cheap API route via agent harness | One narrow ingestion/coding/retrieval task supported by the runtime | Pinned exact model/provider, effort, tool/skill revision, structured return, isolated working directory | Verify returned model and real tool execution. Disable cross-model fallbacks in study config; missing usage prevents a measured efficiency claim. |

These starting points are hypotheses until accompanied by a matching receipt. A candidate may expand into additional task families when its evidence supports that scope. Do not constrain Luna/Haiku forever to trivial pattern matching based only on tier names.

## Applying a lab result

Before adopting a route, read the receipt for the task family, complexity, runtime, model/effort, prompt/skill/harness versions, held-out cases, acceptance check, costs and limitations. Keep observed failures and total repair visible. Provider token counts and dollars are separate comparisons; subscription usage is not free API usage.

A `supervised-pilot` receipt permits the named scope with the specified check on every result. It is not a general model promotion. Model/effort changes, relevant skill/harness changes, new failure classes or corrected reviews trigger revalidation. Missing or stale receipts retain existing routing and mark cheaper substitutions as experiments.

When tests support a change, use the existing resolver/config override path and retain evidence and user preference provenance. Verify the resolved concrete model and effort, then obtain an executed task receipt. Do not copy the experimental ledger into Build Loop or auto-write global routing from a DOE winner.

Send one useful failure back to Benchmark Lab with input/source revisions, model identity, actual output/trace, failed requirement, usage and repair. The lab decides whether to revise the prompt, skill, runtime or model assignment. Avoid repeated blind retries and provider-wide conclusions from one case.
