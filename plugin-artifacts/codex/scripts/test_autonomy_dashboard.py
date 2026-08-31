from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import autonomy_dashboard as dashboard


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.fixture
def repo() -> Path:
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def test_gap_contract_is_complete_and_impact_is_four_lens() -> None:
    assert len(dashboard.DEFAULT_GAPS) == 6
    for gap in dashboard.DEFAULT_GAPS:
        assert gap["gap"] and gap["big_idea"] and gap["pain"] and gap["payoff"] and gap["why"]
        assert len(gap["options"]) >= 3
        assert sum(bool(option["recommended"]) for option in gap["options"]) == 1
        for option in gap["options"]:
            assert set(option["impact"]) == {"owner", "app", "user", "other"}


def test_response_append_log_reconstructs_latest_after_restart(repo: Path) -> None:
    first = dashboard.DecisionStore(repo)
    first.save("convergence", "three", "Initial direction")
    latest = first.save("convergence", "five", "Updated direction")
    restarted = dashboard.DecisionStore(repo)
    assert restarted.latest()["convergence"] == latest
    assert len((repo / dashboard.STORE_PATH).read_text(encoding="utf-8").splitlines()) == 2


def test_free_text_direction_saves_and_queues_without_choice(repo: Path) -> None:
    store = dashboard.DecisionStore(repo)
    saved = store.save("outcome-supervisor", "", "Use checkpoints after every commit.")
    assert saved["choice_id"] == ""
    queued = store.queue("outcome-supervisor")
    text = (repo / queued["queued_path"]).read_text(encoding="utf-8")
    assert "Choice: Free-text direction" in text
    assert "> Use checkpoints after every commit." in text


def test_queue_requires_saved_response_and_creates_agent_readable_followup(repo: Path) -> None:
    store = dashboard.DecisionStore(repo)
    with pytest.raises(ValueError, match="save a response"):
        store.queue("convergence")
    store.save("convergence", "five", "Audit at three; quarantine at five.")
    queued = store.queue("convergence")
    path = repo / queued["queued_path"]
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Choice: Audit 3 · quarantine 5" in text
    assert "> Audit at three; quarantine at five." in text
    assert "dashboard_gap_id: convergence" in text
    assert store.latest()["convergence"]["event"] == "response_queued"


def test_requeue_supersedes_prior_executable_item(repo: Path) -> None:
    store = dashboard.DecisionStore(repo)
    store.save("convergence", "three", "Initial")
    first = repo / store.queue("convergence")["queued_path"]
    store.save("convergence", "five", "Revised")
    assert not first.exists()
    assert list((repo / dashboard.SUPERSEDED_DIR).glob(first.name))
    second_event = store.queue("convergence")
    second = repo / second_event["queued_path"]
    assert not first.exists()
    assert second.exists()
    assert list((repo / dashboard.FOLLOWUP_DIR).glob("dashboard-convergence-*.md")) == [second]


def test_queue_is_idempotent_until_the_response_changes(repo: Path) -> None:
    store = dashboard.DecisionStore(repo)
    store.save("convergence", "five", "")
    first = store.queue("convergence")
    second = store.queue("convergence")
    assert second == first
    assert len(list((repo / dashboard.FOLLOWUP_DIR).glob("dashboard-convergence-*.md"))) == 1
    assert not (repo / dashboard.SUPERSEDED_DIR).exists()


def test_queue_rejects_missing_instruction_and_symlink_escape(repo: Path) -> None:
    store = dashboard.DecisionStore(repo)
    store.save("convergence", "five", "")
    queued = store.queue("convergence")
    (repo / queued["queued_path"]).unlink()
    with pytest.raises(ValueError, match="missing or ambiguous"):
        store.queue("convergence")

    escaped_repo = repo / "escaped"
    escaped_repo.mkdir()
    second = dashboard.DecisionStore(escaped_repo)
    second.save("convergence", "five", "")
    with tempfile.TemporaryDirectory() as outside_dir:
        followup = escaped_repo / dashboard.FOLLOWUP_DIR
        followup.parent.mkdir(parents=True, exist_ok=True)
        followup.symlink_to(outside_dir, target_is_directory=True)
        with pytest.raises(ValueError, match="inside the repository"):
            second.queue("convergence")
        assert not list(Path(outside_dir).iterdir())
        legacy = Path(outside_dir) / "dashboard-convergence-legacy.md"
        legacy.write_text(
            "---\nsource: autonomy-dashboard\n"
            "title: Apply autonomy decision for convergence\n---\n",
            encoding="utf-8",
        )
        with second.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "event": "response_queued", "gap_id": "convergence", "choice_id": "five",
                "note": "", "queued_path": str(followup.relative_to(escaped_repo) / legacy.name),
            }) + "\n")
        with pytest.raises(ValueError, match="inside the repository"):
            second.complete("convergence", "commit:abc; tests:pass; audit:PASS")
        assert legacy.exists()


def test_completion_requires_evidence_and_archives_live_instruction(repo: Path) -> None:
    store = dashboard.DecisionStore(repo)
    store.save("outcome-supervisor", "adopt", "")
    with pytest.raises(ValueError, match="queue the response"):
        store.complete("outcome-supervisor", "commit:abc; tests:pass; audit:PASS")
    queued = store.queue("outcome-supervisor")
    queued_path = repo / queued["queued_path"]
    with pytest.raises(ValueError, match="requires non-empty"):
        store.complete("outcome-supervisor", "")
    applied = store.complete(
        "outcome-supervisor", "commit:abc; tests:12 passed; audit:PASS", "Supervisor active"
    )
    assert applied["event"] == "response_applied"
    assert applied["queued_path"] is None
    assert applied["summary"] == "Supervisor active"
    assert not queued_path.exists()
    assert (repo / applied["applied_paths"][0]).exists()
    assert store.complete(
        "outcome-supervisor", "commit:different; tests:pass; audit:PASS"
    ) == applied


def test_completion_rejects_untrusted_or_missing_queue_path(repo: Path) -> None:
    store = dashboard.DecisionStore(repo)
    path = repo / dashboard.STORE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "event": "response_queued", "gap_id": "convergence", "choice_id": "five",
        "note": "", "queued_path": "outside.md",
    }) + "\n", encoding="utf-8")
    (repo / "outside.md").write_text("must remain", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        store.complete("convergence", "commit:abc; tests:pass; audit:PASS")
    assert (repo / "outside.md").read_text(encoding="utf-8") == "must remain"


def test_completion_rejects_forged_followup_and_archive_symlink(repo: Path) -> None:
    store = dashboard.DecisionStore(repo)
    followup = repo / dashboard.FOLLOWUP_DIR
    followup.mkdir(parents=True)
    forged = followup / "unrelated.md"
    forged.write_text("source: autonomy-dashboard\n", encoding="utf-8")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({
        "event": "response_queued", "gap_id": "convergence", "choice_id": "five",
        "note": "", "queued_path": str(forged.relative_to(repo)),
    }) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        store.complete("convergence", "commit:abc; tests:pass; audit:PASS")
    assert forged.exists()

    store.save("outcome-supervisor", "adopt", "")
    queued = repo / store.queue("outcome-supervisor")["queued_path"]
    with tempfile.TemporaryDirectory() as outside_dir:
        outside = Path(outside_dir)
        applied_link = repo / dashboard.APPLIED_DIR
        applied_link.parent.mkdir(parents=True, exist_ok=True)
        applied_link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="inside the repository"):
            store.complete("outcome-supervisor", "commit:abc; tests:pass; audit:PASS")
        assert queued.exists()
        assert not list(outside.iterdir())


def test_completion_requires_structured_validation_evidence(repo: Path) -> None:
    store = dashboard.DecisionStore(repo)
    store.save("outcome-supervisor", "adopt", "")
    store.queue("outcome-supervisor")
    for evidence in ("x", "commit:abc", "commit:abc; tests:pass; audit:"):
        with pytest.raises(ValueError, match="commit, tests, and audit"):
            store.complete("outcome-supervisor", evidence)


def test_partial_multi_item_archive_rolls_back(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = dashboard.DecisionStore(repo)
    followup = repo / dashboard.FOLLOWUP_DIR
    followup.mkdir(parents=True)
    paths = [followup / f"dashboard-convergence-legacy-{number}.md" for number in (1, 2)]
    events = []
    for path in paths:
        path.write_text(
            "---\nsource: autonomy-dashboard\n"
            "title: Apply autonomy decision for convergence\n---\n",
            encoding="utf-8",
        )
        events.append({
            "event": "response_queued", "gap_id": "convergence", "choice_id": "five",
            "note": "", "queued_path": str(path.relative_to(repo)),
        })
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    original_replace = Path.replace
    move_count = 0

    def fail_second_move(source: Path, target: Path) -> Path:
        nonlocal move_count
        move_count += 1
        if move_count == 2:
            raise OSError("forced second move failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_second_move)
    with pytest.raises(OSError, match="forced second move"):
        store.save("convergence", "three", "revision")
    assert all(path.exists() for path in paths)
    assert store.latest()["convergence"]["event"] == "response_queued"


def test_store_rejects_unknown_choice_and_oversized_note(repo: Path) -> None:
    store = dashboard.DecisionStore(repo)
    with pytest.raises(ValueError, match="select a choice"):
        store.save("convergence", "", "")
    with pytest.raises(ValueError, match="choice_id"):
        store.save("convergence", "unknown", "")
    with pytest.raises(ValueError, match="exceeds"):
        store.save("convergence", "three", "x" * (dashboard.MAX_NOTE_CHARS + 1))


@pytest.fixture
def live_server(repo: Path):
    server = dashboard.create_server(repo, "127.0.0.1", 0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _post(url: str, path: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return response.status, json.loads(response.read())


def test_live_http_save_reload_and_queue(live_server: str, repo: Path) -> None:
    with urllib.request.urlopen(live_server + "/api/state", timeout=2) as response:
        state = json.loads(response.read())
    assert state["ok"] is True
    assert len(state["gaps"]) == 6
    assert state["run"]["status"] == "idle"
    assert len(state["run"]["phases"]) == 6
    assert state["run"]["open_work"]["open_count"] == 0
    assert state["run"]["open_work"]["refresh_interval_seconds"] == 30
    status, saved = _post(live_server, "/api/responses", {
        "gap_id": "bounded-related-work", "choice_id": "adaptive", "note": "Reserve capacity for discovered work."
    })
    assert status == 200 and saved["response"]["choice_id"] == "adaptive"
    status, queued = _post(live_server, "/api/actions", {"gap_id": "bounded-related-work"})
    assert status == 200
    assert (repo / queued["response"]["queued_path"]).exists()
    with urllib.request.urlopen(live_server + "/api/state", timeout=2) as response:
        reloaded = json.loads(response.read())
    assert reloaded["responses"]["bounded-related-work"]["queued_path"]
    status, applied = _post(live_server, "/api/completions", {
        "gap_id": "bounded-related-work", "summary": "Adaptive queue active",
        "evidence": "commit:abc; tests:pass; audit:PASS",
    })
    assert status == 200
    assert applied["response"]["event"] == "response_applied"
    with urllib.request.urlopen(live_server + "/api/state", timeout=2) as response:
        completed = json.loads(response.read())
    assert completed["responses"]["bounded-related-work"]["event"] == "response_applied"


def test_old_cap_12_selection_migrates_to_adaptive_policy(repo: Path) -> None:
    path = repo / dashboard.STORE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "event": "response_queued", "gap_id": "bounded-related-work",
        "choice_id": "cap-12", "note": "", "queued_path": "old.md",
    }) + "\n", encoding="utf-8")
    latest = dashboard.DecisionStore(repo).latest()["bounded-related-work"]
    assert latest["choice_id"] == "adaptive"
    assert latest["migrated_from"] == "cap-12"


def test_http_rejects_foreign_host_header(live_server: str) -> None:
    request = urllib.request.Request(live_server + "/api/state", headers={"Host": "example.com"})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=2)
    assert error.value.code == 403


def test_shutdown_rejects_wrong_instance_token(repo: Path) -> None:
    server = dashboard.create_server(
        repo, "127.0.0.1", 0, quiet=True, instance_id="expected-instance"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(urllib.error.HTTPError) as error:
            _post(url, "/api/shutdown", {"instance_id": "wrong-instance"})
        assert error.value.code == 400
        assert thread.is_alive()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_html_has_semantic_controls_autosave_and_progressive_disclosure_contract() -> None:
    html = (Path(__file__).resolve().parents[1] / "docs/autonomy-dashboard.html").read_text(encoding="utf-8")
    for token in (
        "<main", "node('details'", "node('summary'", "node('fieldset')", "node('legend'",
        "aria-live", "prefers-reduced-motion", "scheduleSave", "Queue Decision",
        '"Avenir Next"', "--canvas: #f7f8ff", "--accent: #4f46e5", "--accent-strong: #312e81",
        "--accent-gradient: linear-gradient(135deg, #2563eb 0%, #4338ca 100%)", "min-height: 44px",
        "is-selected", "Selected policy", "Queued for Build Loop", "response_applied",
        "applied-count", "Applied · validated",
        "const saved = await save(card, gap);", "if (!saved) return;",
        'class="side-nav"', 'id="in-progress"', 'id="queued-work"', 'id="backlog"',
        'id="handoffs"', 'id="history"', 'id="loop-panels"', 'id="active-loop-nav"',
        'id="queued-groups"', 'id="backlog-groups"', 'id="handoff-list"', 'id="history-list"',
        'id="policy-panel"', "Show decisions", "Hide decisions",
        ".when-open { display: none; }", "details[open] > summary .when-closed { display: none; }",
        "details[open] > summary .when-open { display: inline; }",
        "renderWorkspace", "renderLoopPanel", "renderWorkGroups", "renderHandoffs", "renderHistory",
        "refreshRun", "setInterval(refreshRun, 5000)", "Records refresh automatically.",
        "In progress", "Queued work", "Backlog", "Handoffs", "History", "Other-agent queue connected.",
        "phaseRecords", "phaseFocus", "phase-card", "phase-location", "Record: ${phase.location}",
        "['blocked', 'active'].includes(item.status)", "Waiting for ${order.role}",
        "function sentence", "Judge: ${agent.judge ? 'Yes' : 'No'}", "Model: ${agent.model || 'Not recorded'}",
        "workspace.active_loops", "judge_records", "Verdict: ${judge.verdict || 'Not recorded'}",
        "No judge record", "Model not recorded", "Models not recorded",
        "Transfers and coordination records with dates and participants.",
        "semanticWorkspaceSignature", "['generated_at', 'refreshed_at'].includes(key)",
        "captureDynamicViewState", "restoreDynamicViewState", "data-disclosure-key", "data-focus-key",
        "openDisclosureKeys", "focus({ preventScroll: true })", "updateOpenWorkSummary",
        "--type-h1-size", "--type-h2-size", "--type-h3-size", "--type-h4-size",
        "h3, [role=\"heading\"][aria-level=\"3\"]", "h4, [role=\"heading\"][aria-level=\"4\"]",
        "body {", "overflow: hidden", "height: 100dvh", "overflow-y: auto", "scrollbar-gutter: stable",
        "class=\"nav-link\" type=\"button\" data-view=\"in-progress\"", "aria-current=\"page\"",
        "function selectWorkspace", "workspaceScrollPositions", "workspaceElement.scrollTop",
        "section.hidden = section.id !== viewId", "button.setAttribute('aria-pressed', String(selected))",
        "className: 'group-title'", "role: 'heading'", "'aria-level': '3'", "'aria-level': '4'",
        "className: 'loop-link'", "data-loop-target", "history.replaceState",
        "workspaceButtons.forEach(button => { button.onclick", "loopButton.onclick",
        "function workSourceContext", "Shared agent queue", "Executable Build Loop queue",
        ".nav-link:focus-visible", "outline: 3px solid var(--accent)",
    ):
        assert token in html
    assert '<details id="run-details" class="run-disclosure" open>' not in html
    assert '<details id="policy-panel" class="policy-panel" open>' not in html
    assert "phase ${currentPhase.number} active" not in html
    assert 'id="run-refresh" role="status"' not in html
    assert '<div class="metrics" aria-live="polite">' not in html
    assert 'id="phase-output-list"' not in html
    assert "innerHTML" not in html
    assert html.count("linear-gradient(") == 1
    assert "<script src=" not in html
    assert "<link rel=" not in html
    assert "@import" not in html
    for target in ("in-progress", "queued-work", "backlog", "handoffs", "history", "policy-panel"):
        assert f'href="#{target}"' not in html


@pytest.mark.skipif(shutil.which("ibr") is None, reason="IBR CLI is not installed")
def test_browser_preserves_navigation_and_disclosure_state_across_refreshes(live_server: str) -> None:
    started = subprocess.run(
        ["ibr", "session:start", "-w", ".nav-link", live_server],
        capture_output=True, text=True, timeout=20,
    )
    if started.returncode:
        pytest.skip(f"IBR browser unavailable: {started.stderr.strip() or started.stdout.strip()}")
    match = re.search(r"Session started: (\S+)", started.stdout)
    assert match, started.stdout
    session_id = match.group(1)

    def ibr(*args: str) -> str:
        completed = subprocess.run(
            ["ibr", *args], capture_output=True, text=True, timeout=20, check=True,
        )
        return completed.stdout

    def evaluate(script: str) -> dict:
        return json.loads(ibr("session:eval", "--json", session_id, script))

    try:
        ibr("session:press", session_id, "Tab")
        focus = evaluate("""(() => {
          const element = document.activeElement;
          const style = getComputedStyle(element);
          return {
            selected: element.getAttribute('aria-current'),
            outlineStyle: style.outlineStyle,
            outlineWidth: style.outlineWidth,
          };
        })()""")
        assert focus == {"selected": "page", "outlineStyle": "solid", "outlineWidth": "3px"}

        ibr("session:press", session_id, "Tab")
        ibr("session:press", session_id, "Enter")
        navigation = evaluate("""(() => ({
          selectedWorkspace,
          visible: [...document.querySelectorAll('.workspace-section')]
            .filter(section => !section.hidden).map(section => section.id),
          windowScroll: window.scrollY,
        }))()""")
        assert navigation == {"selectedWorkspace": "queued-work", "visible": ["queued-work"], "windowScroll": 0}

        refreshed = evaluate("""(() => {
          const projected = structuredClone(state.run);
          const template = structuredClone(projected);
          delete template.workspace;
          delete template.open_work;
          template.status = 'active';
          template.current_phase_name = 'Execute';
          template.goal = 'Browser refresh regression';
          projected.workspace.active_loops = Array.from({ length: 4 }, (_, index) => ({
            ...structuredClone(template),
            run_id: `browser-regression-${index + 1}`,
          }));
          projected.workspace.generated_at = '2026-01-01T00:00:00Z';
          projected.open_work.refreshed_at = '2026-01-01T00:00:00Z';
          renderWorkspace(projected);
          selectWorkspace('in-progress');

          const workspace = document.querySelector('.workspace');
          const disclosure = document.querySelector('.phase-card');
          disclosure.open = true;
          disclosure.querySelector('summary').focus();
          workspace.scrollTop = Math.min(180, workspace.scrollHeight - workspace.clientHeight);
          const expectedScroll = workspace.scrollTop;
          const sourceSummaryBefore = document.querySelector('#open-work-sources').textContent;

          const volatileOnly = structuredClone(projected);
          volatileOnly.workspace.generated_at = '2026-01-02T00:00:00Z';
          volatileOnly.open_work.refreshed_at = '2026-01-02T00:00:00Z';
          renderWorkspace(volatileOnly);
          const afterVolatile = document.querySelector('.phase-card');
          const volatilePreserved = {
            sameNode: afterVolatile === disclosure,
            open: afterVolatile.open,
            focused: document.activeElement === afterVolatile.querySelector('summary'),
            scroll: workspace.scrollTop === expectedScroll,
            timestampUpdated: document.querySelector('#open-work-sources').textContent !== sourceSummaryBefore,
          };

          const semanticChange = structuredClone(volatileOnly);
          semanticChange.workspace.active_loops[0].phases[0].summary += ' Updated';
          renderWorkspace(semanticChange);
          const afterSemantic = document.querySelector('.phase-card');
          return {
            volatilePreserved,
            semanticRebuilt: afterSemantic !== afterVolatile,
            semanticOpen: afterSemantic.open,
            semanticFocused: document.activeElement === afterSemantic.querySelector('summary'),
            semanticScroll: workspace.scrollTop === expectedScroll,
            semanticUpdated: afterSemantic.textContent.includes('Updated'),
            selectedWorkspace,
            windowScroll: window.scrollY,
          };
        })()""")
        assert refreshed == {
            "volatilePreserved": {
                "sameNode": True, "open": True, "focused": True,
                "scroll": True, "timestampUpdated": True,
            },
            "semanticRebuilt": True,
            "semanticOpen": True,
            "semanticFocused": True,
            "semanticScroll": True,
            "semanticUpdated": True,
            "selectedWorkspace": "in-progress",
            "windowScroll": 0,
        }
    finally:
        subprocess.run(
            ["ibr", "session:close", session_id],
            capture_output=True, text=True, timeout=20, check=False,
        )


def test_brand_gradient_keeps_white_small_text_at_wcag_aa_contrast() -> None:
    for endpoint in ("#2563eb", "#4338ca"):
        assert _contrast_ratio("#ffffff", endpoint) >= 4.5


def test_api_state_reprojects_phase_changes_without_restarting_server(live_server: str, repo: Path) -> None:
    state_path = repo / ".build-loop/state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "active": True,
        "phase": "plan",
        "execution": {"build_loop_id": "run-live", "phase": "plan"},
    }), encoding="utf-8")
    with urllib.request.urlopen(live_server + "/api/state", timeout=2) as response:
        planned = json.loads(response.read())
    assert planned["run"]["current_phase"] == "plan"

    state_path.write_text(json.dumps({
        "active": True,
        "phase": "execute",
        "execution": {"build_loop_id": "run-live", "phase": "execute"},
    }), encoding="utf-8")
    with urllib.request.urlopen(live_server + "/api/state", timeout=2) as response:
        executing = json.loads(response.read())
    assert executing["run"]["current_phase"] == "execute"


def test_non_loopback_bind_is_rejected(repo: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        dashboard.create_server(repo, "0.0.0.0", 0)


def test_dashboard_status_reports_not_started(repo: Path) -> None:
    status = dashboard.dashboard_status(repo)
    assert status["running"] is False
    assert status["reason"] == "not_started"


def test_background_lifecycle_survives_launcher_and_stops_cleanly(repo: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    try:
        started = dashboard.start_dashboard(repo, "127.0.0.1", port, quiet=True)
        assert started["started"] is True
        assert started["running"] is True
        assert started["url"] == f"http://127.0.0.1:{port}"
        assert dashboard.dashboard_status(repo)["running"] is True
    finally:
        stopped = dashboard.stop_dashboard(repo)
    assert stopped["stopped"] is True
    assert dashboard.dashboard_status(repo)["reason"] == "not_started"


def test_stale_runtime_state_is_removed_without_signaling(repo: Path) -> None:
    state_path = repo / dashboard.SERVER_STATE_PATH
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "instance_id": "dead", "pid": 999_999, "url": "http://127.0.0.1:1"
    }), encoding="utf-8")
    result = dashboard.stop_dashboard(repo)
    assert result == {"stopped": False, "reason": "not_running"}
    assert not state_path.exists()
