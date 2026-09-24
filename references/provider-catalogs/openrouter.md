# OpenRouter provider catalog and workload guide

Snapshot date: **2026-09-23**

Review by: **2026-10-07**

Machine-readable companion: `references/provider-catalogs/openrouter-models.json`

Use this guide for downstream workloads that call OpenRouter. OpenRouter is a dynamic multi-provider broker, so the live Models API is the exhaustive catalog. The JSON companion retains aggregate counts, actionable expirations, and representative popular models instead of freezing all 581 model rows.

## Decision summary

- Use OpenRouter when one API, provider fallback, model fallback, data-policy routing, or BYOK aggregation matters more than a direct-provider relationship.
- Select models from the live API by required modality and parameters. Set `provider.require_parameters: true` for tools or structured outputs so OpenRouter cannot choose an endpoint that silently lacks a requested feature.
- Set `provider.zdr: true` or `data_collection: "deny"` when the workload requires those policies. Treat this as an endpoint filter, then verify the resulting provider list against your own privacy requirements.
- Use an explicit `provider.order` when vendor identity, region, compliance, or negotiated capacity matters. Keep fallbacks enabled only when every fallback satisfies the same data and capability contract.
- Do not treat popularity, advertised benchmarks, or a low route price as a quality verdict. Benchmark the finalists on the actual workload and record the provider and model returned on every response.

## Live snapshot

The public `GET /api/v1/models?output_modalities=all` response returned **620 models** on 2026-09-23. The endpoint was called unauthenticated: no credential was sent and no response header was retained. Response digest `7a4e4ddb12b5647fe5b398c82d55467b73c651cf5a31b27c75d3c03a515e5018`.

| Live attribute | Count |
|---|---:|
| Models | 620 |
| Tool-capable | 389 |
| Structured-output capable | 376 |
| Reasoning control | 329 |
| Image input | 364 |
| Audio input | 78 |

These are dated inventory counts, not stable platform limits. Query the endpoint again before changing production routing.

### Representative popular text models

The following rows were returned near the top of `GET /api/v1/models?output_modalities=all&sort=most-popular` on the snapshot date. The 620-row response digest was `f369ef469377de1105988a07ea73ffb2c8e213fc7d4661b0c471191ef1f31fac`. Popularity is an operational signal, not a recommendation.

| Model | Weekly-popularity rank | Context | Input/output per 1M tokens | Inputs | Tools / schema / reasoning |
|---|---:|---:|---:|---|---|
| `z-ai/glm-5.3-flash` | 1 | 1,048,576 | $0.15 / $0.5 | image, text, video | yes / yes / yes |
| `deepseek/deepseek-v4.1-flash` | 2 | 1,048,576 | $0.14 / $0.42 | image, text | yes / yes / yes |
| `tencent/hy4-preview` | 3 | 1,048,576 | $0.834 / $2.501 | text | yes / yes / yes |
| `openai/gpt-5.6-luna` | 4 | 1,050,000 | $0.2 / $1.2 | file, image, text | yes / yes / yes |
| `tencent/hy3` | 8 | 262,144 | $0.132 / $0.528 | text | yes / yes / yes |
| `google/gemini-3.8-flash` | 11 | 1,048,576 | $0.75 / $3.75 | audio, file, image, text, video | yes / yes / yes |

Prices are the Models API top-provider values under default conditions at capture time. Conditional overrides, a promotional model-page price, or an explicit provider route can change the actual price. The 2026-09-07 snapshot's rank-11 slot was `xiaomi/mimo-v2.5` (now ranked 6th); it dropped out of this six-row sample when the top ranks reshuffled and is no longer tracked here. Its official model page listed $0.119/$0.238 per million tokens against $0.119/$0.238 shown on 2026-09-23 (15%-off promotional rate), while the Models API's own top-provider pricing field returned $0.14/$0.28 — the discrepancy this catalog previously flagged persists but is no longer in the representative sample.

## Capability and routing rules

### Tools and structured outputs

- Filter the Models API with `supported_parameters=tools` or `structured_outputs` before selecting a model.
- Include the required request fields and set `require_parameters: true`. Model support alone is insufficient because a specific upstream endpoint can lack the feature.
- Treat tool arguments and structured output as untrusted input. Validate the JSON Schema in the application before executing any tool.
- OpenRouter supports streaming structured outputs for compatible routes. Contract-test the exact model and provider because upstream schema subsets vary.
- MiMo-V2.5 is intentionally `unknown` for schema enforcement in this snapshot: its API record listed `structured_outputs`, while its official model page said `response_format` was unsupported and JSON output was not enforced.

### Reasoning

- OpenRouter normalizes reasoning controls for compatible models through the `reasoning` request object.
- Reasoning tokens are billed as output tokens. Some upstream providers return reasoning text; others keep it hidden.
- Store the requested reasoning configuration and usage fields, not hidden chain-of-thought text, in workload telemetry.

### Vision, files, audio, and video

- The unified API supports model-dependent text, image, file, audio, and video inputs plus image, speech, transcription, embeddings, and rerank output surfaces.
- Filter on modalities before request time. A chat-compatible ID does not imply that every content type or file size works on every endpoint.
- PDF parsing can add a separate plugin and cost. Choose the parser explicitly when reproducibility matters.

## Deprecation watch

The live API returned 24 non-null `expiration_date` values. 20 are operationally actionable (a real shutdown date, not the far-future sentinel), and the two `nex-agi` rows expire nearest this capture:

| Model | Expiration |
|---|---|
| `nex-agi/nex-n2.5-mini:free` | 2026-09-25 |
| `nex-agi/nex-n2.5-pro:free` | 2026-09-25 |
| `deepseek/deepseek-v3.2` | 2026-09-28 |
| `deepseek/deepseek-v3.2-exp` | 2026-09-28 |
| `deepseek/deepseek-v3.1-terminus` | 2026-09-28 |
| `deepseek/deepseek-r1-distill-llama-70b` | 2026-09-28 |
| `minimax/minimax-m2.1` | 2026-10-08 |
| `baidu/ernie-4.5-vl-424b-a47b` | 2026-10-08 |
| `google/gemini-2.5-flash-lite` | 2026-10-20 |
| `google/gemini-2.5-flash` | 2026-10-20 |
| `google/gemini-2.5-flash:batch` | 2026-10-20 |
| `google/gemini-2.5-pro` | 2026-10-20 |
| `google/gemini-2.5-pro:batch` | 2026-10-20 |
| `bytedance-seed/seed-2.0-code` | 2026-11-11 |
| `bytedance/seedance-1-5-pro` | 2026-11-11 |
| `bytedance-seed/seed-1.6-flash` | 2026-11-11 |
| `bytedance-seed/seed-1.6` | 2026-11-11 |
| `z-ai/glm-4.5` | 2026-12-31 |
| `dots-studio/dots-3-note-preview:free` | 2026-12-31 |
| `google/gemini-2.5-flash-image` | 2027-03-15 |

The `nex-agi/nex-n2-mini`, `nex-agi/nex-n2-pro`, and `z-ai/glm-4.7-flash` rows from the 2026-09-07 snapshot have already expired and left the live catalog. `z-ai/glm-4.5v` no longer carries an expiration date. 4 rows (`stealth/space-bunny-alpha`, `z-ai/glm-5.3-flashx`, `z-ai/glm-5v-turbo`, `z-ai/glm-5-turbo`) used `2098-12-31`. The catalog preserves that value as provider metadata but does not interpret it as a real migration deadline. Query the single-model endpoint before relying on any expiring ID.

## Pricing and rate limits

- OpenRouter returns per-token, per-request, image, web-search, reasoning, and cache prices in the Models API where applicable.
- The FAQ documents a 5.5% credit-purchase fee with a $0.80 minimum and pass-through inference pricing.
- The FAQ and BYOK guide no longer describe a request-count waiver. As of 2026-09-23 both agree with the pricing page: the BYOK fee waiver is a plan-level monthly list-price inference-cost allowance (Pay-as-you-go: $25,000/month; Enterprise: $200,000/month), with the 5% fee applying only above that allowance. This supersedes the prior "first 1M BYOK requests/month" reading recorded on 2026-09-07.
- Free routes allow 50 requests per day in total until the account has purchased at least $10 of credits, then 1,000 per day. Treat free routes as evaluation capacity.
- Paid and BYOK capacity depends on the account and upstream provider. Read response metadata and provider responses instead of encoding a fixed paid-plan limit.

## Performance and production guidance

1. Start with capability and data-policy filters, then choose price, throughput, or latency ordering.
2. Measure time to first token, total latency, output rate, retries, fallback count, actual model, actual provider, and billed cost at p50/p95/p99.
3. Use explicit model and provider fallback lists. A fallback request can add latency and can change cost, safety behavior, data handling, and output quality.
4. Retry 429/5xx failures with bounded exponential backoff and jitter only when replay cannot duplicate an external effect.
5. Pin model IDs for repeatable production behavior. Use `:free`, `:thinking`, `:extended`, or other variants only after the live model record confirms the variant.
6. Maintain credit headroom because low balance and key-limit checks can add gateway latency.

## Freshness protocol

Recheck by 2026-10-07 because the live catalog contains near-term expirations and changes continuously.

1. Fetch `GET /api/v1/models?output_modalities=all` and record the response digest, counts, capability flags, prices, and expiration dates without retaining credentials.
2. Re-fetch provider routing, model fallback, structured output, tool, reasoning, multimodal, pricing, FAQ, and BYOK docs.
3. Resolve any pricing-page conflict in favor of the current account contract and record it as account-specific.
4. Run `python3 -m pytest tests/test_provider_catalogs.py -q`.
5. Regenerate and verify the Codex artifact.

Refresh immediately before a production routing or billing change, after an upstream deprecation notice, or when a required parameter disappears from a model record.

## Provenance

All sources were re-fetched on 2026-09-23 and are OpenRouter first-party pages or the first-party Models API.

- [Models API contract](https://openrouter.ai/docs/guides/overview/models)
- [Provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)
- [Model fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks)
- [Structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
- [Tool calling](https://openrouter.ai/docs/guides/features/tool-calling)
- [Reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
- [Multimodal overview](https://openrouter.ai/docs/guides/overview/multimodal/overview)
- [Latency and performance](https://openrouter.ai/docs/features/latency-and-performance)
- [Pricing](https://openrouter.ai/pricing)
- [FAQ](https://openrouter.ai/docs/faq)
- [BYOK](https://openrouter.ai/docs/guides/overview/auth/byok)

## Changelog

- 2026-08-29: Added a live aggregate snapshot, representative popular models, capability and policy routing guidance, expiration watch, commercial caveats, and a seven-day freshness contract.
- 2026-08-29: Kept the 540-model live catalog out of source control and made the Models API the exhaustive source of truth.
- 2026-09-07: Refreshed every dynamic field against the live Models API (581 models, up from 540). The popular-model sample, capability counts, response digests, and expiration watch were re-derived; `moonshotai/kimi-k2.5` no longer carries an expiration date and left the watch list. Capture is unauthenticated, so per-account routing and negotiated pricing may differ. The freshness protocol at the time read "Recheck by 2026-09-21."
- 2026-09-23: Refreshed every dynamic field against the live Models API (620 models, up from 581). Capability counts, both response digests, the actionable-expiration watch (20 rows, up from 6; 3 previously-watched rows expired and left the catalog), and the sentinel count (4, up from 3) were re-derived. The popularity ranking reshuffled: ranks 1-4 are the same four models reordered plus one swap, and the rank-8 and rank-11 sample rows changed to `tencent/hy3` and `google/gemini-3.8-flash`; `xiaomi/mimo-v2.5` fell from rank 11 to rank 6 and is no longer in the six-row representative sample. The BYOK fee-waiver threshold changed from a request-count reading to a documented monthly list-price cost allowance, confirmed consistent across the FAQ, BYOK guide, and pricing page. Capture remains unauthenticated.
