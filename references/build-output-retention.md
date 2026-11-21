# Retain selected build evidence

At the end of a build or benchmark, identify the future question and the files required to answer it. Capture them while the original checkout, nested repos and debug-object paths still resolve. Ordinary compiler caches follow existing cleanup rules.

1. Record the exact producer revision, command, target, profile, toolchain and tests run. For each contributing nested repository, pass its relative path with `--nested-repo`. Retain a binary Git diff and any needed untracked inputs separately when edits matter.
2. If ownership is unclear, use Agent Architect to trace the target or consumer. Cite the source file, revision and reference. An empty scan leaves historical and external use unresolved.
3. Use Code Audit for consequential claims. Replay a patch against the stated base, symbolicate a known address with the matched binary and symbols, or replay a benchmark summary from raw measurements and record test conditions. Write a JSON object with `sha256` of the selected file, `tool`, `status` (passed, failed or inconclusive), and `result` with the command, outcome and limitations. Record incomplete checks explicitly.
4. Run Easy Terminal's `tools/et_worktrees.py retain FILE --repo CHECKOUT --consumer CONSUMER --purpose "future question" --verification CHECK.json --build-revision REVISION --build-command COMMAND --profile PROFILE --target TARGET --toolchain TOOLCHAIN --json` for each selected file. Save stdout as a receipt. A staging backfill takes `--staging-receipt ID --repo STAGED_FOLDER`. Report unknown historical configuration as unknown.
5. Run `python3 scripts/index_retention.py RECEIPT.json [MORE.json ...] --name "searchable product or benchmark name" --project PROJECT --run-id RUN --host HOST --workdir CHECKOUT` from Build Loop. Record the memory reference and receipt IDs in the build handoff. At closeout confirm the retained bytes still match their hashes and that every file required by the stated use has a recovery copy. Surface missing evidence before closeout.

Cleanup Candidate reads the same receipts in the staged folder's evidence panel. Owner approval and measured space recovery stay in the cleanup workflow.
