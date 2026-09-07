# Fireworks AI provider catalog and workload guide

Snapshot date: **2026-09-07**

Review by: **2026-09-21**

Machine-readable companion: `references/provider-catalogs/fireworks-ai-models.json`

Use this guide for downstream Fireworks AI inference. It covers the current official recommendations, headline serverless prices, serving tiers, capability contracts, adaptive limits, and recent serverless deprecations. The dynamic model library and authenticated List Models API remain the exhaustive availability sources.

## Decision summary

- Start with Standard serverless for evaluation and variable traffic. Use Priority when peak-load reliability matters, Fast where the price table lists it and latency tests justify the premium, and on-demand deployments for predictable capacity, version control, custom models, or LoRA.
- Use `accounts/fireworks/models/deepseek-v4-flash-0731` for low-cost extraction/classification candidates and `accounts/fireworks/models/minimax-m3` for a low-cost general agent candidate. Benchmark both because Fireworks recommendations are vendor guidance.
- Evaluate `accounts/fireworks/models/deepseek-v4-pro-0813`, `accounts/fireworks/models/kimi-k3`, and `accounts/fireworks/models/glm-5p2` for harder coding, agent, or reasoning workloads.
- Use `accounts/fireworks/models/qwen3-omni-30b-a3b-instruct` only for a deployed audio/video-input workload after confirming the exact deployment surface. The current changelog and current input guides describe different audio/image surfaces.
- Do not route new work to the 2026-08-27 deprecated serverless IDs. Use their documented replacements.

## Current decision candidates

Context windows are null in the JSON when Fireworks' captured public docs did not state them. Query the authenticated List Models API before enforcing a context limit.

| Model ID | Standard input/cache/output per 1M | Priority input/cache/output | Best fit |
|---|---:|---:|---|
| `accounts/fireworks/models/kimi-k3` | $3.00 / $0.30 / $15.00 | $3.75 / $0.375 / $18.75 | high-latency-budget coding and agents |
| `accounts/fireworks/models/deepseek-v4-pro-0813` | $1.32 / $0.044 / $3.96 | $1.65 / $0.055 / $4.95 | coding, reasoning, long context |
| `accounts/fireworks/models/deepseek-v4-flash-0731` | $0.22 / $0.007 / $0.66 | $0.275 / $0.00875 / $0.825 | extraction, classification, search |
| `accounts/fireworks/models/glm-5p2` | $1.40 / $0.14 / $4.40 | $1.75 / $0.18 / $5.50 | agents and reasoning |
| `accounts/fireworks/models/qwen3p7-plus` | $0.40 / $0.08 / $1.60 | unavailable | long context and vision candidate |
| `accounts/fireworks/models/minimax-m3` | $0.30 / $0.06 / $1.20 | $0.45 / $0.09 / $1.80 | lower-cost coding and agents |
| `accounts/fireworks/models/gpt-oss-120b` | $0.15 / $0.015 / $0.60 | $0.18 / $0.018 / $0.72 | reasoning candidate |
| `accounts/fireworks/models/step-3p7-flash-nvfp4` | not stated | not stated | fast extraction, classification, and vision candidate |
| `accounts/fireworks/models/qwen3-embedding-8b` | $0.10 input | not stated | embeddings |

The Priority column on the pricing page is the source of truth for Priority availability. Batch inference costs 50% of serverless input and output prices.

## Serving and performance guidance

### Standard, Priority, Fast, and on-demand

- Standard is best-effort shared capacity. Fireworks provides no uptime or latency SLA for serverless.
- Priority uses the same model and rate-limit pool but receives higher priority during peak load. It reduces 503 load shedding; it does not guarantee success within the account limit.
- Fast is model-specific and carries a separate price. Confirm the price-table row and benchmark before selecting it.
- On-demand uses dedicated GPUs, has no hard platform rate limit beyond deployment capacity, and supports broader/custom model choices. Set the region at deployment creation; use a global placement when geographic failover matters.

### Adaptive rate limits

Fireworks documents three adaptive serverless metrics per account and model:

| Metric | Starting limit |
|---|---:|
| Total prompt TPM | 3,600,000 |
| Uncached prompt TPM | 900,000 |
| Generated TPM | 36,000 |

Sustained usage can raise the limit. Read the `X-Ratelimit-*` response headers. A 429 means the request crossed the adaptive limit; a 503 means the shared deployment could not serve it. Use bounded backoff for 429 and consider Priority, a fallback, or dedicated capacity for 503.

### Prompt caching

Prompt caching is enabled for every serverless model. Cached input defaults to a 50% discount unless a model-specific row lists another price. Caching is replica-local, so send a stable `x-session-affinity` header or OpenAI `user` value for repeated prefixes.

## Capability rules

### Tools and structured outputs

- Fireworks accepts OpenAI-style tools whose parameters use JSON Schema. Parallel tool calls are model dependent; verify `supportsTools` and the model record.
- JSON Object, JSON Schema, and grammar-constrained output are documented. Fireworks supports most JSON Schema 2020-12 and accepts Draft-7 `definitions` aliases.
- A null per-model capability in the JSON means the captured public pages established the provider feature but did not prove support for that exact model. Verify it through List Models or a bounded runtime probe.
- `response_format: json_schema` suppresses the separate reasoning output. If the workload needs both, put the schema in the prompt, omit `response_format`, and validate the result in the application.

### Reasoning

- Fireworks exposes reasoning through `reasoning_content` and supports `reasoning_effort` or Anthropic-compatible `thinking` controls.
- Do not send both controls in one request.
- Models with interleaved thinking require previous `reasoning_content` to be carried across tool results in the format documented for that model.

### Vision, audio, and video

- Current docs describe vision-language queries and deployed Qwen3 Omni/Nemotron models for video and audio input understanding.
- The 2026-06-10 changelog says audio inference and image generation are deprecated. That statement appears to address output-generation surfaces, while newer guides describe multimodal inputs. Confirm the endpoint and serverless/deployment tag before building either path.

## Recent serverless deprecations

Fireworks marked the following serverless routes deprecated effective 2026-08-27:

| Deprecated route | Replacement |
|---|---|
| `minimax-m2p7` | `minimax-m3` |
| `gpt-oss-20b` | `gpt-oss-120b` or `qwen3-8b` for lower latency |
| `kimi-k2p6` Fast/Turbo | `kimi-k2p6` Standard |
| `kimi-k2p7-code` Fast | `kimi-k2p7-code` Standard |
| `deepseek-v4-pro` | `deepseek-v4-pro-0813` |

`deepseek-v4-flash` was deprecated on 2026-08-14 in favor of `deepseek-v4-flash-0731`. The changelog does not give a later removal date for these entries. Treat them as unavailable for new serverless routing unless the List Models API proves otherwise.

## Production checklist

1. Fetch `supports_serverless=true` with the List Models API using the deployment account's credential.
2. Verify context, `supportsTools`, modalities, lifecycle, and serving-path availability for every chosen ID.
3. Load-test representative prompt sizes and concurrency. Record time to first token, total latency, output rate, 429/503 rate, cache hit rate, and cost.
4. Use idempotency or effect receipts before retrying any tool-using request.
5. Keep a model fallback and a serving-path fallback. Use on-demand for workloads that need stable versions or predictable capacity.

## Freshness protocol

Recheck by 2026-09-21 because the latest serverless deprecation took effect two days before this snapshot.

1. Fetch the model library, List Models API, pricing, serving paths, rate limits, recommended models, capabilities, and changelog.
2. Diff every `dynamic_fields` path in the JSON companion.
3. Advance null context/capability fields only when the runtime API or current Fireworks docs state the value.
4. Run `python3 -m pytest tests/test_provider_catalogs.py -q`.
5. Regenerate and verify the Codex artifact.

## Provenance

All sources were fetched on 2026-08-29 and are Fireworks first-party documentation. The List Models endpoint was not called because `FIREWORKS_API_KEY` was unavailable.

- [Recommended models](https://docs.fireworks.ai/guides/recommended-models)
- [Serverless pricing](https://docs.fireworks.ai/serverless/pricing)
- [Serverless overview](https://docs.fireworks.ai/serverless/overview)
- [Serverless rate limits](https://docs.fireworks.ai/serverless/rate-limits)
- [List serverless models](https://docs.fireworks.ai/faq-new/models-inference/how-to-check-if-a-model-is-available-on-serverless)
- [Tool calling](https://docs.fireworks.ai/guides/function-calling)
- [Structured outputs](https://docs.fireworks.ai/structured-responses/structured-response-formatting)
- [Reasoning](https://docs.fireworks.ai/guides/reasoning)
- [Vision models](https://docs.fireworks.ai/guides/querying-vision-language-models)
- [Video and audio inputs](https://docs.fireworks.ai/guides/video-audio-inputs)
- [Changelog](https://docs.fireworks.ai/updates/changelog)

## Changelog

- 2026-08-29: Added current model recommendations, headline pricing, serving tiers, adaptive limits, caching, tools, schemas, reasoning, multimodal boundaries, and deprecation migrations.
- 2026-08-29: Recorded missing runtime credential coverage and kept unstated context/capability fields null.
