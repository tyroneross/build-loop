<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Resource-Aware Execution

This is the canonical token, CPU, execution-profile, and context-conservation
contract. It is internal. The user still has one Build Loop entry point and no
resource-mode choices to manage.

## Automatic execution profile

Run `scripts/review_trigger.py` in Assess and again after Plan has concrete file
and line estimates. Persist the last envelope at
`state.json.execution.resourceProfile`.

| Profile | Trigger | Required path |
|---|---|---|
| `skip` | Small single-file/config change with no risk signal | Execute directly; deterministic validation only; do not start the full loop |
| `standard` | Multi-file, non-trivial, or 20+ line work with no high-risk signal | One independent auditor, deterministic validation, report, cheap Learn outcome; fact-check/security/simplify only when their signal exists |
| `high` | Auth, security, network, persistence, architecture, runtime, model/tool, dependency, large-diff, or ambiguous-risk signal | Full Review and cross-vendor review when reachable |

Profiles never bypass production, destructive-delete, secret, security, owed-
verification, or user-impact gates. A later high-risk signal promotes the run to
`high`; profiles never demote during a run.

## Resource-aware fan-out

Resolve the concrete model first. Then run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/parallelism.py" \
  --workdir "$PWD" --model "$MODEL" --provider "$PROVIDER" \
  --segment "$SEGMENT" --tier "$TIER" \
  --execution-location "$LOCATION" --output-size "$OUTPUT_SIZE" \
  --agent implementer \
  --independent-items "$READY_CHUNKS" --shared-capacity "$SHARED_CAPACITY" \
  --active-elsewhere "$ACTIVE_ELSEWHERE" --describe --json
```

Persist the envelope at `state.json.execution.fanout`. `effective_max` is
capacity, not a dispatch target. Pass it to `autonomy_supervisor.py fanout`;
the supervisor applies provider/host/cost/failure backpressure and chooses the
next wave. Dispatch only across a MECE partition. The absolute safety ceiling
is 150, while every lower live cap remains binding.

The role supplies an advisory effort when `--effort` is omitted. Today
`agentic_execution/T3` prefers `high`, so Codex execution resolves to Terra-high
and Claude execution resolves to Sonnet-high. Passing `--effort` remains an
explicit override.

## Accuracy-first token efficiency

Minimize tokens only among approaches expected to meet the same acceptance
criteria. Use this order:

1. Run deterministic scripts for repeatable checks, transforms, inventories,
   and schema validation.
2. Use keyword or semantic retrieval to narrow source context before any model
   reads it.
3. Route bounded scanning and classification to Pattern-tier or local models
   when a deterministic verifier can judge the output.
4. Read the relevant implementation and tests before proposing complex code.
   Early source grounding is cheaper than rework and repeated troubleshooting.
5. Use measured ledger rows to tune prompts, effort, and fan-out after quality
   passes. Never count an unmatched or lower-quality run as a token win.

### Adaptive backpressure

- Start with a bounded ramp of at most four workers.
- Reduce admissions after repeated 429s, worker errors, memory pressure,
  serious thermal state, low disk, or 80% cost use.
- Pause new work at critical thermal/memory/disk pressure or the cost ceiling.
- Recover one worker only after two stable telemetry windows.
- External cooling changes measured thermal stability; it does not bypass CPU,
  token, cost, ownership, or provider limits.

Every tool call and result reconciles into
`.build-loop/telemetry/tool-traces.jsonl` as an OTel-shaped, bounded, redacted
span. The supervisor consumes error, retry, 429, and latency summaries; Phase 6
consumes the same signals for recursive learning.

### Synthetic load safety

Never launch an unbounded background CPU loop. Run load-sensitive checks through
`build-loop-load-probe`, which caps admission, names every worker, gives each
worker an internal hard deadline, and verifies cleanup:

```bash
build-loop-load-probe --workers 4 --duration-seconds 30 -- npm test -- --runInBand
```

The lifecycle receipt contains only a fixed product/purpose, opaque run id, PID
birth identity, process group, timestamps, and cleanup result. It must never
contain prompts, URLs, secrets, repository paths, or the wrapped command line.
Host-wide cleanup may signal a probe only when its owned receipt, PID birth
identity, process-group identity, marker, and expired deadline all match.
Unknown or ambiguous processes remain advisory.

### Cloud inference: token-led

1. Use the median measured raw tokens for the same model and agent from the
   cost ledger when available.
2. Otherwise use the model/output/effort T-shirt estimate.
3. Divide the wave token budget by per-worker demand.
4. Apply configuration, CPU headroom, and hard-ceiling caps as secondary limits.

Default heuristic demand before output/effort multipliers:

| Model size | Typical role | Tokens/worker |
|---|---|---:|
| small | Pattern/utility | 8,000 |
| medium | Code/workhorse | 16,000 |
| large | Thinking | 24,000 |
| xlarge | Frontier | 32,000 |

Output multipliers are small `0.5`, medium `1.0`, large `1.75`. Effort
multipliers are low `0.75`, medium `1.0`, high `1.25`, xhigh `1.75`, max
`2.25`, ultra `3.0`. These values are routing heuristics, not pricing claims.
Measured data replaces them automatically.

### Local inference: CPU-led

Local workers reserve CPU according to model size: small `1`, medium `2`, large
`4`, xlarge `8` cores per worker. The resolver keeps two cores for the lead and
OS. Token limits apply to local inference only when the caller supplies one;
local token throughput does not silently override CPU safety.

### Unknown location

Pass the provider/location when known. `auto` recognizes Ollama, MLX, LM Studio,
llama.cpp, and explicit local adapters. Other models default to cloud so an
unknown provider receives the conservative token-led path.

## Token telemetry

`cost_ledger_hook.py` always records dispatch identity, run, phase, execution
location, model/output T-shirt sizes, and a heuristic token estimate. Provider
adapters enrich the same task id through `write_cost_ledger_row.py` with:

- `input_tokens`
- `output_tokens`
- `cache_read_input_tokens`
- `cache_creation_input_tokens`
- `phase`, `fanout_limit`, and `fanout_primary_constraint`

Keep measured buckets separate from `tokens_estimate`. Benchmark conclusions
use measured rows only.

## Bounded context

- Capture each assembled brief once under `.build-loop/briefs/<run>/<chunk>.md`.
- Pass goal, ownership, interface, acceptance criteria, falsifier, and file
  pointers. Do not paste full shared documents into every worker.
- Preserve stable prefixes for provider caching.
- Use the resolved model's `prompting_profile`; compressed/standard profiles
  remove repeated examples and rationale while keeping safety and acceptance
  contracts.
- Prefer one context for sequential/cross-cutting work. Fan-out must earn its
  repeated brief cost through genuinely independent chunks.

## Proposal maintenance

Before Phase 6 pattern detection, run the reversible consumer once:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/drain_self_review_proposals.py" \
  --workdir "$PWD" --archive --stamp "$RUN_ID" --json
```

This archives superseded, stale, and non-actionable proposals before any model
reads the queue. It never applies findings and never deletes evidence.

## Benchmark

Use `evals/token-efficiency/tasks.jsonl` as the starter task set. Record results
for identical task id, repository snapshot, and model, then compare with:

```bash
python3 scripts/token_efficiency_benchmark.py --results results.jsonl \
  --baseline current --candidate resource-aware --json
```

The harness excludes estimates and unmatched tasks from the A/B token claim.
Quality must remain non-inferior before a token reduction counts as a win.
