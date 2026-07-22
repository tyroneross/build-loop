# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""locate.py — find the Claude Code session transcript for a given run.

Claude Code stores transcripts at::

    ~/.claude/projects/<cwd-slug>/<session-uuid>.jsonl

where ``<cwd-slug>`` is the absolute working directory with ``/`` replaced by
``-`` (e.g. ``/Users/<username>/dev/git-folder/build-loop`` →
``-Users-<username>-dev-git-folder-build-loop``).

WHY THE CWD SLUG IS NOT ENOUGH (observed failure, 2026-07-21)
--------------------------------------------------------------
The slug is derived from the cwd the SESSION was started in, not the repo the
work targeted. A build driven from an orchestrator cwd therefore writes its
transcript under the ORCHESTRATOR's slug, and a slug-only lookup for the target
repo finds nothing. Measured on the reporting machine: the target repo's slug
dir held **0** transcripts while the orchestrator's slug held **150**, so every
retrospective for that repo ran with zero transcript signal while still emitting
confident-looking output.

Three resolution sources, strongest evidence first:

1. **Explicit session id** (:func:`find_transcript_by_session_id`) — an exact
   filename match across every slug. Caller-asserted identity.
2. **The cwd slug** — the historical path, unchanged.
3. **cwd attestation across slugs** — Claude Code stamps a top-level ``cwd`` on
   each record, so a transcript can prove it worked in a repo even when it lives
   under a different slug. This mirrors the double gate
   :func:`find_codex_transcript_for_run` already applies to codex rollouts
   (which are likewise not slug-scoped): attest the cwd AND verify the time
   window.

Attestation is SHARE-BASED, not existential. A single transcript legitimately
carries several top-level ``cwd`` values, and accepting the first match would
attach a transcript to a repo it barely touched. Measured on the transcript that
motivated this module: ``/…/TruePace`` appeared 2813× (97.2%) and
``/Users/<username>`` 81× (2.8%) in the SAME file, so an existential gate would
have handed a 97%-other-repo transcript to the 2.8% repo's retrospective — a new
instance of the "nearest-but-wrong" defect ``temporal_membership`` exists to
prevent. :data:`MIN_ATTESTATION_SHARE` is the floor; candidates rank by share.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

try:
    import temporal_membership as _tm
except ImportError:  # pragma: no cover - path fallback when scripts/ not on sys.path
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import temporal_membership as _tm


# Minimum share of a transcript's top-level ``cwd`` records that must name the
# workdir before the transcript counts as attesting it. Calibrated against a
# measured 2.8%-minority / 97.2%-majority split inside a single real transcript:
# an order of magnitude above the false-positive, well below any legitimate
# multi-repo split.
MIN_ATTESTATION_SHARE = 0.25
# Upper bound on cross-slug candidates examined after the mtime prune. Measured
# on a 251-transcript store: the prune alone cut a run window to 26 candidates.
CROSS_SLUG_CANDIDATE_CAP = 40
# A session-id PREFIX must be at least this long to be usable. Uniqueness is not
# correctness — a short hex-ish token ("bed", "face") can uniquely prefix an
# unrelated session file.
MIN_SESSION_PREFIX_LEN = 8

# Glob metacharacters make a caller-supplied id unsafe to interpolate.
_GLOB_META_RE = re.compile(r"[*?\[\]/\\]")
_HEXISH_TOKEN_RE = re.compile(r"[0-9a-fA-F]{%d,}" % MIN_SESSION_PREFIX_LEN)


def cwd_to_slug(cwd: Path | str) -> str:
    """Convert an absolute cwd to its Claude Code slug.

    The slug is the absolute path with leading slash stripped and remaining
    ``/`` replaced by ``-`` (Claude Code's convention).
    """
    p = Path(cwd).resolve()
    abs_str = str(p)
    # Strip the leading '/' (POSIX absolute path) so the slug starts with '-'.
    if abs_str.startswith("/"):
        abs_str = abs_str[1:]
    return "-" + abs_str.replace("/", "-")


def sessions_root() -> Path:
    """Return the Claude Code sessions root (``~/.claude/projects/``)."""
    return Path.home() / ".claude" / "projects"


def find_transcript_by_session_id(session_id: str | None) -> Path | None:
    """Return the transcript whose FILENAME is ``<session_id>.jsonl``, or None.

    Searches every project slug, because the slug encodes the session's starting
    cwd — which is exactly the thing that is wrong when this lookup is needed.
    An exact filename match is unique by construction and is the strongest
    evidence available short of being handed the path outright.

    Falls back to a PREFIX match on the longest hex-ish token in ``session_id``,
    so a Rally tool id (``fable-82ab7452``) resolves to ``82ab7452-….jsonl``. The
    prefix must be at least :data:`MIN_SESSION_PREFIX_LEN` chars AND match exactly
    one file — ambiguity returns None rather than guessing, because uniqueness is
    not correctness for a short token.

    Never raises — IO errors and unusable ids return None.
    """
    sid = str(session_id or "").strip()
    if not sid or _GLOB_META_RE.search(sid):
        return None
    try:
        root = sessions_root()
        if not root.is_dir():
            return None
        exact = [p for p in root.glob(f"*/{sid}.jsonl") if p.is_file()]
        if exact:
            # Same filename under two slugs would be a Claude Code invariant
            # violation; take the newest and stay non-raising.
            return max(exact, key=lambda p: p.stat().st_mtime)
        for token in sorted(_HEXISH_TOKEN_RE.findall(sid), key=len, reverse=True):
            hits = [p for p in root.glob(f"*/{token}*.jsonl") if p.is_file()]
            if len(hits) == 1:
                return hits[0]
    except (OSError, ValueError):
        return None
    return None


def _confirms_top_level_cwd(path: Path, want: str, *, max_checks: int = 5) -> bool:
    """True when ``want`` appears as a record's OWN top-level ``cwd`` field.

    The byte-count prescreen in :func:`transcript_cwd_share` cannot tell a real
    ``cwd`` field from the same bytes embedded inside a tool-call payload, so a
    handful of matching lines are actually parsed. Bounded and early-returning.
    """
    needle = f'"cwd":"{want}"'
    checked = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if needle not in line:
                    continue
                checked += 1
                try:
                    if json.loads(line).get("cwd") == want:
                        return True
                except (json.JSONDecodeError, AttributeError):
                    pass
                if checked >= max_checks:
                    return False
    except OSError:
        return False
    return False


def transcript_cwd_share(path: Path, cwd: Path | str) -> float:
    """Fraction of a transcript's top-level ``cwd`` records that name ``cwd``.

    Returns 0.0 when the transcript never attests ``cwd``, when it records no
    ``cwd`` at all, or on any IO error. Never raises.

    Share, not presence: a transcript legitimately carries several top-level
    ``cwd`` values, so "does this file mention the repo" is far too weak a test.
    See the module docstring for the measured 2.8%/97.2% split that motivated it.

    Cheap by construction — two raw byte counts (sub-millisecond on a 7.7 MB
    file), then at most a few parsed lines to rule out an embedded payload match.
    """
    candidates: list[str] = []
    try:
        candidates.append(str(Path(cwd).expanduser().resolve()))
    except (OSError, ValueError):
        pass
    raw_str = str(cwd)
    if raw_str not in candidates:
        candidates.append(raw_str)

    try:
        blob = path.read_bytes()
    except OSError:
        return 0.0
    total = blob.count(b'"cwd":"')
    if total <= 0:
        return 0.0
    for want in candidates:
        hits = blob.count(f'"cwd":"{want}"'.encode("utf-8", "ignore"))
        if hits > 0 and _confirms_top_level_cwd(path, want):
            return hits / total
    return 0.0


def find_transcript_for_cwd(
    cwd: Path | str,
    *,
    session_id: str | None = None,
) -> Path | None:
    """Return the most-recently-modified JSONL for ``cwd``, or None.

    Args:
        cwd: absolute working directory of the build-loop run.
        session_id: optional session identifier. When supplied it is tried FIRST
            via :func:`find_transcript_by_session_id` (exact filename across all
            slugs), because the cwd slug is unreliable for a run driven from a
            different directory than the target repo.

    Returns:
        Path to the JSONL transcript, or None if no transcript directory or
        no JSONL files exist for this cwd.

    Never raises — IO errors return None.

    NOTE: this is the historical newest-wins helper with no time check. The
    run-scoped locator :func:`find_transcript_for_run` is the production path.
    """
    if session_id:
        hit = find_transcript_by_session_id(session_id)
        if hit is not None:
            return hit
    try:
        slug = cwd_to_slug(cwd)
        root = sessions_root() / slug
        if not root.is_dir():
            return None
        jsonls = sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return jsonls[0] if jsonls else None
    except (OSError, ValueError):
        return None


def transcript_time_span(path: Path | str) -> tuple:
    """Return ``(first_ts, last_ts)`` datetimes from a transcript JSONL, or ``(None, None)``.

    Reads the ``timestamp`` field Claude Code stamps on each record. Used to decide whether
    a candidate transcript's time span overlaps a run's window. Never raises.
    """
    first = last = None
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _tm.parse_ts(rec.get("timestamp"))
                if ts is None:
                    continue
                if first is None:
                    first = ts
                last = ts
    except OSError:
        return None, None
    return first, last


def codex_sessions_root() -> Path:
    """Return the Codex CLI sessions root (``~/.codex/sessions/``)."""
    return Path.home() / ".codex" / "sessions"


def codex_transcript_cwd(path: Path) -> str | None:
    """Read a codex rollout's ``session_meta`` cwd (first line ``payload.cwd``)."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("type") == "session_meta":
                    return (rec.get("payload") or {}).get("cwd")
                return None  # meta is always first; bail once past it
    except (OSError, ValueError):
        return None
    return None


def _candidate_codex_rollouts(run_start, run_end) -> list[Path]:
    """Bounded set of rollout files: the run-window date dirs, else newest-by-mtime."""
    root = codex_sessions_root()
    if not root.is_dir():
        return []
    paths: list[Path] = []
    seen: set[Path] = set()
    for ts in (run_start, run_end):
        if ts is None:
            continue
        day_dir = root / f"{ts.year:04d}" / f"{ts.month:02d}" / f"{ts.day:02d}"
        if day_dir.is_dir():
            for p in day_dir.glob("rollout-*.jsonl"):
                if p not in seen:
                    paths.append(p)
                    seen.add(p)
    if paths:
        return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)
    # Fallback: newest 100 rollouts anywhere (bounds a full-tree walk).
    allp = sorted(root.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return allp[:100]


def find_codex_transcript_for_run(
    cwd: Path | str,
    *,
    run_start=None,
    run_end=None,
    run_host: str | None = None,
    bound_hours: float | None = None,
):
    """Locate the CODEX rollout that provably belongs to this run.

    Codex rollouts (``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``) are global (not
    cwd-slug-scoped like Claude transcripts), so attribution is DOUBLE-gated: the
    rollout's ``session_meta.cwd`` must match the run's repo AND its time span must pass
    temporal-membership against the run window (``record_host="codex"``). Returns
    ``(path, None)`` on a match, else ``(None, marker)``. Never raises.
    """
    kwargs = {} if bound_hours is None else {"bound_hours": bound_hours}
    marker = _tm.absence_marker(run_host, run_start, run_end, kind="codex-transcript")
    try:
        want_cwd = str(Path(cwd).expanduser().resolve())
    except (OSError, ValueError):
        want_cwd = str(cwd)
    last_reason = None
    for path in _candidate_codex_rollouts(run_start, run_end):
        rc = codex_transcript_cwd(path)
        if rc is not None:
            try:
                if str(Path(rc).expanduser().resolve()) != want_cwd:
                    continue  # different repo — not this run
            except (OSError, ValueError):
                if rc != want_cwd:
                    continue
        first, last = transcript_time_span(path)  # top-level "timestamp" per line
        ok, reason = _tm.is_member(
            first, last, run_start, run_end,
            record_host="codex", run_host=run_host, **kwargs,
        )
        if ok:
            return path, None
        last_reason = reason
    if last_reason:
        marker += f" — nearest candidate {last_reason}"
    return None, marker


def _candidate_cross_slug_transcripts(
    cwd: Path | str,
    run_start,
    bound_hours: float,
) -> list[Path]:
    """Transcripts under OTHER slugs that could belong to this run window.

    Bounded three ways: the run's own slug is skipped (source 1 already covered
    it), anything last modified before the window opened is pruned (a transcript
    belonging to a run is still being appended during it), and the survivors are
    capped newest-first at :data:`CROSS_SLUG_CANDIDATE_CAP`.

    The root is resolved through :func:`sessions_root` rather than re-deriving
    ``Path.home()``, because the codex locator tests isolate the store by
    monkeypatching exactly that function — a re-derived path would escape the
    patch and walk the developer's real transcript store during tests.

    Depth is exactly ``*/*.jsonl``. ``~/.claude/projects`` also holds nested
    ``subagents/agent-*.jsonl`` trees, so an ``rglob`` would blow the measured
    bound and change what "newest-first" ranks.
    """
    root = sessions_root()
    if not root.is_dir():
        return []
    try:
        own_slug = cwd_to_slug(cwd)
    except (OSError, ValueError):
        own_slug = None
    cutoff = None
    if run_start is not None:
        cutoff = run_start - _dt.timedelta(hours=max(0.0, bound_hours))

    scored: list[tuple[float, Path]] = []
    try:
        paths = list(root.glob("*/*.jsonl"))
    except OSError:
        return []
    for p in paths:
        if own_slug and p.parent.name == own_slug:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if cutoff is not None:
            if _dt.datetime.fromtimestamp(mtime, _dt.timezone.utc) < cutoff:
                continue
        scored.append((mtime, p))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [p for _m, p in scored[:CROSS_SLUG_CANDIDATE_CAP]]


def find_cwd_attested_transcript_for_run(
    cwd: Path | str,
    *,
    run_start=None,
    run_end=None,
    run_host: str | None = None,
    bound_hours: float | None = None,
):
    """Find a transcript under a DIFFERENT slug that attests it worked in ``cwd``.

    The fallback for the observed failure this module exists to fix: a run driven
    from an orchestrator cwd writes its transcript under the orchestrator's slug,
    so the target repo's slug is empty.

    Double-gated, mirroring :func:`find_codex_transcript_for_run`:
      1. the transcript must name ``cwd`` as a DOMINANT top-level ``cwd`` value
         (``share >= MIN_ATTESTATION_SHARE``), and
      2. its time span must pass temporal membership against the run window.

    Candidates rank by attestation share (desc), then mtime (desc), so the
    transcript that spent the most of itself in this repo wins.

    Returns ``(path, None)`` on a match, else ``(None, reason | None)``. Never raises.
    """
    kwargs = {} if bound_hours is None else {"bound_hours": bound_hours}
    bound = _tm.DEFAULT_BOUND_HOURS if bound_hours is None else bound_hours
    last_reason = None
    scored: list[tuple[float, float, Path]] = []
    for path in _candidate_cross_slug_transcripts(cwd, run_start, bound):
        share = transcript_cwd_share(path, cwd)
        if share < MIN_ATTESTATION_SHARE:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        scored.append((share, mtime, path))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    for _share, _mtime, path in scored:
        first, last = transcript_time_span(path)
        ok, reason = _tm.is_member(
            first, last, run_start, run_end,
            record_host="claude_code", run_host=run_host, **kwargs,
        )
        if ok:
            return path, None
        last_reason = reason
    return None, last_reason


def find_transcript_for_run(
    cwd: Path | str,
    *,
    run_start=None,
    run_end=None,
    run_host: str | None = None,
    bound_hours: float | None = None,
    session_id: str | None = None,
    session_id_is_explicit: bool = False,
):
    """Locate the transcript that PROVABLY belongs to this run.

    Unlike :func:`find_transcript_for_cwd` (newest-wins, no time check), this walks
    candidates newest-first and returns the first whose time span AND host pass the
    temporal-membership check against the run window. Returns ``(path, None)`` on a match,
    or ``(None, reason)`` with an explicit absence marker when no candidate belongs to the
    run. Never raises.

    Four sources, strongest evidence first:
      0. An explicit ``session_id`` — exact filename match across every slug.
      1. Claude Code transcripts under the cwd slug (host ``claude_code``).
      1b. Claude Code transcripts under ANY OTHER slug that ATTEST this cwd — the fix
         for a run driven from an orchestrator cwd, whose transcript lands under the
         orchestrator's slug and is invisible to source 1. See
         :func:`find_cwd_attested_transcript_for_run`.
      2. Codex rollouts (``~/.codex/sessions/``, host ``codex``) — added so a codex-hosted
         run gets a REAL transcript source instead of only an absence marker (retro §10/§11
         came back empty on codex runs). Skipped only when the run is KNOWN to be
         ``claude_code``-hosted.

    ``session_id_is_explicit`` controls the time gate on source 0, and the distinction is
    load-bearing. An id passed by a caller (``--session-id``) is asserted identity — the
    same evidence class as handing over ``transcript=`` directly, which bypasses this
    locator entirely — so it skips the window check. An id merely DERIVED from
    ``state.json`` is a hint: ``started_by_session_id`` is immutable post-generation, so it
    survives resumes and later runs in the same repo, and trusting it without a time check
    would reopen the very RCA-2026-07-11 substitution defect this function exists to close.
    Derived ids are therefore gated like any other candidate.

    This extends the RCA 2026-07-11 fix (422a5c1): the old locator silently substituted a
    ~3-week-stale Claude transcript for a codex run; now we neither substitute NOR leave
    codex runs sourceless — we find the codex rollout when one provably belongs.
    """
    kwargs = {} if bound_hours is None else {"bound_hours": bound_hours}
    marker = _tm.absence_marker(run_host, run_start, run_end, kind="transcript")
    last_reason = None

    # Source 0: session id. Host still gates; the TIME gate applies only to a
    # derived (non-explicit) id.
    if session_id:
        hit = find_transcript_by_session_id(session_id)
        if hit is not None:
            ok, reason = _tm.is_member(
                None, None, run_start, run_end,
                record_host="claude_code", run_host=run_host, **kwargs,
            )
            if ok:
                if session_id_is_explicit:
                    return hit, None
                first, last = transcript_time_span(hit)
                ok2, reason2 = _tm.is_member(
                    first, last, run_start, run_end,
                    record_host="claude_code", run_host=run_host, **kwargs,
                )
                if ok2:
                    return hit, None
                last_reason = reason2
            else:
                last_reason = reason

    try:
        slug = cwd_to_slug(cwd)
        root = sessions_root() / slug
        if root.is_dir():
            jsonls = sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in jsonls:
                first, last = transcript_time_span(path)
                ok, reason = _tm.is_member(
                    first, last, run_start, run_end,
                    record_host="claude_code", run_host=run_host, **kwargs,
                )
                if ok:
                    return path, None
                last_reason = reason
    except (OSError, ValueError):
        pass

    # Source 1b: cwd-attested transcripts under other slugs.
    attested, attested_reason = find_cwd_attested_transcript_for_run(
        cwd, run_start=run_start, run_end=run_end, run_host=run_host,
        bound_hours=bound_hours,
    )
    if attested is not None:
        return attested, None
    if attested_reason:
        last_reason = attested_reason

    # Source 2: codex rollouts, unless the run is explicitly claude_code-hosted.
    if str(run_host or "").lower() != "claude_code":
        codex_path, _codex_reason = find_codex_transcript_for_run(
            cwd, run_start=run_start, run_end=run_end, run_host=run_host,
            bound_hours=bound_hours,
        )
        if codex_path is not None:
            return codex_path, None
    if last_reason:
        marker += f" — nearest candidate {last_reason}"
    return None, marker
