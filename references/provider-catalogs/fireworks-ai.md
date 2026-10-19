# Fireworks AI provider catalog and workload guide

Snapshot date: **2026-10-19**

Review by: **2026-11-02**

Machine-readable companion: `references/provider-catalogs/fireworks-ai-models.json`

Use public evidence to select candidates for workload testing. Recommendation-page inclusion does not establish serverless support; check the lifecycle column and authenticated model availability before routing.

## Current decision candidates

| Model ID | Lifecycle | Standard input/cache/output per 1M | Priority |
|---|---|---:|---:|
| `accounts/fireworks/models/kimi-k3` | serverless | $3 / $0.3 / $15 | $3.75 / $0.375 / $18.75 |
| `accounts/fireworks/models/qwen3p7-plus` | deployment_candidate | unknown | unknown |
| `accounts/fireworks/models/minimax-m3` | serverless | $0.3 / $0.06 / $1.2 | $0.45 / $0.09 / $1.8 |
| `accounts/fireworks/models/gpt-oss-120b` | serverless | $0.15 / $0.015 / $0.6 | $0.18 / $0.018 / $0.72 |
| `accounts/fireworks/models/step-3p7-flash-nvfp4` | deployment_candidate | unknown | unknown |
| `accounts/fireworks/models/qwen3-omni-30b-a3b-instruct` | deployment_candidate | unknown | unknown |
| `accounts/fireworks/models/qwen3-embedding-8b` | serverless | $0.1 input | unknown |
| `accounts/fireworks/models/deepseek-v4p1-flash-1017` | serverless | $0.3 / $0.006 / $1.2 | $0.375 / $0.0075 / $1.5 |
| `accounts/fireworks/models/glm-5p3` | serverless | $1.4 / $0.26 / $4.4 | $1.75 / $0.325 / $5.5 |

Qwen3.7 Plus and Step3.7 public model cards say Serverless Not supported. Their prices remain unknown and they are deployment candidates. Context token counts remain null when the source only gives rounded values. Null capability values mean unknown support for that exact binding.

## Serving, limits and contracts

Standard is the default; Priority costs more and reduces peak-load shedding; Fast uses a supported router ID. Dedicated deployments provide separate GPUs and advertise no rate limits; capacity and account quotas still matter. A blanket serverless SLA claim was not re-established. The current overview advertises SLA-backed Reserved Throughput: verify the selected service contract.

The listed ceilings apply at **Spending Tier 3 and above**; lower spending tiers have lower ceilings. Spending tiers and model-size tiers are separate. Fast has separate actual-limit counters; Priority shares the standard counters.

| Tier | Total parameters | Total prompt TPM | Uncached prompt TPM | Generated TPM |
|---|---|---:|---:|---:|
| Small | <800B | 216,000,000 | 54,000,000 | 2,160,000 |
| Medium | 800B–<1.6T | 43,200,000 | 10,800,000 | 432,000 |
| Large | ≥1.6T | 21,600,000 | 5,400,000 | 216,000 |

Read current response headers; adaptive ceilings do not promise available throughput. Prompt caching is enabled by default, replica-local, and defaults to a 50% text/vision input rate unless model pricing differs. Use stable session affinity for reusable prefixes. Batch costs 50% of standard serverless input and output rates.

## Capability contracts

JSON object/schema and grammar modes are documented. Most JSON Schema 2020-12 and Draft-7 definitions aliases work; external references and some regex constructs do not. Unsupported regex constructs may be ignored, so validate locally. `response_format.type: json_schema` disables separate reasoning output; omit it and prompt the schema if both are needed. `thinking` and `reasoning_effort` are mutually exclusive. Preserve model-required `reasoning_content` across tool turns. Parallel tool calls are model dependent. Generic provider controls do not prove every model binding. Qwen3 Omni audio/video requires dedicated deployment.

## Confirmed serverless deprecations

The 2026-08-27 entries remain historical; subsequent September removals also retired some of their original replacement routes. Current migration destinations below follow that chain. September 25 removals were confirmed September 26; dedicated deployments were unaffected. The November 2 Flash-0731 training notice is a separate surface.

- `accounts/fireworks/models/minimax-m2p7` (2026-08-27): `accounts/fireworks/models/minimax-m3`.
- `accounts/fireworks/models/gpt-oss-20b` (2026-08-27): `accounts/fireworks/models/gpt-oss-120b`, `accounts/fireworks/models/qwen3-8b`.
- `accounts/fireworks/models/kimi-k2p6-fast` (2026-08-27): `accounts/fireworks/models/kimi-k3`.
- `accounts/fireworks/models/kimi-k2p7-code-fast` (2026-08-27): `accounts/fireworks/models/kimi-k3`.
- `accounts/fireworks/models/deepseek-v4-pro` (2026-08-27): `accounts/fireworks/models/deepseek-v4p1-flash-1017`.
- `accounts/fireworks/models/deepseek-v4-flash` (2026-08-14): `accounts/fireworks/models/deepseek-v4p1-flash-1017`.
- `accounts/fireworks/models/deepseek-v4-pro-0813` (2026-09-25): `accounts/fireworks/models/deepseek-v4p1-flash-1017`.
- `accounts/fireworks/models/deepseek-v4-flash-0731` (2026-09-25): `accounts/fireworks/models/deepseek-v4p1-flash-1017`.
- `accounts/fireworks/models/glm-5p2` (2026-09-25): `accounts/fireworks/models/glm-5p3`.
- `accounts/fireworks/models/kimi-k2p6` (2026-09-25): `accounts/fireworks/models/kimi-k3`.
- `accounts/fireworks/models/kimi-k2p7-code` (2026-09-25): `accounts/fireworks/models/kimi-k3`.

## Freshness and evidence

Recheck by 2026-11-02. Recheck lifecycle changes before selecting a route. Public documentation and anonymous metadata do not prove account access, effective capacity, runtime quality, or a contractual SLA. This catalog does not enable a Build Loop host model.

Reverified 2026-10-19: https://docs.fireworks.ai/updates/changelog confirms September 25 inference removals; https://docs.fireworks.ai/serverless/pricing and the listed public model cards verify current named prices and serving paths. https://docs.fireworks.ai/serverless/rate-limits verifies revised thresholds/ceilings. https://docs.fireworks.ai/serverless/overview verifies caching/batch; structured-output/reasoning/tool/input guides verify provider contracts. All original listed public sources were fetched, and additional model/card sources are recorded. Unknown exact contexts and unverified bindings stay null. No FIREWORKS_API_KEY or inference request was used.

## Sources

- [recommended_models](https://docs.fireworks.ai/guides/recommended-models)
- [serverless_pricing](https://docs.fireworks.ai/serverless/pricing)
- [serverless_overview](https://docs.fireworks.ai/serverless/overview)
- [serverless_rate_limits](https://docs.fireworks.ai/serverless/rate-limits)
- [list_models_api](https://docs.fireworks.ai/faq-new/models-inference/how-to-check-if-a-model-is-available-on-serverless)
- [tool_calling](https://docs.fireworks.ai/guides/function-calling)
- [structured_outputs](https://docs.fireworks.ai/structured-responses/structured-response-formatting)
- [reasoning](https://docs.fireworks.ai/guides/reasoning)
- [vision](https://docs.fireworks.ai/guides/querying-vision-language-models)
- [video_audio_inputs](https://docs.fireworks.ai/guides/video-audio-inputs)
- [changelog](https://docs.fireworks.ai/updates/changelog)
- [deepseek-v4p1-flash-1017_model](https://app.fireworks.ai/models/fireworks/deepseek-v4p1-flash-1017)
- [glm-5p3_model](https://app.fireworks.ai/models/fireworks/glm-5p3)
- [kimi-k3_model](https://app.fireworks.ai/models/fireworks/kimi-k3)
- [qwen3p7-plus_model](https://app.fireworks.ai/models/fireworks/qwen3p7-plus)
- [minimax-m3_model](https://app.fireworks.ai/models/fireworks/minimax-m3)
- [gpt-oss-120b_model](https://app.fireworks.ai/models/fireworks/gpt-oss-120b)
- [step-3p7-flash-nvfp4_model](https://app.fireworks.ai/models/fireworks/step-3p7-flash-nvfp4)
- [embedding_details](https://docs.fireworks.ai/guides/querying-embeddings-models)
- [serverless_modes](https://docs.fireworks.ai/serverless/serverless-modes)
- [anthropic_compatibility](https://docs.fireworks.ai/tools-sdks/anthropic-compatibility)
