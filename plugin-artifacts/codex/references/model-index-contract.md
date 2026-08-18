# Model Index Contract — the host-neutral read surface

**What this is.** `scripts/model_index.py` is a stdlib-only, network-free CLI that answers
one question for any host: *"what model should I use for tier X / role Y on host Z?"*

**What this is not.** It is not a second registry and it makes no routing decisions.
`references/model-taxonomy.json` stays the single source of truth; `model_taxonomy.py`,
`model_resolver.py`, `model_overrides.py`, and `resolve_agent_model.py` stay the only
resolvers. This CLI is a thin shell over them. **If it ever disagrees with them, it is
wrong** — that invariant is enforced by `scripts/test_model_index.py::AgreementTests`,
which runs both CLIs and compares.

**The gap it closes.** Before this, every consumer needed a build-loop checkout, a Python
import, and a `sys.path` insert. Codex, a shell profile, a terminal agent, and a
local-model runner had no contract to call. Now they do.

```
python3 /path/to/build-loop/scripts/model_index.py <subcommand> [--json]
```

The CLI resolves its own repo root from `__file__`, so it answers identically from any
working directory. No install, no `PYTHONPATH`, no network.

---

## Subcommands

| Command | Answers | Exit 1 when |
|---|---|---|
| `resolve --tier <t> [--segment <s>] [--host <h>]` | The selected model, the ordered fallback chain, and why | Nothing resolvable for that role/host |
| `tiers` | The T0–T5 + T-S ladder with legacy aliases (`frontier`/`thinking`/`code`/`pattern`), rank, and fallback edge | never |
| `segments` | The segment list with label + status | never |
| `models [--tier] [--segment] [--provider] [--status]` | Filtered model rows with metadata | no row matches |
| `export [--format json\|env\|toml]` | The whole tier→model map, for a non-Python host | never |
| `agent <name>` | The model for a build-loop agent (delegates to `resolve_agent_model.py`) | agent file missing, or role unresolvable |

Every subcommand accepts `--json` and `--workdir`. Default output is human-readable.

**Exit codes: `0` success · `1` not found / unresolved · `2` hard error.** A `1` is a real
answer ("the index has nothing for you"), not a crash — the payload still parses and
`model` is `null`. A `2` means the index itself is unreadable. Errors print JSON to stderr
as `{"error": "...", "kind": "not-found"|"error"}`.

### `--tier`

Accepts **either vocabulary**: a ladder rung (`T0`…`T5`, `T-S`) or a legacy token
(`frontier`, `thinking`, `code`, `pattern`). They fold to the same answer —
`resolve --tier frontier` and `resolve --tier T1` return the same model.

### `--segment`

Omitted, a legacy-mappable tier uses the single-axis path (the historical default).
Supplied, resolution uses the two-axis `(segment, tier)` role path — the same call the
orchestrator makes at dispatch. `T0`/`T5`/`T-S` have no legacy token, so they use the role
path with the implicit segment `generative_reasoning`. The payload's `axis` field says
which path ran.

### `--host`

The provider set the calling host can actually dispatch. Accepts a provider
(`anthropic`, `openai`, `google`), a host name (`claude`, `codex`, `gemini`), a
comma-separated list, or `any` to disable the filter. **Default: detect the current host.**

This matters. A model the host cannot run is never offered: on an Anthropic-only host,
`--tier T5` legitimately returns exit 1 because both T5 candidates are OpenAI/Google. Pass
`--host codex` and you get an OpenAI model for the same rung.

---

## JSON shape

Every `--json` payload is a **staleness envelope** plus a per-command body:

```json
{
  "schema_version": "2.1.0",
  "fingerprint": "4aef9cb01549ef6b",
  "fingerprint_algorithm": "sha256(json.dumps(taxonomy,sort_keys,compact,utf-8))[:16]",
  "taxonomy_path": "/…/build-loop/references/model-taxonomy.json",
  "command": "resolve"
}
```

The path field is `taxonomy_path`, never `source` — resolver envelopes already use
`source` to mean *how this model was chosen*, and `agent` passes a resolver envelope
straight through. The envelope builder raises on a key collision so the two meanings can
never silently merge.

### `resolve`

```json
{
  "tier_requested": "frontier",
  "tier": "T1",
  "legacy_tier": "frontier",
  "segment": null,
  "model": "opus",
  "provider": "anthropic",
  "resolver_source": "in-tier-chain",
  "axis": "tier",
  "host_providers": ["anthropic"],
  "fallback_chain": [
    {"model": "opus",          "tier": "frontier", "provider": "anthropic", "via": "in-tier-chain", "available": true, "host_reachable": true,  "selected": true},
    {"model": "fable",         "tier": "frontier", "provider": "anthropic", "via": "in-tier-chain", "available": true, "host_reachable": true,  "selected": false},
    {"model": "gpt-5.6-terra", "tier": "thinking", "provider": "openai",    "via": "tier-fallback", "available": true, "host_reachable": false, "selected": false}
  ],
  "resolution_path": [{"model": "opus", "tier": "frontier", "selected": true}],
  "prompting_profile": {"examples": "omit", "constraint_posture": "contextual", "…": "…"},
  "why": ["axis=tier", "source=in-tier-chain", "host-providers=['anthropic']"]
}
```

- **`model`** — dispatch this. `null` means unresolved (exit 1).
- **`fallback_chain`** — the ordered candidate walk, built from the same primitives the
  resolver walks (`preferred` + recency tiebreak for the role axis; `in_tier_candidates` +
  `TIER_FALLBACK` for the tier axis). It reports candidates; it does not re-decide. Each
  entry says why it was passed over: `available: false` (declared down in
  `.build-loop/model-availability.json`) or `host_reachable: false` (wrong provider for
  this host).
- **`resolution_path`** — verbatim from the resolver, unmodified.
- **`prompting_profile`** — the tier's prompting posture, so a consumer needs no second
  lookup to know how to prompt the model it was just handed.
- **`why`** — human-readable reasons, same content as the fields above.

The hard floor invariant is visible in the chain: a `frontier` chain never contains a
`code` or `pattern` entry.

### `agent`

Returns `resolve_agent_model.resolve()` verbatim inside the envelope: `agent`, `segment`,
`tier`, `model`, `source`, `resolution_path`, `prompting_profile`. `model: "inherit"` means
the agent flows the caller's model through — pass **no** model override.

### `export`

`models` is the flat `tier → model id` map; `entries` carries the per-tier detail
(`vocabulary`, `ladder_tier`, `provider`, `source`, `env_var`). Both vocabularies are
emitted: the four legacy tokens and every ladder rung. A rung with no reachable model is
`null` rather than omitted, so a consumer can tell *empty cell* from *I forgot to look*.

---

## Fingerprint / staleness contract

Cache the answers. Detect staleness with the fingerprint.

```
fingerprint = sha256(
    json.dumps(parsed_taxonomy, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
).hexdigest()[:16]
```

It covers the **parsed value**, not the file bytes — reformatting the JSON does not read
as a routing change, and a routing edit always does.

**This is byte-identical to `RossLabs-AI-Assistant/registry/sync.py::_json_fingerprint`,
which the AI Assistant registry stores as `source_fingerprint`.** Deliberate: a consumer
caching from one index and validating against the other would thrash forever if the
algorithms differed by a separator. Agreement is asserted by
`test_model_index.py::FingerprintTests::test_agrees_with_ai_assistant_registry_fingerprint`
(skipped when that checkout is absent).

**Consumer rule.** Store `fingerprint` alongside any cached model id. Re-run the CLI when
you need a decision; if the returned `fingerprint` differs from the cached one, the index
changed — discard the cache. Also check `schema_version`: on a **major** bump, the payload
shape may have changed and the consumer should re-read this contract rather than parse
optimistically.

---

## Worked examples

Each example below sets `BL` to the build-loop install path. Replace
`/Users/you/dev/git-folder/build-loop` with wherever you actually installed build-loop —
these examples target Codex shell profiles and CI steps, which have no Claude-Code-specific
path variable to fall back on.

### 1. Codex — a shell profile that pins the tier map

Codex reads env vars, not Python. Materialize the map once per session and let the profile
source it verbatim:

```sh
BL=/Users/you/dev/git-folder/build-loop  # set to your build-loop install path
python3 "$BL/scripts/model_index.py" export --format env --host codex > ~/.config/codex/models.env
. ~/.config/codex/models.env

echo "$BUILDLOOP_MODEL_CODE"        # execution-tier model for an OpenAI host
echo "$BUILDLOOP_MODEL_FRONTIER"    # verification/judgment tier
echo "$BUILDLOOP_MODEL_FINGERPRINT" # cache key
```

The emitted file:

```sh
# generated by scripts/model_index.py export --format env
BUILDLOOP_MODEL_SCHEMA_VERSION=2.1.0
BUILDLOOP_MODEL_FINGERPRINT=4aef9cb01549ef6b
BUILDLOOP_MODEL_FRONTIER=opus
BUILDLOOP_MODEL_THINKING=opus
BUILDLOOP_MODEL_CODE=sonnet
BUILDLOOP_MODEL_PATTERN=haiku
BUILDLOOP_MODEL_T0=mythos
BUILDLOOP_MODEL_T1=opus
…
# BUILDLOOP_MODEL_T5= (unresolved)
```

Unresolved rungs are emitted **commented out**, so `source` never sets an empty variable
that a downstream dispatch would read as a valid model id. `T-S` becomes `T_S` — a shell
identifier cannot contain a hyphen.

Refresh check (cheap, no re-export):

```sh
CURRENT=$(python3 "$BL/scripts/model_index.py" tiers --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["fingerprint"])')
[ "$CURRENT" = "$BUILDLOOP_MODEL_FINGERPRINT" ] || echo "model index changed — re-export"
```

### 2. A shell — one-shot lookup in a script or CI step

```sh
BL=/Users/you/dev/git-folder/build-loop  # set to your build-loop install path
MODEL=$(python3 "$BL/scripts/model_index.py" resolve --tier code --host any --json \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["model"] or "")')

if [ -z "$MODEL" ]; then
  echo "no model available for tier=code on this host" >&2
  exit 1
fi
echo "dispatching at: $MODEL"
```

Or skip JSON entirely and branch on the exit code:

```sh
python3 "$BL/scripts/model_index.py" resolve --tier T5 --host anthropic >/dev/null 2>&1
case $? in
  0) echo "T5 available" ;;
  1) echo "T5 has no host-reachable model — fall back to T4" ;;
  2) echo "model index unreadable — halt" >&2; exit 2 ;;
esac
```

### 3. An HTTP-less local agent — resolve a role, then prompt for it

A local-model runner with no network and no build-loop import can get both the model and
the prompting posture in one call:

```sh
BL=/Users/you/dev/git-folder/build-loop  # set to your build-loop install path
python3 "$BL/scripts/model_index.py" \
        resolve --tier T3 --segment agentic_execution --host any --json > /tmp/route.json
```

```python
import json, subprocess

BL = "/Users/you/dev/git-folder/build-loop/scripts/model_index.py"

def route(tier, segment=None, host="any"):
    cmd = ["python3", BL, "resolve", "--tier", tier, "--host", host, "--json"]
    if segment:
        cmd += ["--segment", segment]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode == 2:
        raise RuntimeError(p.stderr)
    payload = json.loads(p.stdout)
    return payload["model"], payload["prompting_profile"], payload["fingerprint"]

model, profile, fp = route("T3", "agentic_execution")
if model is None:
    model, profile, fp = route("T4", "agentic_execution")   # walk the ladder yourself

# profile["examples"] == "omit" -> skip few-shot;  profile["prompt_budget"] -> length target
```

To discover what is even available locally, filter the model rows:

```sh
python3 "$BL/scripts/model_index.py" models --status local --json
python3 "$BL/scripts/model_index.py" resolve --tier T3 --host ollama   # -> qwen2.5-coder-32b
```

`--host ollama` (also `local`, `lmstudio`, `mlx`) folds to the taxonomy's `local`
provider, so the filter matches the locally-runnable rows rather than nothing.

---

## Consumer checklist

1. Call the CLI by absolute path — it resolves its own repo root, so cwd is irrelevant.
2. Always branch on the exit code before parsing (`1` is an answer, `2` is a failure).
3. Store `fingerprint` with anything you cache; compare before reuse.
4. Pass `--host` when you know your dispatch provider; otherwise detection handles it.
5. Never hard-code a model id read from this CLI into a config file without the
   fingerprint beside it — that is how a stale id outlives the index that produced it.
