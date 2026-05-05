# Memory framework security follow-up (2026-05-05)

Findings from the post-Phase-C security review. HIGH-severity items that touch personal data leaks or user-controlled SQL/path injection are fixed in this branch. The remaining items are tracked here for a separate hardening pass.

## Fixed in this branch

| ID | Sev | Fix | Commit |
|---|---|---|---|
| SEC-002 | HIGH | `recall.py` `_add_meta_filter` and `_add_meta_in_filter` now enforce a frozenset allowlist on `field` before f-string interpolation. Future callers passing dynamic input will raise `ValueError` instead of silently allowing SQL injection. | `de17a72` |
| SEC-004 | MEDIUM (raised — could enable leaks) | `_paths.decisions_dir_for_project` validates project tag against `^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$` and asserts the resolved path stays under `decisions_root()`. Path traversal via crafted project tag now raises `ValueError`. | `de17a72` |
| SEC-009 | MEDIUM (data leak) | Removed `~/.claude/plans/are-the-database-amnd-kind-cook.md` reference from `init_agent_memory_schema.sql`. Replaced hardcoded `/dev/git-folder/build-loop-memory` assertion in `test_project_resolver.py` with a check against `_paths.DEFAULT_AGENT_MEMORY_ROOT`. Replaced `atomize-ai` and `speaksavvy` test fixtures with generic `example-app`/`acme-app`/`another-app` placeholders. | `de17a72`, prior commits |
| (test isolation) | — | All tests now isolate `AGENT_MEMORY_ROOT` via the new `MemIsolationMixin` in `_test_helpers.py`. Tests no longer pollute the user's real memory store. | `2c5ddbf` |

## Remaining items

### SEC-001 (HIGH) — Stop hook shell-metacharacter exposure

`hooks/hooks.json:25` interpolates `$CLAUDE_PROJECT_DIR` and `$CLAUDE_TRANSCRIPT_PATH` via double-quoted shell strings. A value containing a literal `"` could escape the quoting and inject commands at session-end. The vars come from Claude Code itself (not attacker-controlled), so the risk is theoretical for a single-user setup, but the hook should be hardened before this plugin ships to other users.

**Fix path**: replace the inline shell command with a wrapper script (e.g. `scripts/stop_hook_capture.sh`) that takes the paths as `$1`/`$2` argv positions, and have `hooks.json` invoke `bash scripts/stop_hook_capture.sh "$CLAUDE_PROJECT_DIR" "$CLAUDE_TRANSCRIPT_PATH"`. Argv positions don't go through shell parsing, eliminating the injection vector.

### SEC-003 (HIGH) — `execute_script` is an unparameterized DDL escape hatch

`scripts/db.py` exposes `execute_script(sql: str)` which commits arbitrary SQL with no parameterization or schema-name validation. The only current caller is `truncate_facts` in `sync_db_from_files.py`, which validates schema name first — but the function itself imposes no contract on callers.

**Fix path**: rename `execute_script` to `_execute_unparameterized_ddl` (signal that it's privileged), add a docstring requiring callers to pre-validate any interpolated identifiers, and consider an allowlist of permitted DDL statement prefixes (`TRUNCATE`, `CREATE INDEX`, etc.).

### SEC-005 (MEDIUM) — LLM-extracted entity flows into subprocess args

`scan_transcript_for_decisions.py` passes the LLM's `entity` field straight into `write_decision.py --entity`. Subprocess argv is shell-safe, but a hallucinated `entity` like `../other-project/0001` interacts with `_derive_project` and could route writes outside the intended project. SEC-004's project tag validation catches this at the directory boundary, but layering validation at the entity field is cheaper and clearer.

**Fix path**: validate `entity` and `primary_tag` against the same regex used for project tags (`^[A-Za-z0-9_][A-Za-z0-9_.: -]{0,127}$`) inside `write_trusted` before constructing the subprocess args.

### SEC-006 (MEDIUM) — Transcript content sent to local LLM unredacted

`read_transcript` returns the last 60K characters of the session, which can include secrets, tokens, or PII embedded in tool outputs. The data flows to a local Ollama instance (no network egress) but Ollama caches context to disk.

**Fix path**: add a regex scrub pass for common secret patterns before passing text to `call_ollama`. Patterns: `gh[opsu]_[a-zA-Z0-9]{36,}` (GitHub tokens), `sk-[A-Za-z0-9]{32,}` (OpenAI), `xox[baprs]-[A-Za-z0-9-]+` (Slack), `postgres://[^:]*:[^@]*@` (DSN passwords), AWS access keys, etc. Document the redaction set so it can be extended.

### SEC-007 (MEDIUM) — Custom YAML parser truncates on `#` everywhere

`project_resolver._parse_projects_yaml` strips inline comments by `line.split("#", 1)[0]`, which corrupts paths containing literal `#`. Low practical risk (file paths rarely contain `#`) but flagged as fragility.

**Fix path**: use `yaml.safe_load` if PyYAML is acceptable as a dep, or extend the parser to recognize quoted values. The current parser was deliberately stdlib-only; adding PyYAML is a tradeoff worth discussing.

### SEC-008 (MEDIUM) — Cutover lock TOCTOU

`cutover_lock_active()` is checked at the top of `write_decision.py:main` before the per-project flock is acquired. A concurrent process could create the lock between the check and the flock. Window is milliseconds, but two simultaneous Stop-hook invocations from different sessions could race.

**Fix path**: re-check `cutover_lock_active()` inside the `with LockedFile(...)` block.

### SEC-010 (LOW) — `connection.env` permissions not checked

`db.py` reads `~/.config/agent-memory/connection.env` without verifying the file is mode 0600. If the file is world-readable, any local process can read DB credentials.

**Fix path**: log a warning (don't block) when `os.stat(conn_env).st_mode` allows group or world read. Optionally `chmod 600` the file at first read.

## Out of scope

- `test_bridge_preflight.py` failure — pre-existing, unrelated to memory framework. Discussed in plan as out-of-scope.
- v4 KG schema — deferred until LLM wiki ingestion is ready (per plan §KG-foresight).

## How to apply

These fixes are independent and can be addressed in order of severity (or in any order; no dependencies between them). Each is small (≤30 lines), reversible, and testable in isolation.
