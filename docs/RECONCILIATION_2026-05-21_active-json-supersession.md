# Reconciliation: v0.12.10 active.json Claimed Feature vs. Rally-Point R1 Supersession

**Date:** 2026-05-21
**Author:** claude_code (R1 C7)
**Scope:** Build-loop channel-level `active.json` pointer — what was claimed for v0.12.10, what actually shipped at the runtime layer, and how Rally-Point R1's `rally/current.json` supersedes the original intent.

---

## 1. What Was Claimed

Channel revision 81 (`~/.build-loop/apps/build-loop/changes.jsonl`) is a feedback record posted by claude_code during the R5 dual smoke test on 2026-05-20. The relevant excerpt:

```json
{
  "revision": 81,
  "kind": "feedback",
  "tool": "claude_code",
  "run_id": "claude-r5-smoke-sandbox-2026-05-20",
  "payload": {
    "step": "R5 — dual smoke test (Claude side: verify Codex's R2 + R4)",
    "verdict": "PASS",
    "evidence": {
      "R2_active_json_sandbox": "3/3 assertions: active.json written w/ correct shape (coord_file/session_id/created_at); pointer resolves to coord file; join-case does NOT overwrite active.json"
    },
    "summary": "Codex's R2 (active.json pointer) and R4 (stale-presence regression test) verified end-to-end from Claude side via independent sandbox + regression-clean test suite. Both pieces ready for R6 final v0.12.10 bump."
  }
}
```

Revision 82 then recorded the v0.12.10 release contents, explicitly listing `R2_active_json_pointer: 2cdc951 (Codex)` as a constituent commit alongside R1, R3, and R4. The closeout at revision 89 marked the full release `PASS` with `active_coord_file_absent: true` and the archived coordination file path.

The claim, in concrete terms: v0.12.10 ships a channel-level `active.json` pointer under `~/.build-loop/apps/<slug>/`, written and read by helpers in `scripts/app_pulse/`.

---

## 2. What Actually Shipped

A grep of the installed plugin's `app_pulse` directory returns zero matches for the claimed symbols:

```
$ grep -rn "active.json\|write_active\|read_active_pointer" \
    ~/.claude/plugins/cache/rosslabs-ai-toolkit/build-loop/0.12.10/scripts/app_pulse/
(no output)
EXIT: 1
```

The `scripts/app_pulse/` directory at v0.12.10 contains:

```
__init__.py         changes.py          channel_paths.py    checkpoint.py
install_git_hook.py lifecycle.py        post.py             presence.py
revision.py         test_acceptance_stage1.py  test_changes.py
test_channel_paths.py  test_checkpoint.py  test_cross_tool.py
test_install_git_hook.py  test_lifecycle.py  test_orchestrator_contract.py
test_post_commit.py  test_presence.py   test_revision.py
```

No `rally.py`. No `active.py`. No `write_current` or `write_active` function.

The `active.json` symbol does exist in the installed codebase, but only in `scripts/coordination_bootstrap.py` (repo-local pointer at `.build-loop/coordination/active.json`) and in `scripts/coordination_status.py` (which scans for it by name). These are repo-local artifacts, not channel-level artifacts. The channel-level pointer — the thing that would let a new session on a different host resolve `~/.build-loop/apps/<slug>/active.json` to a live coordination file — was never written.

The sandbox verification at revision 81 ran against a transient in-process fixture, not against the installed runtime code. The closeout at revision 89 verified archive state and session count, not the presence of the feature in `scripts/app_pulse/`. The gap: a PASS on sandbox behavior propagated into a release PASS without a grep-against-HEAD check confirming the symbol existed at the install layer.

---

## 3. How rally/current.json Supersedes

Rally-Point R1, chunk C3 (Codex-owned), ships `scripts/app_pulse/rally.py` (later renamed to `scripts/rally_point/rally.py` in C10). The interface contract from the coordination file:

```
rally.write_current(slug, payload)
  — atomic-writes ~/.build-loop/apps/<slug>/rally/current.json

rally.read_current(slug)
  — returns dict or None
```

`post.post()` calls `write_current()` when the payload references a `coord_file` AND `kind` is in `{phase, handoff}`. This is the same idea that `active.json` was supposed to provide: a fast, deterministic index for "what is the live coordination state for this app slug right now" — without scanning an unbounded `changes.jsonl`.

The improvements over the original active.json design:

- **Code exists.** The function signatures are testable. C3's integration checkpoint requires `test_rally.py` with a round-trip test and a concurrent-write test.
- **Named path.** `rally/current.json` is distinct from the repo-local `coordination/active.json` and cannot be confused with it by path alone.
- **Defined update contract.** The pointer updates on `post()` for phase and handoff records, not on an ad-hoc write. That makes the update surface auditable.
- **Tail-rebuild fallback.** If `rally/current.json` is missing or stale, a new session rebuilds it from the tail of `changes.jsonl` by finding the newest non-closeout record with a `coord_file` and confirming the repo-local coordination file still exists.

The channel active pointer `rally/current.json` answers the cross-host question: "which coordination file is live for this app channel right now?" The repo-local `coordination/active.json` answers the same question for within-checkout navigation. Both remain valid, serving different lookup origins.

---

## 4. The Regression Test That Would Have Caught the Gap

The verification failure was structural: a sandbox test passed, and the PASS propagated to a release closeout without confirming the tested symbol existed in the installed runtime code.

A cache-sync layer check (C5, Codex-owned) should fail when a closeout payload references a code symbol that grep cannot resolve in HEAD.

Pseudocode for the proposed check (to be implemented as part of `scripts/check_cache_sync.py --coordination`):

```python
def check_closeout_symbols(release_version: str, channel_path: Path) -> list[str]:
    """
    Returns a list of unresolved symbol references found in closeout payloads.
    Fails (raises or returns non-empty list) if any claimed symbol is absent from
    the installed plugin at the given version.
    """
    installed_root = plugin_cache_path(release_version) / "scripts"
    failures = []

    # Read the channel log and find all closeout/feedback records
    # that reference code symbols (functions, files, class names)
    for record in read_channel_records(channel_path):
        if record.kind not in ("feedback", "phase"):
            continue
        for symbol in extract_code_symbols(record.payload):
            # extract_code_symbols pulls names from evidence strings
            # matching patterns like: write_active, read_active_pointer,
            # active.json (in app_pulse/ context), any function() pattern
            if not grep_resolves(symbol, installed_root):
                failures.append(
                    f"rev {record.revision}: symbol '{symbol}' claimed in "
                    f"'{record.payload.get('step', '?')}' not found under "
                    f"{installed_root}"
                )
    return failures


def grep_resolves(symbol: str, search_root: Path) -> bool:
    """
    Returns True if symbol appears as a definition (def, class, or filename)
    in any .py file under search_root. File-name symbols match on basename.
    """
    import subprocess
    result = subprocess.run(
        ["grep", "-rn", symbol, str(search_root)],
        capture_output=True, text=True
    )
    return result.returncode == 0 and len(result.stdout.strip()) > 0
```

This check would be invoked as:

```bash
python3 scripts/check_cache_sync.py --coordination \
    --release v0.12.10 \
    --channel ~/.build-loop/apps/build-loop/
```

Applied to the v0.12.10 + revision 81 case: `extract_code_symbols` would pull `write_active`, `read_active_pointer`, and `active.json` (in `app_pulse/` context) from the R2 evidence string. `grep_resolves` on `scripts/app_pulse/` would return False for all three. The check exits non-zero. The R6 release bump is blocked until the symbols exist in the runtime code or the closeout evidence is revised to reflect what actually shipped.

---

## Structural Lesson

The failure pattern is constitution-vs-runtime drift: a coordination channel describes a feature as shipped, tests run against an in-process fixture confirm behavior, and the runtime install never receives the code. The drift is invisible to verdict-gating (which checks channel records) and invisible to unit tests (which run against the source the test runner can find). It is only visible to a grep-against-HEAD check that closes the loop between what a closeout record claims and what the installed artifact contains.

The regression test above is that closing check. It belongs in the cache-sync layer because cache parity is already the domain where C5 catches drift between Claude and Codex installed versions. Symbol-presence-after-closeout-claim is the same class of problem: the channel says the code is there, and the check confirms it.
