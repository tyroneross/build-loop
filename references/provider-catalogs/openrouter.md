# OpenRouter provider catalog and workload guide

Snapshot date: **2026-10-19**

Review by: **2026-10-20**

Machine-readable companion: `references/provider-catalogs/openrouter-models.json`

Use this bounded snapshot for downstream provider research, not a recommendation to replace the host model. Query the live API before routing changes.

## Anonymous snapshot

685 models were returned without authentication. Counts are observed metadata, not quality or availability guarantees.

| Capability | Models |
|---|---:|
| tools | 401 |
| structured_outputs | 397 |
| reasoning | 344 |
| image_input | 411 |
| audio_input | 87 |

## Representative samples

| Model | Weekly rank | Context tokens | Input / output per 1M |
|---|---:|---:|---:|
| `deepseek/deepseek-v4.1-flash` | 1 | 1,048,576 | $0.0304 / $0.08 |
| `z-ai/glm-5.3-flash` | 2 | 1,048,576 | $0.048 / $0.16 |
| `tencent/hy4-preview` | 5 | 1,048,576 | $0.7506 / $2.2509 |
| `tencent/hy3` | 13 | 262,144 | $0.0825 / $0.33 |
| `google/gemini-3.8-flash` | 21 | 1,048,576 | $0.825 / $4.125 |
| `openai/gpt-5.6-luna` | 22 | 1,050,000 | $0.2 / $1.2 |

All six samples currently report tools, structured outputs and reasoning parameters. Their original modality metadata remains in JSON. Prices describe the API top provider; conditional time/token overrides remain in their reported per-token units. Weekly popularity measures tokens processed, not independent model quality.

## Routing and privacy

Provider order, fallback control, price/throughput/latency sorting, `data_collection` and `zdr` remain documented. Set `require_parameters: true` for capabilities that must survive routing; structured JSON also requires `response_format.type: json_schema`. Routing metrics use a rolling five-minute window with p50/p75/p90/p99. ZDR filters inference endpoints; it does not filter tool backends. EU/US in-region inference uses Business/Enterprise regional domains.

## Limits and commercial terms

Free models allow 20 requests/minute and 50/day, rising to 1,000/day after $10 purchased. Paid-model limits depend on providers; these figures do not establish production capacity. Standard credit purchases carry 5.5%, Business 8%; Enterprise terms are contractual. Standard/Business BYOK includes $25,000/month list-price inference before a 5% fee; Enterprise allowances are custom. The earlier $0.80 minimum was not re-established.

## Expiration watchlist

- `google/gemini-2.5-flash`: 2026-10-20.
- `google/gemini-2.5-flash-lite`: 2026-10-20.
- `google/gemini-2.5-flash:batch`: 2026-10-20.
- `google/gemini-2.5-pro`: 2026-10-20.
- `google/gemini-2.5-pro:batch`: 2026-10-20.
- `qwen/qwen3.5-9b`: 2026-10-21.
- `xiaomi/mimo-v2.5`: 2026-10-21.
- `xiaomi/mimo-v2.5-pro`: 2026-10-21.
- `poolside/laguna-s-2.1`: 2026-10-31.
- `poolside/laguna-s-2.1:free`: 2026-10-31.
- `poolside/laguna-xs-2.1`: 2026-10-31.
- `poolside/laguna-xs-2.1:free`: 2026-10-31.
- `nousresearch/hermes-3-llama-3.1-405b`: 2026-11-02.
- `nousresearch/hermes-3-llama-3.1-70b`: 2026-11-02.
- `bytedance-seed/seed-1.6`: 2026-11-11.
- `bytedance-seed/seed-1.6-flash`: 2026-11-11.
- `bytedance-seed/seed-2.0-code`: 2026-11-11.
- `bytedance/seedance-1-5-pro`: 2026-11-11.
- `dots-studio/dots-3-note-preview:free`: 2026-12-31.
- `z-ai/glm-4.5`: 2026-12-31.

Past dates removed from current metadata do not alone prove a model was retired. Requery the endpoint at the next review.

## Freshness and evidence

Recheck by 2026-10-20. Recheck lifecycle changes before selecting a route. Public documentation and anonymous metadata do not prove account access, effective capacity, runtime quality, or a contractual SLA. This catalog does not enable a Build Loop host model.

Reverified 2026-10-19: anonymous https://openrouter.ai/api/v1/models?output_modalities=all and the most-popular query supplied counts, sample metadata, prices and expiration records; exact response hashes are recorded. https://openrouter.ai/docs/guides/overview/models defines pricing and sort semantics. https://openrouter.ai/docs/guides/routing/provider-selection and https://openrouter.ai/pricing verified routing/privacy/performance and current commercial/free-limit controls. All listed public sources were fetched; this does not establish account access or end-to-end inference acceptance.

## Sources

- [models_api](https://openrouter.ai/api/v1/models?output_modalities=all)
- [popular_models_api](https://openrouter.ai/api/v1/models?output_modalities=all&sort=most-popular)
- [single_model_api](https://openrouter.ai/docs/guides/overview/models)
- [mimo_model_page](https://openrouter.ai/xiaomi/mimo-v2.5-20260422/apps)
- [provider_routing](https://openrouter.ai/docs/guides/routing/provider-selection)
- [model_fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks)
- [structured_outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
- [tool_calling](https://openrouter.ai/docs/guides/features/tool-calling)
- [reasoning](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
- [multimodal](https://openrouter.ai/docs/guides/overview/multimodal/overview)
- [latency](https://openrouter.ai/docs/features/latency-and-performance)
- [pricing](https://openrouter.ai/pricing)
- [faq](https://openrouter.ai/docs/faq)
- [byok](https://openrouter.ai/docs/guides/overview/auth/byok)
