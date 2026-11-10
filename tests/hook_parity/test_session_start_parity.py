# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Parity cases for the SessionStart hooks.

``test_legacy_contract`` runs the five shell hooks twice per case: the two
runs must agree (so the comparison itself is stable) and meet the case's
expectation. ``test_bl_matches_legacy`` runs ``bl hook session-start`` on the
same case and requires the identical result.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from tests.hook_parity import session_start_harness as h
from tests.hook_parity.session_start_harness import Case, event

TRUST = "Codex hook trust"
TOOL_STATE = "Treat these as tool state"
CLOSEOUT = "build-loop closeout OWED"


def context(snap) -> str:
    return h.registration.context_of(snap.stdout) or ""


def scripts(snap) -> list[str]:
    return sorted(os.path.basename(s[0][0]) for s in snap.spawns)


def expect_contexts(*needles: str, absent: tuple[str, ...] = ()):
    """The merged context holds ``needles`` in this order."""
    def check(snap):
        text = context(snap)
        positions = [text.find(n) for n in needles]
        assert all(p >= 0 for p in positions), (needles, text)
        assert positions == sorted(positions), (needles, positions)
        for n in absent:
            assert n not in text, (n, text)
    return check


def expect_background(snap):
    assert snap.background == ["session_probe.py", "hooks.py", "peer_collision.py"], snap.background


def both(*checks):
    def check(snap):
        for c in checks:
            c(snap)
    return check


def pending_marker(world):
    d = world.proj / ".build-loop" / "closeout-pending"
    d.mkdir()
    (d / "run-1.md").write_text("---\nrun_id: run-1\ncloseout_incomplete: true\n---\nowed\n")


def architecture(age: float | None):
    def setup(world):
        d = world.proj / ".build-loop" / "architecture"
        d.mkdir()
        if age is not None:
            m = d / "manifest.json"
            m.write_text("{}")
            os.utime(m, (time.time() - age, time.time() - age))
    return setup


def old_dirs(world):
    """`.build-loop/` and `.rally/` that predate the session."""
    (world.proj / ".rally").mkdir()
    (world.proj / ".rally" / "keep").write_text("")


def no_arch_scan(snap):
    assert "_arch_scan_bg.py" not in scripts(snap)


def arch_scan(snap):
    assert scripts(snap).count("_arch_scan_bg.py") == 1


def no_rally(snap):
    assert "hooks.py" not in scripts(snap) and snap.background == []


CASES = [
    Case("project_claude", expect=both(expect_contexts(TOOL_STATE, TRUST), expect_background, no_arch_scan)),
    Case("project_existing_rally_room", setup=old_dirs, expect=expect_background),
    Case("closeout_owed_sits_between_tool_state_and_trust", setup=pending_marker,
         expect=expect_contexts(TOOL_STATE, CLOSEOUT, TRUST)),
    Case("not_opted_in", kind="plain", expect=both(expect_contexts(TOOL_STATE, TRUST, absent=(CLOSEOUT,)),
                                                    expect_background)),
    Case("non_git_directory", kind="nogit", expect=expect_contexts(TRUST, absent=(TOOL_STATE,))),
    Case("non_git_launch_dir_without_project_dir", kind="nogit", project_dir_set=False, expect=no_rally),
    Case("git_launch_dir_without_project_dir", project_dir_set=False, expect=expect_background),
    Case("linked_worktree", kind="worktree", expect=both(expect_contexts(TOOL_STATE, TRUST), expect_background)),
    Case("rally_native_cache_fresh", cache_age=5, expect=expect_background),
    Case("rally_native_cache_stale", cache_age=90_000, expect=expect_background),
    Case("rally_not_native", rally=False, expect=expect_background),
    Case("codex_event", stdin=event(model="gpt-5.5", permission_mode="default", cwd="/x", transcript_path=None),
         env={"CODEX_THREAD_ID": "thread-9"}, project_dir_set=False,
         expect=lambda s: any("codex" in a for rec in s.spawns for a in rec[0])),
    Case("named_tool", env={"RALLY_TOOL_ID": "easy-terminal:pane-7"}, expect=expect_background),
    Case("event_without_session_id", stdin=b'{"hook_event_name": "SessionStart"}', expect=expect_background),
    Case("empty_stdin", stdin=b"", expect=expect_background),
    Case("stdin_not_json", stdin=b"not json\n\n", expect=expect_background),
    Case("session_id_needs_cleaning", stdin=event(session_id=" My Session/ID\t1 "), expect=expect_background),
    Case("architecture_manifest_missing", setup=architecture(None), expect=arch_scan),
    Case("architecture_manifest_stale", setup=architecture(90_000), expect=arch_scan),
    Case("architecture_manifest_fresh", setup=architecture(60), expect=no_arch_scan),
    Case("tool_state_times_out", timing=True, env={"HARNESS_SLOW": "tool_state_paths.py"},
         expect=expect_contexts(TRUST, absent=(TOOL_STATE,))),
    Case("rally_times_out", timing=True, env={"HARNESS_SLOW": "hooks.py"},
         expect=both(expect_contexts(TOOL_STATE, TRUST), lambda s: s.background == [] or pytest.fail(str(s.background)))),
    Case("trust_check_times_out", timing=True, env={"HARNESS_SLOW": "codex_hook_trust_check.py"},
         expect=expect_contexts(TOOL_STATE, absent=(TRUST,))),
    Case("short_deadline_from_env", timing=True, env={"HARNESS_SLOW": "tool_state_paths.py", "HARNESS_SLOW_SECONDS": "1.1",
                                         "BUILD_LOOP_TOOL_STATE_DEADLINE_MS": "800"},
         expect=expect_contexts(TRUST, absent=(TOOL_STATE,))),
    Case("tool_state_fails", env={"HARNESS_FAIL": "tool_state_paths.py"}, expect=expect_contexts("partial", TRUST)),
    Case("rally_fails", env={"HARNESS_FAIL": "hooks.py"}, expect=both(expect_contexts(TOOL_STATE, TRUST),
                                                                       expect_background)),
    Case("closeout_fails", setup=pending_marker, env={"HARNESS_FAIL": "stop_closeout.py"},
         expect=expect_contexts(TOOL_STATE, TRUST, absent=(CLOSEOUT,))),
    Case("trust_check_fails", env={"HARNESS_FAIL": "codex_hook_trust_check.py"},
         expect=expect_contexts(TOOL_STATE, "partial")),
    Case("hooks_switched_off", env={"BUILD_LOOP_HOOKS": "off"}, expect=expect_contexts(TOOL_STATE, TRUST)),
    Case("python3_missing", path="no-python", handoff=True,
         expect=lambda s: s.stdout == b"{}" or pytest.fail(repr(s.stdout))),
    Case("perl_missing", path="no-perl", handoff=True,
         expect=lambda s: s.stdout == b"{}" or pytest.fail(repr(s.stdout))),
    Case("session_id_not_a_string", stdin=event(session_id=42), handoff=True, expect=expect_background),
    Case("rally_foreground_diagnosis", env={"BUILD_LOOP_RALLY_SESSION_START_FOREGROUND": "1"}, handoff=True,
         expect=expect_background),
]
IDS = [c.name for c in CASES]


def diff(a, b) -> str:
    lines = []
    for key in a:
        if a[key] != b[key]:
            lines.append(f"{key}:\n  legacy: {a[key]!r}\n  other:  {b[key]!r}")
    return "\n".join(lines)


def skip_if_loaded(case, *snaps):
    if not case.timing and any(s.slow for s in snaps):
        pytest.skip(f"machine too loaded for a stable comparison (load {os.getloadavg()[0]:.0f})")


@pytest.fixture(scope="module")
def legacy(tmp_path_factory):
    """Each case's legacy result, computed once."""
    cache: dict[str, h.Snapshot] = {}
    base = tmp_path_factory.mktemp("legacy")

    def get(case: Case) -> h.Snapshot:
        if case.name not in cache:
            cache[case.name] = h.run_stable("legacy", case, base / case.name)
        return cache[case.name]
    return get


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_legacy_contract(case, legacy, tmp_path):
    first = legacy(case)
    again = h.run_stable("legacy", case, tmp_path / "again")
    skip_if_loaded(case, first, again)
    assert first.rc == 0
    assert first.comparable() == again.comparable(), diff(first.comparable(), again.comparable())
    if case.expect:
        assert case.expect(first) is not False


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_bl_matches_legacy(case, legacy, tmp_path):
    if not h.BL.exists():
        pytest.skip(f"{h.BL} not built")
    want = legacy(case)
    got = h.run_stable("bl", case, tmp_path / "bl")
    skip_if_loaded(case, want, got)
    assert (b"bl: fallback:" in got.stderr) == case.handoff, got.stderr
    assert want.comparable() == got.comparable(), diff(want.comparable(), got.comparable())


def test_swapped_outputs_change_the_expected_stdout(legacy):
    """The comparison is order-sensitive: the same hook outputs merged in a
    different order are a different expected stdout."""
    snap = legacy(CASES[0])
    swapped = list(snap.parts)
    swapped[0], swapped[4] = swapped[4], swapped[0]
    assert h.registration.context_of(snap.parts[0]) and h.registration.context_of(snap.parts[4])
    assert h.registration.merge(swapped) != h.registration.merge(snap.parts)



LAUNCHER_CASES = [c for c in CASES if c.name in (
    "project_claude", "closeout_owed_sits_between_tool_state_and_trust", "not_opted_in", "tool_state_fails")]


@pytest.mark.parametrize("state", ["absent", "stale", "off", "fresh"])
@pytest.mark.parametrize("case", LAUNCHER_CASES, ids=[c.name for c in LAUNCHER_CASES])
def test_launcher_registered_commands(case, state, tmp_path):
    """The registered commands, run as the host runs them. Binary absent,
    stale or switched off: the native entry prints nothing and the five
    legacy entries behave exactly as the old commands. Fresh: the native
    entry prints the merged context and the legacy entries print nothing."""
    if not h.BL.exists():
        pytest.skip(f"{h.BL} not built")
    want = h.run_stable("legacy", case, tmp_path / "legacy", plugin=state)
    got = h.run_stable("registered", case, tmp_path / "registered", plugin=state)
    skip_if_loaded(case, want, got)
    native, legacy_parts = got.parts[0], got.parts[1:]
    if state == "fresh":
        assert all(p == b"" for p in legacy_parts), legacy_parts
        assert native.startswith(b"{")
    else:
        assert native == b""
        assert [h.registration.context_of(p) for p in legacy_parts] == \
            [h.registration.context_of(p) for p in want.parts]
    assert want.comparable() == got.comparable(), diff(want.comparable(), got.comparable())


def test_registration_is_generated():
    assert h.registration.main(["--check"]) == 0
