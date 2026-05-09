# Autonomy Gate — Configuration Reference

## Purpose

`scripts/autonomy_gate.py` is the generalized action classifier that lets Phase 4.5 Auto-Resolve drain non-destructive open items (guidance fixes, cache resyncs, lint cleanups, open-rec executions) without prompting the operator. The existing `scripts/deployment_policy.py` already handles push/deploy/release commands; this gate handles everything else, and delegates to deployment_policy when a command looks deployment-flavored. Together they form the complete autonomy boundary: deployment_policy is authoritative for deploy targets, autonomy_gate is authoritative for everything else.

## Default policy

### confirmFor — 7 built-in patterns

These patterns require operator confirmation (`exit 1`). They are active whenever a repo does NOT supply its own `confirmFor` list:

```
npm publish*
git push --force*
git push * main
git push * master
production deploy*
DROP TABLE*
rm -rf /*
```

Matching uses `fnmatch.fnmatch` (Unix-shell-style globs, case-insensitive).

### blockFor — empty by default

No commands are hard-blocked by default. Repos may add patterns to `blockFor` to create absolute vetoes (`exit 2`).

### Boolean flags

| Flag | Default | Meaning |
|---|---|---|
| `autoFixGuidance` | `true` | Phase 4.5 may auto-apply guidance-class fixes without operator confirmation |
| `autoExecuteOpenRecs` | `true` | Phase 4.5 may auto-execute open recommendations without operator confirmation |

These flags are read by the skill body and orchestrator (Chunks B/C). `autonomy_gate.py` surfaces them in the envelope's `flags` key but does not act on them directly.

## Repo override schema

Add an `autonomy` block to `.build-loop/config.json`:

```json
{
  "autonomy": {
    "autoFixGuidance": true,
    "autoExecuteOpenRecs": true,
    "confirmFor": [
      "wipe database*",
      "reset production*"
    ],
    "blockFor": [
      "rm -rf /"
    ]
  }
}
```

All four fields are optional. Omitting `confirmFor` or `blockFor` leaves the defaults active. Setting them (even to `[]`) replaces the defaults.

## Replacement semantics

`confirmFor` and `blockFor` **REPLACE** the defaults — they do not extend them. This is intentional: repos that need a smaller or entirely different set of guarded commands should not be forced to fight the defaults.

To extend the defaults, copy the 7 default `confirmFor` patterns into your config and add your custom patterns alongside them:

```json
{
  "autonomy": {
    "confirmFor": [
      "npm publish*",
      "git push --force*",
      "git push * main",
      "git push * master",
      "production deploy*",
      "DROP TABLE*",
      "rm -rf /*",
      "wipe database*"
    ]
  }
}
```

## Precedence

The gate applies rules in this order, stopping at the first match:

1. **deployment_policy first** — if the command contains deploy/push/release keywords, shell out to `python3 scripts/deployment_policy.py --workdir <path> --command <command>`. Use its verdict directly. `list_source: "deployment_policy"`.
2. **Repo blockFor next** — if any `blockFor` glob from `.build-loop/config.json` matches the command, return `block` (`exit 2`). `list_source: "config"`.
3. **Repo confirmFor next** — if any `confirmFor` glob from config matches, return `confirm` (`exit 1`). `list_source: "config"`.
4. **Default confirmFor next** — if no repo `confirmFor` was provided, check the 7 default patterns. Match → `confirm` (`exit 1`). `list_source: "default"`.
5. **Default blockFor next** — if no repo `blockFor` was provided, check the default block list (empty). Match → `block` (`exit 2`). `list_source: "default"`.
6. **Otherwise** — return `auto` (`exit 0`). `list_source: "default"` or `"config"` depending on whether a config file exists.

Note: repo `confirmFor` and repo `blockFor` only apply when explicitly set in config. If `confirmFor` is absent from config, step 3 is skipped and step 4 applies. If `confirmFor` is present (even as `[]`), step 4 is skipped entirely.

## Relationship to deployment_policy.py

`deployment_policy.py` is the canonical gate for push/deploy commands. It understands deployment targets (preview, testflight, production, unknown) and repo-level target policies. `autonomy_gate.py` delegates to it rather than duplicating that logic.

`autonomy_gate.py` generalizes for everything else: lint fixes, cache ops, open-rec execution, guidance application, and any other non-deploy action the orchestrator may want to auto-execute. It also delegates to deployment_policy when it detects deployment-flavored keywords.

Do not modify deployment_policy.py to handle non-deploy actions. The two scripts are intentionally decoupled; autonomy_gate calls deployment_policy as a subprocess.

## Usage from skill body / orchestrator

### Human-readable output (default)

```bash
python3 scripts/autonomy_gate.py \
  --workdir /path/to/repo \
  --action "cache resync" \
  --command "rsync ... codex cache"
# output: auto: cache resync — no pattern matched; safe to execute
# exit: 0
```

### JSON envelope (for machine consumers)

```bash
python3 scripts/autonomy_gate.py \
  --workdir /path/to/repo \
  --action "npm publish" \
  --command "npm publish" \
  --json
```

Output:

```json
{
  "action": "confirm",
  "matched_rule": "npm publish*",
  "list_source": "default",
  "reason": "matched default confirmFor pattern",
  "label": "npm publish",
  "command": "npm publish",
  "flags": {
    "autoFixGuidance": true,
    "autoExecuteOpenRecs": true
  }
}
```

### Exit code mapping (mirror of deployment_policy.py)

| Exit code | Meaning |
|---|---|
| `0` | `auto` — safe to execute without operator input |
| `1` | `confirm` — operator must approve before proceeding |
| `2` | `block` — do not execute under any circumstance |

### Consuming in a shell script

```bash
python3 scripts/autonomy_gate.py \
  --workdir "$WORKDIR" \
  --action "$ACTION_LABEL" \
  --command "$COMMAND" \
  --json > /tmp/gate_result.json
exit_code=$?

case $exit_code in
  0) echo "Auto-executing: $ACTION_LABEL" ;;
  1) echo "Needs confirmation: $ACTION_LABEL" ; exit 1 ;;
  2) echo "BLOCKED: $ACTION_LABEL" ; exit 2 ;;
esac
```

### Reading flags from the envelope

The `flags` key surfaces the two boolean flags regardless of which rule triggered. Consumers check them before deciding whether to auto-apply guidance or open-rec items:

```python
import json, subprocess, sys

result = subprocess.run(
    [sys.executable, "scripts/autonomy_gate.py",
     "--workdir", workdir, "--action", label, "--command", cmd, "--json"],
    capture_output=True, text=True,
)
data = json.loads(result.stdout)
if result.returncode == 0 and data["flags"]["autoFixGuidance"]:
    apply_guidance_fix()
```
