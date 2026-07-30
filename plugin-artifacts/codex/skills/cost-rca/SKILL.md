---
name: cost-rca
description: Use for structured cost-impact root-cause analysis — quantify what a context/caching/model change did to token spend and dollars. Reads MEASURED tokens from the cost ledger (never estimates), splits inbound/outbound + cache-read/cache-write, computes context-window utilization, then prices each bucket against the CURRENT rate card fetched LIVE (never from memory). Triggers on "tokens saved", "cache impact", "cost of context", "dollar savings", "how much did that cost", "token delta", "cost regression". NOT for choosing a model tier (use model-tiering) or benchmarking models on a task (use model-bakeoff).
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Cost-Impact RCA

Answer "what did this change do to cost, and why" with MEASURED evidence, not guesses.
Deterministic-first: the script measures; you narrate and price live.

## Non-negotiables

- **Measured tokens only.** Never quote `tokens_estimate` or the ledger's `est_cost_usd`
  as the answer. `scripts/cost_rca.py` already excludes estimate-only rows and surfaces
  them separately as *unmeasured spend* — call that out, do not price it as if measured.
- **Price LIVE, never from memory.** Rate cards drift. Fetch the current per-MTok price
  for each bucket (input / output / cache-read / cache-write) at analysis time and CITE
  the source. Prices from training data are `[UNVERIFIED]` and must not drive a number.
- **Per bucket, not a blended rate.** Cache-read is ~10× cheaper than input and cache-write
  is ~1.25× input on Anthropic's card — a blended rate hides the whole point of the RCA.

## Procedure

1. **Aggregate measured tokens** (deterministic):
   ```
   python3 scripts/cost_rca.py --ledger ~/.bookmark/cost-ledger.jsonl [--run-id R] [--since ISO] [--group-by model] --json
   ```
   Output gives, per model and in total: `inbound`, `outbound`, `cache_read`, `cache_write`,
   `total_tokens`, `context_utilization` (peak input-side vs the model's window), and the
   `estimate_only_rows` count. For a before/after RCA, run it twice (two `--since`/`--run-id`
   windows) and diff the buckets — that diff IS the token delta.

2. **Fetch the current rate card LIVE** for each model present. Order:
   `/api-registry:lookup <provider>` → the `claude-api` skill (for Anthropic) →
   WebSearch the provider's official pricing page. Record per-MTok: input, output,
   cache-read (5-min/1-hr as applicable), cache-write. Cite each. Mark confidence
   (✅ official docs / ⚠️ secondary / ❓ unverified).

3. **Price each bucket**: `dollars_bucket = tokens_bucket / 1_000_000 * price_bucket`.
   Report per bucket AND per model, then the total. Show the arithmetic — the reader
   must be able to re-derive it.

4. **Context-window utilization**: report `peak_input_side / context_window`. High
   utilization + high cache-write with low cache-read is the classic "cache thrash"
   signature — a candidate root cause for a cost regression. Confirm the current window
   live (windows change); the script's baked-in windows are advisory.

5. **Root cause + lever** (blameless): name the token-level cause (e.g. "system prompt
   re-sent uncached each call → all inbound, zero cache-read"), then the smallest durable
   lever (stable-prefix ordering for cache hits, prompt trim, tier swap, context offload).
   Quantify the projected saving in tokens AND live dollars.

## Output shape

Lead with the headline dollar/token delta and the ✅/⚠️/❓ confidence on the pricing.
Then the per-bucket table (tokens · live $/MTok · $), then the root cause and the lever
with its projected saving. Keep the ledger path + pricing citations in a footer so the
number is auditable.
