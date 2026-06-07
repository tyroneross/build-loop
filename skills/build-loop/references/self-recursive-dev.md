<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Self-recursive build-loop dev (dogfooding)

A run is **self-recursive** when the build-loop working tree IS the loaded runtime. That's the signal that arms per-commit mode and the self-modification safety machinery — without it, both stay dormant.

## How to load the working tree as the live runtime

Recommended: pass the working tree directly to Claude Code at session start.

```sh
claude --plugin-dir ~/dev/git-folder/build-loop
```

`--plugin-dir` takes session precedence over any cached marketplace copy, and Claude Code sets `CLAUDE_PLUGIN_ROOT` to that directory. The detector reads it. No symlink, no `~/.claude/` mutation.

Convenience alias (optional, in `~/.zshrc` or `~/.bashrc`):

```sh
alias claude-bl='claude --plugin-dir ~/dev/git-folder/build-loop'
```

Use the alias when you intend to dogfood build-loop changes; use plain `claude` for normal work that should run against the released cache version.

## Why not symlink into `~/.claude/plugins/`

Marketplace plugins are installed by **copy**, not symlink, into `~/.claude/plugins/cache/<marketplace>/<name>/<version>/`. A manual symlink there is fragile:

- Auto-update GC removes orphans after 7 days.
- A version bump replaces the cache directory and clobbers the symlink.
- `~/.claude/` is a per-user config surface — drift between machines breaks reproducibility.

The detector still walks the symlink layout as a **fallback** so existing setups keep working, but it is not the recommended path.

## How detection works

`scripts/detect_self_recursive.py` (called from Phase 1 Assess) checks signals in precedence:

1. **`--runtime-root <path>` arg** (Phase 1 passes `"$CLAUDE_PLUGIN_ROOT"`). `self_recursive = (realpath(runtime_root) == realpath(workdir))`. Method = `runtime_root_arg`.
2. **`CLAUDE_PLUGIN_ROOT` env var** when the arg is absent. Same check. Method = `plugin_root_env`.
3. **`__file__` self-location** — `Path(__file__).resolve().parents[1]` gives the plugin root of the running script copy (the script lives at `<plugin_root>/scripts/<name>.py`). If that resolves to `workdir`, this is ground truth — env-independent, because `CLAUDE_PLUGIN_ROOT` is not propagated to Bash-tool subprocesses. Mismatch falls through (heuristic, not operator assertion). Method = `self_location`.
4. **Legacy fallback** — walk `~/.claude/plugins/` for a symlink resolving to the workdir. Method = `cache_symlink`.

Both manifest (`.claude-plugin/plugin.json` with a `name`) and `.git/` must be present in the workdir regardless of method.

When an explicit signal (arg or env) is present and **does not** match the workdir, detection returns `self_recursive: false` with `reason_if_false: no_runtime_link` — the explicit signal has answered the question and we do not fall through to the symlink walk.

Output JSON keys: `self_recursive`, `plugin_name`, `runtime_symlink_path`, `working_copy_branch`, `working_copy_sha`, `reason_if_false`, `detection_method`.

## Restart-boundary caveat

Changing how build-loop is loaded (cache → `--plugin-dir`, or vice versa) takes effect **only at a fresh Claude Code session**. Do not switch mid-session: the live cache copy continues to serve your skills/agents until restart, and switching can GC the in-use cache and break the current session (`Agent not found` mid-run). Deploy plugin updates at a restart boundary; the same rule applies here.

## Quick verification

After launching with `--plugin-dir`, from inside the working tree:

```sh
python3 "$CLAUDE_PLUGIN_ROOT/scripts/detect_self_recursive.py" \
  --workdir "$PWD" --runtime-root "$CLAUDE_PLUGIN_ROOT" --json
```

Expect `"self_recursive": true` and `"detection_method": "runtime_root_arg"`. Phase 1 surfaces the same in `.build-loop/state.json.selfRecursive`.
