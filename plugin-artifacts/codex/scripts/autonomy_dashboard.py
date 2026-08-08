#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Loopback-only dashboard for durable Build Loop autonomy decisions."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_io import LockedFile, atomic_write_bytes  # noqa: E402

MAX_BODY_BYTES = 64 * 1024
MAX_NOTE_CHARS = 8_000
STORE_PATH = Path(".build-loop/autonomy-dashboard/responses.jsonl")
FOLLOWUP_DIR = Path(".build-loop/followup")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

DEFAULT_GAPS: tuple[dict[str, Any], ...] = (
    {
        "id": "outcome-supervisor",
        "priority": "P0",
        "gap": "Prose describes autonomy; no supervisor owns the run.",
        "big_idea": "One supervisor should turn intent into continuous, restart-safe work.",
        "pain": ["A host can interpret flags differently.", "A restart can lose the active route.", "The owner must reconnect steps that the system already knows."],
        "payoff": ["One initializer resolves mode and run state.", "Checkpoints preserve the next action.", "The loop resumes without repeating completed chunks."],
        "why": "A deterministic state machine gives every host the same next action from the same evidence.",
        "options": [
            {"id": "adopt", "label": "Adopt supervisor", "recommended": True, "impact": {"owner": "Fewer restarts and status prompts.", "app": "One source of runtime state.", "user": "Faster delivery of complete changes.", "other": "Requires supervisor wiring at phase boundaries."}},
            {"id": "observe", "label": "Observe first", "recommended": False, "impact": {"owner": "Receives comparisons before enforcement.", "app": "Keeps current routing during measurement.", "user": "No immediate behavior change.", "other": "Autonomy gaps remain until promotion."}},
            {"id": "defer", "label": "Defer", "recommended": False, "impact": {"owner": "Continues manual recovery.", "app": "Keeps host-dependent behavior.", "user": "Longer time to complete broad work.", "other": "No migration cost now."}},
        ],
    },
    {
        "id": "bounded-related-work",
        "priority": "P0",
        "gap": "Open queues can expand faster than a run can finish.",
        "big_idea": "Snapshot a small, intent-aligned queue and finish it before admitting more work.",
        "pain": ["New arrivals move the finish line.", "Large proposal pools consume review time.", "Unrelated work can enter through broad keywords."],
        "payoff": ["Each run has a finite manifest.", "Related discoveries still complete in the same run.", "Later arrivals wait for the next manifest."],
        "why": "Bounded admission preserves the user's outcome while allowing useful discoveries to extend the plan.",
        "options": [
            {"id": "cap-12", "label": "Cap at 12", "recommended": True, "impact": {"owner": "Predictable batches with useful breadth.", "app": "Intent scoring selects the manifest.", "user": "Related fixes land together.", "other": "Excess aligned items defer automatically."}},
            {"id": "cap-6", "label": "Cap at 6", "recommended": False, "impact": {"owner": "Shorter review batches.", "app": "More queue cycles.", "user": "Smaller releases.", "other": "More related work waits."}},
            {"id": "manual", "label": "Manual admission", "recommended": False, "impact": {"owner": "Chooses every item.", "app": "No automatic manifest.", "user": "Related fixes may split across runs.", "other": "Highest intervention cost."}},
        ],
    },
    {
        "id": "convergence",
        "priority": "P0",
        "gap": "Repeated verdicts can consume the full run without changing evidence.",
        "big_idea": "Three identical verdicts should quarantine the item and keep the run moving.",
        "pain": ["One item can monopolize the budget.", "Retries repeat the same reasoning.", "Other valid work waits behind a stuck item."],
        "payoff": ["The run advances after three identical outcomes.", "The stuck item retains evidence for follow-up.", "Failures become visible patterns for learning."],
        "why": "A persisted per-item counter converts a written limit into an enforceable route.",
        "options": [
            {"id": "three", "label": "Quarantine at 3", "recommended": True, "impact": {"owner": "Gets a concise blocked-item report.", "app": "Prevents infinite item loops.", "user": "Other fixes continue shipping.", "other": "A difficult item may need a later focused run."}},
            {"id": "two", "label": "Quarantine at 2", "recommended": False, "impact": {"owner": "Gets faster escalation.", "app": "Uses less retry evidence.", "user": "More issues defer early.", "other": "Lower compute use."}},
            {"id": "five", "label": "Quarantine at 5", "recommended": False, "impact": {"owner": "Allows deeper automated recovery.", "app": "Spends more time per stuck item.", "user": "Other work waits longer.", "other": "Higher compute use."}},
        ],
    },
    {
        "id": "preflight-learning",
        "priority": "P1",
        "gap": "Long work discovers missing context after execution starts.",
        "big_idea": "Ask consequential questions once; assume and validate every reversible detail.",
        "pain": ["Late questions break unattended work.", "Blanket assumptions can change product direction.", "Every task starts with the same generic checklist."],
        "payoff": ["Production, irreversible, and major user choices surface early.", "Reversible gaps receive explicit assumptions and tests.", "Run history adapts preflight to the task shape."],
        "why": "Task profiles connect observed duration, discoveries, completion, and interventions to future supervision.",
        "options": [
            {"id": "learn", "label": "Learn by task shape", "recommended": True, "impact": {"owner": "Answers fewer repeated questions.", "app": "Preflight changes from run evidence.", "user": "Fewer mid-build pauses.", "other": "History stays local to the repo."}},
            {"id": "fixed", "label": "Use fixed checklist", "recommended": False, "impact": {"owner": "Gets consistent prompts.", "app": "Ignores observed task patterns.", "user": "Some avoidable pauses remain.", "other": "Simpler policy."}},
            {"id": "ask-all", "label": "Ask every gap", "recommended": False, "impact": {"owner": "Controls every assumption.", "app": "Stops before most long runs.", "user": "Delivery depends on availability.", "other": "No adaptive learning."}},
        ],
    },
    {
        "id": "related-issue-routing",
        "priority": "P1",
        "gap": "Discovered issues lack one route from evidence to action.",
        "big_idea": "Execute related, reversible, testable issues; explain every decision that remains.",
        "pain": ["Agents flag fixable issues instead of fixing them.", "Decision requests omit impact.", "Unrelated cleanup can expand scope."],
        "payoff": ["Safe related issues complete automatically.", "Unproven or unrelated issues become follow-up records.", "Consequential choices name options and four impact lenses."],
        "why": "A MECE route makes execute, follow-up, and decision outcomes predictable.",
        "options": [
            {"id": "execute-related", "label": "Execute related work", "recommended": True, "impact": {"owner": "Reviews outcomes instead of micro-approvals.", "app": "Receives complete systemic fixes.", "user": "Encounters fewer adjacent defects.", "other": "Requires deterministic validation."}},
            {"id": "followup-only", "label": "File follow-ups", "recommended": False, "impact": {"owner": "Approves a later task.", "app": "Keeps current scope narrow.", "user": "Adjacent defects remain longer.", "other": "Lower current-run risk."}},
            {"id": "ask-first", "label": "Ask before action", "recommended": False, "impact": {"owner": "Controls each discovery.", "app": "Pauses on safe work.", "user": "Completion slows when the owner is absent.", "other": "Highest coordination load."}},
        ],
    },
    {
        "id": "resource-backpressure",
        "priority": "P2",
        "gap": "Static fan-out cannot react to live provider and machine pressure.",
        "big_idea": "Reduce concurrency when the provider or machine shows stress; recover only after evidence improves.",
        "pain": ["Repeated 429s waste retries.", "Memory, disk, and thermal pressure can destabilize workers.", "Cost can rise after useful work plateaus."],
        "payoff": ["Provider errors slow new admissions.", "Resource ceilings protect the host.", "Recovery ramps gradually instead of oscillating."],
        "why": "Feedback-driven backpressure uses observed signals rather than a fixed worker count.",
        "options": [
            {"id": "adaptive", "label": "Adaptive backpressure", "recommended": True, "impact": {"owner": "Gets steadier unattended runs.", "app": "Adjusts concurrency from live signals.", "user": "Receives fewer partial results.", "other": "Needs provider and host telemetry."}},
            {"id": "hard-ceilings", "label": "Hard ceilings", "recommended": False, "impact": {"owner": "Sets simple resource limits.", "app": "Cannot reclaim capacity dynamically.", "user": "Stable but potentially slower delivery.", "other": "Lower implementation complexity."}},
            {"id": "current", "label": "Keep current fan-out", "recommended": False, "impact": {"owner": "Keeps existing behavior.", "app": "Uses CPU/token heuristics only.", "user": "Long runs remain sensitive to live pressure.", "other": "No new telemetry."}},
        ],
    },
)

GAPS_BY_ID = {gap["id"]: gap for gap in DEFAULT_GAPS}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with LockedFile(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class DecisionStore:
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir.resolve()
        self.path = self.workdir / STORE_PATH

    def latest(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        latest: dict[str, dict[str, Any]] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("gap_id") in GAPS_BY_ID:
                latest[event["gap_id"]] = event
        return latest

    def save(self, gap_id: str, choice_id: str, note: str) -> dict[str, Any]:
        gap = GAPS_BY_ID.get(gap_id)
        if gap is None:
            raise ValueError("unknown gap_id")
        allowed = {option["id"] for option in gap["options"]}
        if choice_id and choice_id not in allowed:
            raise ValueError("choice_id is not valid for this gap")
        if len(note) > MAX_NOTE_CHARS:
            raise ValueError(f"note exceeds {MAX_NOTE_CHARS} characters")
        if not choice_id and not note.strip():
            raise ValueError("select a choice or enter direction")
        event = {"event": "response_saved", "gap_id": gap_id, "choice_id": choice_id, "note": note, "saved_at": _now(), "queued_path": None}
        _append_event(self.path, event)
        return event

    def queue(self, gap_id: str) -> dict[str, Any]:
        latest = self.latest().get(gap_id)
        if latest is None or latest.get("event") not in {"response_saved", "response_queued"}:
            raise ValueError("save a response before queuing it")
        gap = GAPS_BY_ID[gap_id]
        choice = next((option for option in gap["options"] if option["id"] == latest["choice_id"]), None)
        if choice is None:
            choice = {
                "label": "Free-text direction",
                "impact": {
                    "owner": "The written direction controls this follow-up.",
                    "app": "The agent maps the direction to current repository evidence.",
                    "user": "Impact depends on the recorded direction.",
                    "other": "Normal autonomy and validation gates remain active.",
                },
            }
        queue_dir = self.workdir / FOLLOWUP_DIR
        queue_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = queue_dir / f"dashboard-{gap_id}-{stamp}.md"
        suffix = 1
        while path.exists():
            path = queue_dir / f"dashboard-{gap_id}-{stamp}-{suffix}.md"
            suffix += 1
        note_lines = str(latest.get("note") or "No additional direction.").splitlines() or ["No additional direction."]
        impact_lines = [f"- {name.title()}: {value}" for name, value in choice["impact"].items()]
        content = "\n".join([
            "---", f"title: Apply autonomy decision for {gap_id}", f"created: {datetime.now(timezone.utc).date().isoformat()}",
            "source: autonomy-dashboard", "classify: SAFE", "status: open", "---", "", "## Decision", "",
            f"Gap: {gap['gap']}", f"Choice: {choice['label']}", "", "## Direction", "",
            *[f"> {line}" for line in note_lines], "", "## Impact", "", *impact_lines, "", "## Acceptance", "",
            "- Re-check this decision against the live repository and current intent.",
            "- Apply the selected policy through the canonical Build Loop mechanism.",
            "- Validate the affected behavior and record the evidence.", "",
        ])
        atomic_write_bytes(path, content.encode())
        event = {**latest, "event": "response_queued", "queued_at": _now(), "queued_path": str(path.relative_to(self.workdir))}
        _append_event(self.path, event)
        return event

    def state(self) -> dict[str, Any]:
        return {"big_idea": "Build Loop should finish related work by default and interrupt you only when your decision changes the outcome.", "gaps": DEFAULT_GAPS, "responses": self.latest()}


def make_handler(workdir: Path, html_path: Path) -> type[BaseHTTPRequestHandler]:
    store = DecisionStore(workdir)

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "BuildLoopAutonomy/1"

        def log_message(self, fmt: str, *args: object) -> None:
            if getattr(self.server, "quiet", False):
                return
            super().log_message(fmt, *args)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _request_allowed(self) -> bool:
            host = urlparse("//" + (self.headers.get("Host") or "")).hostname
            if host not in LOOPBACK_HOSTS:
                return False
            origin = self.headers.get("Origin")
            if origin:
                origin_host = urlparse(origin).hostname
                if origin_host not in LOOPBACK_HOSTS:
                    return False
            return True

        def _payload(self) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length or "0")
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 1 or length > MAX_BODY_BYTES:
                raise ValueError(f"request body must be 1..{MAX_BODY_BYTES} bytes")
            try:
                payload = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError("request body must be a JSON object") from exc
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            return payload

        def do_GET(self) -> None:  # noqa: N802
            if not self._request_allowed():
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "loopback requests only"})
                return
            if self.path == "/api/state":
                self._json(HTTPStatus.OK, {"ok": True, **store.state()})
                return
            if self.path not in {"/", "/index.html"}:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                return
            body = html_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if not self._request_allowed():
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "loopback requests only"})
                return
            try:
                payload = self._payload()
                if self.path == "/api/responses":
                    event = store.save(str(payload.get("gap_id") or ""), str(payload.get("choice_id") or ""), str(payload.get("note") or ""))
                elif self.path == "/api/actions":
                    event = store.queue(str(payload.get("gap_id") or ""))
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                    return
            except (OSError, ValueError, TimeoutError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return
            self._json(HTTPStatus.OK, {"ok": True, "response": event})

    return DashboardHandler


def create_server(workdir: Path, host: str, port: int, *, quiet: bool = False) -> ThreadingHTTPServer:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("dashboard host must be loopback")
    html_path = Path(__file__).resolve().parents[1] / "docs" / "autonomy-dashboard.html"
    server = ThreadingHTTPServer((host, port), make_handler(workdir.resolve(), html_path))
    server.quiet = quiet  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Build Loop autonomy dashboard")
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--print-state", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    workdir = Path(args.workdir).resolve()
    if args.print_state:
        print(json.dumps(DecisionStore(workdir).state(), indent=2, sort_keys=True))
        return 0
    try:
        server = create_server(workdir, args.host, args.port, quiet=args.quiet)
    except (OSError, ValueError) as exc:
        print(f"autonomy_dashboard: {exc}", file=sys.stderr)
        return 2
    print(f"Autonomy dashboard: http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
