#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Draft a repeated user correction into memory, as a candidate awaiting a human.

A correction the user made ten times across five projects is the highest-signal
thing this miner produces, and today it waits in a JSON file for somebody to
open it. The August run found exactly that -- a cluster of ten, last seen
2026-08-24 -- and nothing consumed it.

The line this module does not cross
-----------------------------------
It drafts; it never decides. Every entry it writes carries `status: candidate`
and `confirmation_required: true` in its frontmatter, and its body opens by
saying it was auto-drafted from transcript mining and is not yet a rule. A
behavioural rule that an agent wrote for itself, from its own transcripts,
without a human ever seeing it, is a self-reinforcing loop -- the model's own
past output becomes its future instruction. `status: candidate` is what keeps
that from happening silently.

Drafting is recorded in the disposition ledger as `routed`, which is explicitly
NOT one of the terminal states. Routing is an effect; confirming is a decision;
recording the effect as a decision would close a loop nobody closed.

Idempotence
-----------
A candidate already marked `routed` is skipped. Without that, the miner would
redraft the same cluster on every run and the memory lane would fill with copies
of one observation -- the read->effect loop replaced by a write amplifier, which
is worse than the gap it was meant to close.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from . import disposition

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MEMORY_WRITER = _REPO_ROOT / "scripts" / "memory_writer.py"

# Below this, a "cluster" is three people saying vaguely similar things once.
# Ten was the August cluster; four was the next two. Five keeps the strong
# signal and drops the tail, and is the one number here worth tuning from
# observed precision rather than from taste.
MIN_COUNT = 5

# The miner runs headless from launchd and from SessionEnd. Neither has a build
# run id, and inventing one would put a fabricated provenance into memory.
RUN_ID_PREFIX = "transcript-miner"


def _slug(text: str, limit: int = 48) -> str:
    keep = [ch.lower() if ch.isalnum() else "-" for ch in text]
    out = "".join(keep)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")[:limit] or "correction"


def draft_body(candidate: dict[str, Any], window_label: str) -> str:
    """The memory entry's body. Says what it is before it says anything else."""
    quote = str(candidate.get("representative_quote", "")).strip()
    projects = ", ".join(candidate.get("projects") or []) or "(unattributed)"
    return "\n".join([
        "> **CANDIDATE — not a rule yet.** Auto-drafted by "
        "`transcript_pattern_miner` from repeated corrections in local session "
        "transcripts. Nobody has confirmed it. Promote it by editing this file "
        "and removing `status: candidate`, or close it with "
        "`python3 scripts/transcript_pattern_miner/disposition.py close "
        f"{candidate.get('candidate_id', '?')} --state waived --record <path>`.",
        "",
        "## What recurred",
        "",
        f"The same correction was raised **{candidate.get('count', '?')} times** "
        f"in {window_label}, across: {projects}.",
        "",
        "## Representative wording",
        "",
        "> " + (quote.replace("\n", "\n> ") if quote else "(no quote captured)"),
        "",
        "## What a human still has to decide",
        "",
        "Whether this is a standing behavioural rule, a one-project convention, "
        "or the same frustration expressed about several unrelated things. The "
        "miner clusters on 3-gram overlap, which groups wording rather than "
        "intent, so a tight cluster is evidence of repetition and not yet "
        "evidence of a single cause.",
        "",
        f"_candidate_id: `{candidate.get('candidate_id', '?')}` — "
        f"last seen {candidate.get('last_seen', 'unknown')}._",
    ])


def route(
    candidates: list[dict[str, Any]],
    out_dir: Path,
    *,
    window_label: str,
    min_count: int = MIN_COUNT,
    memory_dir: str | None = None,
    now: dt.datetime | None = None,
    runner=None,
) -> list[dict[str, Any]]:
    """Draft each eligible correction cluster into memory. Returns per-candidate results.

    `runner` is injected so the suite can exercise the selection and idempotence
    rules without writing to the real memory store.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    runner = runner or _write_via_memory_writer
    ledger = disposition.load(disposition.ledger_path(out_dir))
    results: list[dict[str, Any]] = []

    for candidate in candidates:
        if candidate.get("shape") != "user_correction_cluster":
            continue
        cid = candidate.get("candidate_id") or disposition.candidate_id(candidate)
        count = int(candidate.get("count") or 0)
        prior = ledger.get(cid)

        if count < min_count:
            results.append({"candidate_id": cid, "action": "skipped",
                            "reason": f"count {count} < {min_count}"})
            continue
        if prior and prior.get("state") == "routed":
            results.append({"candidate_id": cid, "action": "skipped",
                            "reason": "already drafted into memory",
                            "record": prior.get("record")})
            continue
        if disposition.is_closed(prior):
            results.append({"candidate_id": cid, "action": "skipped",
                            "reason": f"already {prior.get('state')}"})
            continue

        title = f"Repeated correction: {_slug(candidate.get('representative_quote', ''))}"
        try:
            written = runner(candidate, title, draft_body(candidate, window_label),
                             memory_dir=memory_dir, now=now)
        except AlreadyPromoted as promoted:
            # A closed loop, not a failure: a human confirmed it. Recorded as a
            # terminal `waived` so no out_dir's ledger re-surfaces it either.
            disposition.close(out_dir, cid, "waived", str(promoted),
                              rationale="human promoted the drafted memory entry",
                              shape="user_correction_cluster", now=now)
            results.append({"candidate_id": cid, "action": "skipped",
                            "reason": "already promoted by a human",
                            "record": str(promoted)})
            continue
        except Exception as exc:  # noqa: BLE001 - a mining run must not die on this
            results.append({"candidate_id": cid, "action": "failed",
                            "reason": f"{type(exc).__name__}: {exc}"})
            continue

        disposition.mark_routed(out_dir, cid, written,
                               shape="user_correction_cluster", now=now)
        results.append({"candidate_id": cid, "action": "drafted", "record": written})

    return results


class AlreadyPromoted(Exception):
    """A human removed `status: candidate`. The draft must never overwrite that."""


def _promotion_state(path: Path) -> str:
    """`absent` | `candidate` | `promoted`, read from the file's own frontmatter.

    Read from the file rather than from a ledger because the file is the thing
    a human edits, and the ledger the miner consults depends on which out_dir it
    was invoked from. An unreadable file is reported `absent`: re-drafting over
    a corrupt entry is recoverable, refusing forever is not.
    """
    try:
        if not path.exists():
            return "absent"
        head = path.read_text(encoding="utf-8", errors="ignore")[:2000]
    except OSError:
        return "absent"
    return "candidate" if "status: candidate" in head else "promoted"


def _load_memory_writer():
    """Load `scripts/memory_writer.py` by path, the way this repo's tests do.

    By path rather than by import name because `scripts/` is not a package and a
    bare `import memory_writer` would depend on whatever happens to be on
    sys.path when launchd invokes the miner.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_tpm_memory_writer", str(MEMORY_WRITER))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MEMORY_WRITER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_via_memory_writer(candidate, title, body, *, memory_dir=None, now=None):
    """Call the canonical writer's own `write()`, not a second implementation.

    The Python API rather than the CLI because `extra_frontmatter` is the only
    way to stamp `status: candidate`, and that stamp is the whole safety
    property here -- the CLI has no flag for it. `memory_writer.py` still owns
    frontmatter validation, lane placement and provenance; a memory file written
    any other way would be a second definition of what a memory entry is.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    writer = _load_memory_writer()
    # `default_memory_dir()` is already the top-level lessons lane, so the
    # filename carries NO lane prefix. A `lessons/` prefix would make
    # `_normalize_file_rel` strip it and re-point memory_dir at the REAL store
    # root -- silently ignoring an injected directory and writing a test file
    # into live memory. Observed here on the first run of this module.
    target = Path(memory_dir) if memory_dir else writer.default_memory_dir()
    run_id = f"{RUN_ID_PREFIX}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    cid = candidate.get("candidate_id", "unknown")
    file_rel = f"candidate-correction-{cid}.md"
    # Ask the writer where it will land rather than re-deriving it. Two answers
    # to "which file did this become" is how the caller ends up reporting a path
    # that was never written.
    effective_rel, effective_dir = writer._normalize_file_rel(
        file_rel, scope="top-level", project=None, memory_dir=target)
    target_path = Path(effective_dir) / effective_rel

    # The idempotence guard in `route` reads the ledger inside `out_dir`, but
    # this path is GLOBAL -- and the miner runs from at least three out_dirs
    # (launchd's default, learn_accruing.py, self_review/gather.py), each with
    # its own ledger. Without this check the second out_dir re-drafts a
    # candidate the first already drafted, and `memory_writer.write` rebuilds
    # frontmatter from scratch and replaces the body wholesale -- so a human who
    # promoted the entry by removing `status: candidate`, exactly as this
    # module's own draft body instructs, would have that promotion silently
    # reverted and their edits overwritten. Refusing to touch a promoted file is
    # the one behaviour this module cannot get wrong.
    promoted = _promotion_state(target_path)
    if promoted == "promoted":
        raise AlreadyPromoted(str(target_path))
    if promoted == "candidate":
        return str(target_path)

    writer.write(
        target,
        file_rel,
        body,
        name=title,
        description=(
            "CANDIDATE (unconfirmed): a correction the user repeated "
            f"{candidate.get('count', '?')} times, auto-drafted from transcript "
            "mining. Needs human confirmation before it is treated as a rule."),
        type_="feedback",
        run_id=run_id,
        workdir=str(_REPO_ROOT),
        host="claude_code",
        scope="top-level",
        extra_frontmatter={
            "status": "candidate",
            "confirmation_required": True,
            "source": "transcript_pattern_miner",
            "candidate_id": cid,
            "observed_count": candidate.get("count"),
            "last_seen": candidate.get("last_seen"),
        },
    )
    return str(target_path)
