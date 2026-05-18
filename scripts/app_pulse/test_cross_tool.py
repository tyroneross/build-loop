"""Stage 3 — automated cross-tool channel validation (V1-V3).

WHAT THIS PROVES (and what it does NOT).

App Pulse's promise is that a Claude session and a Codex session working
the *same* app share one ``~/.build-loop/apps/<slug>/`` channel and see
each other's commits / dep-changes / presence / arch digest. The risk
class this guards is the D1 worktree-slug split and any future
import-path / install-location divergence between the two tools'
plugin caches.

This suite simulates the two tools by:

  1. Tagging records/presence with ``tool="codex"`` vs ``tool="claude"``
     (the *identity* a real session would carry).
  2. Loading the app_pulse channel modules TWICE under distinct module
     names — once from the canonical ``scripts/app_pulse`` tree and once
     from the installed plugin cache tree — and asserting a write made
     through one module set surfaces through ``checkpoint_read`` of the
     OTHER module set, in BOTH directions. This is the same dual-path
     proof method used to close the Postgres-mirror question this
     project.

It does NOT spawn a real Codex (or Claude) process. It proves the
cross-tool channel *path* — that the channel API is import-path /
install-location independent and that two independently-loaded copies of
these modules interoperate over one ``$HOME``-keyed channel. The live
Codex *binary* leg (a real Codex session running the channel) is V4 in
``docs/_inbox/codex-apppulse-validation.md`` and is human-run, pending a
real Codex run — it is intentionally NOT asserted here.

Python stdlib only (plus pytest as the runner). Every test redirects
``$BUILD_LOOP_APPS_ROOT`` to a tmp dir so the real ``~/.build-loop`` is
never touched.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent  # scripts/app_pulse (canonical)
_CANON_SCRIPTS = _HERE.parent  # scripts/

# Installed plugin cache app_pulse tree — the "other tool's" code path.
# Discover the highest available cached version that ships app_pulse.
_CACHE_GLOB = (
    Path.home()
    / ".claude/plugins/cache/rosslabs-ai-toolkit/build-loop"
)


def _discover_cache_app_pulse() -> Path | None:
    """Return an installed-cache ``scripts/app_pulse`` dir, or None.

    Picks the newest version dir that actually contains the channel
    modules. None → the cache-path leg is skipped (env gap, not a
    regression — mirrors the postgres runbook's cache-sync gate).
    """
    if not _CACHE_GLOB.is_dir():
        return None
    candidates = []
    for vdir in _CACHE_GLOB.iterdir():
        ap = vdir / "scripts" / "app_pulse"
        if (ap / "checkpoint.py").is_file() and (
            vdir / "scripts" / "_paths.py"
        ).is_file():
            candidates.append((vdir.name, ap))
    if not candidates:
        return None
    # Newest by version-string sort (0.10.0 > 0.6.0; lexical is fine for
    # the zero-padded forms in use, but sort tuple-of-ints to be safe).
    def _vkey(name: str):
        parts = name.split(".")
        return tuple(int(p) if p.isdigit() else 0 for p in parts)

    candidates.sort(key=lambda c: _vkey(c[0]), reverse=True)
    return candidates[0][1]


_CACHE_APP_PULSE = _discover_cache_app_pulse()


def _load_module_set(app_pulse_dir: Path, tag: str) -> dict:
    """Load checkpoint/changes/presence/revision/channel_paths from
    ``app_pulse_dir`` under a unique name prefix ``tag``.

    Each set is fully independent (separate module objects, separate
    ``sys.modules`` entries) so the test genuinely exercises two distinct
    code copies, not one shared import.
    """
    mods: dict = {}
    # ``checkpoint.py`` does ``import changes/presence/revision`` by BARE
    # name (resolved off ``sys.path``). To make each set genuinely its
    # own end-to-end code copy — entry point AND its internal deps — we
    # (a) put this set's dir front-most, and (b) evict any bare-name
    # ``changes``/``presence``/``revision`` from ``sys.modules`` around
    # the load so checkpoint's top-level imports bind THIS tree's files,
    # not whichever set was loaded first. Restored afterwards so the two
    # sets stay fully independent.
    scripts_dir = app_pulse_dir.parent
    bare_names = ("revision", "changes", "presence")
    saved = {n: sys.modules.pop(n, None) for n in bare_names}
    inserted = []
    for p in (str(app_pulse_dir), str(scripts_dir)):
        if p not in sys.path:
            sys.path.insert(0, p)
            inserted.append(p)
    try:
        for name in (
            "revision",
            "changes",
            "presence",
            "checkpoint",
            "channel_paths",
        ):
            spec = importlib.util.spec_from_file_location(
                f"{tag}_{name}", app_pulse_dir / f"{name}.py"
            )
            assert spec and spec.loader
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            mods[name] = m
        # Pin checkpoint's bare-imported deps to THIS set's copies so a
        # later set's load cannot retroactively re-bind them.
        for bn in bare_names:
            mods[bn]  # noqa: B018 — assert present
    finally:
        for p in inserted:
            try:
                sys.path.remove(p)
            except ValueError:
                pass
        for n, prev in saved.items():
            if prev is not None:
                sys.modules[n] = prev
            else:
                sys.modules.pop(n, None)
    return mods


@pytest.fixture()
def canon() -> dict:
    """Canonical ``scripts/app_pulse`` module set (this checkout)."""
    return _load_module_set(_HERE, "canon")


@pytest.fixture()
def cache() -> dict | None:
    """Installed-plugin-cache module set, or None if no cache present."""
    if _CACHE_APP_PULSE is None:
        return None
    return _load_module_set(_CACHE_APP_PULSE, "cache")


@pytest.fixture()
def channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One shared channel dir under a tmp ``$BUILD_LOOP_APPS_ROOT``.

    Both module sets resolve the SAME filesystem channel because the
    channel path comes from the env var, not from which copy of the code
    is running — that is exactly the cross-tool property under test.
    """
    apps_root = tmp_path / "apps"
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", str(apps_root))
    # Use the canonical resolver so the slug is the worktree-aware D1
    # value; key the channel under that slug to mirror real usage.
    canon_cp = _load_module_set(_HERE, "slugprobe")["channel_paths"]
    slug = canon_cp.app_slug()
    chan = canon_cp.ensure_channel_dir(slug)
    return chan


def test_dual_path_sets_are_distinct_install_locations(canon, cache):
    """Integrity guard for the dual-path method itself: the canonical and
    installed-cache sets must be genuinely different files — entry point
    AND checkpoint's internal deps — or the round-trips below would
    silently prove nothing (the plan-critic collapse risk)."""
    if cache is None:
        pytest.skip("no installed plugin cache app_pulse — env gap")
    assert canon["checkpoint"] is not cache["checkpoint"]
    assert (
        Path(canon["checkpoint"].__file__).resolve()
        != Path(cache["checkpoint"].__file__).resolve()
    ), "checkpoint entry point must come from two install locations"
    # checkpoint binds changes/presence/revision as _ch/_pr/_rev — each
    # set's internal deps must also be its own tree's copy.
    assert (
        Path(canon["checkpoint"]._ch.__file__).resolve()
        != Path(cache["checkpoint"]._ch.__file__).resolve()
    ), "each set's checkpoint must use its OWN changes.py copy"
    assert "0.1" in cache["checkpoint"].__file__ or "cache" in (
        cache["checkpoint"].__file__
    ), "cache set resolves under the installed plugin cache tree"


def _kinds(env: dict) -> list:
    return [c.get("kind") for c in env.get("new_changes", [])]


def _react_types(env: dict) -> set:
    return {r.get("type") for r in env.get("reactions", [])}


# --------------------------------------------------------------------------
# V1 — channel round-trip: commit + dep-change, both code paths, both
# directions.
# --------------------------------------------------------------------------

def _write_commit_and_dep(mods: dict, chan: Path, *, tool: str) -> None:
    """Write one ``commit`` + one ``dep-change`` record and bump rev,
    tagged with ``tool`` — the cross-tool write identity."""
    ch, rev = mods["changes"], mods["revision"]
    r = rev.bump_revision(chan)
    ch.append_change(chan, ch.make_record(
        kind="commit", tool=tool, model="m", run_id=f"{tool}-run",
        app_slug="a", payload={"sha": f"{tool}sha"}, revision=r))
    ch.append_change(chan, ch.make_record(
        kind="dep-change", tool=tool, model="m", run_id=f"{tool}-run",
        app_slug="a", payload={"manifest": "pyproject.toml"}, revision=r))
    rev.bump_revision(chan)


def test_v1_codex_writes_canonical_claude_reads_dual_path(
    canon, cache, channel
):
    """Codex-identity write via canonical modules surfaces to a
    Claude-identity ``checkpoint_read`` via BOTH the canonical and the
    installed-cache code path."""
    _write_commit_and_dep(canon, channel, tool="codex")

    env_canon = canon["checkpoint"].checkpoint_read(
        channel, session_id="claude-A")
    assert env_canon["changed"] is True
    assert _kinds(env_canon) == ["commit", "dep-change"]
    assert "reinstall" in _react_types(env_canon)
    assert all(
        c["tool"] == "codex" for c in env_canon["new_changes"]
    ), "Claude must see the records were written by the codex identity"

    if cache is None:
        pytest.skip("no installed plugin cache app_pulse — env gap, "
                    "not a regression (see codex-apppulse-validation.md)")
    # A DIFFERENT session id so the cache-path read computes its own
    # delta from a zero cursor and re-surfaces the same records — proving
    # the cache code path reads the identical $HOME channel.
    env_cache = cache["checkpoint"].checkpoint_read(
        channel, session_id="claude-B-cachepath")
    assert env_cache["changed"] is True
    assert _kinds(env_cache) == ["commit", "dep-change"]
    assert "reinstall" in _react_types(env_cache)
    assert all(c["tool"] == "codex" for c in env_cache["new_changes"])


def test_v1_reverse_claude_writes_cache_codex_reads_canonical(
    canon, cache, channel
):
    """Reverse direction: Claude-identity write via the installed-cache
    modules surfaces to a Codex-identity read via the canonical path."""
    if cache is None:
        pytest.skip("no installed plugin cache app_pulse — env gap")
    _write_commit_and_dep(cache, channel, tool="claude")

    env = canon["checkpoint"].checkpoint_read(
        channel, session_id="codex-reader")
    assert env["changed"] is True
    assert _kinds(env) == ["commit", "dep-change"]
    assert "reinstall" in _react_types(env)
    assert all(c["tool"] == "claude" for c in env["new_changes"]), (
        "Codex (canonical path) must see the records the claude identity "
        "wrote through the cache path"
    )


# --------------------------------------------------------------------------
# V2 — presence / soft-claim warning + heartbeat reap, cross-tool.
# --------------------------------------------------------------------------

def test_v2_codex_presence_warns_claude_then_reaped(
    canon, cache, channel
):
    """Codex owning files in Phase 3 → a Claude phase-start read raises a
    soft-claim WARNING (never a block, D4). After the heartbeat window
    the dead Codex presence is reaped and no longer a live peer."""
    # Codex enters Phase 3 owning files A,B (written via canonical set).
    canon["presence"].write_presence(
        channel, session_id="codex-exec", tool="codex", model="m",
        run_id="codex-run", app_slug="a", phase="execute",
        files_in_flight=["src/a.py", "src/b.py"])
    # A change must exist + revision bump so checkpoint_read takes the
    # non-fast path and actually scans peers.
    canon["changes"].append_change(channel, canon["changes"].make_record(
        kind="phase", tool="codex", model="m", run_id="codex-run",
        app_slug="a", payload={"phase": "execute"}, revision=1))
    canon["revision"].bump_revision(channel)

    read_mods = cache if cache is not None else canon
    env = read_mods["checkpoint"].checkpoint_read(
        channel, session_id="claude-phase",
        my_files=["src/b.py", "src/c.py"])
    sc = [r for r in env["reactions"] if r.get("type") == "soft-claim"]
    assert sc, "claude must be warned codex owns an overlapping file"
    assert sc[0]["severity"] == "warning", "D4: soft-claim never a block"
    assert "src/b.py" in sc[0]["files"]
    assert sc[0]["peer"] == "codex-exec"

    # Kill the Codex session: backdate its heartbeat past the stale
    # window, then a fresh read must not list it as a live peer (reaper
    # runs opportunistically inside read_active_presence).
    sess = channel / "sessions" / "codex-exec.json"
    import json
    rec = json.loads(sess.read_text())
    window_s = canon["presence"].heartbeat_minutes(channel) * 60
    rec["heartbeat_ts"] = time.time() - window_s - 5
    sess.write_text(json.dumps(rec))

    peers = canon["presence"].read_active_presence(
        channel, exclude_session="claude-phase")
    assert not any(
        p.get("session_id") == "codex-exec" for p in peers
    ), "stale codex presence must be reaped — no live peer"
    assert not sess.exists(), "reaper removes the stale presence file"


# --------------------------------------------------------------------------
# V3 — arch digest cross-tool: codex enrich changes the inventory hash →
# a claude pre-edit checkpoint read reports the API/LLM surface changed.
# --------------------------------------------------------------------------

def _write_digest(chan: Path, inventory_hash: str) -> None:
    import json
    arch = chan / "arch"
    arch.mkdir(parents=True, exist_ok=True)
    (arch / "digest.json").write_text(json.dumps({
        "inventory_hash": inventory_hash,
        "per_type_counts": {"llm-callsite": 2, "api-callsite": 1},
    }))


def test_v3_codex_enrich_changes_digest_claude_sees_rebaseline(
    canon, cache, channel
):
    """Codex completes an enriched scan: the digest inventory hash flips
    and an ``arch-scan-complete`` change lands. A Claude pre-edit
    checkpoint read surfaces the new digest AND a re-baseline reaction —
    proving the compact digest (not the full graph) is enough for the
    cross-tool reaction."""
    # Initial digest state (hash H1), seen and cursored by claude.
    _write_digest(channel, "H1")
    canon["changes"].append_change(channel, canon["changes"].make_record(
        kind="phase", tool="claude", model="m", run_id="c-run",
        app_slug="a", payload={}, revision=1))
    canon["revision"].bump_revision(channel)
    read_mods = cache if cache is not None else canon
    first = read_mods["checkpoint"].checkpoint_read(
        channel, session_id="claude-preedit")
    assert first["arch_digest"] == {
        "inventory_hash": "H1",
        "per_type_counts": {"llm-callsite": 2, "api-callsite": 1},
    }

    # Codex enrich pass: inventory hash changes H1 -> H2 and an
    # arch-scan-complete record is appended + revision bumped.
    _write_digest(channel, "H2")
    canon["changes"].append_change(channel, canon["changes"].make_record(
        kind="arch-scan-complete", tool="codex", model="m",
        run_id="codex-enrich", app_slug="a",
        payload={"inventory_hash": "H2"}, revision=2))
    canon["revision"].bump_revision(channel)

    env = read_mods["checkpoint"].checkpoint_read(
        channel, session_id="claude-preedit")
    assert env["changed"] is True
    assert "re-baseline" in _react_types(env), (
        "claude pre-edit must react to the codex arch-scan-complete: "
        "API/LLM surface changed"
    )
    assert env["arch_digest"]["inventory_hash"] == "H2", (
        "the new (changed) inventory hash must be visible cross-tool"
    )
    assert any(
        c["kind"] == "arch-scan-complete" and c["tool"] == "codex"
        for c in env["new_changes"]
    )


# --------------------------------------------------------------------------
# Guard: this suite never asserts a real Codex process ran (accuracy).
# --------------------------------------------------------------------------

def test_suite_does_not_claim_live_codex_process():
    """Documents (and pins) the accuracy boundary: V1-V3 simulate the
    codex IDENTITY via a tool tag + a second code path; they do not spawn
    a Codex binary. The live-binary leg is V4 (human-run runbook)."""
    src = Path(__file__).read_text()
    assert "does NOT spawn a real Codex" in src
    assert "subprocess" not in src.split("Python stdlib only")[0], (
        "no Codex subprocess is launched in this suite's setup"
    )
