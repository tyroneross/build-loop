# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""End-to-end run/drain, incl. the FALSIFIER proof: a simulated retro failure
must produce a fallback entry, never a silent skip (acceptance #4)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from post_push_retro import __main__ as m  # noqa: E402
from post_push_retro import coverage, router, fallback  # noqa: E402


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["git", "-C", str(repo), "init", "-q", "-b", "main"])
    subprocess.check_call(["git", "-C", str(repo), "config", "user.email", "t@e.com"])
    subprocess.check_call(["git", "-C", str(repo), "config", "user.name", "T"])


def _commit(repo: Path, f: str) -> None:
    (repo / f).parent.mkdir(parents=True, exist_ok=True)
    (repo / f).write_text("x")
    subprocess.check_call(["git", "-C", str(repo), "add", f])
    subprocess.check_call(["git", "-C", str(repo), "commit", "-q", "-m", f"add {f}"])


def _ns(repo, **kw):
    base = dict(workdir=str(repo), armed=None, llm_available=False, dry_run=False, json=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_run_trivial_end_to_end(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")  # 1 commit => trivial
    monkeypatch.setattr(router, "run_deterministic_retro",
                        lambda repo, rid, **k: {"ok": True, "output": {}, "error": None})
    out = m.cmd_run(_ns(repo))
    assert out["tier"] == "trivial"
    assert out["action"] == "deterministic_only"
    # checkpoint advanced => a second run has no new work
    assert m.cmd_run(_ns(repo)).get("skipped") == "no_new_work"


def test_run_fallback_fires_on_retro_failure(tmp_path, monkeypatch):
    # THE FALSIFIER: simulate a retro failure; a fallback entry MUST be produced.
    repo = tmp_path / "r"
    _init(repo)
    for i in range(4):
        _commit(repo, f"f{i}.py")
    monkeypatch.setattr(router, "run_deterministic_retro",
                        lambda repo, rid, **k: {"ok": False, "output": None,
                                                "error": "Fable unavailable (simulated)"})
    seen = {}

    def fake_fb(repo, rng, tier, reason, **kw):
        seen.update(dict(tier=tier, reason=reason))
        return {"filed": True, "witness": None}

    monkeypatch.setattr(fallback, "write", fake_fb)
    out = m.cmd_run(_ns(repo))
    assert out["action"] == "fallback"
    assert out["filed"] is True
    assert "Fable unavailable" in seen["reason"]  # the real reason is carried through


def test_run_crash_still_writes_witness(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")

    def boom(*a, **k):
        raise RuntimeError("coverage exploded")

    monkeypatch.setattr(m, "_run_once", boom)
    calls = {}
    monkeypatch.setattr(fallback, "write",
                        lambda *a, **k: calls.__setitem__("hit", True) or {"filed": True, "witness": "/w"})
    out = m.cmd_run(_ns(repo))
    assert out["action"] == "fallback_crash"
    assert calls.get("hit") is True  # never silent, even on an unexpected crash


def test_run_consumes_baton(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")
    state = coverage.retro_state_dir(repo)
    baton = state / "armed-test.json"
    baton.write_text(json.dumps({"pushed_range": None}))
    monkeypatch.setattr(router, "run_deterministic_retro",
                        lambda repo, rid, **k: {"ok": True, "output": {}, "error": None})
    m.cmd_run(_ns(repo, armed=str(baton)))
    assert not baton.exists()  # consumed


def test_run_disabled_skips(tmp_path):
    repo = tmp_path / "r"
    _init(repo)
    (repo / ".build-loop").mkdir(parents=True)
    (repo / ".build-loop" / "config.json").write_text('{"retrospective": {"optOut": true}}')
    assert m.cmd_run(_ns(repo))["skipped"] == "disabled_or_opted_out"


def test_run_dry_run_classifies_without_side_effects(tmp_path):
    repo = tmp_path / "r"
    _init(repo)
    for i in range(12):
        _commit(repo, f"f{i}.py")
    out = m.cmd_run(_ns(repo, dry_run=True))
    assert out["dry_run"] is True
    assert out["tier"] == "substantial"  # 12 commits
    assert not (coverage.retro_state_dir(repo) / "checkpoint.json").exists()


def test_drain_reruns_stale_baton(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")
    state = coverage.retro_state_dir(repo)
    baton = state / "armed-stale.json"
    baton.write_text(json.dumps({"pushed_range": None}))
    old = time.time() - (m.STALE_BATON_SECONDS + 30)
    os.utime(baton, (old, old))  # make it stale
    monkeypatch.setattr(router, "run_deterministic_retro",
                        lambda repo, rid, **k: {"ok": True, "output": {}, "error": None})
    out = m.cmd_drain(_ns(repo))
    assert len(out["reran_batons"]) == 1
    assert not baton.exists()  # consumed by the rerun


def test_drain_ignores_fresh_baton(tmp_path):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")
    state = coverage.retro_state_dir(repo)
    baton = state / "armed-fresh.json"
    baton.write_text(json.dumps({"pushed_range": None}))  # just written => fresh
    out = m.cmd_drain(_ns(repo))
    assert out["reran_batons"] == []  # in-flight detached job owns it
    assert baton.exists()


def test_drain_escalates_stale_upgrade(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")
    state = coverage.retro_state_dir(repo)
    (state / "upgrade.json").write_text(json.dumps(
        {"tier": "medium", "range_label": "a..b", "armed_at": "2020-01-01T00:00:00Z"}))
    esc = {}
    monkeypatch.setattr(fallback, "write",
                        lambda *a, **k: esc.__setitem__("hit", True) or {"filed": True, "witness": "/w"})
    out = m.cmd_drain(_ns(repo))
    assert out["upgrade"]["escalated"] is True
    assert esc.get("hit") is True
    assert not (state / "upgrade.json").exists()  # escalated + cleared


def test_drain_fresh_upgrade_surfaced_not_escalated(tmp_path):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")
    state = coverage.retro_state_dir(repo)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (state / "upgrade.json").write_text(json.dumps(
        {"tier": "substantial", "range_label": "a..b", "armed_at": now}))
    out = m.cmd_drain(_ns(repo))
    assert out["upgrade"]["pending"] is True
    assert (state / "upgrade.json").exists()  # left for the in-context agent


def test_main_cli_run_exits_zero(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")
    monkeypatch.setattr(router, "run_deterministic_retro",
                        lambda repo, rid, **k: {"ok": True, "output": {}, "error": None})
    assert m.main(["run", "--workdir", str(repo), "--json"]) == 0
