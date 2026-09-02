# Session close — 2026-08-25

All numbers below were captured directly by running `git`, `pytest`, and `rally`
this session — none copied from a prior brief without re-checking.
Snapshot: **2026-08-25T16:26:53Z**. Machine is under heavy concurrent-agent
load (`uptime`: 15 logged-in users, load avg ~14) — `build-loop-memory`'s HEAD
moved twice and `build-loop` went from "ahead 6" to "even" while this doc was
being written. Treat every number below as a snapshot, not a fixed fact.

## Repo state (git), as of 2026-08-25T16:26:53Z

| Repo | HEAD | ahead/behind origin | dirty files | unmerged local branches |
|---|---|---|---|---|
| build-loop | `8854f880` "consent: pin canonical hashing to raw UTF-8 before a second implementation exists" | 0 / 0 | 1 (`tmp/`, untracked) | 2 (`codex/state-finalize-tombstone`, `codex/state-finalize-tombstone-v2`) |
| build-loop-memory | `ce9f8532` "Retro: rosslabs.ai mobile UI pass + Agent Rally Point reframe (2026-08-25)" | 0 ahead / 5 behind origin | 1022 (1015 untracked + ~7 modified, moving — telemetry/lessons write live) | 0 |
| RossLabs Ambient Agent | `2f71346` "Merge branch 'bl/rust-foundation'" | 0 / 0 | 19 (mostly untracked `.designdoc/*` files from a recent groundwork run, plus 4 modified `.build-loop`/dashboard files) | 2 (`codex/groundwork-ui-alignment`, `codex/ambient-pet-ui`) |
| agent-rally-point | `e57b7c0` "style(cockpitd): rustfmt the consent gate files" | 0 ahead / 5 behind origin | 0 | 2 (`oc/57797b1aeeef34dae7a58272d8919eb2`, `worktree-agent-a9d51497af6888983`) |

Local branches beyond `main`: build-loop has `codex/memory-staleness-fix`
(merged), `codex/state-finalize-tombstone`, `codex/state-finalize-tombstone-v2`.
build-loop-memory has none. Ambient has `bl/rust-v0-c13` and
`rally/claude-ambient-main-integration-01` (both 0 commits ahead — stale) plus
the 2 unmerged ones in the table above. agent-rally-point has
`bl/coordination-controls-dispatch-20260824` and `codex/high-confidence-improvements`
(both 0 ahead — stale) plus the 2 unmerged ones above (1 and 2 commits ahead).

agent-rally-point's "5 ahead of origin" is the same 4 parity/consent commits
seen earlier plus one more landed mid-session — push-after-green-CI is
pre-authorized per the room's mission fact, but I did not push; read-only.

## build-loop test state

Two interpreters, complementary missing deps — confirmed by direct import:

| Interpreter | Runs | `pathspec` | `psycopg` | `mlx_embeddings` |
|---|---|---|---|---|
| bare `python3` (3.14.5) | `scripts/` | **missing** | present | present |
| `./.venv/bin/python3` (3.13.11) | `tests/` | present | **missing** | **missing** |

**`tests/` under `.venv` — COMPLETE, verified:**
`901 passed, 2 failed, 5 skipped, 4 deselected in 78.10s`. Neither failure is
in `known_red_baseline.json` (that file only covers a `scripts/` test) — both
new/unlisted: `test_capability_registry.py::test_no_unknown_category` (two
new capabilities — `SessionStart[1.0]` hook, `codex_hook_trust_check` script —
classify as `unknown`; `CATEGORY_KEYWORDS` in `build_capability_registry.py`
needs an entry) and
`test_phase_6_gating_docs.py::test_helper_script_imports_and_exposes_scan`
(`enforce_retro_signals.py::scan()` on a missing dir now returns an extra
`dispositionedSkipped` key; test expects the old 2-key envelope).

**`scripts/` under bare python3 — INCOMPLETE, could not get a clean full run.**
`pytest --collect-only` succeeded cleanly: **5362 tests collected in 8.70s**,
so collection itself is not broken. Three attempts to run the full suite were
killed by machine load — each background invocation silently spawned as 2
concurrent copies of the same pytest process competing for CPU/disk, and even
a single clean invocation stalled at 4% after several minutes (reproduced 3x
as duplicate-process contention, not a code defect). **Next agent: re-run
`python3 -m pytest scripts/ --ignore=scripts/test_db.py -q` when load is
lower, and check `ps aux | grep pytest` first to confirm you're not stacking a
2nd copy.** A partial run (killed at 69%) showed ≥4 `F` markers — not a
reliable count, don't cite it as final.

**Known-red baseline — verified against the real test, not just the file:**
`scripts/known_red_baseline.json` lists one entry:
`scripts/test_embed_backend.py::EmbedBackendTests::test_cross_backend_cosine_above_threshold`,
owner `tyroneross`, expires **2026-09-22**. Ran it directly: **still fails,
cosine = -0.04868668...**, matching the recorded -0.0487. Reason: MLX
defaults to `mxbai-embed-large-v1`, Ollama to `bge-m3` — different embedding
spaces, both emit 1024 dims so shape checks pass while vectors are
near-orthogonal. Blocked on the user decision in
`BUIL-MEMORY-m0hcg9vzf60y895rj3vt9` below, not on engineering work.

## THE ACTIVE WORK — rally read-only fix, designed but not built

**Problem, reproduced fresh in a throwaway repo** — `git init`, one commit,
`rally enter --tool X --json`, `chmod -R a-w .rally`, then:

```
$ rally room --tool handoff-repro --json
{"error":"open .../.rally/direct.owner.lock: Permission denied (os error 13)","exit_code":1,"ok":false,...}

$ rally next --audit --tool handoff-repro --json
{"error":"open .../.rally/direct.owner.lock: Permission denied (os error 13)","exit_code":1,"ok":false,...}

$ rally doctor --json
{"ok":true, ..., "data":{"doctor":{"healthy":true, ...}}}   # succeeds
```

`--audit` is confirmed NOT to help — it still opens the lock file for write
before checking the audit flag (root cause below). My repro used `chmod -R
a-w` (macOS `EACCES`/errno 13); the originally-reported codex sandbox failure
was `EPERM`/errno 1 (seatbelt). Same failure class, different errno source —
the fix needs to handle both, not just the one this repro produced.

**Root cause — every line number checked directly against
`crates/rally-cli/src/store.rs` at HEAD `bb979d7`.** Six write sites sit on
the read path, all inside `open_direct_at_with_engagement` /
`open_direct_existing_at` — the functions every `RoomStore::open()` call goes
through, read or write: `store.rs:2512` `open_named_lock_file` (`create_dir_all`
then `.create(true).write(true).open(...)`, every command routes through
this); `:3757` `fs::create_dir_all(&dir)`; `:3762`
`crate::init::ensure_ignore_present(&dir)`; `:3763` `acquire_room_mutation_lock(&dir)?`;
`:3764` `fs::remove_file(dir.join("room.db"))`; `:3820` a second
`acquire_room_mutation_lock(&dir)?` (in `open_direct_existing_at`).

**Key insight, confirmed at `lib.rs:4249-4251` (`command_next`):**
```rust
let room = RoomStore::open()?;
// Default `next` remains a writeful coordination action. `--audit` is the
// explicit coordination-fact observation contract used by hooks and
// reviewers; opening the derived cache may still repair/rebuild it.
if !audit {
    ensure_presence(&room, &tool)?;
}
```
`RoomStore::open()` — the writeful store-layer open — runs **unconditionally**,
before the `!audit` check. The read-only contract the comment describes
exists in intent but not in the code path: `--audit` only skips
`ensure_presence`, not the underlying store open that already took a write
lock. **The fix belongs at the store layer (a read-only open path used when
the caller doesn't need to write), not as a new special case bolted onto
`command_next`.**

**Precedent already in the codebase — `doctor.rs:601-623`:**
`immutable_sqlite_uri()` builds a `?mode=ro&immutable=1` URI; the read path
opens with `SQLITE_OPEN_READ_ONLY | NO_MUTEX | URI` and sets `pragma
query_only`. This is how `rally doctor` gets true read-only access today.

**OPEN CORRECTNESS QUESTION — unresolved, gates whether the fix is sound:**
Is `?mode=ro&immutable=1` safe against `facts.db` while a writer (daemon or a
peer's direct-mode session) is mid-append? `immutable=1` tells the reader to
skip change-detection entirely — if the file changes underneath a reader
promised immutability, that's a torn-read hazard. `doctor.rs` may get away
with it only because its checks tolerate a stale snapshot; `rally
room`/`next --audit` feed decisions, where a torn read is worse. **Could not
find rally-room evidence of an in-flight Codex design review** — searched the
last 500 facts (`rally recent --all --limit 500 --json`) for `read-only`,
`audit`, `direct.owner.lock`, `mode=ro`, `store.rs`: no matches; `rally
claims` shows zero active claims. Either the review is happening outside the
ledger (local Codex session, not yet posted) or it hasn't started — **check
for a live Codex session before assuming it already ran**, and treat the
torn-read question as open.

**`RoomStore::open()` call-site count — counted directly in `lib.rs` at HEAD
`bb979d7`:** **46 real call sites** (48 raw grep hits minus 2 comment-only
lines). Of those, **13 are immediately followed (within 5 lines) by
`ensure_presence`** — the writeful marker. That leaves **33 with no immediate
writeful marker**, needing manual classification (read-only candidate vs.
writeful via a less-immediate call, e.g. `command_next`'s flag-gated pattern).
**This classification is not done** — it's the next concrete step before
touching `store.rs`.

**Binary vs. source — reinstall required before any empirical test of a fix:**
`rally --version` → `0.2.5+f53e209`. Source HEAD is `bb979d7`, **11 commits
ahead** of the installed build (`git log --oneline f53e209..HEAD` = 11 lines).
`~/.local/bin/rally` and `target/release/rally` both report the same stale
version. **Any test against the installed binary is testing 11-commits-old
code** — rebuild (`cargo build --release` + reinstall) before verifying a fix.

## ALREADY LANDED — verified

`dd3ddf5c` "fix(preflight): skip the rally preflight when .rally is not
writable" is on `build-loop` `main` (confirmed via `git show --stat`). Guards
the AGENTS.md codex preflight by probing writability (write+remove a probe
file — mode bits lie under macOS seatbelt) and skips cleanly, naming what's
lost (peer/claim visibility). Caller-side workaround only, doesn't touch
`store.rs` — the rally-side fix above is still fully open.

## DECISIONS OWED BY THE USER — all 3 build-loop items verified present

All three live under `build-loop/.build-loop/backlog/items/` — **gitignored**
(`.gitignore:18: /.build-loop/backlog/`), so these exist only on this machine.

- **`BUIL-MEMORY-m0hcg9vzf60y895rj3vt9`** — P2, status open, title: "MLX and
  Ollama embed backends use different models, so the fallback silently
  changes embedding space." `evidence: []` in frontmatter but the -0.0487
  cosine is independently reproduced above by re-running the test. Now has
  the hard 2026-09-22 expiry via `known_red_baseline.json` (verified above).
- **`BUIL-HOOKS-m0nyh775bzkscjb1ndnqz`** — P1, status open, title: "PreToolUse
  gates fail open under Codex (CLAUDE_PLUGIN_ROOT unset)." Cites
  `docs/2026-08-22-codex-hook-root-resolution.md` — file exists (3752 bytes).
  4 options written up there; none applied yet (still `open`).
- **`BUIL-SECURITY-m0hsaw0f7zpg13ggd4gst`** — status open, **priority is P3
  in the frontmatter today** (already downgraded). Body confirms the
  2026-08-24 re-validation: measured every `_archive`-named directory on this
  machine (4 of them, 14 `route.ts` files total), found none reachable under a
  Next.js `app/` tree — zero observed exposure, matching the brief. **Minor
  inconsistency**: frontmatter `updated:` still says `2026-08-21`, not bumped
  to match the body's 08-24 re-validation section — fix while editing this file.

**Ambient's 2 held branches — verified the subset claim, could not verify
"live design review":** `codex/groundwork-ui-alignment` is 11 commits ahead
of `main` (last commit 2026-08-18); `codex/ambient-pet-ui` is 4 ahead.
`git log codex/groundwork-ui-alignment..codex/ambient-pet-ui` returns **0**
commits — confirmed true strict subset. Could **not** confirm "a live design
review" is in progress: no open rally claim, branch's last commit is a week
old. The working tree does have an untracked `.designdoc/` dir (12 files —
architecture.json, design.md, mockups/, etc.) suggesting a recent `groundwork`
skill run in this checkout, but nothing ties it to that specific branch.
Treat "review is live" as unverified until you find the actual session.

## Research findings, no action taken — build-loop-memory telemetry

- **Row/query counts, re-measured directly, not copied from a prior brief:**
  `indexes/TELEMETRY.jsonl` had **40,937 rows / 286 distinct queries** at
  first measurement, **40,937 / 285** a few minutes later in the same session
  — the file writes live (this repo's HEAD moved twice during this session).
  Treat any exact count as a moving target.
- **"232 usable non-boilerplate queries" — could NOT reproduce.** No script
  in `build-loop-memory` or `build-loop/scripts/` defines a "boilerplate
  query" filter; grepped both repos for `boilerplate`/`usable`, found nothing
  that operates on `TELEMETRY.jsonl`. Mark `UNVERIFIED` until the methodology
  surfaces.
- **`memory_ids_used` and `effect` are 0 rows — confirmed.** Scanned all
  40,937 rows for a non-empty `memory_ids_used` or `effect` field: **zero
  matches for both**, though both keys exist in every row's schema. The
  fields exist in shape but nothing ever populates them.
- **`scripts/memory_telemetry.py::emit_use` — confirmed narrowly used.**
  These scripts live in `build-loop`, not `build-loop-memory` — verify the
  repo before grepping. `emit_use` (`scripts/memory_telemetry.py:272`) has
  exactly 2 callers in `scripts/`+`tests/`: `memory_telemetry.py` itself and
  its own test, `scripts/test_memory_telemetry.py`. No production caller found.
- **`scripts/recall.py` never calls `emit_read` — confirmed.** `emit_read`
  (`scripts/memory_telemetry.py:157`); grep for it in `recall.py` returns
  nothing.
- **`markdown_graph_parser.py::parse_decisions_dir` non-recursive glob —
  confirmed, and confirmed it actually breaks the graph.** Line 246:
  `for p in sorted(root.glob("*.md")):` — non-recursive. The top-level
  `build-loop-memory/decisions` dir has **0 files directly inside it**, 21 one
  level down, 83 three levels down — so `parse_decisions_dir("./decisions")`
  returns **0 edges**, matching the claim (my depth count is 0-indexed from
  the passed root; the brief's "2 and 4" is 1-indexed from repo root — same
  fact). Same pattern in project-scoped dirs (`research-plugin`: 2 direct/75
  total; `agent-rally-point`: 6/460; `rosslabs-lsp`: 0/50) — systemic, not a
  one-off. Only outside caller: `scripts/recall_graph.py:190`, which passes
  through whatever `decisions_dir` its own caller resolves — worth checking
  what that resolves to before assuming every graph query is silently empty.

## Hazards for the next agent

1. **Heavy concurrent-agent load on this machine** (15 sessions, load avg
   ~14). Background test runs can silently duplicate into 2 competing
   processes — check `ps aux | grep pytest` before trusting a "hung" result.
2. **`build-loop-memory` and `agent-rally-point` both moved HEAD during this
   single session** — other agents are actively committing. Re-run the git
   checks above before acting on them if time has passed.
3. **Installed `rally` binary is 11 commits behind source** — rebuild before
   any empirical test of the read-only fix.
4. **Don't test the read-only fix against a live room** — use a throwaway
   repo (repro steps above) until the torn-read question is resolved.
