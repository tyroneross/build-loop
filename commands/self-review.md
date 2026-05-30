---
description: "Periodic self-review: mine recent build activity for issues and efficiency signals, optionally apply SAFE improvements. Use --install/--uninstall/--status to manage the launchd schedule."
argument-hint: "[light|deep] [--install|--uninstall|--status]"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

{{#if ARGUMENTS}}
{{#contains ARGUMENTS "--install"}}
Run `python3 scripts/install_self_review.py install --json` in the current repo and report the result.
{{else}}
{{#contains ARGUMENTS "--uninstall"}}
Run `python3 scripts/install_self_review.py uninstall --json` in the current repo and report the result.
{{else}}
{{#contains ARGUMENTS "--status"}}
Run `python3 scripts/install_self_review.py status --json` in the current repo and report loaded/not-loaded state for both scheduled jobs.
{{else}}
Determine mode from ARGUMENTS: if it contains "deep" use `deep`, otherwise use `light`.
Run `python3 scripts/self_review.py --mode <mode> --workdir . --json` and display the digest path and queued proposal count. If mode is deep, also describe the queued proposals and their classify_hint values.
{{/contains}}
{{/contains}}
{{/contains}}
{{else}}
Run `python3 scripts/self_review.py --mode light --workdir . --json` and display the digest path and queued proposal count.
{{/if}}

---

## Reference

**Manual trigger (light — daily digest, no apply)**
```
/build-loop:self-review light
```

**Manual trigger (deep — full review; routes SAFE proposals through /build-loop:run)**
```
/build-loop:self-review deep
```

**Launchd schedule management**
```
/build-loop:self-review --install    # install both launchd jobs (reads .build-loop/config.json)
/build-loop:self-review --status     # check loaded/not-loaded
/build-loop:self-review --uninstall  # remove jobs and plists
```

Scheduled runs are fully autonomous: the deep job (Sunday 03:00 by default) runs the gatherer, then calls `claude -p` with the apply prompt to route SAFE proposals through `/build-loop:run`, which applies + commits + collapses. Push behavior is governed by `selfReview.autonomy` in `.build-loop/config.json` (`apply_push` / `apply_local` / `propose`). RISKY and DECISION proposals are never applied autonomously — they stay queued for manual review.

See `skills/build-loop/references/self-review.md` for the full config schema, graceful-degradation notes, and the APPLY PROMPT.
