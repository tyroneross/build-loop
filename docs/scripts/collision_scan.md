# collision_scan.py

**Purpose:** Statically detect every Claude Code plugin where a slash-command file and a skill directory share the same base name — a shape that Claude Code's runtime resolves incorrectly, returning the slash-command body verbatim instead of executing the skill.

## What problem does this solve?

Claude Code 2.1.x registers slash-commands and skills under a single per-plugin namespace. When a plugin contains both `commands/foo.md` and `skills/foo/SKILL.md`, calling `Skill("plugin:foo")` does not load and execute the skill. Instead, the runtime emits `Launching skill: plugin:foo` and then forwards the slash-command file's body — including any unrendered Handlebars (`{{args}}` etc.) — to the model as a user message. The skill body at `SKILL.md` is never read.

This bug shipped through six versions of the build-loop plugin before being caught (see `KNOWN-ISSUES.md` "Skill-runtime collision"). It is not detectable by frontmatter validation, manifest schema checks, or namespace uniqueness checks — both files are well-formed and live in different directories. The collision is purely a runtime resolver bug, but its trigger condition is a static shape, so a static scanner can find every instance.

`collision_scan.py` walks any number of directory trees, identifies plugin roots (anything with a `plugin.json` manifest or top-level `commands/`/`skills/`), and reports every (`commands/<name>.md`, `skills/<name>/SKILL.md`) pair it finds. Output is either pretty-printed for humans or JSON for downstream tools. With `--strict`, it exits 1 on any collision so it can be wired into CI.

## How it works (algorithm)

1. **Find plugin roots.** Walk each provided root with `os.walk`, treating any directory matching one of these as a plugin: contains `.claude-plugin/plugin.json`, contains `plugin.json` or `.claude-plugin.json`, or contains a top-level `commands/` or `skills/` subdirectory. Once a directory looks like a plugin, the walker stops descending into it (a plugin is not nested inside another plugin).
2. **Resolve plugin name.** Read the manifest if present; fall back to the directory name. The manifest's `name` field is what callers use in `Skill("name:foo")`, so it must be the value used to construct the qualified collision identifier.
3. **For each plugin, enumerate slash-command files.** For every `commands/*.md`, take the file stem (`foo` from `foo.md`) and check whether `skills/foo/SKILL.md` exists. If yes, that's a collision.
4. **Aggregate.** Group results by qualified name (`plugin:foo`) so the same collision appearing in multiple plugin caches (a common situation: `~/.claude/plugins/cache/<vendor>/<name>/<version>/`) is reported once with all locations listed beneath it.

The algorithm is shape-blind by design: every collision detected is high-confidence buggy because the Skill resolver is itself shape-blind. There are no false positives.

## Inputs and outputs

- **Inputs:**
  - `--path PATH` (repeatable): directory to scan. Default: `~/.claude/plugins`.
  - `--json`: emit machine-readable JSON instead of human text.
  - `--strict`: exit 1 if any collision is found (CI gate).
- **Outputs:**
  - stdout: human-readable list grouped by qualified name, OR a JSON array of `{plugin_path, plugin_name, collision_name, qualified_name, command_file, skill_file}` records.
  - exit code: 0 normally; 1 if `--strict` and at least one collision found.
  - No filesystem side effects.

## Worked example

Scanning the user's installed plugins:

```bash
python3 scripts/collision_scan.py --path ~/.claude/plugins
```

Output (truncated):

```
Found 2 unique collision shape(s) across 4 plugin location(s):

  cccd-plugin:debug-loop
    Status: BUGGY (Skill('cccd-plugin:debug-loop') returns slash-command template)
    Locations (2):
      - /Users/.../plugins/cache/cccd/cccd-plugin/1.7.0
      - /Users/.../plugins/cache/cccd/cccd-plugin/1.8.0

  example-plugin:foo
    Status: BUGGY ...
    Locations (1):
      - /Users/.../plugins/cache/example/example-plugin/0.3.0

Fix pattern: rename commands/<name>.md to commands/<other>.md (skill name unchanged keeps Skill() callers stable).
```

JSON output of the same scan emits an array of 3 records (one per location), suitable for piping into `jq` or a test harness.

## Edge cases and known limits

- **Symlinks:** `os.walk` is called with `followlinks=False`, so symlinked plugins are not double-counted but also not traversed. If a plugin lives only behind a symlink, pass its target with `--path` explicitly.
- **Plugins without manifests:** detected via the `commands/` or `skills/` directory heuristic. Plugin name falls back to the directory name. This is correct in practice because directory names track plugin names by convention.
- **Multiple manifests in one tree:** the walker stops at the first plugin-shaped directory, so a workspace containing N plugins side by side is correctly enumerated. A plugin nested inside another plugin would be missed, but that's not a valid layout.
- **False positives:** zero. The collision shape is the bug; no further check is needed.
- **False negatives:** dynamic plugins generated at runtime are not scanned (the script is purely static). In practice, all real plugins are shipped on disk, so this is theoretical.

## Verification / how do we know it works

`scripts/test_skill_resolution.py` wraps `collision_scan.py` and asserts:
1. The build-loop repo itself contains zero unaccepted collisions.
2. Strict mode exits 1 when an accepted-sibling collision is artificially added (catches a regression in the strict-mode logic).
3. The set of accepted siblings declared in the test file matches what the scanner reports — no quiet drift.

The empirical accuracy of the underlying detection rule was verified by reproducing the original FlowDoro session bug: `Skill("build-loop:build-loop")` returned the slash-command body before the rename to `commands/run.md`; after the rename, the same call resolved correctly. See `KNOWN-ISSUES.md` "Skill-runtime collision".

## Related files

- `scripts/test_skill_resolution.py` — wraps this script and asserts repo-level invariants
- `KNOWN-ISSUES.md` — documents the original bug discovery and the fix pattern
- `skills/plugin-tests/SKILL.md` — describes when the orchestrator runs this script as part of the static plugin-tests suite
- `agents/build-orchestrator.md` §Phase 4 Review-B — auto-dispatches the plugin-tests suite (advisory, not blocking)
