#!/usr/bin/env python3
"""Bounded local session mining; observations never become model quality rankings.

API: ``mine_sessions(config=None, *, sources=None) -> JSON-compatible dict``.
``sources`` overrides config.sources; a source is a path or a dict containing
path, format (native|normalized|rosslabs), host and optional provider/model/effort/project/
task/harness. Native Codex/Claude JSONL uses transcript_adapter. Other hosts must
provide normalized JSONL: each row has schema ``daydream.session-event.v1``,
session_id, role (user|assistant|tool_result|metadata), optional content (text),
timestamp, event_id and dimension fields. Tool-result rows can supply is_error,
test_outcome (pass|fail), and tool. Text and tool commands never leave this module.

With no selected sources, bounded discovery reads activity.db session pointers
(read-only) and supported JSONL under host roots. Paths are only pointers, never
proof of an adapter or successful extraction. Missing/unsupported sources remain
coverage gaps. Config can set discover=False, discovery_roots (host -> directory),
activity_db (path or False), max_documents (24), max_bytes_per_document (8 MiB),
max_total_bytes (64 MiB), max_events_per_document (10000), max_discovery_entries
(1000), max_discovery_depth (5). Native input uses a private temporary regular
snapshot, removed before return. No network, input mutation, schedule or install.

Output documents carry source path, SHA256, status and session ids. Incidents
contain fixed signal tags and source locators, never raw quotes. Segments are a
list with stable segment_id and host/provider/model/effort/project/task/harness/
session_id dimensions, evidence, signals and unknown quality fields. Lexicon
counts are heuristic upper bounds. Every comparison is observational.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any

try:
    from . import transcript_adapter as adapter
    from .transcript_pattern_miner.textproc import CORRECTION_RE, META_PREFIXES
except ImportError:
    import transcript_adapter as adapter
    from transcript_pattern_miner.textproc import CORRECTION_RE, META_PREFIXES

SCHEMA = "build-loop.daydream-sessions.v1"
NORMALIZED_SCHEMA = "daydream.session-event.v1"
RULES_VERSION = "2026-10-21.2"
SCANNER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
DIMENSIONS = ("host", "provider", "model", "effort", "project", "task", "harness", "session_id")
HOSTS = ("codex", "claude", "cursor", "muse", "antigravity", "rosslabs")
DEFAULTS = {"max_documents": 24, "max_bytes_per_document": 8 << 20,
            "max_total_bytes": 64 << 20, "max_events_per_document": 10000,
            "max_discovery_entries": 1000, "max_discovery_depth": 5}
POSITIVE = re.compile(r"\b(?:that works|looks good|tests? pass(?:ed)?|works correctly|much better|ship it)\b", re.I)
SCOPE = re.compile(r"^\s*(?:also\b|additionally\b|next\b|now\b|please add\b|can you also\b)", re.I)
CLARIFICATION = re.compile(r"^\s*(?:actually[,\s]+)?(?:what|how|which|why|when|where|define|explain|clarify)\b", re.I)
TEST_PASS = re.compile(r"(?:\b\d+ passed\b|^OK\s*$|\bTest Suite .+ passed\b)", re.M)
TEST_FAIL = re.compile(r"(?:\b[1-9]\d* failed\b|^FAILED\b|\bTest Suite .+ failed\b)", re.M)
TEST_COMMAND = re.compile(r"(?:^|(?:&&|;|[|\n])\s*)(?:(?:uv run|poetry run|python3? -m)\s+)?(?:pytest|unittest|xcodebuild|swift test|npm (?:run )?test|pnpm (?:run )?test|cargo test)\b")
SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+ -]{0,119}\Z")


def _label(value: Any) -> str:
    if not isinstance(value, str) or not SAFE_LABEL.fullmatch(value.strip()):
        return "unknown"
    value = value.strip()
    if re.search(r"(?:sk-|api[_-]?key|password|secret|bearer\s|://)", value, re.I):
        return "unknown"
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _dimensions(metadata: dict) -> dict[str, str]:
    return {key: _label(metadata.get(key)) for key in DIMENSIONS}


def _default_roots(home: Path) -> dict[str, Path]:
    return {"codex": home / ".codex/sessions", "claude": home / ".claude/projects",
            "cursor": home / ".cursor/projects", "muse": home / "Library/Application Support/Muse",
            "antigravity": home / ".gemini/antigravity/conversations",
            "rosslabs": home / "dev/git-folder/rosslabs-agent-harness/.harness/sessions"}


def discover_sources(config: dict | None = None) -> dict:
    """Return a bounded discovery manifest and explicit source descriptors.

    Reading a directory or a DB pointer never counts as mining a session. Known
    Cursor text and Antigravity protobuf exports require a normalized adapter.
    """
    config = config or {}
    budget = {key: int(config.get(key, val)) for key, val in DEFAULTS.items()}
    roots = config.get("discovery_roots")
    roots = ({host: Path(path).expanduser() for host, path in roots.items()}
             if roots is not None else _default_roots(Path.home()))
    selected: list[dict] = []
    manifest = []
    remaining = max(0, budget["max_discovery_entries"])
    # The activity store is a locator index, not a source of raw preview text.
    db = config.get("activity_db", Path.home() / ".local/share/agent-activity/activity.db")
    db_status = {"status": "disabled" if db is False else "missing"}
    if db is not False and Path(db).expanduser().is_file():
        dbpath = Path(db).expanduser().resolve()
        db_status = {"path": str(dbpath), "status": "queried", "rows": 0}
        connection = None
        try:
            connection = sqlite3.connect(dbpath.as_uri() + "?mode=ro", uri=True, timeout=1)
            connection.execute("PRAGMA query_only=ON")
            steps = [0]
            def progress():
                steps[0] += 1
                return steps[0] > 200
            connection.set_progress_handler(progress, 1000)
            for host in ("codex", "claude"):
                rows = connection.execute(
                    "SELECT path,model,cwd,entrypoint FROM sessions WHERE agent=? "
                    "ORDER BY started_at DESC LIMIT ?", (host, max(1, budget["max_documents"] // 3))
                ).fetchall()
                for path, model, cwd, entrypoint in rows:
                    if not isinstance(path, str):
                        continue
                    selected.append({"path": path, "host": host, "format": "native",
                                     "project": Path(cwd).name if cwd else None,
                                     "harness": entrypoint})
                db_status["rows"] += len(rows)
        except (sqlite3.Error, OSError, ValueError):
            db_status["status"] = "query_failed"
        finally:
            if connection is not None:
                connection.close()
    for host in HOSTS:
        root = roots.get(host)
        if root is None:
            manifest.append({"host": host, "status": "not_configured", "selected_documents": 0})
            continue
        record = {"host": host, "root": str(root), "status": "missing", "location_semantics": "candidate_location_not_format_proof", "selected_documents": 0,
                  "visited_entries": 0, "unsupported_files": 0, "truncated": False}
        if root.is_dir() and not root.is_symlink():
            record["status"] = "scanned"
            queue = [(root, 0)]
            host_cap = max(1, budget["max_documents"] // len(HOSTS))
            # Reserve a share for later hosts; a large Codex tree cannot erase
            # the distinction between absent Antigravity and unvisited sources.
            host_remaining = max(1, budget["max_discovery_entries"] // len(HOSTS))
            while queue and remaining > 0 and host_remaining > 0 and record["selected_documents"] < host_cap:
                folder, depth = queue.pop(0)
                try:
                    with os.scandir(folder) as entries:
                        for entry in entries:
                            if remaining <= 0 or host_remaining <= 0:
                                record["truncated"] = True
                                break
                            remaining -= 1
                            host_remaining -= 1
                            record["visited_entries"] += 1
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if depth < budget["max_discovery_depth"]:
                                    queue.append((Path(entry.path), depth + 1))
                                else:
                                    record["truncated"] = True
                            elif entry.is_file(follow_symlinks=False):
                                suffix = Path(entry.name).suffix
                                if suffix == ".jsonl":
                                    selected.append({"path": entry.path, "host": host,
                                                     "format": "native" if host in {"codex", "claude"} else "rosslabs" if host == "rosslabs" else "normalized"})
                                    record["selected_documents"] += 1
                                    if record["selected_documents"] >= host_cap:
                                        record["truncated"] = True
                                        break
                                elif suffix in {".pb", ".txt", ".db", ".sqlite"}:
                                    record["unsupported_files"] += 1
                except OSError:
                    record["status"] = "read_failed"
            record["truncated"] |= bool(queue) or remaining <= 0 or host_remaining <= 0
            if record["visited_entries"] == 0 and remaining <= 0:
                record["status"] = "skipped_due_budget"
        manifest.append(record)
    return {"sources": selected[:budget["max_documents"]], "hosts": manifest,
            "activity_db": db_status, "truncated": len(selected) > budget["max_documents"]
            or any(row.get("truncated") for row in manifest)}


class SourceRejected(ValueError):
    pass


def _read_source(path: Path, byte_limit: int, event_limit: int, charge) -> tuple[bytes, list[dict], tuple]:
    if path.is_symlink():
        raise SourceRejected("symlink_source")
    with adapter._open_regular_binary(path) as handle:
        before = os.fstat(handle.fileno())
        if before.st_size > byte_limit:
            raise SourceRejected("document_byte_limit")
        data = handle.read(byte_limit)
        charge(len(data))  # Even malformed/rejected documents consumed this budget.
        after = os.fstat(handle.fileno())
    signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if signature(before) != signature(after):
        raise SourceRejected("source_changed")
    lines = data.splitlines()
    if len(lines) > event_limit:
        raise SourceRejected("document_record_limit")
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            raise SourceRejected("malformed_jsonl") from None
        if not isinstance(row, dict):
            raise SourceRejected("non_object_record")
        rows.append(row)
    return data, rows, signature(after)


def _metadata(rows: list[dict], spec: dict, native: bool) -> tuple[list[dict], set[str]]:
    current = {key: spec.get(key) for key in DIMENSIONS}
    snapshots = []
    sessions: set[str] = set()
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        if native:
            if row.get("type") in {"session_meta", "turn_context"}:
                fields = dict(payload)
                fields["session_id"] = payload.get("session_id") or payload.get("id") if row.get("type") == "session_meta" else payload.get("session_id")
                fields["effort"] = payload.get("effort") or payload.get("reasoning_effort")
                fields["provider"] = payload.get("model_provider")
                if row.get("type") == "turn_context" and not fields["effort"]:
                    current["effort"] = "unknown"
                if row.get("type") == "session_meta":
                    fields["harness"] = payload.get("originator") or current.get("harness")
                    source = payload.get("source")
                    if source == "exec" or isinstance(source, dict) and "subagent" in source:
                        current["interaction_kind"] = "headless"
            else:
                origin = row.get("_source") if isinstance(row.get("_source"), dict) else {}
                fields = {"session_id": row.get("sessionId") or origin.get("session_id"), "model": message.get("model"),
                          "effort": message.get("effort"), "cwd": row.get("cwd"),
                          "harness": row.get("entrypoint")}
        else:
            fields = row
            if spec.get("host") and fields.get("host") not in {None, spec["host"]}:
                raise SourceRejected("conflicting_host_identity")
        if native:
            fields["host"] = spec["host"]
        for key in DIMENSIONS:
            if fields.get(key) is not None:
                current[key] = fields[key]
        if isinstance(fields.get("harness"), str) and fields["harness"].startswith("sdk-"):
            current["interaction_kind"] = "headless"
        if fields.get("cwd") and not spec.get("project"):
            current["project"] = Path(str(fields["cwd"])).name
        snapshots.append(_dimensions(current))
        snapshots[-1]["interaction_kind"] = current.get("interaction_kind", "unknown")
        if snapshots[-1]["session_id"] != "unknown":
            sessions.add(snapshots[-1]["session_id"])
    if len(sessions) > 1:
        raise SourceRejected("conflicting_session_identity")
    return snapshots, sessions


def _normalized_events(rows: list[dict]) -> list[tuple[int, dict]]:
    result = []
    for line, row in enumerate(rows, 1):
        if row.get("schema") != NORMALIZED_SCHEMA or row.get("role") not in {"user", "assistant", "tool_result", "metadata"}:
            raise SourceRejected("unsupported_normalized_record")
        role = row["role"]
        if role == "metadata":
            continue
        content = row.get("content", "")
        if not isinstance(content, str):
            raise SourceRejected("unsupported_normalized_content")
        blocks = ([{"type": "tool_result", "content": content, "is_error": row.get("is_error") is True,
                    "test_outcome": row.get("test_outcome"), "tool": row.get("tool")}]
                  if role == "tool_result" else [{"type": "text", "text": content}])
        result.append((line, {"type": "user" if role == "tool_result" else role,
                              "message": {"content": blocks}, "uuid": row.get("event_id"),
                              "timestamp": row.get("timestamp"), "isMeta": row.get("isMeta", False)}))
    return result


def _harness_events(rows: list[dict]) -> list[tuple[int, dict]]:
    """Observed RossLabs .harness/sessions schema, with unknown model/session."""
    events = []
    for line, row in enumerate(rows, 1):
        event = row.get("event")
        if not isinstance(event, dict):
            raise SourceRejected("unsupported_rosslabs_record")
        kind = event.get("type")
        if kind in {"UserMessage", "AssistantMessage"} and isinstance(event.get("content"), str):
            events.append((line, {"type": "user" if kind == "UserMessage" else "assistant",
                "uuid": row.get("id"), "message": {"content": event["content"]}}))
        elif kind == "ToolResult" and isinstance(event.get("output"), dict):
            # Read/file content is not test execution or evidence of code quality.
            output = event["output"]
            events.append((line, {"type": "user", "uuid": row.get("id"), "message": {"content": [
                {"type": "tool_result", "content": "", "is_error": output.get("is_error") is True}]}}))
        elif kind not in {"ToolCall", "GateDecision", "ProviderAttempt", "Compaction"}:
            raise SourceRejected("unsupported_rosslabs_event")
    return events


def _native_events(data: bytes, expected_host: str | None, limit: int) -> tuple[str, list, dict]:
    # A private regular snapshot keeps canonical adapter behavior without a
    # second read/race against the user's live log. Deleted before return.
    with tempfile.TemporaryDirectory(prefix="daydream-native-") as directory:
        snapshot = Path(directory) / "snapshot.jsonl"
        descriptor = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        fmt = adapter.detect_format(snapshot)
        if fmt not in {"codex", "claude"} or expected_host not in {None, fmt}:
            raise SourceRejected("unsupported_native_host")
        events, diagnostics = [], {}
        for event in adapter.iter_events(snapshot, include_provenance=True, diagnostics=diagnostics):
            if len(events) >= limit:
                raise SourceRejected("document_event_limit")
            events.append((event["_container"]["record_index"], event))
        return fmt, events, diagnostics


def _signals(event: dict) -> list[str]:
    if event.get("isMeta"):
        return []
    content = (event.get("message") or {}).get("content", [])
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    tags = set()
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result":
            text = block.get("content")
            text = text if isinstance(text, str) else adapter.human_text(text)
            if block.get("is_error") is True:
                tags.add("tool_error_observed")
            executed = block.get("test_invocation") is True
            if block.get("test_outcome") == "pass" or executed and TEST_PASS.search(text):
                tags.add("test_pass_observed")
            if block.get("test_outcome") == "fail" or executed and TEST_FAIL.search(text):
                tags.add("test_fail_observed")
        elif block.get("type") == "text" and event.get("type") == "user":
            if event.get("interaction_kind") == "headless":
                continue
            text = block.get("text", "")
            if not isinstance(text, str) or text.lstrip().startswith(META_PREFIXES):
                continue
            # Quoted and fenced material is not owner feedback.
            text = re.sub(r"```.*?```", "", text, flags=re.S)
            text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
            if POSITIVE.search(text):
                tags.add("positive_feedback_heuristic")
            correction_matches = [match.group().casefold() for match in CORRECTION_RE.finditer(text)]
            # 'Actually' can modify a concept ('what headless actually means')
            # without rejecting the preceding answer. Keep discourse-initial
            # 'Actually use X' and explicit rejection such as 'No, actually...'.
            stronger_correction = any(match != "actually" for match in correction_matches)
            initial_actually = bool(re.match(r"\s*actually\b", text, re.I))
            clarification = bool(CLARIFICATION.search(text))
            correction = stronger_correction or (initial_actually and not clarification)
            if SCOPE.search(text):
                tags.add("new_scope_heuristic")
            elif correction and not re.search(r"\bno[, ]+(?:that(?:'s| is) correct|problem|issues?)\b", text, re.I):
                tags.add("correction_heuristic")
            elif clarification and correction_matches and not stronger_correction:
                tags.add("new_scope_heuristic")
    return sorted(tags)


def mine_sessions(config: dict | None = None, *, sources: list[dict | str] | None = None) -> dict:
    """Mine selected documents locally and return aggregate evidence, never text."""
    config = config or {}
    limits = {key: int(config.get(key, value)) for key, value in DEFAULTS.items()}
    if any(value < 1 for value in limits.values()):
        raise ValueError("all mining budgets must be positive integers")
    explicit = sources if sources is not None else config.get("sources")
    discovery = discover_sources({**config, **limits}) if config.get("discover", explicit is None) else {"hosts": [], "sources": [], "truncated": False, "activity_db": {"status": "disabled"}}
    selected = list(explicit or []) + discovery["sources"]
    documents: list[dict] = []
    incidents: dict[str, dict] = {}
    segments: dict[str, dict] = {}
    segment_documents: dict[str, set[str]] = {}
    seen_paths = set()
    consumed = 0
    snapshot_bytes = 0
    for source in selected[:limits["max_documents"]]:
        spec = {"path": source} if isinstance(source, (str, Path)) else dict(source)
        path = Path(spec.get("path", "")).expanduser().absolute()
        # Preserve final-component symlink rejection, but canonicalize parent
        # aliases such as /tmp -> /private/tmp before emitting shared locators.
        if not path.is_symlink():
            try:
                path = path.resolve()
            except (OSError, RuntimeError):
                # The bounded reader below records unreadable/looping sources
                # as rejected documents rather than aborting the whole run.
                pass
        if str(path) in seen_paths:
            continue
        seen_paths.add(str(path))
        document = {"path": str(path), "sha256": None, "status": "rejected", "session_ids": [], "event_count": 0,
                    "host": _label(spec.get("host")), "reason": None}
        documents.append(document)
        try:
            remaining = limits["max_total_bytes"] - consumed
            if remaining <= 0:
                raise SourceRejected("total_byte_limit")
            def charge(count):
                nonlocal consumed
                consumed += count
            data, rows, signature = _read_source(path, min(remaining, limits["max_bytes_per_document"]), limits["max_events_per_document"], charge)
            document["sha256"] = hashlib.sha256(data).hexdigest()
            document["bytes"] = len(data)
            native = spec.get("format", "native") == "native"
            if spec.get("format", "native") not in {"native", "normalized", "rosslabs"}:
                raise SourceRejected("unsupported_format")
            if native:
                fmt, events, diagnostics = _native_events(data, spec.get("host"), limits["max_events_per_document"])
                snapshot_bytes += len(data)
                spec["host"] = fmt
                document["host"] = fmt
            elif spec.get("format") == "rosslabs":
                # SessionTree::session_path is <root>/.harness/sessions/<id>.jsonl.
                spec.setdefault("session_id", path.stem)
                document["session_identity_basis"] = "rosslabs SessionTree::session_path filename contract"
            snapshots, sessions = _metadata(rows, spec, native)
            if native:
                info = path.stat()
                if signature != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                    raise SourceRejected("source_changed")
            elif spec.get("format") == "rosslabs":
                events = _harness_events(rows)
            else:
                events = _normalized_events(rows)
            if not rows or not events:
                raise SourceRejected("no_supported_events")
            for line, event in events:
                message = event.get("message")
                if not isinstance(message, dict) or not isinstance(message.get("content"), (str, list)):
                    raise SourceRejected("unsupported_event_content")
                if not 1 <= line <= len(snapshots):
                    raise SourceRejected("invalid_event_locator")
            document.update({"status": "complete", "session_ids": sorted(sessions), "event_count": len(events)})
            if native and diagnostics.get("unsupported_records"):
                document.update({"status": "partial", "reason": "unsupported_native_records",
                                 "unsupported_records": diagnostics["unsupported_records"]})
            test_calls = set()
            for line, event in events:
                if event.get("isMeta"):
                    continue
                dims = snapshots[line - 1]
                event["interaction_kind"] = dims["interaction_kind"]
                # A file read containing '3 passed' is not an executed test.
                blocks = (event.get("message") or {}).get("content", [])
                for block in blocks if isinstance(blocks, list) else []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        inp = block.get("input")
                        command = (inp.get("command") or inp.get("cmd")) if isinstance(inp, dict) else None
                        execution_tool = block.get("name") in {"Bash", "exec", "exec_command", "functions.exec_command"}
                        if execution_tool and isinstance(command, str) and TEST_COMMAND.search(command) and isinstance(block.get("id"), str):
                            test_calls.add(block.get("id"))
                    elif block.get("type") == "tool_result" and block.get("tool_use_id") in test_calls:
                        block["test_invocation"] = True
                # Absent native session ids stay unknown; filename is not a session identity.
                segment_id = _digest([dims, document["sha256"] if dims["session_id"] == "unknown" else None])
                segment_documents.setdefault(segment_id, set()).add(str(path))
                locator = {"path": str(path), "sha256": document["sha256"], "line": line,
                           "session_id": dims["session_id"], "timestamp": _label(event.get("timestamp"))}
                segment = segments.setdefault(segment_id, {"segment_id": segment_id, **dims,
                    "observational_only": True, "evidence": [], "incident_ids": [], "signals": {},
                    "event_count": 0, "document_count": 0,
                    "quality": {key: "unknown" for key in ("prompt", "response", "code", "integration", "ui")}})
                if len(segment["evidence"]) < 5:
                    segment["evidence"].append(locator)
                segment["event_count"] += 1
                # Hash the private content internally; only the opaque incident id leaves.
                original = event.get("_source") if isinstance(event.get("_source"), dict) else {}
                record_index = original.get("record_index") or line
                identity = event.get("uuid") or _digest([event.get("message", {}), event.get("timestamp"), record_index])
                session_identity = dims["session_id"] if dims["session_id"] != "unknown" else document["sha256"]
                for signal in _signals(event):
                    incident_id = _digest([dims["host"], session_identity, identity, signal])
                    if incident_id not in incidents:
                        incidents[incident_id] = {"incident_id": incident_id, "signal": signal, **dims,
                                                  "observational_only": True, "evidence": [locator]}
                        segment["signals"][signal] = segment["signals"].get(signal, 0) + 1
                        segment["incident_ids"].append(incident_id)
                    elif locator not in incidents[incident_id]["evidence"] and len(incidents[incident_id]["evidence"]) < 5:
                        incidents[incident_id]["evidence"].append(locator)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            document["reason"] = str(exc) if isinstance(exc, SourceRejected) else type(exc).__name__
    for segment in segments.values():
        segment["document_count"] = len(segment_documents[segment["segment_id"]])
    gaps = [{"path": row["path"], "reason": row["reason"]} for row in documents if row["status"] != "complete"]
    for row in discovery["hosts"]:
        host_docs = [doc for doc in documents if doc["host"] == row["host"] and doc["status"] in {"complete", "partial"}]
        row["mined_documents"] = len(host_docs)
        if not host_docs:
            reason = ("unsupported_native_format_requires_normalized_export" if row.get("unsupported_files") and row["host"] not in {"codex", "claude"}
                      else "no_supported_source_mined")
            gaps.append({"host": row["host"], "reason": reason})
    truncated = len(selected) > limits["max_documents"] or discovery["truncated"]
    if truncated:
        gaps.append({"reason": "discovery_or_document_limit"})
    if not documents:
        gaps.append({"reason": "no_sources_selected"})
    signal_counts = Counter(item["signal"] for item in incidents.values())
    recommendations = []
    actions = {
        "correction_heuristic": ("prompt-builder", "Review correction evidence; separate instruction gaps from task changes before drafting a prompt or skill."),
        "new_scope_heuristic": ("build-loop", "Keep scope extensions separate from failures and update task intent before deriving a reusable workflow."),
        "positive_feedback_heuristic": ("build-loop", "Review the accepted response as an exemplar; verify code, integration and UI outcomes independently before reuse."),
        "tool_error_observed": ("build-loop", "Group error locators by verified root cause before drafting a preflight or regression test."),
        "test_pass_observed": ("build-loop", "Reuse the observed verification workflow after checking task coverage; passing output does not establish UI acceptance."),
        "test_fail_observed": ("build-loop", "Trace failing test evidence to a regression fixture and confirm the repair on the same task."),
    }
    for signal, count in sorted(signal_counts.items()):
        matches = [item for item in incidents.values() if item["signal"] == signal]
        owner, action = actions[signal]
        recommendations.append({"signal": signal, "incident_count": count,
            "independent_session_count": len({(item["host"], item["session_id"]) for item in matches if item["session_id"] != "unknown"}),
            "suggested_reuse_owner": owner, "recommendation": action,
            "evidence": [item["evidence"][0] for item in matches[:5]], "observational_only": True,
            "status": "review_candidate"})
    return {"schema": SCHEMA, "observational_only": True,
            "method": {"rules_version": RULES_VERSION, "scanner_sha256": SCANNER_SHA256,
                       "signals": "lexicon counts are heuristic upper bounds; tests are observed output only",
                       "quality": "unknown without independent task-specific evaluation", "causal_rankings": False,
                       "observed_success": "test_pass_observed means matching test output, not task success",
                       "user_acceptance": "positive_feedback_heuristic is a review candidate, not verified acceptance",
                       "independence": "distinct declared host/session ids are a proxy; source hashes prove copies, not independent trials",
                       "incident_identity": "host/session/event id; absent ids use message hash, timestamp and source record ordinal; shifted exports without ids may not deduplicate"},
            "coverage": {"complete": not gaps, "gaps": gaps, "truncated": truncated, "limits": limits,
                         "bytes_read": consumed, "byte_semantics": "input source bytes read, including rejected JSON; excludes DB/directory metadata",
                         "native_temporary_snapshot_bytes": snapshot_bytes, "raw_snapshots_retained": False,
                         "discovery": discovery},
            "counts": {"documents": len(documents), "complete_documents": sum(d["status"] == "complete" for d in documents),
                       "distinct_sessions": len({(s["host"], s["session_id"]) for s in segments.values() if s["session_id"] != "unknown"}),
                       "incidents": len(incidents), "signals": dict(sorted(signal_counts.items()))},
            "documents": documents, "incidents": list(incidents.values()), "segments": list(segments.values()),
            "recommendations": recommendations,
            "positive_exemplars": [{"incident_id": item["incident_id"], "evidence": item["evidence"],
                                    "code_quality": "unknown", "integration_quality": "unknown", "ui_acceptance": "unknown"}
                                   for item in incidents.values() if item["signal"] == "positive_feedback_heuristic"][:20]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="JSON configuration; no raw logs are written")
    parser.add_argument("--source", action="append", default=[], help="Explicit native JSONL path (repeatable)")
    parser.add_argument("--normalized-host", choices=HOSTS, help="Interpret --source as normalized exports from this host")
    parser.add_argument("--discover", action="store_true", help="Include bounded local source discovery")
    parser.add_argument("--output", type=Path, help="Save a private new JSON report; fail if the path already exists")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text()) if args.config else {}
    if args.discover:
        config["discover"] = True
    sources = None
    if args.source:
        sources = [{"path": path, **({"host": args.normalized_host, "format": "normalized"} if args.normalized_host else {})}
                   for path in args.source]
    report = mine_sessions(config, sources=sources)
    if args.output:
        descriptor = os.open(args.output.expanduser(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(json.dumps({"path": str(args.output.expanduser().absolute()), "counts": report["counts"],
                          "coverage": {"complete": report["coverage"]["complete"], "truncated": report["coverage"]["truncated"],
                                       "gap_count": len(report["coverage"]["gaps"])}}))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["coverage"]["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
