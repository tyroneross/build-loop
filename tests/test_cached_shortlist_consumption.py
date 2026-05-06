"""Tests for Priority 16 — cached shortlist consumption by subagent dispatch.

Producer side: `scripts/capability_shortlist.py:cache_into_state` must write
`state.json.activeCapabilities` as a phase-keyed dict
`{ "1": [...], "2": [...], "3": [...] }` rather than a flat list, so a
dispatcher can index by phase without scanning.

Consumer side: `read_active_capabilities()` must return the most-recent
shortlist for a given phase, with optional fallback to another phase.

Backward-compat: when the cache is empty, the dispatcher must behave as
before (no shortlist injected).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import capability_shortlist as cs  # type: ignore  # noqa: E402


def _populate_cache(tmp_path: Path, phase: int, intent: str, names: List[str]) -> Path:
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if not state_path.exists():
        state_path.write_text("{}", encoding="utf-8")
    fake_result: Dict[str, Any] = {
        "phase": phase,
        "intent": intent,
        "results": [{"name": n, "kind": "skill", "score": 5} for n in names],
    }
    cs.cache_into_state(tmp_path, fake_result)
    return state_path


# ---------------------------------------------------------------------------
# Producer: phase-keyed dict shape
# ---------------------------------------------------------------------------

def test_state_json_has_phase_keyed_active_capabilities(tmp_path: Path) -> None:
    """P16 producer: activeCapabilities is a phase-keyed dict, not a flat list."""
    _populate_cache(tmp_path, phase=2, intent="plan something", names=["a", "b"])
    _populate_cache(tmp_path, phase=3, intent="execute it", names=["x", "y", "z"])

    state = json.loads((tmp_path / ".build-loop" / "state.json").read_text(encoding="utf-8"))
    cap = state["activeCapabilities"]
    assert isinstance(cap, dict), "must be dict, not list"
    assert "2" in cap and "3" in cap
    assert isinstance(cap["2"], list)
    assert cap["2"][-1]["shortlist"] == ["a", "b"]
    assert cap["3"][-1]["shortlist"] == ["x", "y", "z"]


def test_results_array_preserved_for_dispatch(tmp_path: Path) -> None:
    """Each cached entry preserves the full results array — not just names —
    so dispatchers can embed kind/category/score context into briefs."""
    _populate_cache(tmp_path, phase=2, intent="plan something", names=["a", "b"])
    state = json.loads((tmp_path / ".build-loop" / "state.json").read_text(encoding="utf-8"))
    entry = state["activeCapabilities"]["2"][-1]
    assert "results" in entry
    assert entry["results"][0]["name"] == "a"
    assert "kind" in entry["results"][0]
    assert "score" in entry["results"][0]


def test_per_phase_cap_keeps_most_recent(tmp_path: Path) -> None:
    """The per-phase bucket caps at 10 entries — fresher invocations win."""
    for i in range(15):
        _populate_cache(tmp_path, phase=2, intent=f"intent {i}", names=[f"r{i}"])
    state = json.loads((tmp_path / ".build-loop" / "state.json").read_text(encoding="utf-8"))
    bucket = state["activeCapabilities"]["2"]
    assert len(bucket) == 10
    # Most recent entry must be intent #14 (last appended).
    assert bucket[-1]["intent"] == "intent 14"


# ---------------------------------------------------------------------------
# Consumer: read_active_capabilities()
# ---------------------------------------------------------------------------

def test_dispatch_template_inherits_cached_shortlist(tmp_path: Path) -> None:
    """Dispatcher consumer reads the cached shortlist for the right phase
    without re-running the matcher."""
    _populate_cache(tmp_path, phase=2, intent="plan", names=["alpha", "beta"])
    _populate_cache(tmp_path, phase=3, intent="execute", names=["gamma"])

    state = json.loads((tmp_path / ".build-loop" / "state.json").read_text(encoding="utf-8"))
    phase_2_results = cs.read_active_capabilities(state, phase=2)
    phase_3_results = cs.read_active_capabilities(state, phase=3)
    assert [r["name"] for r in phase_2_results] == ["alpha", "beta"]
    assert [r["name"] for r in phase_3_results] == ["gamma"]


def test_phase3_falls_back_to_phase2_when_not_separately_scored(tmp_path: Path) -> None:
    """Phase 3 dispatch can fall back to Phase 2's shortlist when Phase 3
    wasn't separately scored. The orchestrator template uses this pattern."""
    _populate_cache(tmp_path, phase=2, intent="plan", names=["alpha", "beta"])

    state = json.loads((tmp_path / ".build-loop" / "state.json").read_text(encoding="utf-8"))
    phase_3_results = cs.read_active_capabilities(state, phase=3, fallback_phase=2)
    assert [r["name"] for r in phase_3_results] == ["alpha", "beta"]


def test_backward_compat_when_cache_empty(tmp_path: Path) -> None:
    """When the cache is empty, the consumer returns [] — dispatchers behave
    as they did before P16 (no shortlist injected)."""
    state = {"phase": "test"}  # no activeCapabilities key at all
    assert cs.read_active_capabilities(state, phase=2) == []

    state2 = {"activeCapabilities": {}}  # empty dict
    assert cs.read_active_capabilities(state2, phase=2) == []

    state3 = {"activeCapabilities": {"1": []}}  # phase missing
    assert cs.read_active_capabilities(state3, phase=2) == []


def test_backward_compat_legacy_flat_list_shape(tmp_path: Path) -> None:
    """If state.json was written by a pre-P16 build, the consumer still reads
    correctly from the legacy flat-list shape."""
    legacy_state = {
        "activeCapabilities": [
            {"phase": 1, "intent": "old", "shortlist": ["x"], "results": [{"name": "x"}]},
            {"phase": 2, "intent": "older", "shortlist": ["y", "z"],
             "results": [{"name": "y"}, {"name": "z"}]},
            {"phase": 2, "intent": "newer", "shortlist": ["w"], "results": [{"name": "w"}]},
        ]
    }
    # Most recent phase=2 entry wins.
    out = cs.read_active_capabilities(legacy_state, phase=2)
    assert [r["name"] for r in out] == ["w"]
    # Phase 3 with fallback to phase 2.
    out_fb = cs.read_active_capabilities(legacy_state, phase=3, fallback_phase=2)
    assert [r["name"] for r in out_fb] == ["w"]


def test_legacy_list_shape_migrates_on_next_write(tmp_path: Path) -> None:
    """When cache_into_state encounters the legacy flat-list shape, it
    migrates to the phase-keyed dict on the next write."""
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    legacy_state = {
        "activeCapabilities": [
            {"phase": 1, "intent": "old1", "shortlist": ["a"]},
            {"phase": 2, "intent": "old2", "shortlist": ["b"]},
        ]
    }
    state_path.write_text(json.dumps(legacy_state), encoding="utf-8")

    fake_new = {"phase": 3, "intent": "new", "results": [{"name": "c"}]}
    cs.cache_into_state(tmp_path, fake_new)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    cap = state["activeCapabilities"]
    assert isinstance(cap, dict), "must migrate to dict on next write"
    assert "1" in cap and "2" in cap and "3" in cap
    assert cap["3"][-1]["shortlist"] == ["c"]
    # Legacy entries preserved.
    assert cap["1"][0]["intent"] == "old1"


# ---------------------------------------------------------------------------
# Synthetic dispatch logger — assert the consumer is actually invoked
# ---------------------------------------------------------------------------

class _DispatchRecorder:
    """Stand-in for a subagent dispatcher; records the brief it would send."""

    def __init__(self) -> None:
        self.briefs: List[Dict[str, Any]] = []

    def dispatch(self, agent: str, brief: Dict[str, Any]) -> None:
        self.briefs.append({"agent": agent, **brief})


def _simulated_phase_3_dispatch(
    state: Dict[str, Any],
    recorder: _DispatchRecorder,
) -> None:
    """Mirrors the orchestrator's Phase 3 wiring (added in P16): read the
    cached shortlist for phase 3 (falling back to phase 2), embed it as
    `available_capabilities` in each implementer brief."""
    available = cs.read_active_capabilities(state, phase=3, fallback_phase=2)
    brief = {
        "task": "implement chunk A",
        "files_touched": ["src/a.py"],
    }
    if available:
        brief["available_capabilities"] = [
            {"name": r["name"], "kind": r.get("kind"), "score": r.get("score")}
            for r in available[:8]
        ]
    recorder.dispatch("implementer", brief)


def test_simulated_dispatch_injects_capabilities_when_cached(tmp_path: Path) -> None:
    _populate_cache(tmp_path, phase=2, intent="plan", names=["alpha", "beta"])
    _populate_cache(tmp_path, phase=3, intent="execute", names=["gamma"])
    state = json.loads((tmp_path / ".build-loop" / "state.json").read_text(encoding="utf-8"))

    recorder = _DispatchRecorder()
    _simulated_phase_3_dispatch(state, recorder)
    assert len(recorder.briefs) == 1
    brief = recorder.briefs[0]
    assert "available_capabilities" in brief
    names = [c["name"] for c in brief["available_capabilities"]]
    assert names == ["gamma"]


def test_simulated_dispatch_no_injection_when_cache_empty(tmp_path: Path) -> None:
    """Backward-compat: empty cache → no `available_capabilities` injected,
    dispatcher behaves as before P16."""
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{}", encoding="utf-8")
    state = json.loads(state_path.read_text(encoding="utf-8"))

    recorder = _DispatchRecorder()
    _simulated_phase_3_dispatch(state, recorder)
    brief = recorder.briefs[0]
    assert "available_capabilities" not in brief
