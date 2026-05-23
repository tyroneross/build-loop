# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Stage 3 — automated cross-tool channel validation (V1-V3).

WHAT THIS PROVES (and what it does NOT).

Rally Point's promise is that a Claude session and a Codex session working
the *same* app share one ``~/.build-loop/apps/<slug>/`` channel and see
each other's commits / dep-changes / presence / arch digest. The risk
class this guards is the D1 worktree-slug split and any future
import-path / install-location divergence between the two tools'
plugin caches.

This suite simulates the two tools by:

  1. Tagging records/presence with ``tool="codex"`` vs ``tool="claude"``
     (the *identity* a real session would carry).
  2. Loading the rally_point channel modules TWICE under distinct module
     names — once from the canonical ``scripts/rally_point`` tree and once
     from a hermetic ``tmp_path`` copy of that package (a real,
     physically separate install location that is NOT a symlink back to
     the canonical tree) — and asserting a write made through one module
     set surfaces through ``checkpoint_read`` of the OTHER module set, in
     BOTH directions. This is the same dual-path proof method used to
     close the Postgres-mirror question this project.

     The second set was previously discovered from
     ``~/.claude/plugins/cache/.../scripts/rally_point``. In a standard
     local-dev install that cache dir is a symlink back to this repo, so
     ``Path(...).resolve()`` collapsed both "install locations" onto the
     SAME real files and the dual-path proof was environment-contingent
     (it only "passed" when a worktree path happened to differ from the
     symlink target). The hermetic ``shutil.copytree`` makes the two
     trees genuinely distinct real dirs in EVERY environment (main
     checkout, worktree, CI, any user) and the suite no longer touches
     any ``~/.claude/plugins/cache`` path.

It does NOT spawn a real Codex (or Claude) process. It proves the
cross-tool channel *path* — that the channel API is import-path /
install-location independent and that two independently-loaded copies of
these modules interoperate over one ``$HOME``-keyed channel. The live
Codex *binary* leg (a real Codex session running the channel) is V4 in
``docs/_inbox/codex-rally-point-validation.md`` and is human-run, pending a
real Codex run — it is intentionally NOT asserted here.

Python stdlib only (plus pytest as the runner). Every test redirects
``$BUILD_LOOP_APPS_ROOT`` to a tmp dir so the real ``~/.build-loop`` is
never touched.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent  # scripts/rally_point (canonical)
_CANON_SCRIPTS = _HERE.parent  # scripts/


def _materialize_peer_rally_point(dest_root: Path) -> Path:
    """Build a hermetic, physically-distinct copy of the rally_point
    package under ``dest_root`` and return its ``rally_point`` dir.

    This is the "second tool / second install location" code path. It is
    a real ``shutil.copytree`` of ``scripts/rally_point/*.py`` plus the
    sibling ``scripts/_paths.py`` (``channel_paths.py`` loads it via
    ``__file__.parent.parent / "_paths.py"``), laid out as::

        <dest_root>/scripts/_paths.py
        <dest_root>/scripts/rally_point/<modules>.py

    Provenance — NOT the ``~/.claude/plugins/cache`` tree — is the whole
    point: that cache path is, in a standard local-dev install, a symlink
    back to this very repo, so ``Path(...).resolve()`` collapsed both
    "install locations" onto identical real files and the dual-path proof
    proved nothing on the main checkout (it only "passed" by the accident
    of a worktree path differing from the symlink target).

    A pytest tmp dir is never a symlink to the repo, so the copy resolves
    to a genuinely different real location in EVERY environment (main
    checkout, worktree, CI, any user). Stdlib + ``shutil`` only.
    """
    scripts_dst = dest_root / "scripts"
    ap_dst = scripts_dst / "rally_point"
    # Copy only the non-test .py module tree (deterministic, no pycache,
    # no recursive test collection under the tmp dir).
    shutil.copytree(
        _HERE,
        ap_dst,
        ignore=shutil.ignore_patterns(
            "test_*", "__pycache__", "*.pyc"
        ),
    )
    # channel_paths.py reaches up to scripts/_paths.py — copy the sibling
    # so the peer set's slug resolver is also its own tree's code.
    shutil.copy2(_CANON_SCRIPTS / "_paths.py", scripts_dst / "_paths.py")
    return ap_dst


def _load_module_set(rally_point_dir: Path, tag: str) -> dict:
    """Load checkpoint/changes/presence/revision/channel_paths from
    ``rally_point_dir`` under a unique name prefix ``tag``.

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
    scripts_dir = rally_point_dir.parent
    bare_names = ("revision", "changes", "presence")
    saved = {n: sys.modules.pop(n, None) for n in bare_names}
    inserted = []
    for p in (str(rally_point_dir), str(scripts_dir)):
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
                f"{tag}_{name}", rally_point_dir / f"{name}.py"
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
    """Canonical ``scripts/rally_point`` module set (this checkout)."""
    return _load_module_set(_HERE, "canon")


@pytest.fixture(scope="session")
def _peer_rally_point_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped hermetic copy of the rally_point package.

    One physically-distinct ``copytree`` reused across the suite (the
    copy is read-only for the tests — channel state lives elsewhere under
    the per-test ``$BUILD_LOOP_APPS_ROOT`` tmp dir).
    """
    dest = tmp_path_factory.mktemp("peer_install")
    return _materialize_peer_rally_point(dest)


@pytest.fixture()
def cache(_peer_rally_point_dir: Path) -> dict:
    """Second ("other tool") module set, loaded from the hermetic
    ``tmp_path`` copy — a real, physically separate install location that
    is NOT a symlink back to the canonical tree. Always present (no
    env-gap None): the dual-path proof is now environment-independent.
    """
    return _load_module_set(_peer_rally_point_dir, "peer")


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
    second module sets must be genuinely different *real* files — entry
    point AND checkpoint's internal deps — or the round-trips below would
    silently prove nothing (the plan-critic collapse risk).

    UNCONDITIONAL: there is no env-gap escape. The second set is a
    hermetic copy that always exists, so the guard always runs and the
    distinctness is a property of *path provenance*, never of which
    environment (main checkout / worktree / CI) happens to run it.
    """
    assert cache is not None, (
        "the second module set must always be present (hermetic copy) — "
        "no ~/.claude/plugins/cache env gap is tolerated"
    )
    assert canon["checkpoint"] is not cache["checkpoint"]
    canon_cp = Path(canon["checkpoint"].__file__).resolve()
    peer_cp = Path(cache["checkpoint"].__file__).resolve()
    assert canon_cp != peer_cp, (
        "checkpoint entry point must come from two PHYSICALLY DISTINCT "
        "real dirs (resolved). Equality here is the symlink-collapse the "
        f"cache-path provenance suffered: {canon_cp} == {peer_cp}"
    )
    # checkpoint binds changes/presence/revision as _ch/_pr/_rev — each
    # set's internal deps must also be its own tree's copy.
    assert (
        Path(canon["checkpoint"]._ch.__file__).resolve()
        != Path(cache["checkpoint"]._ch.__file__).resolve()
    ), "each set's checkpoint must use its OWN changes.py copy"


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

    # A DIFFERENT session id so the peer-path read computes its own
    # delta from a zero cursor and re-surfaces the same records — proving
    # the second (hermetic-copy) code path reads the identical $HOME
    # channel. The second set always exists; no env-gap skip.
    env_cache = cache["checkpoint"].checkpoint_read(
        channel, session_id="claude-B-cachepath")
    assert env_cache["changed"] is True
    assert _kinds(env_cache) == ["commit", "dep-change"]
    assert "reinstall" in _react_types(env_cache)
    assert all(c["tool"] == "codex" for c in env_cache["new_changes"])


def test_v1_reverse_claude_writes_cache_codex_reads_canonical(
    canon, cache, channel
):
    """Reverse direction: Claude-identity write via the second
    (hermetic-copy) modules surfaces to a Codex-identity read via the
    canonical path."""
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

    # Read via the second (hermetic-copy) set — always present.
    read_mods = cache
    env = read_mods["checkpoint"].checkpoint_read(
        channel, session_id="claude-phase",
        my_files=["src/b.py", "src/c.py"])
    sc = [r for r in env["reactions"] if r.get("type") == "soft-claim"]
    assert sc, "claude must be warned codex owns an overlapping file"
    # 2026-05-19: severity reason-keyed; D4 ("never a block") still holds.
    assert sc[0]["severity"] in {"warning", "informational"}, \
        "D4: soft-claim never a block"
    assert sc[0].get("reason") in {"merged_residue", "squash_landed",
                                   "active_conflict"}
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
    # Read via the second (hermetic-copy) set — always present.
    read_mods = cache
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
