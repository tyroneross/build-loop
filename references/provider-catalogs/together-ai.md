# Together AI provider catalog and workload guide

Snapshot date: **2026-11-01**

Review by: **2026-11-03**

Machine-readable companion: `references/provider-catalogs/together-ai-models.json`

This is a curated, non-exhaustive public-documentation snapshot. Serverless and dedicated inference share an API; their model availability differs.

## Decision summary

Evaluate `deepseek-ai/DeepSeek-V4.1-Flash-1017` or `Qwen/Qwen3.8-Flash` for economical general work, `nvidia/NVIDIA-Nemotron-3.5-Super-VL` for vision, and `zai-org/GLM-5.3` or `moonshotai/Kimi-K3` for harder coding/reasoning. Migrate off `Qwen/Qwen3.8-2.4T-A95B`, `MiniMaxAI/MiniMax-M3`, and `thinkingmachines/Inkling` before their November removals.

## Current serverless decision candidates

| Model | Context tokens | Input/cache/output per 1M | Tools / structured outputs |
|---|---:|---:|---|
| `meta-models/Muse-Spark-Open-816B-A42B` | 1,048,576 | $1.25 / $0.15 / $4.25 | yes / yes |
| `MiniMaxAI/MiniMax-M3.1-Flash` | 1,048,576 | $0.15 / $0.015 / $0.6 | yes / yes |
| `Qwen/Qwen3.7-Max` | unknown | $2.5 / $0.5 / $7.5 | yes / yes |
| `Qwen/Qwen3.8-Flash` | 1,000,000 | $0.15 / unknown / $0.47 | yes / yes |
| `deepseek-ai/DeepSeek-V4.1-Flash-1017` | 1,000,000 | $0.3 / $0.006 / $1.2 | yes / yes |
| `nvidia/NVIDIA-Nemotron-3.5-Super-VL` | 262,144 | $0.15 / $0.02 / $0.5 | yes / yes |
| `moonshotai/Kimi-K3` | 1,048,576 | $3 / $0.3 / $15 | yes / yes |
| `zai-org/GLM-5.3-Flash` | 1,048,575 | $0.15 / $0.03 / $0.5 | yes / yes |
| `zai-org/GLM-5.3` | 1,048,575 | $1.4 / $0.26 / $4.4 | yes / yes |
| `zai-org/GLM-5.2` | 1,048,575 | $1.4 / $0.26 / $4.4 | yes / yes |
| `openai/gpt-oss-120b` | 131,072 | $0.15 / unknown / $0.6 | yes / yes |
| `Qwen/Qwen3.5-9B` | 262,144 | $0.17 / unknown / $0.25 | yes / yes |
| `deepseek-ai/DeepSeek-V4-Pro-0813` | 1,048,576 | $1.32 / $0.13 / $3.96 | yes / yes |
| `Qwen/Qwen3.7-Plus` | 1,000,000 | $0.32 / unknown / $1.28 | yes / yes |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 1,000,000 | $0.3 / $0.006 / $1.2 | yes / yes |
| `meta-models/Muse-Glimmer-30B` | 131,072 | $0.35 / $0.04 / $1.5 | yes / yes |

These are model-table values. Null reasoning flags do not mean disabled support; they mean the exact binding was not established by this capture. GLM-5.2 context conflicts with the reasoning guide; the model table takes precedence.

## Specialist surfaces

- vision: `nvidia/NVIDIA-Nemotron-3.5-Super-VL`. The older `Qwen/Qwen3.8-2.4T-A95B` route is scheduled for removal.
- image_generation: `openai/gpt-image-2`.
- video_generation: `ByteDance/Seedance-2.5`.
- text_to_speech: `cartesia/sonic-3`.
- speech_to_text: `nvidia/nemotron-3.5-asr-streaming-0.6b`.

There are no current serverless embedding models. The provider still documents an embeddings API; dedicated availability is model-specific. Nemotron3.5 ASR is $0.0015/minute in the model table versus $0.0045 on pricing. Qwen3.8 cached input is $0.50/M versus $0.25/M. Both conflicts remain in the JSON; model-table precedence applies.

## Capability and capacity contracts

Use the model tables to select tool/schema support; send the documented JSON Schema format and validate application inputs. Reasoning fields and controls vary by model; preserve unmodified reasoning under the same returned field where the model requires it. Vision, transcription/translation, HTTP speech streaming and WebSocket speech delivery are documented. Use canonical `https://api.together.ai/v1`; the older .xyz alias was not reverified.

Rate limits are contract- and model-specific. Demand can cause 429 or 503: use bounded backoff and consider provisioned throughput for contracted guarantees. Earlier claims about automatic limit growth, specific headers and per-second enforcement were not re-established. Batch discount and original-version retention are model-specific, not blanket guarantees. Account-specific access was not tested; no TOGETHER_API_KEY was used.

## Scheduled removals

- `Qwen/Qwen3.8-2.4T-A95B`: 2026-11-04; replace with `Qwen/Qwen3.7-Max`; no dedicated route documented.
- `MiniMaxAI/MiniMax-M3`: 2026-11-06; replace with `MiniMaxAI/MiniMax-M3.1-Flash`; dedicated availability remains documented.
- `thinkingmachines/Inkling`: 2026-11-06; replace with `meta-models/Muse-Spark-Open-816B-A42B`; dedicated availability remains documented.

## Recent removals

The four September removals have September 15 in history and September 14 in the older schedule. Preserve the discrepancy rather than silently choosing one source statement.

- `deepseek-ai/DeepSeek-V4-Flash-0731`: 2026-10-22; dedicated availability: yes.
- `meta-llama/Llama-3.3-70B-Instruct-Turbo`: 2026-10-22; dedicated availability: yes.
- `openai/gpt-oss-20b`: 2026-09-15; dedicated availability: yes.
- `google/gemma-4-31B-it`: 2026-09-15; dedicated availability: yes.
- `thinkingmachines/Inkling-Small`: 2026-09-15; dedicated availability: yes.
- `intfloat/multilingual-e5-large-instruct`: 2026-09-15; dedicated availability: no.
- `nvidia/Nemotron-3-ultra-550b-a55b`: 2026-08-27; dedicated availability: no.
- `pearl-ai/gemma-4-31b-it`: 2026-08-27; dedicated availability: no.
- `deepseek-ai/DeepSeek-V4-Pro`: 2026-08-27; dedicated availability: yes.
- `moonshotai/Kimi-K2.7-Code`: 2026-08-27; dedicated availability: yes.
- `meta-llama/Llama-Guard-4-12B`: 2026-08-25; dedicated availability: no.
- `moonshotai/Kimi-K2.6`: 2026-08-19; dedicated availability: yes.

The 2026-08-27 removals remain historical records.

## Freshness and evidence

Recheck by 2026-11-03. Recheck lifecycle changes before selecting a route. Public documentation and anonymous metadata do not prove account access, effective capacity, runtime quality, or a contractual SLA. This catalog does not enable a Build Loop host model.

Reverified 2026-11-01: https://docs.together.ai/docs/serverless/models supplies current chat contexts, prices, tool flags, and schema flags; https://docs.together.ai/docs/deprecations supplies November 4 and November 6 removals plus October 22 history. The recommendation page still names routes scheduled for removal, so deprecations take precedence. All listed public URLs were fetched. No TOGETHER_API_KEY or inference request was used.

## Sources

- [serverless_models](https://docs.together.ai/docs/serverless/models)
- [recommended_models](https://docs.together.ai/docs/inference/recommended-models)
- [rate_limits](https://docs.together.ai/docs/serverless/rate-limits)
- [deprecations](https://docs.together.ai/docs/deprecations)
- [structured_outputs](https://docs.together.ai/docs/inference/chat/structured-outputs)
- [reasoning](https://docs.together.ai/docs/inference/chat/reasoning)
- [function_calling](https://docs.together.ai/docs/inference/function-calling/overview)
- [vision](https://docs.together.ai/docs/inference/vision/overview)
- [transcription](https://docs.together.ai/docs/inference/transcription/overview)
- [text_to_speech](https://docs.together.ai/docs/inference/text-to-speech/overview)
- [pricing](https://www.together.ai/pricing)
- [openai_compatibility](https://docs.together.ai/docs/inference/openai-compatibility)
