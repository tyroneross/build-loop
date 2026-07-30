# Plugin distribution — DETECT the shape, then apply the policy

> **Detect first. Do not apply a hard rule.** A blanket policy is how this went wrong once
> already (2026-07-14): "omit `version` everywhere" was right for 17 plugins and wrong for
> build-loop, and the wrongness was invisible until someone reasoned about the *install
> source* instead of the fleet default.

## Two things people conflate — keep them apart

| | What it is | How it's decided |
|---|---|---|
| **POLICY** | auto-SHA vs semver | **DETECTED per repo** — depends on the *install source*, which the repo does not choose unilaterally. Run the detector. |
| **INVARIANT** | version must be CONSISTENT across every surface | **HARD RULE, always.** Enforced in CI by the shared `verify-plugin-manifests.yml`. |

A version set on only ONE surface silently masks the others (`plugin.json` wins over the
marketplace entry). That masking is the defect that drifted the whole fleet. Consistency is
non-negotiable; *which* value (or none) is a detected choice.

**Orthogonal:** `package.json` version. An npm-published repo ALWAYS keeps semver there — npm
requires it — regardless of the plugin's distribution policy. Both coexist happily; four repos
in the fleet run auto-SHA plugins *and* semver npm artifacts.

## The detector

```bash
python3 scripts/detect_plugin_distribution.py <repo> \
  --hub ~/dev/git-folder/RossLabs-AI-Toolkit/.claude-plugin/marketplace.json
```

Advisory only — it never edits. Run it BEFORE changing any version field.

## Decision table (what the detector emits)

| Shape | Detected by | Policy | Why |
|---|---|---|---|
| `git-sourced-plugin` | marketplace entry `source` = github / git-subdir / url | **auto-sha** | Omitting `version` makes the host resolve to the commit SHA — every push ships. A pinned version freezes `/plugin update` until someone remembers to bump. That forgetting is exactly how 15 plugins drifted. |
| `directory-sourced-tool` | source = `directory` (local path) only | **semver** | The host reads the local dir and never resolves from git. `version` is not the update key; omitting it buys nothing. |
| `dual-sourced` | BOTH a directory source and a git source | **semver-but-must-bump** | The two paths imply opposite policies. The directory path makes `version` look irrelevant; the git path means that same pinned version freezes `/plugin update` for *everyone else*. Semver is viable ONLY if you truly bump every release. **Decide explicitly — never infer from the local path alone.** |
| `app-companion` | plugin under `plugin/` inside a private app repo | **follow-app** | Ships with the app; follows the app's cadence, not the fleet's. |
| `unknown-source` | can't determine | auto-sha (default) | Verify the marketplace entry before acting. |

## The trap that produced this doc

build-loop was waved through as "directory-sourced, so `version` isn't its update key." True —
**on the author's machine.** It is *also* published in the public hub as a **github** source, so
its pinned `plugin.json` version genuinely *did* freeze `/plugin update` for anyone installing
it from the toolkit. Reasoning from one distribution path and generalizing is the failure mode.
The detector now reports **every** source and refuses to silently pick one.

## Applying it

- **New plugin:** run the detector; it will say `unknown-source` until the marketplace entry
  exists. Default to auto-SHA (omit `version`), and call the shared verification workflow.
- **Existing plugin:** run the detector before touching a version field. If it says
  `dual-sourced`, that is a decision to make out loud, not a default to apply.
- **CI (all shapes):** call the shared workflow — it enforces the consistency invariant
  regardless of which policy the repo runs:
  ```yaml
  jobs:
    verify-manifests:
      uses: tyroneross/RossLabs-AI-Toolkit/.github/workflows/verify-plugin-manifests.yml@plugin-ci-v1
    publish:
      needs: verify-manifests   # keeps the release gated
  ```
