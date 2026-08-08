from __future__ import annotations

import json
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import autonomy_dashboard as dashboard


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
    latest = first.save("convergence", "two", "Updated direction")
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
    store.save("convergence", "three", "Keep evidence from all three attempts.")
    queued = store.queue("convergence")
    path = repo / queued["queued_path"]
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Choice: Quarantine at 3" in text
    assert "> Keep evidence from all three attempts." in text
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
    status, saved = _post(live_server, "/api/responses", {
        "gap_id": "bounded-related-work", "choice_id": "cap-12", "note": "Reserve capacity for discovered work."
    })
    assert status == 200 and saved["response"]["choice_id"] == "cap-12"
    status, queued = _post(live_server, "/api/actions", {"gap_id": "bounded-related-work"})
    assert status == 200
    assert (repo / queued["response"]["queued_path"]).exists()
    with urllib.request.urlopen(live_server + "/api/state", timeout=2) as response:
        reloaded = json.loads(response.read())
    assert reloaded["responses"]["bounded-related-work"]["queued_path"]


def test_http_rejects_foreign_host_header(live_server: str) -> None:
    request = urllib.request.Request(live_server + "/api/state", headers={"Host": "example.com"})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=2)
    assert error.value.code == 403


def test_html_has_semantic_controls_and_autosave_contract() -> None:
    html = (Path(__file__).resolve().parents[1] / "docs/autonomy-dashboard.html").read_text(encoding="utf-8")
    for token in ("<main", "node('fieldset')", "node('legend'", "aria-live", "prefers-reduced-motion", "scheduleSave", "Queue this decision"):
        assert token in html
    assert "innerHTML" not in html
    assert "linear-gradient" not in html


def test_non_loopback_bind_is_rejected(repo: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        dashboard.create_server(repo, "0.0.0.0", 0)
