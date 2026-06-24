# Transcript Pattern Miner — Detector Transparency

Accuracy harness: `eval_accuracy.py`. Run: `python -m scripts.transcript_pattern_miner.accuracy.eval_accuracy`

Measured on boundary fixture (`fixtures.py`) — all cases are deterministic hand-labeled pairs.

## Measured precision/recall (boundary fixture)

| Category | Precision | Recall | TP | FP | FN |
|---|---|---|---|---|---|
| cluster_corrections | 1.0000 | 1.0000 | 1 | 0 | 0 |
| repeated_tool_sequences | 1.0000 | 1.0000 | 1 | 0 | 0 |
| cross_project_files | 1.0000 | 1.0000 | 2 | 0 | 0 |
| manual_command_rituals | 1.0000 | 1.0000 | 1 | 0 | 0 |
| test_pattern_outcomes | 1.0000 | 1.0000 | 1 | 0 | 0 |

All categories pass the documented bar (precision=1.0, recall=1.0). On a deterministic boundary fixture this is expected; the scores are meaningful only as a regression gate, not as a claim about real-data accuracy.

---

## 1. cluster_corrections

**Rule** (`categories.py:15–67`):
- Iterates `agg.user_messages` (list of `(ts, text, proj)`) for each session.
- Message is a candidate only if `CORRECTION_RE.search(text)` matches (`categories.py:21`). `CORRECTION_RE` is defined in `textproc.py:34` — an alternation of correction-signal phrases (`no`, `stop`, `wrong`, `actually`, `instead`, etc.).
- Token gate: `re.findall(r"[a-z0-9']+", text.lower())` must produce **≥3 tokens** (`categories.py:24`).
- 3-grams are computed as all overlapping 3-tuples of tokens (`categories.py:26`).
- Clustering (union-find-ish, `categories.py:36–46`): a candidate joins the first existing cluster whose **representative** (first member) shares **≥2 3-grams** with it. Only the representative is compared, not all members — order of insertion matters.
- A cluster surfaces only when it has **≥3 members** (`categories.py:50`).
- Output is sorted by descending count, then last_seen.

**Known failure modes:**
- CORRECTION_RE false positives on quoted or negated text: `"no, that's correct"` fires CORRECTION_RE (`\bno\b`) even though the user is affirming. High false-positive rate on short messages.
- 3-gram spine collision: unrelated corrections whose text happens to share 2 common 3-grams (e.g. `("you", "should", "use")`) will incorrectly cluster. Common phrases produce false clusters.
- Union-find compares only the cluster representative (first member), not centroid. A cluster can drift: if member 3 is close to member 1 but members 1 and 2 are far from each other, the cluster silently becomes incoherent.
- Messages with fewer than 3 tokens are silently excluded — very short corrections like `"wrong"` or `"no stop"` never surface.
- No deduplication within a single session: the same message appearing twice (e.g. from a resumed transcript without UUID dedup at the session level) inflates the cluster count.

---

## 2. repeated_tool_sequences

**Rule** (`categories.py:70–96`):
- For each session, iterates all sub-sequences of length **3, 4, 5, 6** from `agg.tool_sequence` (`categories.py:75`).
- Window format: `"{ToolName}:{first_input_key}"` (e.g. `"Read:file_path"`, `"Bash:command"`).
- Skip condition: `len(set(window)) == 1` — a window where all tools are identical is discarded (`categories.py:79`).
- Each unique window is counted once per session (`seen_in_session` deduplication, `categories.py:80`).
- A window recurs only if it appears in **≥3 distinct sessions** (`categories.py:88`).
- Output is capped at 20 results, sorted by descending session count then sequence length (`categories.py:95–96`).

**Known failure modes:**
- Coincidental common sequences: `Read:file_path → Edit:file_path → Bash:command` is the most common pattern in any editing workflow. It will recur across hundreds of sessions for completely unrelated tasks, generating false positives that look like meaningful rituals.
- First-input-key normalization is shallow: `Read:file_path` and `Read:pattern` are counted as different sequences even when the same logical action is performed. Sequences involving Glob are unlikely to cluster with those using Read.
- Length-6 windows subsume length-3: if `[A, B, C, D, E, F]` recurs in 3 sessions, the harness also records `[A, B, C]`, `[B, C, D]`, `[A, B, C, D]`, etc. as separate hits, inflating the output count.
- The all-identical window skip (`len(set(window)) == 1`) only fires for exact string equality. A sequence like `Read:file_path, Read:file_path, Read:pattern` (2/3 identical) passes through.

---

## 3. cross_project_files

**Rule** (`categories.py:99–125`):
- Iterates `agg.files_touched` (list of `(proj, abs_path)` tuples) across all sessions.
- `file_to_projects[fp].add(proj)` tracks which projects each file path was seen in (`categories.py:107`).
- `file_count_per_project[(proj, fp)] += 1` counts how many times each `(project, file)` pair appears (`categories.py:108`).
- **Cross**: a file appears in cross output when `len(projects) >= 3` (`categories.py:112`).
- **Churn**: a `(proj, file)` pair appears in churn output when count `>= 5` (`categories.py:122`).
- Both outputs are capped at 15 results, sorted by descending count/project_count.

**Known failure modes:**
- Shared config filenames inflate project_count: `package.json`, `tsconfig.json`, `pyproject.toml`, `.gitignore`, `README.md` appear in virtually every project, so the cross list is dominated by generic filenames rather than genuinely shared modules.
- Path normalization is exact-string: `/Users/tyroneross/dev/git-folder/project-a/src/util.ts` and `/Users/tyrone/project-a/src/util.ts` count as different files. Symlinks and checked-out-twice repos produce no cross-project signal.
- `project_from_cwd` in `textproc.py` extracts the project name from the path segment after `/dev/git-folder/`; any path outside that convention gets project `"(other)"`, so all files in non-standard directories are bucketed into one fake "project".
- Churn counts raw `files_touched` list insertions, which include Read operations. A file that is read 5 times but never written shows up in churn output alongside files that were actually churned by edits.

---

## 4. manual_command_rituals

**Rule** (`categories.py:128–145`):
- Iterates `agg.bash_commands` (list of normalized shapes from `normalize_bash()` in `textproc.py:157`).
- `normalize_bash` keeps the program name + subcommand + flag names, replaces argument values with `<arg>`, and collapses repeated `<arg>` tokens.
- Shape counts are summed across all sessions (`categories.py:133`).
- A shape surfaces when its total count `>= 5` (`categories.py:137`).
- Output is capped at 20 results, sorted by descending count.

**Known failure modes:**
- `normalize_bash` is a best-effort tokenizer; complex pipelines, heredocs, and multi-line scripts produce garbage shapes or empty strings (empty shapes are silently excluded at `categories.py:134` via falsy check on `shape`).
- High-frequency universal commands like `git status`, `ls`, `pwd`, and `echo` will always fire and dominate the output regardless of whether they represent user rituals or incidental scaffolding.
- The normalizer treats `--flag value` (space-separated) as one flag (skips the next token as its value), but `--flag=value` as one token. Inconsistent CLI conventions produce different shapes for semantically identical commands.
- `agg.bash_commands` excludes tools in `RITUAL_SKIP` (Read, Edit, Write, etc.) but these are already non-bash. All Bash tool calls are included without filtering, even one-time diagnostic commands run during debugging.

---

## 5. test_pattern_outcomes

**Rule** (`categories.py:148–193`):
- For each `agg.test_invocations` entry (populated by session.py during stream processing), calls `classify_outcome(agg, inv)`.
- `classify_outcome` (`session.py:444`) combines:
  1. **Strict signal**: `tool_result.is_error` from the tool result event following the invocation.
  2. **Soft signal**: CORRECTION_RE or ACCEPT_TOKEN_RE matches in the next 1–3 user messages after the event.
- Outcome classes: `POSITIVE | MIXED | REWORK | NO_SIGNAL`.
- `directional_only=True` is set when outcome is grounded only in soft (user-text) signals — these are labeled directional, not hard metrics (Trap 3 guard, `session.py:461`).
- No threshold: every test invocation produces a per-invocation row. The aggregate table rolls up counts per `TEST_CATEGORIES` (A_ibr through H_typecheck, defined in `session.py:93`).

**Known failure modes:**
- CORRECTION_RE false positives as rework signal: a user message containing `"no"` or `"again"` within 3 events of a test runner invocation will classify it as REWORK even if it refers to something else entirely.
- `classify_outcome` scans `events[idx+1:]` using `event_idx` from the `test_invocations` entry; if `event_idx` is stale (e.g. from serialization or the event list is built differently), the scan window is wrong.
- Test category detection in `session.py` uses regex matching on the raw bash command string: a comment like `# note: pytest is deprecated here` will register as a B_runner invocation.
- `tool_use_id` correlation (`_find_tool_result_error`) requires the result event to appear within 8 events of the test invocation (`session.py:495`). Long tool call queues (parallel dispatches) can push the result outside the window, producing `None` (no strict signal) and falling through to soft-signal-only classification.
- `NO_SIGNAL` is the outcome when neither tool_result nor user signals are found. This is the most common outcome for real transcripts, meaning the aggregate table's signal-to-noise ratio is low.
