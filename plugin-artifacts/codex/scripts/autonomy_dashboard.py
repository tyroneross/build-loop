#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Loopback-only dashboard for durable Build Loop autonomy decisions."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_io import LockedFile, atomic_write_bytes  # noqa: E402
from dashboard_projection import build_run_projection  # noqa: E402

MAX_BODY_BYTES = 64 * 1024
MAX_NOTE_CHARS = 8_000
STORE_PATH = Path(".build-loop/autonomy-dashboard/responses.jsonl")
SERVER_STATE_PATH = Path(".build-loop/autonomy-dashboard/server.json")
SERVER_LOG_PATH = Path(".build-loop/autonomy-dashboard/server.log")
FOLLOWUP_DIR = Path(".build-loop/followup")
APPLIED_DIR = Path(".build-loop/autonomy-dashboard/applied")
SUPERSEDED_DIR = Path(".build-loop/autonomy-dashboard/superseded")
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
        "big_idea": "Let the supervisor size a finite, intent-aligned manifest from the task and live capacity.",
        "pain": ["New arrivals move the finish line.", "Large proposal pools consume review time.", "Unrelated work can enter through broad keywords."],
        "payoff": ["Each run still has a finite manifest.", "Task shape and evidence determine useful breadth.", "Later arrivals wait for the next manifest."],
        "why": "Adaptive bounded admission changes batch size without moving the finish line.",
        "options": [
            {"id": "adaptive", "label": "Supervisor sets batch", "recommended": True, "impact": {"owner": "Gets finite batches sized to the work.", "app": "Uses task shape, history, and live capacity.", "user": "Related fixes land together without arbitrary breadth.", "other": "The 150 absolute ceiling remains binding."}},
            {"id": "conservative", "label": "Prefer small batches", "recommended": False, "impact": {"owner": "Gets shorter review batches.", "app": "Ramps capacity more slowly.", "user": "Receives smaller releases.", "other": "More aligned work waits."}},
            {"id": "manual", "label": "Manual admission", "recommended": False, "impact": {"owner": "Chooses every item.", "app": "No automatic manifest.", "user": "Related fixes may split across runs.", "other": "Highest intervention cost."}},
        ],
    },
    {
        "id": "convergence",
        "priority": "P0",
        "gap": "Repeated verdicts can consume the full run without changing evidence.",
        "big_idea": "Audit the third unresolved repeat; quarantine the fifth and keep the run moving.",
        "pain": ["One item can monopolize the budget.", "Retries repeat the same reasoning.", "Other valid work waits behind a stuck item."],
        "payoff": ["An independent auditor challenges the third repeat.", "The fifth repeat quarantines with full evidence.", "Resolved issues reset the counter."],
        "why": "A persisted counter and mandatory audit prevent both infinite retries and premature quarantine.",
        "options": [
            {"id": "five", "label": "Audit 3 · quarantine 5", "recommended": True, "impact": {"owner": "Gets an independent challenge before deferral.", "app": "Requires audit before attempt four.", "user": "Difficult fixes get evidence without blocking forever.", "other": "Quarantine retains all five attempts."}},
            {"id": "three", "label": "Quarantine at 3", "recommended": False, "impact": {"owner": "Gets faster deferral.", "app": "Uses less recovery evidence.", "user": "More difficult issues defer early.", "other": "Lower compute use."}},
            {"id": "manual-audit", "label": "Choose each audit", "recommended": False, "impact": {"owner": "Controls each escalation.", "app": "Waits at repeat boundaries.", "user": "Delivery depends on owner availability.", "other": "Highest intervention cost."}},
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
CHOICE_MIGRATIONS = {("bounded-related-work", "cap-12"): "adaptive"}


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
        self.operation_lock = self.path.with_name("operations")

    def _events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("gap_id") in GAPS_BY_ID:
                events.append(event)
        return events

    def latest(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for raw_event in self._events():
            event = dict(raw_event)
            migrated = CHOICE_MIGRATIONS.get((event["gap_id"], event.get("choice_id")))
            if migrated:
                event["choice_id"] = migrated
                event["migrated_from"] = "cap-12"
            latest[event["gap_id"]] = event
        return latest

    def _queued_paths(self, gap_id: str) -> list[Path]:
        queue_dir = (self.workdir / FOLLOWUP_DIR).resolve()
        try:
            queue_dir.relative_to(self.workdir)
        except ValueError as exc:
            raise ValueError("dashboard queue directory must stay inside the repository") from exc
        paths: list[Path] = []
        for event in self._events():
            if event.get("gap_id") != gap_id or event.get("event") != "response_queued":
                continue
            value = event.get("queued_path")
            if not isinstance(value, str) or not value:
                continue
            path = (self.workdir / value).resolve()
            try:
                path.relative_to(queue_dir)
            except ValueError:
                continue
            if path.is_file() and self._is_dashboard_item(path, gap_id) and path not in paths:
                paths.append(path)
        return paths

    @staticmethod
    def _is_dashboard_item(path: Path, gap_id: str) -> bool:
        if not path.name.startswith(f"dashboard-{gap_id}-") or path.suffix != ".md":
            return False
        text = path.read_text(encoding="utf-8", errors="replace")[:4_000]
        if "source: autonomy-dashboard" not in text:
            return False
        declared = f"dashboard_gap_id: {gap_id}"
        legacy_title = f"title: Apply autonomy decision for {gap_id}"
        return declared in text or legacy_title in text

    def _archive_queued(self, gap_id: str, directory: Path) -> list[tuple[Path, Path]]:
        sources = self._queued_paths(gap_id)
        if not sources:
            return []
        destination_dir = self._contained_directory(directory)
        moved: list[tuple[Path, Path]] = []
        try:
            for source in sources:
                destination = destination_dir / source.name
                suffix = 1
                while destination.exists():
                    destination = destination_dir / f"{source.stem}-{suffix}{source.suffix}"
                    suffix += 1
                source.replace(destination)
                moved.append((source, destination))
        except Exception:
            for source, destination in reversed(moved):
                destination.replace(source)
            raise
        return moved

    def _contained_directory(self, directory: Path) -> Path:
        requested_dir = self.workdir / directory
        try:
            requested_dir.parent.resolve().relative_to(self.workdir)
        except ValueError as exc:
            raise ValueError("dashboard storage directory must stay inside the repository") from exc
        requested_dir.mkdir(parents=True, exist_ok=True)
        resolved = requested_dir.resolve()
        try:
            resolved.relative_to(self.workdir)
        except ValueError as exc:
            raise ValueError("dashboard storage directory must stay inside the repository") from exc
        return resolved

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
        with LockedFile(self.operation_lock):
            moved: list[tuple[Path, Path]] = []
            try:
                moved = self._archive_queued(gap_id, SUPERSEDED_DIR)
                _append_event(self.path, event)
            except Exception:
                for source, destination in reversed(moved):
                    destination.replace(source)
                raise
        return event

    def queue(self, gap_id: str) -> dict[str, Any]:
        with LockedFile(self.operation_lock):
            latest = self.latest().get(gap_id)
            if latest is None or latest.get("event") not in {"response_saved", "response_queued"}:
                raise ValueError("save a response before queuing it")
            if latest.get("event") == "response_queued":
                live = self._queued_paths(gap_id)
                value = latest.get("queued_path")
                current = (self.workdir / value).resolve() if isinstance(value, str) else None
                if current is not None and live == [current]:
                    return latest
                raise ValueError("queued follow-up is missing or ambiguous; save the response again before requeueing")
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
            queue_dir = self._contained_directory(FOLLOWUP_DIR)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            path = queue_dir / f"dashboard-{gap_id}-{stamp}.md"
            suffix = 1
            while path.exists():
                path = queue_dir / f"dashboard-{gap_id}-{stamp}-{suffix}.md"
                suffix += 1
            note_lines = str(latest.get("note") or "No additional direction.").splitlines() or ["No additional direction."]
            impact_lines = [f"- {name.title()}: {value}" for name, value in choice["impact"].items()]
            content = "\n".join([
                "---", f"title: Apply autonomy decision for {gap_id}", f"created: {datetime.now(timezone.utc).date().isoformat()}",
                "source: autonomy-dashboard", f"dashboard_gap_id: {gap_id}", "classify: SAFE", "status: open", "---", "", "## Decision", "",
                f"Gap: {gap['gap']}", f"Choice: {choice['label']}", "", "## Direction", "",
                *[f"> {line}" for line in note_lines], "", "## Impact", "", *impact_lines, "", "## Acceptance", "",
                "- Re-check this decision against the live repository and current intent.",
                "- Apply the selected policy through the canonical Build Loop mechanism.",
                "- Validate the affected behavior and record the evidence.",
                "- After validation, mark this dashboard decision applied with the evidence.", "",
            ])
            atomic_write_bytes(path, content.encode())
            moved: list[tuple[Path, Path]] = []
            try:
                moved = self._archive_queued(gap_id, SUPERSEDED_DIR)
                event = {**latest, "event": "response_queued", "queued_at": _now(), "queued_path": str(path.relative_to(self.workdir))}
                _append_event(self.path, event)
            except Exception:
                path.unlink(missing_ok=True)
                for source, destination in reversed(moved):
                    destination.replace(source)
                raise
            return event

    def complete(self, gap_id: str, evidence: str, summary: str = "") -> dict[str, Any]:
        evidence = evidence.strip()
        required = ("commit", "tests", "audit")
        evidence_parts = {
            key.strip().lower(): value.strip()
            for part in evidence.split(";")
            if ":" in part
            for key, value in [part.split(":", 1)]
        }
        missing = [key for key in required if not evidence_parts.get(key)]
        if missing:
            raise ValueError(f"completion evidence requires non-empty commit, tests, and audit fields; missing: {', '.join(missing)}")
        if len(evidence) > MAX_NOTE_CHARS or len(summary) > MAX_NOTE_CHARS:
            raise ValueError(f"completion text exceeds {MAX_NOTE_CHARS} characters")
        with LockedFile(self.operation_lock):
            latest = self.latest().get(gap_id)
            if latest is None:
                raise ValueError("unknown or unsaved gap_id")
            if latest.get("event") == "response_applied":
                return latest
            if latest.get("event") != "response_queued":
                raise ValueError("queue the response before marking it applied")
            moved: list[tuple[Path, Path]] = []
            try:
                moved = self._archive_queued(gap_id, APPLIED_DIR)
                if not moved:
                    raise ValueError("queued follow-up is missing; cannot prove completion")
                event = {
                    **latest,
                    "event": "response_applied",
                    "applied_at": _now(),
                    "applied_paths": [str(destination.relative_to(self.workdir)) for _, destination in moved],
                    "evidence": evidence,
                    "summary": summary.strip(),
                    "queued_path": None,
                }
                _append_event(self.path, event)
            except Exception:
                for source, destination in reversed(moved):
                    destination.replace(source)
                raise
            return event

    def state(self) -> dict[str, Any]:
        return {
            "big_idea": "Build Loop should finish related work by default and interrupt you only when your decision changes the outcome.",
            "gaps": DEFAULT_GAPS,
            "responses": self.latest(),
            "run": build_run_projection(self.workdir),
        }


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
            if self.path == "/api/health":
                self._json(HTTPStatus.OK, {
                    "ok": True,
                    "instance_id": getattr(self.server, "instance_id", ""),
                    "pid": os.getpid(),
                })
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
                elif self.path == "/api/completions":
                    event = store.complete(
                        str(payload.get("gap_id") or ""),
                        str(payload.get("evidence") or ""),
                        str(payload.get("summary") or ""),
                    )
                elif self.path == "/api/shutdown":
                    expected = getattr(self.server, "instance_id", "")
                    if not expected or payload.get("instance_id") != expected:
                        raise ValueError("instance_id does not match the running dashboard")
                    event = {"event": "server_stopping", "pid": os.getpid()}
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                    return
            except (OSError, ValueError, TimeoutError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return
            self._json(HTTPStatus.OK, {"ok": True, "response": event})

    return DashboardHandler


def create_server(
    workdir: Path,
    host: str,
    port: int,
    *,
    quiet: bool = False,
    instance_id: str = "",
) -> ThreadingHTTPServer:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("dashboard host must be loopback")
    html_path = Path(__file__).resolve().parents[1] / "docs" / "autonomy-dashboard.html"
    server = ThreadingHTTPServer((host, port), make_handler(workdir.resolve(), html_path))
    server.quiet = quiet  # type: ignore[attr-defined]
    server.instance_id = instance_id  # type: ignore[attr-defined]
    return server


def _server_state(workdir: Path) -> dict[str, Any]:
    path = workdir.resolve() / SERVER_STATE_PATH
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _health(state: dict[str, Any], timeout: float = 0.5) -> bool:
    url = str(state.get("url") or "")
    instance_id = str(state.get("instance_id") or "")
    if not url or not instance_id:
        return False
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=timeout) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return (
        bool(payload.get("ok"))
        and payload.get("instance_id") == instance_id
        and payload.get("pid") == state.get("pid")
    )


def dashboard_status(workdir: Path) -> dict[str, Any]:
    workdir = workdir.resolve()
    state = _server_state(workdir)
    running = _health(state)
    return {
        "running": running,
        "pid": state.get("pid"),
        "url": state.get("url"),
        "started_at": state.get("started_at"),
        "log_path": str(workdir / SERVER_LOG_PATH),
        "reason": "healthy" if running else ("stale_state" if state else "not_started"),
    }


def _serve_foreground(
    workdir: Path,
    host: str,
    port: int,
    *,
    quiet: bool,
    instance_id: str,
) -> int:
    try:
        server = create_server(workdir, host, port, quiet=quiet, instance_id=instance_id)
    except (OSError, ValueError) as exc:
        print(f"autonomy_dashboard: {exc}", file=sys.stderr)
        return 2
    url = f"http://{host}:{server.server_port}"
    state_path = workdir / SERVER_STATE_PATH
    state = {
        "instance_id": instance_id,
        "pid": os.getpid(),
        "url": url,
        "host": host,
        "port": server.server_port,
        "workdir": str(workdir),
        "started_at": _now(),
    }
    atomic_write_bytes(state_path, (json.dumps(state, indent=2, sort_keys=True) + "\n").encode())
    print(f"Autonomy dashboard: {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        current = _server_state(workdir)
        if current.get("instance_id") == instance_id:
            state_path.unlink(missing_ok=True)
    return 0


def start_dashboard(workdir: Path, host: str, port: int, *, quiet: bool = False) -> dict[str, Any]:
    workdir = workdir.resolve()
    current = dashboard_status(workdir)
    if current["running"]:
        return {**current, "started": False}
    (workdir / SERVER_STATE_PATH).unlink(missing_ok=True)
    log_path = workdir / SERVER_LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    instance_id = uuid.uuid4().hex
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--workdir", str(workdir),
        "--host", host,
        "--port", str(port),
        "--foreground",
        "--instance-id", instance_id,
        "--quiet",
    ]
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
            close_fds=True,
        )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = _server_state(workdir)
        if state.get("instance_id") == instance_id and _health(state):
            return {**dashboard_status(workdir), "started": True}
        if process.poll() is not None:
            break
        time.sleep(0.05)
    detail = log_path.read_text(encoding="utf-8", errors="replace")[-1000:]
    raise RuntimeError(f"dashboard did not start; see {log_path}: {detail.strip()}")


def stop_dashboard(workdir: Path) -> dict[str, Any]:
    workdir = workdir.resolve()
    state = _server_state(workdir)
    state_path = workdir / SERVER_STATE_PATH
    if not state or not _health(state):
        state_path.unlink(missing_ok=True)
        return {"stopped": False, "reason": "not_running"}
    pid = int(state["pid"])
    request = urllib.request.Request(
        str(state["url"]) + "/api/shutdown",
        data=json.dumps({"instance_id": state["instance_id"]}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"stopped": False, "reason": f"shutdown_request_failed: {exc}", "pid": pid}
    if not payload.get("ok"):
        return {"stopped": False, "reason": "shutdown_rejected", "pid": pid}
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not _health(state, timeout=0.1):
            state_path.unlink(missing_ok=True)
            return {"stopped": True, "pid": pid, "url": state.get("url")}
        time.sleep(0.05)
    return {"stopped": False, "reason": "shutdown_timeout", "pid": pid, "url": state.get("url")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Build Loop autonomy dashboard")
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--print-state", action="store_true")
    parser.add_argument("--complete", metavar="GAP_ID")
    parser.add_argument("--evidence", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--instance-id", default="")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    workdir = Path(args.workdir).resolve()
    if args.print_state:
        print(json.dumps(DecisionStore(workdir).state(), indent=2, sort_keys=True))
        return 0
    if args.complete:
        try:
            event = DecisionStore(workdir).complete(args.complete, args.evidence, args.summary)
        except (OSError, ValueError, TimeoutError) as exc:
            print(f"autonomy_dashboard: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(event, indent=2, sort_keys=True))
        return 0
    if args.status:
        status = dashboard_status(workdir)
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0 if status["running"] else 1
    if args.stop:
        stopped = stop_dashboard(workdir)
        print(json.dumps(stopped, indent=2, sort_keys=True))
        return 0 if stopped.get("reason") != "shutdown_timeout" else 2
    if args.foreground:
        return _serve_foreground(
            workdir,
            args.host,
            args.port,
            quiet=args.quiet,
            instance_id=args.instance_id or uuid.uuid4().hex,
        )
    try:
        status = start_dashboard(workdir, args.host, args.port, quiet=args.quiet)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"autonomy_dashboard: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
