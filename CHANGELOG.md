# Changelog

## Unreleased

### Added
- Attribution discovery layer: `scripts/attribution_audit.py` checks each repo you own for the places that credit you (README credit line, NOTICE, CITATION.cff, package/plugin `homepage`/`author`/`funding`, GitHub website field and topic, website footer, data license), decides which apply (public vs private, forks and third-party repos skipped), and files each gap as a backlog item. After every push, `hooks/post-push-closeout.sh` runs it fully detached, so it never delays the push; items close themselves once fixed. Safe gaps are fixed by `attribution_audit.py apply` (writes files, never commits); license choice and public GitHub settings stay your decision. Owner details come from `attribution-profile.json` in the memory store, so nothing is added for anyone without one. Off switch: `BUILDLOOP_ATTRIBUTION_AUDIT=0`. See `skills/attribution-standard/SKILL.md`.
- Privileged-command broker: a macOS administrator-password request is now named before the dialog appears, coalesced with any identical read-only request already in flight, and appended to a hash-chained ledger. `scripts/privileged_broker.py` (coordinator + CLI), `scripts/privileged_commands.json` (registry data), `scripts/hooks/pre_bash_privileged.py` (PreToolUse:Bash redirect), `scripts/privileged_audit.py` (read-only forensics + before/after counts). Mutating requests never coalesce and never inherit another task's approval; a denial is remembered for a bounded window so a retry cannot open a second dialog; the password is never read, stored, or replayed. Earned by the 2026-08-20 incident where one Codex turn produced two anonymous `sfltool` dialogs 14 seconds apart. Contract: `skills/build-loop/references/privileged-request-broker.md`.
- Groundwork BuildRequest v1 intake and digest-bound ImplementationMap v1 output connect intended product architecture to verified repository implementation evidence.
- Claude and Codex artifacts ship the same stdlib-only exchange adapter and prove checksum plus execution parity in isolated caches.

### Fixed
- Phase 6 Learn writes its `runs[].learn` summary to the LAST `state.json.runs[]` row carrying the run_id, matching the row `run_close_lint.py --require-learn` grades. `scripts/learn/runner.py::_persist_state_summary` and `_load_run_context` both took the FIRST match, so a run_id owning two rows (`write_run_entry` blind-appends when the existing row is already orchestrator-grade) left the graded row without a summary and `--require-learn` returned `learn_missing` on a run that had genuinely finished Phase 6. Observed 2026-08-31 on `bl-20260831T070458Z-codex:01a0569d-buildloop-01-899329`, rows 14 and 15. New `_canonical_run()` is the single writer-side rule; `scripts/test_run_close_lint.py::DuplicateRunIdTest` pins both consumers together and fails against the pre-fix front-scan.
- `append_run` corroborates the caller's commit and goal before writing `state.json.runs[]` instead of trusting them (enforce-candidate E3). A SHA reachable from neither the run's push range nor HEAD is recorded as `pending`, with the refused value kept on `provenance.supplied_commit`; a goal that diverges from the run's own `intent.md` warns without changing the record. New `scripts/run_provenance.py` (ported from agent-rally-point, where the logic was first tested) plus `--push-range` / `--intent` / `--strict-provenance` flags.

### Changed
- Verification-verdict agents (`fix-critique`, `fact-checker`, `overfitting-reviewer`, `promotion-reviewer`, `scope-auditor`) repinned `model: opus` → `model: fable`, closing the drift against the standing model org (2026-06-09: Fable owns verification verdicts). Orchestrators stay Opus; execution stays Sonnet; pattern scanners stay Haiku.
- Renamed the repository-governance entrypoint to `repo-maintenance`; `repo-closeout` remains a one-release compatibility alias.
- Repository artifact audits now discover nested caches recursively, protect canonical build roots by default, distinguish distributable release artifacts from ordinary build products, and surface live processes that reference already-removed artifact roots.

## [0.48.0](https://github.com/tyroneross/build-loop/compare/build-loop-v0.47.0...build-loop-v0.48.0) (2026-10-21)


### Features

* **learn:** require compatibility disposition and local evidence links ([7012d9f](https://github.com/tyroneross/build-loop/commit/7012d9f8e8a7da75672a9502342e4f597afdb291))
* **plan:** measured effort estimates, dispatch waves and critical path in Phase 2 ([596002b](https://github.com/tyroneross/build-loop/commit/596002bdd5f01bb555ef4118ddced38088522cee))


### Bug Fixes

* **artifacts:** keep architecture snapshots fresh across commits ([fcd1c1b](https://github.com/tyroneross/build-loop/commit/fcd1c1ba065fbfe7e51281ebf02c191ed8394f39))
* **audit:** accept status strings in run trajectory metadata ([3feadbd](https://github.com/tyroneross/build-loop/commit/3feadbd97401f47dc0d7541c61c480729c81b675))
* **build-loop:** automatically repair confirmed authorized findings ([d3dda1c](https://github.com/tyroneross/build-loop/commit/d3dda1c0c36844525b900acc2c06d4a3067c9f3b))
* **cache-sync:** sync plugin caches only from main in the primary checkout ([33936bb](https://github.com/tyroneross/build-loop/commit/33936bb49b7c4574bc2c8d247a1b771f2b03ec9e))
* **cache:** sync Claude plugins to their registered active installation ([45266af](https://github.com/tyroneross/build-loop/commit/45266afefedf5be6daddf6e78c44fc81488ab997))
* **catalogs:** reverify provider lifecycle pricing and evidence ([131830b](https://github.com/tyroneross/build-loop/commit/131830b5d4be9f1481a093dbbf63c988ed170447))
* **config:** validate runtime inputs and gate TypeScript changes ([c3b9de0](https://github.com/tyroneross/build-loop/commit/c3b9de0ba8fa8720173594c7c3dc1089ecc00a44))
* **coordination:** distinguish fresh heartbeats from evidence progress ([6d1b927](https://github.com/tyroneross/build-loop/commit/6d1b927f435dc82a4913e3bd2615897bb8e9b118))
* **evidence:** bind retrospectives and telemetry to exact run identities ([03746b3](https://github.com/tyroneross/build-loop/commit/03746b3044e50840d30b40994d3ca7b0f27fc586))
* **groundwork:** accept Groundwork 0.5.0 write-boundary and exchange effect fields ([d45121b](https://github.com/tyroneross/build-loop/commit/d45121be68c46b3fb6dbea11d2bfd1862c5052de))
* **hook-lint:** heredoc bodies and quoted-command arguments are not commands ([10237d0](https://github.com/tyroneross/build-loop/commit/10237d0521183e063ad22c3003776ee822b997b9))
* **hooks:** bound complete lifecycle and preserve incomplete gate decisions ([7660f5f](https://github.com/tyroneross/build-loop/commit/7660f5f380c41cc720b491c0a7f3a9844cd478ff))
* **hooks:** bound remaining SessionStart work before host deadlines ([3ad7696](https://github.com/tyroneross/build-loop/commit/3ad769614d2f19610500dbb022d61b4e2d2e1c8b))
* **hooks:** drain completed output and guard JSON runtime ([79c37df](https://github.com/tyroneross/build-loop/commit/79c37dff0c57a33c3e27ee208d937e3ed430c9d6))
* **hooks:** emit Codex-valid PreToolUse and Stop output ([9e81c06](https://github.com/tyroneross/build-loop/commit/9e81c0624cdfad8b88cef9235ae939b51af194c2))
* **hooks:** emit valid startup JSON within the host deadline ([815da4b](https://github.com/tyroneross/build-loop/commit/815da4ba6dd4172c388bcf23b5fee12a910f121f))
* **hooks:** keep Rally and observational hooks inside Claude Code timeouts ([8524b29](https://github.com/tyroneross/build-loop/commit/8524b293038f61444ce3867e3924d305789632cc))
* **hooks:** preserve explicit session when cache import fails ([dc4bdfe](https://github.com/tyroneross/build-loop/commit/dc4bdfea63eff3372c39f9f875bdc6363b47a2df))
* **hooks:** preserve manual diagnosis and handle missing modules ([386bd7d](https://github.com/tyroneross/build-loop/commit/386bd7d5770595fcfb88028e6b236632f66518f7))
* **hooks:** preserve native events and remove invalid Stop observers ([69d09f7](https://github.com/tyroneross/build-loop/commit/69d09f7c7356d76904b4630e265e60b6097aecf9))
* **hooks:** preserve session identity on failure and protect literal quotes ([c223d56](https://github.com/tyroneross/build-loop/commit/c223d564c67563a6a856eacb5fe7a18d127379aa))
* **identity:** reject conflicting history and undated discovery ([053a5ed](https://github.com/tyroneross/build-loop/commit/053a5ed3ba87c3bad1c1227be96f0b14701cc67e))
* **ingestion:** guard source descriptors and preserve exact outcomes ([11db0d2](https://github.com/tyroneross/build-loop/commit/11db0d218423dcd8e4bbc00375ae07a40fa28bba))
* **learn:** execute RossLabs LSP for applicable compatibility reviews ([10d7d7d](https://github.com/tyroneross/build-loop/commit/10d7d7d64d908b8de44e99531c32e1af889f3241))
* **learning:** retain provisional source evidence and stable suppression ([64157ba](https://github.com/tyroneross/build-loop/commit/64157ba8ea665d5f3375b5d6e95a4779c1ac50b2))
* **optimize:** enforce evaluator scope and keep accounting ([21b77e8](https://github.com/tyroneross/build-loop/commit/21b77e8d3f8c8c29430635120c2cc62ed39c861a))
* preserve Stop payloads and reject incomplete transcript formats ([d5cdd44](https://github.com/tyroneross/build-loop/commit/d5cdd44ae29fa619bcdf3c96e2d8eb1cae27ccb3))
* **rally:** let RALLY_TOOL_ID name the native actor ([80a01ec](https://github.com/tyroneross/build-loop/commit/80a01ecdf9af858b8ea7240af56bf928bf664bd5))
* **rally:** refuse to install git hooks into a tracked core.hooksPath dir ([3ed8fec](https://github.com/tyroneross/build-loop/commit/3ed8feced758c6c0c73d051a764f5753696a0934))
* **reliability:** repair full-suite root causes and outcome boundaries ([f196d0d](https://github.com/tyroneross/build-loop/commit/f196d0d026a50e8fcd0cd05474a4278919b28eff))
* retain usable transcript prose with explicit coverage gaps ([b292560](https://github.com/tyroneross/build-loop/commit/b292560bd4f2a8a0f8c8ff8191ec51ff9af99685))
* **review:** keep unrepaired statuses blocking despite closed flags ([46b3bab](https://github.com/tyroneross/build-loop/commit/46b3bab3dcbdf17d344df0dafdc3477b1ab353ad))
* **storage:** bind guarded reads to verified file identities ([ff48312](https://github.com/tyroneross/build-loop/commit/ff48312a1b38f85a121544818127d13a13d32d39))
* **storage:** isolate corrupt records and expose partial coverage ([a9a8fb2](https://github.com/tyroneross/build-loop/commit/a9a8fb20d8e3ce4a978854f1d0c680db67648751))
* **storage:** publish validated trace revisions and bound log scans ([56fe9c4](https://github.com/tyroneross/build-loop/commit/56fe9c465ae3326723749677763a59e2b96b00d5))
* **storage:** reject blocking sources and preserve trace contracts ([d04418f](https://github.com/tyroneross/build-loop/commit/d04418fb2a2214e3be9504b045a6f27ae2752590))
* **storage:** scope canonical lookups independently of legacy faults ([69571d3](https://github.com/tyroneross/build-loop/commit/69571d38d2cc74d0b568561a4bdefcf39b7b9c32))
* **tasks:** share outcome validation and expose live ref inventory ([1508000](https://github.com/tyroneross/build-loop/commit/150800025decca2891ffb85fec6ee4457cf61b85))
* **telemetry:** share bounded ingestion and preserve source attribution ([5e1d305](https://github.com/tyroneross/build-loop/commit/5e1d3058431ae479d1b9910cb8cf8513996f5239))
* **transcripts:** preserve discovery failures in coverage receipts ([606f788](https://github.com/tyroneross/build-loop/commit/606f788fd606d4a15fa235768bb5a1e0c61a15f1))
* **verification:** share full-suite coverage across local and CI gates ([ad35231](https://github.com/tyroneross/build-loop/commit/ad35231624b1c724bc3b407abaa2716aecddfca6))
* **wake:** preserve requested and earlier deadline bounds ([de3f690](https://github.com/tyroneross/build-loop/commit/de3f690ba4a053b8486d701f32e31fc1544e203d))


### Performance Improvements

* **ingestion:** stream canonical transcript events with revision-safe caching ([df7392d](https://github.com/tyroneross/build-loop/commit/df7392dc84be92d58b9ad7037b62e36ca9ae81ce))
* **rally:** cut pre-edit hook latency by deduping git lookups and lazy imports ([ccf8b34](https://github.com/tyroneross/build-loop/commit/ccf8b3461e120ab3dcd9c9aeb5d9668394c7d1e4))
* **rally:** keep the per-tool hook off the rally CLI (1,550 ms -&gt; 180 ms) ([ecc9068](https://github.com/tyroneross/build-loop/commit/ecc9068c342a5fa6d0383d1686485c0f04b37314))

## [0.47.0](https://github.com/tyroneross/build-loop/compare/build-loop-v0.46.0...build-loop-v0.47.0) (2026-09-27)


### Features

* **dispatch:** lint dispatch prompts for prohibitions without a fallback ([500d198](https://github.com/tyroneross/build-loop/commit/500d1987b05ef5fa1a0ab62c822aa6c0624766a6))
* **dispatch:** lint write-capable briefs for a forbidden-actions clause and codex stdin (P2) ([7825a98](https://github.com/tyroneross/build-loop/commit/7825a98839174b4411844b05203f4329c97e8f7a))
* **execute:** machine-load gate for parallel builds and perf measurements (P4a) ([e7f1a22](https://github.com/tyroneross/build-loop/commit/e7f1a22d353c34dce5398ef1bee65b939e7f08b8))
* **integration:** branch-head drift check before integration build/smoke/deploy (P3) ([89acc38](https://github.com/tyroneross/build-loop/commit/89acc3889ba9531de29fd99cdd21f8f1624d259f))
* **review:** auditor preflight, orchestrator-owned test evidence, strict re-review on risk runs (P1) ([1734c55](https://github.com/tyroneross/build-loop/commit/1734c55a56aa0906a035dbc3559305dfd58ed67a))
* **review:** enforce the stranger test at commit and verdict time ([9e82464](https://github.com/tyroneross/build-loop/commit/9e8246470fffedccc3da048fcb2fdc0fe5ee1535))
* **review:** require the stranger test for auth/admin/owner/gated surfaces ([c893ecd](https://github.com/tyroneross/build-loop/commit/c893ecde9e946d8a7d5ee46624f1c97ab144c963))
* **search:** add fast repository evidence search ([d7b2cc9](https://github.com/tyroneross/build-loop/commit/d7b2cc903e52f0a2543cff1a660763e67eb044a0))
* **security:** add release_surface_scan for dev surfaces compiled into Release ([b63aa48](https://github.com/tyroneross/build-loop/commit/b63aa482e39d9447c23c8e847b8cce7b72e7e3b2))


### Bug Fixes

* **ci:** gate generated skill index before push ([70d4888](https://github.com/tyroneross/build-loop/commit/70d48889eb501a1ad622922662e0f0e1bc5a9790))
* **ci:** restore routing and closeout invariants ([8152773](https://github.com/tyroneross/build-loop/commit/81527737c04e8541dd3aea94747d233265125d99))
* **closeout,retro:** read canonical judge-decisions.json and detect direct durable lessons ([2aaf2a7](https://github.com/tyroneross/build-loop/commit/2aaf2a7430274e4082dc27aab4490ef9c3ac21f7))
* **fixtures:** replace private app identifiers with generic examples ([86f9d60](https://github.com/tyroneross/build-loop/commit/86f9d600d55b018a0626e14ee702837ddf7448d6))
* **hooks:** attribute hook-created tool state instead of calling it pre-existing ([dbb5149](https://github.com/tyroneross/build-loop/commit/dbb514982f22651398b9885773af4c8b6432db24))
* **memory:** classify the target workdir, not only the process cwd ([3df75cf](https://github.com/tyroneross/build-loop/commit/3df75cf05cff9b159ecb84b06ce7f05e400b8382))
* **memory:** keep throwaway workdirs out of the canonical memory store ([6635b0c](https://github.com/tyroneross/build-loop/commit/6635b0c442263e899985b1d89e024592e8bca4bb))
* **package:** ship search dependencies and classify new surfaces ([6e279a5](https://github.com/tyroneross/build-loop/commit/6e279a59aa439cbef69c3b0ce926c2268bdd4142))
* **review:** close independent-auditor findings on the stranger-test + P1-P4 build ([b9187f9](https://github.com/tyroneross/build-loop/commit/b9187f9fe0d7bee93c2bc9efe0da256ffdf9005a))

## [0.46.0](https://github.com/tyroneross/build-loop/compare/build-loop-v0.45.1...build-loop-v0.46.0) (2026-09-24)


### Features

* **attribution:** file credit-link gaps as open items on every push ([58a22dc](https://github.com/tyroneross/build-loop/commit/58a22dc0202515688fcf079ee08178b810191ebb))
* **capture:** split transcripts by who said what before asking a model ([6eff193](https://github.com/tyroneross/build-loop/commit/6eff19377c9f7d4aed652ec9f58044678fe3ec15))
* select risk-targeted acceptance lanes ([9887fb0](https://github.com/tyroneross/build-loop/commit/9887fb07a7cb483fc77fb7e08a128f820c1008ab))


### Bug Fixes

* **attribution:** never delay a push; address independent-audit findings ([09040e1](https://github.com/tyroneross/build-loop/commit/09040e1c72f47b9459544e5878c129c8eeb3456e))
* classify acceptance_selector and drop maintainer-only paths from shipped docs ([eafdf0a](https://github.com/tyroneross/build-loop/commit/eafdf0ab3ba5e351726a12e58bfb1d44cc877f8b))
* **decisions:** separate a decision MADE from a decision NEEDED ([97fb7dc](https://github.com/tyroneross/build-loop/commit/97fb7dc07b990a05fbb14d90333f4f90884971ab))
* **knowledge:** read the canonical review queue, not only the legacy .episodic path ([65c5c66](https://github.com/tyroneross/build-loop/commit/65c5c66695f461751973a1cdfe7e2dd6e2636329))
* **memory_writer:** dedupe by body hash and reject truncated bodies ([b50c285](https://github.com/tyroneross/build-loop/commit/b50c285a3af0b6639f9d44ea27a59dbe0b8db8ca))
* **memory_writer:** stop the truncation guard rejecting finished text ([3255acb](https://github.com/tyroneross/build-loop/commit/3255acbec2aa427c555fb6712bf5933d575698ea))
* **memory:** one malformed run row no longer blanks all canonical memory ([09f1f89](https://github.com/tyroneross/build-loop/commit/09f1f89d62bc2a57f8cd7e43f1e2082538f2358b))
* **memory:** rank on summaries, ignore function words, resolve paths only for hits ([fa000b2](https://github.com/tyroneross/build-loop/commit/fa000b2fb24b3735d1da4db46074c6dadc9920bd))
* **memory:** stale index answers from index plus changed files; never return unvetted captures ([28caaa4](https://github.com/tyroneross/build-loop/commit/28caaa45f4d97a719efc1caa47157fae12ecdaaa))
* **models:** treat Cursor as a multi-provider host ([761dcdf](https://github.com/tyroneross/build-loop/commit/761dcdf44e03b56e0e3098aeee0b8468f74722a7))
* **run_close_lint:** check owed review before acceptance receipts ([b95f8d1](https://github.com/tyroneross/build-loop/commit/b95f8d1af19169d1a7cf8fc1d1276b2e55677390))


### Performance Improvements

* **run:** decide small-vs-full before loading the skill; silence the Stop advisory outside runs ([48b8ef2](https://github.com/tyroneross/build-loop/commit/48b8ef26f691aae7a7ac7004ccbc3bc768b255e6))

## [0.45.1](https://github.com/tyroneross/build-loop/compare/build-loop-v0.45.0...build-loop-v0.45.1) (2026-09-13)


### Bug Fixes

* **handoff:** withhold intent.md when it marks itself stale ([5ce58e4](https://github.com/tyroneross/build-loop/commit/5ce58e42ffc8cc14ddf5292ede4af86b7588bb39))
* **learn:** count distinct runs, not rows, on both promotion arms ([be96661](https://github.com/tyroneross/build-loop/commit/be96661284367b9b34f5e796b830484668c86e94))
* **learn:** give applied experiment rows a real metric so the A/B gate can fire ([eb57c9f](https://github.com/tyroneross/build-loop/commit/eb57c9fb3a9cb790abc2686086d7af43295bce7b))
* **memory:** over-fetch every backend, then truncate once after ranking ([29392f0](https://github.com/tyroneross/build-loop/commit/29392f00858d92b511421a53331e784069b7e7f4))
* **memory:** thread project through runs, decisions and lessons readers ([2c88c26](https://github.com/tyroneross/build-loop/commit/2c88c26d6aecdadb18914e8b6b33629012ba1222))
* **review:** carry the debts[] contract into the instruction layer and the base gate ([365731c](https://github.com/tyroneross/build-loop/commit/365731c55dc5708663863d1a9d8b0b4ee1a4d8e7))
* **review:** close the discharge half and the validity half the auditor found ([ec15160](https://github.com/tyroneross/build-loop/commit/ec15160b474fd3507c06e7ea38b14431f23fb706))
* **review:** key owed debts by (verifier, run) so a second run cannot drop the first's ([7ab3bcc](https://github.com/tyroneross/build-loop/commit/7ab3bccced7335274f731e96589beeb3e2d46e59))
* **review:** make a recorded verdict discharge the debt, and a claim prove itself ([e9a2a62](https://github.com/tyroneross/build-loop/commit/e9a2a6270967c6a6bfbea4f51896df883c38bf02))
* **review:** owe the cross-vendor round back instead of leaving it advisory ([15be6ea](https://github.com/tyroneross/build-loop/commit/15be6eae58bb597baff7f583dab3a782c117dbce))
* **review:** run the debt LIFECYCLE off the debt rows, not the name-keyed views ([699ac85](https://github.com/tyroneross/build-loop/commit/699ac850a548b3149b7e10dbbc01217a346b8f65))
* **review:** stop the verdict allowlist re-arming 103 already-audited rows ([815285a](https://github.com/tyroneross/build-loop/commit/815285ab99b51fb5a63590d474abab8ddd980682))
* **scripts:** track convert.py so the regenerated diagram is reproducible ([8f67016](https://github.com/tyroneross/build-loop/commit/8f670166992333912ea0ddc17873838f1b211c08))
* **tests,registry:** unwedge the self-mod gate on two real failures ([7bbe31d](https://github.com/tyroneross/build-loop/commit/7bbe31d775823d34184b6b165b762be0b2899677))

## [0.45.0](https://github.com/tyroneross/build-loop/compare/build-loop-v0.44.0...build-loop-v0.45.0) (2026-09-13)


### Features

* add deterministic run status observer ([af554f2](https://github.com/tyroneross/build-loop/commit/af554f2ac0624605f61dc416a68a23fa4d40b64a))
* **airlock:** gate foreign-repo instruction files as untrusted data ([f977967](https://github.com/tyroneross/build-loop/commit/f977967833a81e28c5f2bfd4f6a4187966a51798))
* **models:** register gpt-6-astra (alias astra) in the routing index ([677377a](https://github.com/tyroneross/build-loop/commit/677377a033b64067605a7099036aad3e77713590))
* **research:** add opt-in matched architecture pilot ([f69b72b](https://github.com/tyroneross/build-loop/commit/f69b72b96595f1e51a92cfd57c43ffd00fa99531))


### Bug Fixes

* **airlock:** follow CLAUDE.md [@imports](https://github.com/imports), which hid the payload one hop away ([a85a142](https://github.com/tyroneross/build-loop/commit/a85a14216ea2f44fe8c324b8cc3a1fab7c757368))
* **ci:** isolate dashboard service reads and keep orchestration compact ([61d932c](https://github.com/tyroneross/build-loop/commit/61d932cbcccd7d832a065c9ac34c4534ae44b356))
* **dashboard:** read a terminal execution phase as closed, not mid-Review ([7628e55](https://github.com/tyroneross/build-loop/commit/7628e55dec1fbf1657627db334f84ba0a3cd8ef7))
* **docs:** make 8 retrospective citations repo-relative so the portability guard passes ([25f8630](https://github.com/tyroneross/build-loop/commit/25f863040b387e7f065895462f5642cd35d13098))
* **docs:** repair mojibake pushed in fd15506f and restore two deleted paragraphs ([a7095b6](https://github.com/tyroneross/build-loop/commit/a7095b687b7254e15108fd36c2d086a2415af5ac))
* **docs:** retain Groundwork release contract ([ade8331](https://github.com/tyroneross/build-loop/commit/ade8331890dbfb5f1e487793bccfc1c6714293b6))
* **memory:** share scoring snapshots and attribute bootstrap reads ([2d19bea](https://github.com/tyroneross/build-loop/commit/2d19bea489933e6b4d2b3f3696d0394c69b7e7fc))
* resolve stale builds autonomously ([a5bf9eb](https://github.com/tyroneross/build-loop/commit/a5bf9ebfe2c4f30a81765f81b5d0179ec86cb48b))
* **run-ledger,worktree-lifecycle:** close five findings from the closure audit ([34d18b7](https://github.com/tyroneross/build-loop/commit/34d18b7dd4229da233cae033c33b833cd5ac5388))
* **run-ledger,worktree-lifecycle:** close four findings from the round-4 audit ([52699fd](https://github.com/tyroneross/build-loop/commit/52699fdb9cc09dee2e807a2d76db29e1ec411285))
* **run-ledger,worktree-lifecycle:** close four re-audit findings ([0fd7eba](https://github.com/tyroneross/build-loop/commit/0fd7ebaa0674240769bcf7cab4d51a9eae258234))
* **run-ledger,worktree-lifecycle:** close the auditor's two HIGH findings ([d1e0287](https://github.com/tyroneross/build-loop/commit/d1e02870a6f7fa36910b8cbd983e49c4d15d4d69))
* **run-ledger,worktree-lifecycle:** close the five low findings from the yay verdict ([79b3c2b](https://github.com/tyroneross/build-loop/commit/79b3c2bf4338dc7d2c5b760ceaac9ebf4ff715fc))
* **run-ledger:** drop the floor writer's source label on a Review-G merge ([3666dad](https://github.com/tyroneross/build-loop/commit/3666dad28a4e2c1e661b879489daee4d839f211e))
* **run-ledger:** log the write action the writer actually took ([03efd34](https://github.com/tyroneross/build-loop/commit/03efd34b80a204b2a1c3c87ede9a6b45f4d8e17a))
* **run-ledger:** upsert runs[] on run_id instead of appending a second row ([f1b3b95](https://github.com/tyroneross/build-loop/commit/f1b3b9547af780946dbf093b45bc76183ae3fb2d))
* **self-mod-gate:** close eight revert paths a cross-vendor audit found still live ([2798c0b](https://github.com/tyroneross/build-loop/commit/2798c0b3be8e666a87d58078cc59bf86a4cb0faf))
* **self-mod-gate:** make the destructive auto-revert inexpressible ([50c505d](https://github.com/tyroneross/build-loop/commit/50c505d567da7ecd691f1f7441e1e1c8e57ef6ad))
* **supply-chain:** close four findings from the cooldown commit audit ([1e51887](https://github.com/tyroneross/build-loop/commit/1e518873a711125b0192365e0190e29119cc55d4))
* **supply-chain:** version-gate pnpm cooldown and seed the packages field ([bd9ec11](https://github.com/tyroneross/build-loop/commit/bd9ec1119f845d4c5813b5ca4133be31ea667aec))
* **worktree-lifecycle,run-ledger:** close two Review-D gaps in the prior commits ([4ccc5c5](https://github.com/tyroneross/build-loop/commit/4ccc5c5e9182e9ceef682a54ce0629f310063ebf))
* **worktree-lifecycle:** inventory ignored files before a worktree removal ([c60d66e](https://github.com/tyroneross/build-loop/commit/c60d66ed69295431d0ed1e556da5bbb4c74712b7))
* **worktree-lifecycle:** stop discarding git's own incomplete-listing warning ([dbed2aa](https://github.com/tyroneross/build-loop/commit/dbed2aa75ef81b30e0886ae0effaf9c7575c116e))
* **worktree-lifecycle:** treat a symlinked subdirectory as uninspected ([963cca7](https://github.com/tyroneross/build-loop/commit/963cca75e3587330d32647b4b3c8ea2150513fb0))

## [0.44.0](https://github.com/tyroneross/build-loop/compare/build-loop-v0.43.0...build-loop-v0.44.0) (2026-09-07)


### Features

* **release:** detect a release that was cut but never reached the registry ([387b059](https://github.com/tyroneross/build-loop/commit/387b05908918d063b4b60e42130693edf0a48f04))

## [0.43.0](https://github.com/tyroneross/build-loop/compare/build-loop-v0.42.5...build-loop-v0.43.0) (2026-09-06)


### Features

* **database-practice:** table map script, vector/graph tuning reference, Sonnet-tier runbook ([308949b](https://github.com/tyroneross/build-loop/commit/308949bde63d372b23e635b292ea76ea2cfa92a8))
* **dispatch:** make every agent brief declare where it reports, before launch ([c04a803](https://github.com/tyroneross/build-loop/commit/c04a803102e7c36c004e8dc737ecf2215f515089))
* **release:** cut a release automatically, once a week, and notice when it stops ([7820cea](https://github.com/tyroneross/build-loop/commit/7820cea89181ddf5682d10320a039d6b39eb1850))


### Bug Fixes

* **artifact:** the shipped Codex gate would auto-apply a production migration ([faa91ad](https://github.com/tyroneross/build-loop/commit/faa91ad27d4b647062ecc32c8b96109d55175def))
* **ci:** make main green again — stale skill index, README skill count, claim-scope disambiguator past char 300 ([d2f64c2](https://github.com/tyroneross/build-loop/commit/d2f64c2ce2d672f78d304d1fddceb028be74e911))
* **ci:** stop a workflow filename reading as a test invocation ([71a10b4](https://github.com/tyroneross/build-loop/commit/71a10b4f3e9f7c0a6d66a41ad74d595289b4c36e))
* **ci:** stop two dead workflows going red, and name a bad npm token before it publishes ([d3440d0](https://github.com/tyroneross/build-loop/commit/d3440d026bad26cd105a35959a30c4fc338229c4))
* **database-practice:** describe the connection as host:port/db, never as a URL ([7765cfa](https://github.com/tyroneross/build-loop/commit/7765cfad4dbc36e07d5120b8edfcc2512fb5923f))
* **docs:** resync the surface counts, and unbury a new skill's routing boundary ([776a463](https://github.com/tyroneross/build-loop/commit/776a4632c38fbdce99b6928580a41d188f580439))
* **hooks:** close five false-clearance paths the independent auditor found in the push-scan fix ([a6105a9](https://github.com/tyroneross/build-loop/commit/a6105a9bb4accd5410426a007f555bcab11ec36b))
* **hooks:** couple --tracked-only to --diff, and close five more findings from the closure audit ([b50abee](https://github.com/tyroneross/build-loop/commit/b50abee81a9b85d9e27a61fd456da510f6d89405))
* **hooks:** reject a condition-poll that cannot terminate, not just a literal infinite loop ([1d76b55](https://github.com/tyroneross/build-loop/commit/1d76b55e23808175ec9d28443b93c24158aa7337))
* **hooks:** stop the pre-push scan from full-scanning a plain push and from blocking on untracked files ([e917dcf](https://github.com/tyroneross/build-loop/commit/e917dcf93f4c71a8b30e2c3ecc7f60e052052657))
* **release:** do not treat a parked bot workflow run as a red release PR ([0bb00c6](https://github.com/tyroneross/build-loop/commit/0bb00c6199e1e13d1b45bc8f352b17fe64a66859))
* **release:** flip the pre-push version gate to the invariant release-please needs ([50aef21](https://github.com/tyroneross/build-loop/commit/50aef21e99111e6f67f3fc7c9036ea8622011e69))
* **release:** put last-release-sha where the schema reads it ([8772a0f](https://github.com/tyroneross/build-loop/commit/8772a0f5625dc030154ac4c74aee274a8b2a23e4))
* **resume:** reconcile terminal closeout residue ([b93ebb8](https://github.com/tyroneross/build-loop/commit/b93ebb8e97a019ffce2afb9b810ec873e68df21e))
* **runtime:** bound and identify synthetic load probes ([9909821](https://github.com/tyroneross/build-loop/commit/99098216c3cb5ff29c2a7069d8787b99e9fc7446))

## 0.36.4 — 2026-07-11

First published release since v0.36.1. The 0.36.2/0.36.3 entries below were
staged (plugin manifests + CHANGELOG only) but never tagged, released, or
published; their changes first ship here. Release notes: `docs/releases/v0.36.4.md`.

### Fixed
- Git commit/push hook trigger is segment-parsed instead of substring-globbed, ending classifier false-fires on prose/paths that merely contain "git commit" (`54268bd`).
- Retrospective temporal-membership preflight stops attaching wrong-run records to a run's retro (`422a5c1`).
- Independent-auditor finding closures: wrapper push detection, heredoc over-strip, audit-guard test (`93fe6c8`); idle-scan skip when an unterminated heredoc has no git (`673a7fa`).

### Changed
- Worktree-isolation doctrine widened to commit-less file editors: any long-running background writer that edits files must use a dedicated worktree — commit authority no longer required; lint rule `wake-path-grew-a-file-edit` added (`e45cfa6`).

### Added
- runtime-parity-verification: doc↔interface parity checks for CLIs, tools, and flows (`7dc1854`).

## 0.36.3 — 2026-07-10 (not published; first shipped in v0.36.4)

### Added
- Eager Phase-6 Learn detector-pass for inline runs: `stop_closeout.py` runs the deterministic pattern detector on the record path and folds an owed "Phase-6 Learn drafting" item into the existing `closeout-pending` marker (surfaced once at SessionStart) when `runs[]>=3` and a root-cause cluster >= threshold has no experimental draft yet. Fills the `learn/pending` lane's "not yet an automated detector pass" TODO so inline runs no longer leave the learning loop dark until the user asks.


## 0.36.2 — 2026-07-10 (not published; first shipped in v0.36.4)

### Fixed
- Pre-push security gate scans the **push delta**, not the whole tree, so a pre-existing HIGH in an unrelated file no longer hard-blocks unrelated pushes (delta-scoping + 9 false-negative closures + conservative-by-construction classifier).
- `perturbation_spotcheck --check-cmd` now runs as an argv list (`shell=False`), closing the A03/LLM02 shell-injection HIGH (metacharacters in the path/template are inert).


## 0.36.0 — 2026-06-26

### Changed

- **Model resolution — availability fallback + outage TTL.** A tier role observed
  unavailable at dispatch auto-falls-back to the next host-reachable model in its
  tier (a frontier/judgment role degrades at most to the thinking tier, never below
  the floor); a model the current host cannot dispatch is filtered out. Outages are
  recorded with a timestamp and auto-expire after a TTL (default 1800s,
  `BUILD_LOOP_OUTAGE_TTL_SECONDS`); legacy untimestamped records self-heal on read.
  (`scripts/model_resolver.py`, `scripts/dispatch_fallback.py`,
  `scripts/model_availability_store.py`, `scripts/classify_model_tier.py`)
- **Rust-rally coordination facade — retire the Python policy mirror (4-phase migration).**
  build-loop's coordination layer is now a thin facade over the canonical Rust
  `rally` binary that FAILS LOUD when the binary is unavailable, replacing the
  cross-language Python parity mirror and eliminating the parity tax (golden
  fixtures + drift manifest).
  - **Capability contract** (`scripts/rally_point/capability.py`): every
    coordination envelope carries `capability_level` (`full` /
    `degraded-breadcrumb` / `unavailable`) + a `coordination_unavailable` reason.
    `FULL_ONLY_OPERATIONS` (claim/reclaim/lead/reap/liveness/before_write/
    checkpoint) are permitted only at full capability.
  - **Reaper is Rust-only** (`reaper.py` facade): shells `rally sessions --reap`
    at full capability; refuses (no shadow sweep) below it. `presence.reap_stale`
    and `leadership` reclaim gain fail-closed Rust-only guards; the EMPTY-seat
    claim and self-relinquish stay breadcrumb-class.
  - **Degraded breadcrumb path**: a binary-less but supported host may write only
    capability-marked presence/handoff breadcrumb facts — never ownership,
    reclaim, liveness, reap, or before-write protection.
  - **Fetch-on-install** (`binary_fetch.py`): when no system/sibling/PATH binary
    is found, build-loop fetches the host-platform asset from the PINNED
    agent-rally-point `v0.1.3` release, SHA256-verifies it (fail-closed), strips
    the macOS quarantine xattr, version-pins (refuses != 0.1.3), and caches under
    `$XDG_CACHE_HOME/build-loop/rally`. Wired as a `discovery_bridge` tier. An
    UNSUPPORTED host (no matching asset — Intel mac / musl / exotic arch) → loud
    `coordination_unavailable: unsupported_host`, never a mirror.
  - **One-way legacy migration**: `discovery_bridge.maybe_auto_migrate` fires on
    any full-capability resolution and replays a stranded `build-loop-internal`
    fallback store into `.rally` via `rally migrate-legacy` (idempotent).
  - **Parity tax removed**: deleted `decay_vectors.json`, `liveness_vectors.json`,
    `heartbeat_parity_vectors.json` and 6 `_provenance.json` drift entries;
    `decay.py`/`liveness.py` survive as in-process math helpers (window/weight
    only). `test_decay.py`/`test_liveness.py` rewritten as inline unit tests;
    `test_reaper.py` rewritten as facade wire-contract tests; `test_binary_fetch.py`
    adds a native integration test running the FETCHED v0.1.3 binary.

### Added

- **Zombie-tmux prevention — shared decision policy (Python mirror).**
  `scripts/rally_point/liveness.py` mirrors agent-rally-point's two new shared
  reaper/self-exit authorities: `reapable(liveness, parent_alive)` (the single
  "may this session be killed?" decision — Live/Unknown never reaped; parent-dead
  reaps only a `Stale` session; missing parent info degrades to the window
  criterion alone) and `completion_self_exit_eligible(...)` (a task-scoped session
  self-exits only when work is resolved AND `rally next` is non-actionable for a
  sustained streak; `--persistent` opts out). Parity double-pinned by the
  byte-identical `liveness_vectors.json` (now carries `reapable_cases` +
  `self_exit_cases`, ≡ the Rust fixture; `_provenance.json` updated to the new
  upstream `liveness.rs` hash). `test_liveness.py` asserts the same vectors the
  Rust suite asserts. `references/coordination-rules.md` §"Zombie-tmux prevention"
  documents all three layers + the fail-safe directions. The tmux/CLI plumbing
  (Layers 1–3 actuators) is Rust-canonical; this Python layer mirrors the decision
  policy only, as it does for `is_live`.
- **Adaptive, multi-signal session liveness (Python mirror).**
  `scripts/rally_point/liveness.py` mirrors agent-rally-point's canonical
  `liveness.rs`: staleness ADAPTS to each session's planned heartbeat cadence
  (`window = planned_interval * MISS_MULTIPLIER + GRACE`; defaults 300 s / 6 / 60 s
  → 5-min cadence stale at ~31 min, 5-hour cadence at ~30 h) and weighs four
  signals — LIVE if ANY is fresh. `presence.reap_stale` is now adaptive
  (per-record cadence from `planned_heartbeat_secs`, legacy `heartbeat_minutes` as
  a fallback cadence source) with a code-progress keep-alive (a session whose
  branch HEAD moved between polls survives a lapsed heartbeat, tracked via
  `liveness-sha-cache.json`). `coordination_policy.py` gains `default_cadence_secs`,
  `miss_multiplier`, `grace_secs` tunables. Parity double-pinned by the
  byte-identical `liveness_vectors.json` (≡ the Rust fixture, tracked in
  `_provenance.json`); `test_liveness.py` asserts the same vectors the Rust suite
  asserts. `references/coordination-rules.md` documents the model + the FAIL-OPEN
  (squad visibility) vs FAIL-CLOSED (reaper removal) split.
- `scripts/rally_point/reaper.py` — Python fallback reaper that physically removes
  over-TTL presence files, expired claims, and reclaimable lead leases. FAIL-CLOSED
  on unprovable timestamps. Respects the resolved-via rule: defers claim-index.json
  rewrites to Rust when `resolved_via == "repo-local-rally-cli"`. Callable CLI:
  `python3 scripts/rally_point/reaper.py --workdir <path> [--apply] [--json]`.

- `scripts/rally_point/test_reaper.py` — Pytest suite for the reaper covering
  presence unlink, claims expired/missing/future/rust-deferred, lead
  expired/valid/missing, idempotency, dry-run semantics, heartbeat parity vectors.

- `scripts/rally_point/heartbeat_parity_vectors.json` — Golden parity fixture
  (byte-identical to the Rust counterpart in
  `crates/rally-cli/tests/fixtures/heartbeat_parity_vectors.json`). Proves that
  claude and codex sessions decay identically; `test_reaper.py` asserts each
  vector's `expected_weight` and `stale_at_15m` verdict via `decay.recency_weight`.

- **Actuator wiring** (`hooks/session-start-rally-point.sh`) — fire-and-forget
  `reaper.py --apply` call added at Step 3 so every session-start opportunistically
  cleans over-TTL coordination state.

- **Codex parity hook** (`.codex/hooks.json` `SessionStart`) — codex sessions now
  emit a presence record via `session_probe.py --tool codex`, so their
  presence/claims/lead decay identically to claude sessions.

- **Session-end self-release** (`.codex/hooks.json` `Stop` + `scripts/hooks/stop_finalize.sh`)
  — both codex and claude emit `rally stop <tool>` at turn completion, containing
  accretion at the source instead of relying on TTL expiry.

### Changed

- `scripts/rally_point/presence.py` — `reap_stale` gains an optional `apply: bool = True`
  parameter (backward-compatible). `apply=False` returns the would-reap session IDs
  without unlinking files, enabling reaper dry-run inspection.

- `scripts/rally_point/_provenance.json` — added entries for `reaper.py` (build-loop
  original; `source: null`) and `heartbeat_parity_vectors.json` (Rust fixture parity;
  sha256 `8d88c3e23fd8688b9a536ad06e3bdc89ede71a0637ff0455e87889f2869099c3`).

- `references/coordination-rules.md` — new subsection "In-room stale-state reaper
  (actuator) & codex parity" under "Recency decay & size-scaled lead/ownership
  auto-reclaim", documenting the actuator, FAIL-CLOSED invariant, Rust-vs-Python
  claim store rule, codex parity proof, and session-end self-release.

- `AGENTS.md` — one-line codex heartbeat-parity note added near the rally section.
