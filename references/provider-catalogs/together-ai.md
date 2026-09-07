# Together AI provider catalog and workload guide

Snapshot date: **2026-09-07**

Review by: **2026-09-21**

Machine-readable companion: `references/provider-catalogs/together-ai-models.json`

Use this guide for downstream Together AI workloads. The user confirmed Together AI as the intended provider. The official serverless models page remains the exhaustive catalog; the JSON companion retains the decision-relevant subset, specialist modality defaults, and recent removals.

## Decision summary

- Start with `deepseek-ai/DeepSeek-V4-Flash-0731` for a low-cost general/function-calling candidate and `MiniMaxAI/MiniMax-M3` for a low-cost long-context multimodal agent candidate.
- Evaluate `moonshotai/Kimi-K3` for harder chat, reasoning, and coding-agent workloads. Its list price is materially higher, so require a workload-quality gain before adoption.
- Use `zai-org/GLM-5.3-Flash` when long context and low token price matter, then verify quality and actual throughput on the workload.
- Use `Qwen/Qwen3.5-9B` for small/fast work only after benchmark validation. Use `openai/gpt-oss-120b` as a lower-price reasoning candidate. Together scheduled `openai/gpt-oss-20b` for removal on 2026-09-14 and names `Qwen/Qwen3.5-9B` as its replacement, so do not start new work on it.
- Move steady, high-volume, or SLA-sensitive traffic from serverless to batch, dedicated inference, or provisioned throughput based on latency and capacity needs.
- Do not route to a model listed as removed on the deprecations page even if another catalog surface still shows it.

## Current serverless decision candidates

| Model | Context | Input/cache/output per 1M | Tools | Structured output | Best fit |
|---|---:|---:|---|---|---|
| `thinkingmachines/Inkling` | 524,288 | $1.00 / $0.17 / $4.05 | yes | yes | benchmark as a general candidate |
| `MiniMaxAI/MiniMax-M3` | 524,288 | $0.30 / $0.06 / $1.20 | yes | yes | lower-cost agent and vision candidate |
| `moonshotai/Kimi-K3` | 1,048,576 | $3.00 / $0.30 / $15.00 | yes | yes | chat, reasoning, coding agents |
| `zai-org/GLM-5.3-Flash` | 1,000,000 | $0.15 / $0.03 / $0.50 | yes | yes | low-cost long context |
| `zai-org/GLM-5.2` | 1,000,000 | $1.40 / $0.26 / $4.40 | yes | yes | coding agents and functions |
| `openai/gpt-oss-120b` | 128,000 | $0.15 / — / $0.60 | yes | yes | reasoning candidate |
| `Qwen/Qwen3.5-9B` | 262,144 | $0.17 / — / $0.25 | yes | yes | small, fast, and vision candidate |
| `deepseek-ai/DeepSeek-V4-Flash-0731` | 1,000,000 | $0.14 / $0.03 / $0.28 | yes | yes | general, functions, coding |
| `deepseek-ai/DeepSeek-V4-Pro-0813` | 1,048,576 | $1.32 / $0.13 / $3.96 | yes | yes | harder reasoning |
| `Qwen/Qwen3.7-Plus` | 1,000,000 | $0.32 / — / $1.28 | unstated | unstated | long-context reasoning candidate |

A dash in Together's capability table means unstated. The JSON records that as `null`, not `false`.

## Specialist surfaces

| Workload | Current documented candidate | Price snapshot |
|---|---|---:|
| Vision | `Qwen/Qwen3.8-2.4T-A95B` | $2.50 / $0.50 cached / $6.25 per 1M |
| Image generation | `openai/gpt-image-2` | check per-megapixel page |
| Video generation | `ByteDance/Seedance-2.5` | check per-output page |
| Text to speech | `cartesia/sonic-3` | $65 per 1M characters |
| Speech to text | `nvidia/nemotron-3.5-asr-streaming-0.6b` | $0.0015 per audio minute |
| Embeddings | `intfloat/multilingual-e5-large-instruct` | $0.02 per 1M input tokens; 514-token context |

Together currently lists rerank and moderation only on dedicated inference, not serverless.

## Capability rules

### Function calling and structured outputs

- Use only models marked `Yes` for the required capability in the serverless table.
- Validate tool arguments in the application and retain stable tool-call IDs across retries.
- Structured output and vision extraction are documented for compatible models. Contract-test the exact JSON Schema because support can differ by model.

### Reasoning

- Together exposes model-specific reasoning controls and reasoning fields. Use the exact model guide; do not assume one provider-wide reasoning parameter works identically across families.
- Measure the output-token cost of reasoning. A lower input price can still produce a higher total request cost when reasoning expands output.

### Vision and audio

- Vision supports image URLs, local/base64 inputs, multiple images, and structured extraction for compatible models.
- Transcription and translation use dedicated audio endpoints. Text-to-speech supports HTTP and WebSocket streaming.
- Use the specialist endpoint and model ID, not a chat-completion assumption, for speech and image/video generation.

## Rate limits and capacity

- Serverless limits are dynamic per organization and model. Sustained successful traffic can raise them; sudden bursts can be throttled.
- Requests above the dynamic rate return 429. Requests at or below it can still return 503 when the model is overloaded.
- Read `x-ratelimit-*` and `x-tokenlimit-*` response headers. Together enforces limits per second even when documentation displays a per-minute equivalent.
- Use batch for large latency-insensitive jobs, dedicated inference for reserved hardware and pinned versions, or provisioned throughput for supported production models that need committed token capacity.

## Scheduled removals

Together has published a removal date for these models. They are excluded from the current-candidate table above; migrate before the date rather than after it.

| Model | Removal date | Documented replacement |
|---|---|---|
| `openai/gpt-oss-20b` | 2026-09-14 | `Qwen/Qwen3.5-9B` |
| `google/gemma-4-31B-it` | 2026-09-14 | `zai-org/GLM-5.3-Flash` |
| `thinkingmachines/Inkling-Small` | 2026-09-14 | `zai-org/GLM-5.3-Flash` |
| `intfloat/multilingual-e5-large-instruct` | 2026-09-14 | none published |

## Recent removals

The deprecations page records these current removals:

| Removed | Date | Migration note |
|---|---|---|
| `nvidia/Nemotron-3-ultra-550b-a55b` | 2026-08-27 | dedicated endpoint remains available |
| `pearl-ai/gemma-4-31b-it` | 2026-08-27 | no dedicated endpoint |
| `deepseek-ai/DeepSeek-V4-Pro` | 2026-08-27 | use `deepseek-ai/DeepSeek-V4-Pro-0813` |
| `moonshotai/Kimi-K2.7-Code` | 2026-08-27 | dedicated endpoint remains available |
| `meta-llama/Llama-Guard-4-12B` | 2026-08-25 | no dedicated endpoint |
| `moonshotai/Kimi-K2.6` | 2026-08-19 | evaluate `moonshotai/Kimi-K3` |

Together distinguishes redirects from new models. Same-lineage upgrades can redirect after three days; materially different models use an explicit deprecation window. Pin and test model IDs when behavior stability matters.

## Production checklist

1. Re-read the serverless table and deprecations page before selecting an ID.
2. Verify account access and live headers with the production project's key.
3. Benchmark representative prompts for quality, time to first token, total latency, output rate, 429/503 rate, context truncation, and cost.
4. Canary migrations because replacement models rarely behave identically.
5. Keep a fallback route and move stable high-volume traffic to reserved capacity when serverless variance violates the workload target.

## Freshness protocol

Recheck by 2026-09-21 because Together removed six decision-relevant models within ten days of this snapshot.

1. Fetch serverless models, recommended models, deprecations, limits, pricing, and every capability page used by a production workload.
2. If `TOGETHER_API_KEY` is available, test account access and record only model IDs and non-secret metadata.
3. Diff the JSON companion's `dynamic_fields` and keep unstated capability fields null.
4. Run `python3 -m pytest tests/test_provider_catalogs.py -q`.
5. Regenerate and verify the Codex artifact.

## Provenance

All sources were fetched on 2026-08-29 and are Together AI first-party documentation. Account-specific runtime access was not tested because `TOGETHER_API_KEY` was unavailable.

- [Serverless models](https://docs.together.ai/docs/serverless/models)
- [Recommended models](https://docs.together.ai/docs/inference/recommended-models)
- [Serverless rate limits](https://docs.together.ai/docs/serverless/rate-limits)
- [Deprecations](https://docs.together.ai/docs/deprecations)
- [Structured outputs](https://docs.together.ai/docs/inference/chat/structured-outputs)
- [Reasoning](https://docs.together.ai/docs/inference/chat/reasoning)
- [Function calling](https://docs.together.ai/docs/inference/function-calling/overview)
- [Vision](https://docs.together.ai/docs/inference/vision/overview)
- [Transcription](https://docs.together.ai/docs/inference/transcription/overview)
- [Text to speech](https://docs.together.ai/docs/inference/text-to-speech/overview)
- [Pricing](https://www.together.ai/pricing)

## Changelog

- 2026-08-29: Added current serverless decision candidates, specialist modalities, capability rules, dynamic limits, capacity guidance, and recent removals.
- 2026-08-29: Recorded the missing account-runtime check and preserved unstated capability fields as unknown.
- 2026-09-07: Re-verified against the live deprecations page. Three models this guide listed as current serverless candidates now carry a 2026-09-14 removal date and moved to Scheduled removals with their documented replacements; the historical removal table reproduced unchanged.
