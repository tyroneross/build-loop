# Codex: validate the Postgres memory mirror (run this after the DB-resolver fix)

**Why this exists.** Earlier we proved Claude and Codex share the same *filesystem*
memory (the decision/lesson files). The *Postgres* mirror was the one piece we could
not confirm — Codex's earlier sentinel never reached Postgres because the code looked
for a database setting under the wrong name (`BUILD_LOOP_DATABASE_URL`) and gave up
when it was empty, even though the database was actually configured under the older
name (`DATABASE_URL`).

That bug is now fixed and live on `main` (and in your `0.12.0` plugin cache). All the
DB code now checks `BUILD_LOOP_DATABASE_URL`, then falls back to `DATABASE_URL`, then
to `~/.config/agent-memory/connection.env`. So a Codex-written memory fact should now
land in the same Postgres database that Claude reads from. This procedure proves it
end to end.

You do not need to understand the internals. Just run the steps in order and report
what you see.

---

## Step 1 — Confirm your plugin cache has the fix

Run:

```bash
cd ~/dev/git-folder/build-loop
ls ~/.codex/plugins/cache/ross-labs-local/build-loop/0.12.0/scripts/_db_url.py
python3 scripts/check_cache_sync.py --host codex --json
```

**Expected:** the `ls` prints the file path (it exists), and the check prints
`"version": "0.12.0"` with an empty `"diffs": []` and exits 0.

If the file is missing or diffs are non-empty: stop and report that — your cache is
not on the fixed version and the rest of this test would be invalid.

---

## Step 2 — Confirm the database is now reachable from the fixed code

Run:

```bash
cd ~/dev/git-folder/build-loop
uv run python scripts/backend_health.py --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d.get('semantic') or d, indent=2))"
```

**Expected:** the `semantic` / Postgres probe reports it is **available** (not
`postgres_unavailable: no DB URL ...`). If it still says no DB URL, the database
genuinely is not configured on this machine — report that exact message and stop;
it is an environment gap, not a code bug.

---

## Step 3 — Write a Codex-tagged memory fact

Use the full, current argument set (the short form from the earlier round is
out of date). Pick any unique number for the title:

```bash
cd ~/dev/git-folder/build-loop
STAMP=$(date +%s)
uv run python scripts/write_decision.py \
  --title "codex-pg-roundtrip-$STAMP" \
  --decision "Codex-authored sentinel to validate the shared Postgres mirror after the DB-URL resolver fix." \
  --status accepted \
  --project _unscoped \
  --tags tooling,validation \
  --primary-tag tooling \
  --entity "codex-pg-roundtrip-$STAMP" \
  --confidence explicit \
  --tool codex
echo "Wrote sentinel with stamp: $STAMP"
```

**Expected:** it prints the path of a new decision file under
`~/dev/git-folder/build-loop-memory/decisions/_unscoped/` and does **not** print a
database error. **Write down the `STAMP` value** — you need it in Step 4 and to hand
back to Claude.

---

## Step 4 — Confirm it reached Postgres (the actual proof)

Replace `<STAMP>` with the number from Step 3:

```bash
cd ~/dev/git-folder/build-loop
uv run python -c "
import sys; sys.path.insert(0,'scripts')
import db
c = db.get_connection().cursor()
c.execute(\"SELECT tool, subject FROM personal_memory.semantic_facts WHERE tool='codex' AND subject LIKE '%codex-pg-roundtrip-<STAMP>%'\")
rows = c.fetchall()
print('FOUND IN POSTGRES:', rows if rows else 'NOTHING — sentinel did not reach Postgres')
"
```

**Expected (success):** it prints `FOUND IN POSTGRES:` followed by a row whose first
value is `codex`. That single row is the proof: a Codex-written fact is now sitting
in the same Postgres table Claude queries — the mirror is shared, not split.

**If it prints `NOTHING`:** the filesystem write worked but the Postgres mirror still
didn't take. That would mean a *second*, separate problem (most likely the embedding
step failing). Report it; do not treat the validation as passed.

---

## Step 5 — Report back

Tell Claude:

1. Step 1 result (cache on 0.12.0 with the fix? yes/no).
2. Step 2 result (Postgres available? the exact line).
3. The `STAMP` you used.
4. Step 4 result (the printed `FOUND IN POSTGRES:` line, verbatim).

Claude will then run the same Step-4 query from its side using the *Claude* plugin
cache. If both sides see the same Codex-written row, the shared-Postgres claim is
empirically closed for both tools — not just "by construction."

---

## Cleanup (after both sides confirm)

These sentinels are validation noise, not real memory. Once Claude confirms, either
side can remove them:

- The four earlier sentinels: `0004-...codex-cross-tool-sentinel-1779054001.md`,
  `~/.build-loop/memory/validation/codex-memory-writer-sentinel-1779054111.md`,
  and any `personal_memory.semantic_facts` rows whose subject contains
  `codex-cross-tool-sentinel` or `codex-pg-roundtrip`.
- Leave the `INDEX.md` / `events.jsonl` entries — they are append-only logs;
  rewriting them is riskier than the harmless stale line they contain.
