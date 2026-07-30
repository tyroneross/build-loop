# Codex: validate the Rally Point cross-tool channel (run this on `main`)

**Why this exists.** Rally Point gives every app a shared channel at
`~/.build-loop/apps/<slug>/` so a Claude session and a Codex session working the
*same* app see each other's commits, dep-changes, presence (soft-claim warnings),
and architecture digest — without a daemon, just a checkpoint poll.

We have already proven the channel *path* works cross-tool **automatically**: an
in-repo test (`scripts/rally_point/test_cross_tool.py`) loads the channel code
twice from two different install locations (this repo's `scripts/rally_point/` and
the installed plugin cache), points both at one `$HOME` channel, and shows a
write through one copy surfaces through the other — both directions. That is
V1–V3, and it is green.

What that automated test does **not** do is spawn a real Codex process. It
simulates the *Codex identity* (records tagged `tool=codex`) and a *second code
path*; it does not run the Codex binary against the live channel. **You closing
this loop is V4** — a real Codex session writing to the channel and Claude
confirming it from its own plugin cache. That is the one piece only you can do.

You do not need to understand the internals. Run the steps in order and report
what you see.

---

## Step 1 — Confirm your plugin cache is on the fixed version

```bash
cd ~/dev/git-folder/build-loop
ls ~/.codex/plugins/cache/*/build-loop/*/scripts/rally_point/checkpoint.py
python3 scripts/check_cache_sync.py --host codex --json
```

**Expected:** the `ls` prints at least one `checkpoint.py` path (Rally Point is in
your cache), and the check exits 0 with an empty `"diffs": []`.

If the `ls` finds nothing, Rally Point is not in your Codex plugin cache yet —
stop and report that; V4 cannot run and the rest is invalid for your side.

---

## Step 2 — Run the automated cross-tool suite (V1–V3) from your side

```bash
cd ~/dev/git-folder/build-loop
uv run --with pytest python -m pytest scripts/rally_point/test_cross_tool.py -q
```

**Expected:** all tests pass (`N passed`), **zero failed**. A line like
`6 passed`. If any test is *skipped* with "no installed plugin cache rally_point",
that means the dual-path leg could not find a cache copy on your machine —
report the skip; it is an environment gap, not a code regression, but it means
your side could not exercise the second code path.

This proves, from the Codex host, that the channel API is install-location
independent (V1 round-trip, V2 presence/reap, V3 arch-digest reaction).

---

## Step 3 — Write a real Codex-tagged change to a live channel (V4 setup)

This is the live-binary leg. You write to a *real* channel under a throwaway
slug; Claude will read it back from its own cache.

```bash
cd ~/dev/git-folder/build-loop
STAMP=$(date +%s)
uv run python -c "
import sys, os
sys.path.insert(0, 'scripts/rally_point')
import channel_paths as cp, changes as ch, revision as rev, presence as pr
os.environ['BUILD_LOOP_APPS_ROOT'] = os.path.expanduser('~/.build-loop/apps')
slug = 'codex-rally-point-v4-$STAMP'
chan = cp.ensure_channel_dir(slug)
r = rev.bump_revision(chan)
ch.append_change(chan, ch.make_record(
    kind='commit', tool='codex', model='gpt-5', run_id='codex-v4-$STAMP',
    app_slug=slug, payload={'sha': 'codexlive$STAMP'}, revision=r))
ch.append_change(chan, ch.make_record(
    kind='dep-change', tool='codex', model='gpt-5', run_id='codex-v4-$STAMP',
    app_slug=slug, payload={'manifest': 'pyproject.toml'}, revision=r))
pr.write_presence(chan, session_id='codex-v4-$STAMP', tool='codex',
    model='gpt-5', run_id='codex-v4-$STAMP', app_slug=slug,
    phase='execute', files_in_flight=['src/live_a.py'])
rev.bump_revision(chan)
print('WROTE channel:', chan)
print('SLUG:', slug)
"
echo "Hand this SLUG back to Claude: codex-rally-point-v4-$STAMP"
```

**Expected:** it prints `WROTE channel: /Users/.../.build-loop/apps/codex-rally-point-v4-<STAMP>`
and the `SLUG`. **Write down the `STAMP` / `SLUG`** — Claude needs the slug to
read it back, and you need it for cleanup.

> Note: this uses your Codex plugin cache *only if* you run it from a directory
> where that cache is the resolved `scripts/rally_point`. Running it from
> `~/dev/git-folder/build-loop` uses the repo copy, which is byte-identical to
> the released cache — fine for proving the *channel* is shared. If you want the
> strict cache-binary leg, `cd` into your Codex cache's `scripts/` parent first.

---

## Step 4 — Report back

Tell Claude:

1. Step 1 result (Rally Point in your Codex cache? `check_cache_sync` exit code).
2. Step 2 result (the `N passed` / any failed or skipped line, verbatim).
3. The `SLUG` you used in Step 3.
4. The exact `WROTE channel:` line.

Claude will then run a Claude-identity `checkpoint_read` against that slug from
*its* plugin cache and confirm it sees your `commit` + `dep-change` records
(with `tool: codex`), the `reinstall` reaction, and the `codex-v4-<STAMP>`
presence as a soft-claim-capable peer. If Claude's read surfaces what you wrote,
the cross-tool channel is empirically closed for both real binaries — not just
"by construction" and not just the automated dual-path proof.

---

## Cleanup (after Claude confirms)

These V4 channels are validation noise, not real app state. Once Claude
confirms, either side can remove them:

```bash
rm -rf ~/.build-loop/apps/codex-rally-point-v4-*
```

The automated V1–V3 suite needs no cleanup — every test writes to a pytest
tmp dir (`$BUILD_LOOP_APPS_ROOT` is redirected) and never touches
`~/.build-loop`.
