#!/usr/bin/env bash
# PreToolUse(Edit|Write) hook — Rally Point cheap revision-stat hint.
#
# Contract:
#   - command-type only (NEVER prompt-type)
#   - exit 0 ALWAYS — NEVER blocks the Edit/Write
#   - cheap: a single `revision` stat+read, NO tail read, NO lock
#   - prints one short hint ONLY when the channel revision is ahead of
#     this reader's last-seen revision; silent otherwise
#   - graceful absence: no channel -> silent exit 0
#
# This is intentionally lighter than checkpoint_read: it must not slow an
# edit. It only nudges "something changed since you last looked — run a
# checkpoint". The orchestrator/SessionStart hook does the full read.

WORKDIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="$SCRIPT_DIR/../scripts/rally_point"

[ -d "$PKG" ] || exit 0

WORKDIR="$WORKDIR" PKG="$PKG" python3 - <<'PYEOF' 2>/dev/null || exit 0
import os
import sys

sys.path.insert(0, os.environ["PKG"])
try:
    import channel_paths as ap
    import presence as pr
    import revision as rev
except Exception:
    sys.exit(0)

try:
    slug = ap.app_slug(cwd=os.environ["WORKDIR"])
    chan = ap.app_channel_dir(slug)
    if not chan.exists():
        sys.exit(0)
    cur = rev.read_revision(chan)            # one stat+read, no lock
    sid = f"sessionstart-{slug.replace('/', '_')}"
    seen = pr.get_cursor(chan, sid).get("revision", 0)
    if cur > seen:
        print(
            f"Rally Point: {slug} channel advanced "
            f"(rev {seen} -> {cur}) — run a checkpoint before editing."
        )
except Exception:
    sys.exit(0)
PYEOF

exit 0
