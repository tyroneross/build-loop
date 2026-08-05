#!/usr/bin/env python3
"""Validate Groundwork build requests and emit Build Loop implementation maps.

The adapter is intentionally stdlib-only. Groundwork remains authoritative for
desired state and convergence; this script validates the immutable request,
verifies repository-local delivery evidence, and returns a digest-bound map.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import parse_qsl, unquote, urlsplit


BUILD_REQUEST_CONTRACT = "groundwork.build-request/v1"
IMPLEMENTATION_MAP_CONTRACT = "build-loop.implementation-map/v1"
CONVERGENCE_CONTRACT = "groundwork.convergence/v1"
DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
ARCHITECTURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
KINDS = {"task", "component", "contract", "requirement"}
STATUSES = {"not-started", "implemented", "verified", "blocked", "manual", "diverged"}
EVIDENCE_KINDS = {"test", "runtime", "inspection"}
EVIDENCE_OUTCOMES = {"passed", "failed", "blocked", "manual"}
IMPACTS = {"none", "low", "medium", "high", "blocking"}
PLATFORMS = {"web", "vite-spa", "ios", "macos", "watchos", "tvos", "visionos", "android", "claude-plugin", "agent-system", "api", "service", "other"}
SURFACE_ROLES = {"primary", "companion", "admin", "extension", "service"}
PROVENANCE = {"observed", "decided", "assumed", "derived"}
PRIVATE_SEGMENTS = {".git", ".ssh", ".aws", ".gnupg", ".env", "secrets", "credentials"}
PRIVATE_NAME_RE = re.compile(r"^(?:credentials(?:\.[^.]+)?|secrets?(?:\.[^.]+)?|id_(?:rsa|dsa|ecdsa|ed25519))$", re.I)
URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+")
PRIVATE_PATH_RE = re.compile(r"(?:^|[\s\"'(])(?:/(?:Users|home|root|var/folders)/[^\s\"')]+|[A-Za-z]:\\Users\\[^\s\"')]+)", re.I)
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$")
SECRET_ASSIGNMENT_RE = re.compile(r"\b(?:api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|client[-_ ]?secret|password|passwd|private[-_ ]?key|secret|credential|authorization)\b\s*(?:=|:|\bis\b)", re.I)
SECRET_VALUE_RES = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{12,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)


class ExchangeError(ValueError):
    """Fail-closed contract or evidence validation error."""


def _fail(message: str) -> NoReturn:
    raise ExchangeError(message)


def _js_number(value: int | float) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if abs(value) <= 9_007_199_254_740_991:
            return str(value)
        try:
            value = float(value)
        except OverflowError:
            _fail("JSON numbers must be finite")
    if not math.isfinite(value):
        _fail("JSON numbers must be finite")
    if value == 0:
        return "0"
    absolute = abs(value)
    shortest = repr(value).lower()
    if 1e-6 <= absolute < 1e21:
        if "e" in shortest:
            return format(Decimal(shortest), "f")
        if shortest.endswith(".0"):
            return shortest[:-2]
        return shortest
    if "e" not in shortest:
        coefficient, exponent = f"{value:.15e}".split("e")
        coefficient = coefficient.rstrip("0").rstrip(".")
    else:
        coefficient, exponent = shortest.split("e")
    exponent_number = int(exponent)
    sign = "+" if exponent_number >= 0 else "-"
    return f"{coefficient}e{sign}{abs(exponent_number)}"


def _quote_json_string(value: str) -> str:
    quoted = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return re.sub(
        r"[\ud800-\udfff]",
        lambda match: f"\\u{ord(match.group(0)):04x}",
        quoted,
    )


def _utf16_sort_key(value: str) -> bytes:
    return value.encode("utf-16-be", errors="surrogatepass")


def normalize_json(value: Any) -> str:
    """Match Groundwork's recursively sorted compact UTF-8 JSON projection."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _js_number(value)
    if isinstance(value, str):
        return _quote_json_string(value)
    if isinstance(value, list):
        return "[" + ",".join(normalize_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            _fail("JSON object keys must be strings")
        return "{" + ",".join(
            f"{_quote_json_string(key)}:{normalize_json(value[key])}"
            for key in sorted(value, key=_utf16_sort_key)
        ) + "}"
    _fail(f"unsupported JSON value: {type(value).__name__}")


def digest_normalized(value: Any) -> str:
    return "sha256:" + hashlib.sha256(normalize_json(value).encode("utf-8")).hexdigest()


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda token: _fail(f"non-finite number {token}"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read JSON {path}: {exc}")


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    return value


def _strict_keys(value: dict[str, Any], required: set[str], optional: set[str], label: str) -> None:
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required - optional)
    if missing:
        _fail(f"{label} missing required fields: {', '.join(missing)}")
    if extra:
        _fail(f"{label} contains unsupported fields: {', '.join(extra)}")


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label} must be a non-empty string")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{label} must be an array")
    return value


def _digest(value: Any, label: str) -> str:
    text = _nonempty(value, label)
    if not DIGEST_RE.fullmatch(text):
        _fail(f"{label} must be a lowercase sha256 digest")
    return text


def _timestamp(value: Any, label: str) -> datetime:
    text = _nonempty(value, label)
    if not DATETIME_RE.fullmatch(text):
        _fail(f"{label} must use RFC 3339 date-time syntax with seconds and an offset")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        _fail(f"{label} must be an ISO-8601 timestamp: {exc}")
    if parsed.tzinfo is None:
        _fail(f"{label} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        _fail(f"{label} contains duplicate values")


def _enum(value: Any, allowed: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        _fail(f"{label} is unsupported")
    return value


def _architecture_id(value: Any, label: str) -> str:
    text = _nonempty(value, label)
    if not ARCHITECTURE_ID_RE.fullmatch(text):
        _fail(f"{label} must use Groundwork architecture ID syntax")
    return text


def _safe_repo_path(value: Any, label: str) -> str:
    text = _nonempty(value, label)
    if "\0" in text or text.startswith(("/", "~", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", text):
        _fail(f"{label} must be repository-relative")
    if "\\" in text or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text):
        _fail(f"{label} must use a repository-relative forward-slash path")
    if re.search(r"%(?![0-9A-Fa-f]{2})", text):
        _fail(f"{label} contains invalid percent encoding")
    try:
        decoded = unquote(text, errors="strict")
    except UnicodeDecodeError:
        _fail(f"{label} contains invalid UTF-8 percent encoding")
    segments = decoded.split("/")
    if any(not segment or segment in {".", ".."} for segment in segments):
        _fail(f"{label} contains an unsafe path segment")
    for segment in segments:
        lower = segment.lower()
        if lower in PRIVATE_SEGMENTS or (lower.startswith(".env.") and lower != ".env.example") or PRIVATE_NAME_RE.fullmatch(segment):
            _fail(f"{label} points to a private or credential-bearing location")
    return text


def _safe_text(value: str, label: str) -> None:
    if PRIVATE_PATH_RE.search(value):
        _fail(f"{label} contains an absolute private path")
    for candidate in URL_RE.findall(value):
        cleaned = candidate.rstrip("),.;!?")
        try:
            parsed = urlsplit(cleaned)
        except ValueError:
            _fail(f"{label} contains a malformed URL")
        if parsed.username or parsed.password:
            _fail(f"{label} contains a credential-bearing URL")
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
            if re.search(r"(?:token|secret|password|passwd|api[-_]?key|credential|signature|access[-_]?key)", key, re.I):
                _fail(f"{label} contains a credential-bearing URL")


def _safe_manual_text(value: str, label: str) -> None:
    _safe_text(value, label)
    if SECRET_ASSIGNMENT_RE.search(value) or any(pattern.search(value) for pattern in SECRET_VALUE_RES):
        _fail(f"{label} contains a secret or credential assignment")


def _string_list(raw: Any, label: str, *, minimum: int = 0, unique: bool = False) -> list[str]:
    values = [_nonempty(item, label) for item in _list(raw, label)]
    if len(values) < minimum:
        _fail(f"{label} must contain at least {minimum} value(s)")
    if unique:
        _unique(values, label)
    return values


def _validate_qualified_ref(raw: Any, label: str) -> dict[str, Any]:
    value = _require_object(raw, label)
    _strict_keys(value, {"specId", "kind", "id"}, set(), label)
    _architecture_id(value["specId"], f"{label}.specId")
    _architecture_id(value["id"], f"{label}.id")
    _enum(value["kind"], {"component", "contract", "feature", "screen", "element", "requirement", "flow", "task"}, f"{label}.kind")
    return value


def _validate_architecture(architecture: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    component_ids: list[str] = []
    for index, raw in enumerate(architecture["components"]):
        label = f"architecture.components[{index}]"
        item = _require_object(raw, label)
        _strict_keys(item, {"id", "name", "kind", "featureIds", "owner"}, {"description", "provenance"}, label)
        component_ids.append(_architecture_id(item["id"], f"{label}.id"))
        _nonempty(item["name"], f"{label}.name")
        _nonempty(item["owner"], f"{label}.owner")
        _enum(item["kind"], {"ui", "service", "data", "integration", "agent", "library", "platform"}, f"{label}.kind")
        for feature_index, feature_id in enumerate(_string_list(item["featureIds"], f"{label}.featureIds", unique=True)):
            _architecture_id(feature_id, f"{label}.featureIds[{feature_index}]")
        if "description" in item and not isinstance(item["description"], str):
            _fail(f"{label}.description must be a string")
        if "provenance" in item:
            _enum(item["provenance"], PROVENANCE, f"{label}.provenance")
    _unique(component_ids, "architecture component ids")

    contract_ids: list[str] = []
    for index, raw in enumerate(architecture["contracts"]):
        label = f"architecture.contracts[{index}]"
        item = _require_object(raw, label)
        _strict_keys(item, {"id", "name", "provider", "consumers", "ports", "transport", "failureModes", "securityNotes"}, {"provenance"}, label)
        contract_ids.append(_architecture_id(item["id"], f"{label}.id"))
        _nonempty(item["name"], f"{label}.name")
        _validate_qualified_ref(item["provider"], f"{label}.provider")
        consumers = _list(item["consumers"], f"{label}.consumers")
        if not consumers:
            _fail(f"{label}.consumers must not be empty")
        consumer_ids: list[str] = []
        for ref_index, ref in enumerate(consumers):
            parsed_ref = _validate_qualified_ref(ref, f"{label}.consumers[{ref_index}]")
            consumer_ids.append(f"{parsed_ref['specId']}:{parsed_ref['kind']}:{parsed_ref['id']}")
        _unique(consumer_ids, f"{label}.consumers")
        ports = _list(item["ports"], f"{label}.ports")
        if not ports:
            _fail(f"{label}.ports must not be empty")
        port_ids: list[str] = []
        for port_index, raw_port in enumerate(ports):
            port_label = f"{label}.ports[{port_index}]"
            port = _require_object(raw_port, port_label)
            _strict_keys(port, {"id", "name", "type", "direction"}, {"required"}, port_label)
            port_ids.append(_architecture_id(port["id"], f"{port_label}.id"))
            _nonempty(port["name"], f"{port_label}.name")
            _nonempty(port["type"], f"{port_label}.type")
            _enum(port["direction"], {"input", "output", "bidirectional"}, f"{port_label}.direction")
            if "required" in port and not isinstance(port["required"], bool):
                _fail(f"{port_label}.required must be boolean")
        _unique(port_ids, f"{label} port ids")
        _nonempty(item["transport"], f"{label}.transport")
        _string_list(item["failureModes"], f"{label}.failureModes", minimum=1)
        _string_list(item["securityNotes"], f"{label}.securityNotes", minimum=1)
        if "provenance" in item:
            _enum(item["provenance"], PROVENANCE, f"{label}.provenance")
    _unique(contract_ids, "architecture contract ids")

    relationship_ids: list[str] = []
    for index, raw in enumerate(architecture["relationships"]):
        label = f"architecture.relationships[{index}]"
        item = _require_object(raw, label)
        _strict_keys(item, {"id", "from", "to", "direction", "criticality", "optional", "rationale"}, {"contractRef", "provenance"}, label)
        relationship_ids.append(_architecture_id(item["id"], f"{label}.id"))
        _validate_qualified_ref(item["from"], f"{label}.from")
        _validate_qualified_ref(item["to"], f"{label}.to")
        if "contractRef" in item:
            _validate_qualified_ref(item["contractRef"], f"{label}.contractRef")
        _enum(item["direction"], {"unidirectional", "bidirectional", "event"}, f"{label}.direction")
        _enum(item["criticality"], {"hard", "soft", "informational"}, f"{label}.criticality")
        if not isinstance(item["optional"], bool):
            _fail(f"{label}.optional must be boolean")
        _nonempty(item["rationale"], f"{label}.rationale")
        if "provenance" in item:
            _enum(item["provenance"], PROVENANCE, f"{label}.provenance")
    _unique(relationship_ids, "architecture relationship ids")

    flow_ids: list[str] = []
    for index, raw in enumerate(architecture["flows"]):
        label = f"architecture.flows[{index}]"
        item = _require_object(raw, label)
        _strict_keys(item, {"id", "name", "trigger", "exchanges"}, {"provenance"}, label)
        flow_ids.append(_architecture_id(item["id"], f"{label}.id"))
        _nonempty(item["name"], f"{label}.name")
        _nonempty(item["trigger"], f"{label}.trigger")
        exchanges = _list(item["exchanges"], f"{label}.exchanges")
        if not exchanges:
            _fail(f"{label}.exchanges must not be empty")
        exchange_ids: list[str] = []
        orders: list[int] = []
        for exchange_index, raw_exchange in enumerate(exchanges):
            exchange_label = f"{label}.exchanges[{exchange_index}]"
            exchange = _require_object(raw_exchange, exchange_label)
            _strict_keys(exchange, {"id", "order", "from", "to", "contractRef", "inputRefs", "outputRefs", "failurePaths"}, set(), exchange_label)
            exchange_ids.append(_architecture_id(exchange["id"], f"{exchange_label}.id"))
            if not isinstance(exchange["order"], int) or isinstance(exchange["order"], bool) or exchange["order"] < 1:
                _fail(f"{exchange_label}.order must be a positive integer")
            orders.append(exchange["order"])
            for key in ("from", "to", "contractRef"):
                _validate_qualified_ref(exchange[key], f"{exchange_label}.{key}")
            for key in ("inputRefs", "outputRefs"):
                for ref_index, ref in enumerate(_list(exchange[key], f"{exchange_label}.{key}")):
                    _validate_qualified_ref(ref, f"{exchange_label}.{key}[{ref_index}]")
            _string_list(exchange["failurePaths"], f"{exchange_label}.failurePaths", minimum=1)
        _unique(exchange_ids, f"{label} exchange ids")
        if len(orders) != len(set(orders)):
            _fail(f"{label} exchange orders must be unique")
        if "provenance" in item:
            _enum(item["provenance"], PROVENANCE, f"{label}.provenance")
    _unique(flow_ids, "architecture flow ids")

    dependency_ids: list[str] = []
    dependency_specs: list[str] = []
    for index, raw in enumerate(architecture["specDependencies"]):
        label = f"architecture.specDependencies[{index}]"
        item = _require_object(raw, label)
        _strict_keys(item, {"id", "specId", "location", "schemaVersion", "revision", "digest", "relationship"}, {"optional"}, label)
        dependency_ids.append(_architecture_id(item["id"], f"{label}.id"))
        dependency_specs.append(_architecture_id(item["specId"], f"{label}.specId"))
        location = _require_object(item["location"], f"{label}.location")
        if location.get("kind") == "local":
            _strict_keys(location, {"kind", "path"}, set(), f"{label}.location")
            _safe_repo_path(location["path"], f"{label}.location.path")
        elif location.get("kind") == "uri":
            _strict_keys(location, {"kind", "uri"}, set(), f"{label}.location")
            uri = _nonempty(location["uri"], f"{label}.location.uri")
            _safe_text(uri, f"{label}.location.uri")
            if not URL_RE.fullmatch(uri):
                _fail(f"{label}.location.uri must be an absolute URI")
        else:
            _fail(f"{label}.location.kind is unsupported")
        if not isinstance(item["schemaVersion"], int) or isinstance(item["schemaVersion"], bool) or item["schemaVersion"] < 1:
            _fail(f"{label}.schemaVersion must be a positive integer")
        _nonempty(item["revision"], f"{label}.revision")
        _digest(item["digest"], f"{label}.digest")
        _enum(item["relationship"], {"uses", "extends", "implements", "companion"}, f"{label}.relationship")
        if "optional" in item and not isinstance(item["optional"], bool):
            _fail(f"{label}.optional must be boolean")
    _unique(dependency_ids, "architecture dependency ids")
    _unique(dependency_specs, "architecture dependency specIds")
    return set(component_ids), set(contract_ids), set(dependency_specs)


def _request_projection(request: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in request.items() if key != "requestDigest"}


def _validate_platform_surfaces(raw: Any) -> list[dict[str, Any]]:
    surfaces = _list(raw, "platformSurfaces")
    if not surfaces:
        _fail("platformSurfaces must not be empty")
    ids: list[str] = []
    for index, entry in enumerate(surfaces):
        item = _require_object(entry, f"platformSurfaces[{index}]")
        _strict_keys(item, {"id", "platform", "role", "name", "interactionModes", "featureIds"}, {"provenance"}, f"platformSurfaces[{index}]")
        ids.append(_architecture_id(item["id"], f"platformSurfaces[{index}].id"))
        _nonempty(item["name"], f"platformSurfaces[{index}].name")
        if not isinstance(item["platform"], str) or not isinstance(item["role"], str) or item["platform"] not in PLATFORMS or item["role"] not in SURFACE_ROLES:
            _fail(f"platformSurfaces[{index}] has an unsupported platform or role")
        for key in ("interactionModes", "featureIds"):
            values = [_nonempty(value, f"platformSurfaces[{index}].{key}") for value in _list(item[key], f"platformSurfaces[{index}].{key}")]
            _unique(values, f"platformSurfaces[{index}].{key}")
            if key == "featureIds":
                for value_index, entry in enumerate(values):
                    _architecture_id(entry, f"platformSurfaces[{index}].featureIds[{value_index}]")
        if "provenance" in item and (not isinstance(item["provenance"], str) or item["provenance"] not in PROVENANCE):
            _fail(f"platformSurfaces[{index}].provenance is unsupported")
    _unique(ids, "platform surface ids")
    return surfaces


def _validate_object_ids(raw: Any, label: str, id_key: str = "id") -> set[str]:
    ids: list[str] = []
    for index, entry in enumerate(_list(raw, label)):
        item = _require_object(entry, f"{label}[{index}]")
        ids.append(_nonempty(item.get(id_key), f"{label}[{index}].{id_key}"))
    _unique(ids, f"{label} ids")
    return set(ids)


def validate_request(request: Any, canonical_spec: Any) -> dict[str, Any]:
    value = _require_object(request, "build request")
    _strict_keys(value, {
        "contract", "runId", "specId", "specDigest", "taskDigest", "platformSurfaces",
        "architecture", "tasks", "acceptanceCriteria", "manualActions", "returnVersions",
        "requestDigest", "createdAt",
    }, set(), "build request")
    if value["contract"] != BUILD_REQUEST_CONTRACT:
        _fail(f"unsupported build request contract: {value['contract']!r}")
    _nonempty(value["runId"], "runId")
    spec_id = _nonempty(value["specId"], "specId")
    _digest(value["specDigest"], "specDigest")
    _digest(value["taskDigest"], "taskDigest")
    _digest(value["requestDigest"], "requestDigest")
    _timestamp(value["createdAt"], "createdAt")
    surfaces = _validate_platform_surfaces(value["platformSurfaces"])
    architecture = _require_object(value["architecture"], "architecture")
    _strict_keys(architecture, {"components", "contracts", "relationships", "flows", "specDependencies"}, set(), "architecture")
    for key in ("components", "contracts", "relationships", "flows", "specDependencies"):
        _list(architecture[key], f"architecture.{key}")
    components, contracts, dependency_specs = _validate_architecture(architecture)

    canonical = _require_object(canonical_spec, "canonical Spec")
    if _nonempty(canonical.get("id"), "canonical Spec id") != spec_id:
        _fail("specId does not match the canonical Spec id")
    if "platformSurfaces" not in canonical or normalize_json(surfaces) != normalize_json(canonical["platformSurfaces"]):
        _fail("platformSurfaces do not match the canonical Spec")
    if "architecture" not in canonical or normalize_json(architecture) != normalize_json(canonical["architecture"]):
        _fail("architecture does not match the canonical Spec")

    tasks = _list(value["tasks"], "tasks")
    if not tasks:
        _fail("tasks must not be empty")
    task_ids: list[str] = []
    acceptance_ids_list: list[str] = []
    for index, raw in enumerate(_list(value["acceptanceCriteria"], "acceptanceCriteria")):
        criterion = _require_object(raw, f"acceptanceCriteria[{index}]")
        _strict_keys(criterion, {"id", "statement"}, {"testHint"}, f"acceptanceCriteria[{index}]")
        acceptance_ids_list.append(_nonempty(criterion["id"], f"acceptanceCriteria[{index}].id"))
        _safe_text(_nonempty(criterion["statement"], f"acceptanceCriteria[{index}].statement"), f"acceptanceCriteria[{index}].statement")
        if "testHint" in criterion:
            if not isinstance(criterion["testHint"], str):
                _fail(f"acceptanceCriteria[{index}].testHint must be a string")
            _safe_text(criterion["testHint"], f"acceptanceCriteria[{index}].testHint")
    _unique(acceptance_ids_list, "acceptance criterion ids")
    acceptance_ids = set(acceptance_ids_list)

    manual_ids: list[str] = []
    for index, raw in enumerate(_list(value["manualActions"], "manualActions")):
        action = _require_object(raw, f"manualActions[{index}]")
        _strict_keys(action, {"id", "location", "action", "requiredValueName", "destination", "verification"}, set(), f"manualActions[{index}]")
        manual_id = _nonempty(action["id"], f"manualActions[{index}].id")
        _safe_manual_text(manual_id, f"manualActions[{index}].id")
        manual_ids.append(manual_id)
        for key in ("location", "action", "requiredValueName", "destination", "verification"):
            text = _nonempty(action[key], f"manualActions[{index}].{key}")
            _safe_manual_text(text, f"manualActions[{index}].{key}")
    _unique(manual_ids, "manual action ids")
    for index, raw in enumerate(tasks):
        task = _require_object(raw, f"tasks[{index}]")
        _strict_keys(task, {"id", "title", "componentRefs", "contractRefs", "requirementIds", "dependsOn", "acceptanceCriterionIds"}, set(), f"tasks[{index}]")
        task_ids.append(_nonempty(task["id"], f"tasks[{index}].id"))
        _nonempty(task["title"], f"tasks[{index}].title")
        for list_key in ("requirementIds", "dependsOn", "acceptanceCriterionIds"):
            entries = [_nonempty(item, f"tasks[{index}].{list_key}") for item in _list(task[list_key], f"tasks[{index}].{list_key}")]
            _unique(entries, f"tasks[{index}].{list_key}")
        for ref_key, expected_kind, local_ids in (("componentRefs", "component", components), ("contractRefs", "contract", contracts)):
            identities: list[str] = []
            for ref_index, raw_ref in enumerate(_list(task[ref_key], f"tasks[{index}].{ref_key}")):
                ref = _require_object(raw_ref, f"tasks[{index}].{ref_key}[{ref_index}]")
                _strict_keys(ref, {"specId", "kind", "id"}, set(), f"tasks[{index}].{ref_key}[{ref_index}]")
                ref_spec = _nonempty(ref["specId"], "qualified ref specId")
                ref_id = _nonempty(ref["id"], "qualified ref id")
                if ref["kind"] != expected_kind:
                    _fail(f"tasks[{index}].{ref_key}[{ref_index}] must be a {expected_kind} reference")
                if ref_spec == spec_id and ref_id not in local_ids:
                    _fail(f"tasks[{index}].{ref_key}[{ref_index}] references unknown local {expected_kind} {ref_id}")
                if ref_spec != spec_id and ref_spec not in dependency_specs:
                    _fail(f"tasks[{index}].{ref_key}[{ref_index}] has no declared Spec dependency")
                identities.append(f"{ref_spec}:{expected_kind}:{ref_id}")
            _unique(identities, f"tasks[{index}].{ref_key}")
    _unique(task_ids, "task ids")
    task_id_set = set(task_ids)
    requirement_ids = {item for task in tasks for item in task["requirementIds"]}
    target_owners: dict[str, list[str]] = {}
    for kind, targets in {
        "task": task_id_set,
        "component": components,
        "contract": contracts,
        "requirement": requirement_ids,
    }.items():
        for target in targets:
            target_owners.setdefault(target, []).append(kind)
    ambiguous_targets = {target: kinds for target, kinds in target_owners.items() if len(kinds) > 1}
    if ambiguous_targets:
        rendered = ", ".join(
            f"{target} ({'/'.join(sorted(kinds))})"
            for target, kinds in sorted(ambiguous_targets.items())
        )
        _fail(f"v1 target ids must be globally unique across mapping kinds: {rendered}")
    task_positions = {task_id: index for index, task_id in enumerate(task_ids)}
    for index, task in enumerate(tasks):
        for dependency in task["dependsOn"]:
            if dependency not in task_id_set:
                _fail(f"tasks[{index}].dependsOn references unknown task {dependency}")
            if task_positions[dependency] >= index:
                _fail(f"tasks[{index}].dependsOn must reference an earlier task in the final order")
        for criterion in task["acceptanceCriterionIds"]:
            if criterion not in acceptance_ids:
                _fail(f"tasks[{index}].acceptanceCriterionIds references unknown criterion {criterion}")

    versions = _require_object(value["returnVersions"], "returnVersions")
    _strict_keys(versions, {"implementationMap", "convergence"}, set(), "returnVersions")
    implementation_versions = _list(versions["implementationMap"], "returnVersions.implementationMap")
    convergence_versions = _list(versions["convergence"], "returnVersions.convergence")
    if not implementation_versions or any(item != IMPLEMENTATION_MAP_CONTRACT for item in implementation_versions):
        _fail("unsupported implementation-map return version")
    if not convergence_versions or any(item != CONVERGENCE_CONTRACT for item in convergence_versions):
        _fail("unsupported convergence return version")
    if value["taskDigest"] != digest_normalized(tasks):
        _fail("taskDigest does not match the final ordered task list")
    if value["requestDigest"] != digest_normalized(_request_projection(value)):
        _fail("requestDigest does not match its normalized self-digest projection")
    if value["specDigest"] != digest_normalized(canonical_spec):
        _fail("specDigest does not match the normalized canonical Spec")
    return value


def _intended_targets(request: dict[str, Any]) -> dict[str, set[str]]:
    return {
        "task": {task["id"] for task in request["tasks"]},
        "component": {item["id"] for item in request["architecture"]["components"]},
        "contract": {item["id"] for item in request["architecture"]["contracts"]},
        "requirement": {item for task in request["tasks"] for item in task["requirementIds"]},
    }


def _resolve_repo_file(workdir: Path, raw: Any, label: str, *, allow_internal: bool = False) -> tuple[str, Path]:
    if allow_internal:
        text = _safe_repo_path(raw, label)
        if not text.startswith(".build-loop/evidence/"):
            _fail(f"{label} must be under .build-loop/evidence/")
    else:
        text = _safe_repo_path(raw, label)
    root = workdir.resolve()
    joined = root / text
    cursor = root
    for segment in text.split("/"):
        cursor = cursor / segment
        if cursor.is_symlink():
            _fail(f"{label} must not traverse a symlink")
    candidate = joined.resolve()
    if candidate == root or root not in candidate.parents:
        _fail(f"{label} escapes the repository")
    if not candidate.is_file():
        _fail(f"{label} must name an existing non-symlink file")
    return text, candidate


def _verify_commit(workdir: Path, commit: str, label: str) -> str:
    if not COMMIT_RE.fullmatch(commit):
        _fail(f"{label} must be a 7-64 character hexadecimal commit")
    result = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=workdir, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        _fail(f"{label} does not resolve to a repository commit")
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=workdir, capture_output=True, text=True, check=False)
    if ancestor.returncode != 0:
        _fail(f"{label} is not reachable from the current repository HEAD")
    resolved = subprocess.run(["git", "rev-parse", f"{commit}^{{commit}}"], cwd=workdir, capture_output=True, text=True, check=False)
    if resolved.returncode != 0:
        _fail(f"{label} cannot be canonicalized")
    return resolved.stdout.strip()


def _verify_file_at_commit(workdir: Path, file_ref: str, commits: list[str], label: str) -> None:
    current = (workdir / file_ref).read_bytes()
    for commit in commits:
        result = subprocess.run(["git", "show", f"{commit}:{file_ref}"], cwd=workdir, capture_output=True, check=False)
        if result.returncode == 0 and result.stdout == current:
            return
    _fail(f"{label} is not byte-identical in any cited commit")


def build_implementation_map(
    request: dict[str, Any],
    draft_input: Any,
    *,
    workdir: Path,
    producer_version: str,
    producer_commit: str | None,
    created_at: str,
    verified_evidence_ids: set[str] | None = None,
) -> dict[str, Any]:
    draft = _require_object(draft_input, "evidence draft")
    _strict_keys(draft, {"mappings", "evidence", "deviations"}, set(), "evidence draft")
    created = _timestamp(created_at, "createdAt")
    if created < _timestamp(request["createdAt"], "request.createdAt"):
        _fail("implementation map cannot predate the build request")
    _nonempty(producer_version, "producer version")
    trusted_evidence = verified_evidence_ids or set()
    if producer_commit:
        producer_root = Path(__file__).resolve().parent.parent
        producer_commit = _verify_commit(producer_root, producer_commit, "producer commit")
        _verify_file_at_commit(
            producer_root,
            "scripts/groundwork_exchange.py",
            [producer_commit],
            "producer adapter",
        )

    intended = _intended_targets(request)
    owners: dict[str, list[str]] = {}
    for kind, targets in intended.items():
        for target in targets:
            owners.setdefault(target, []).append(kind)
    ambiguous = {target: kinds for target, kinds in owners.items() if len(kinds) > 1}
    if ambiguous:
        rendered = ", ".join(f"{target} ({'/'.join(sorted(kinds))})" for target, kinds in sorted(ambiguous.items()))
        _fail(f"v1 target ids must be globally unique across mapping kinds: {rendered}")

    evidence_output: list[dict[str, Any]] = []
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(_list(draft["evidence"], "evidence")):
        item = _require_object(raw, f"evidence[{index}]")
        _strict_keys(item, {"id", "kind", "command", "outcome", "recordedAt"}, {"summary", "artifactPath"}, f"evidence[{index}]")
        evidence_id = _nonempty(item["id"], f"evidence[{index}].id")
        if evidence_id in evidence_by_id:
            _fail(f"duplicate evidence id {evidence_id}")
        _enum(item["kind"], EVIDENCE_KINDS, f"evidence[{index}].kind")
        _enum(item["outcome"], EVIDENCE_OUTCOMES, f"evidence[{index}].outcome")
        recorded = _timestamp(item["recordedAt"], f"evidence[{index}].recordedAt")
        if recorded < _timestamp(request["createdAt"], "request.createdAt") or recorded > created:
            _fail(f"evidence[{index}].recordedAt falls outside the request/map window")
        command = _nonempty(item["command"], f"evidence[{index}].command")
        _safe_text(command, f"evidence[{index}].command")
        output = {key: item[key] for key in ("id", "kind", "command", "outcome")}
        if "summary" in item:
            if not isinstance(item["summary"], str):
                _fail(f"evidence[{index}].summary must be a string")
            _safe_text(item["summary"], f"evidence[{index}].summary")
            output["summary"] = item["summary"]
        if item["outcome"] == "passed" and "artifactPath" not in item:
            _fail(f"evidence[{index}] passed evidence requires artifactPath")
        if item["outcome"] == "passed" and evidence_id not in trusted_evidence:
            _fail(f"evidence[{index}] passed outcome requires --verified-evidence-id from Review-B")
        if "artifactPath" in item:
            _, artifact = _resolve_repo_file(workdir, item["artifactPath"], f"evidence[{index}].artifactPath", allow_internal=True)
            output["artifactDigest"] = digest_file(artifact)
        output["recordedAt"] = item["recordedAt"]
        evidence_output.append(output)
        evidence_by_id[evidence_id] = output
    missing_trusted = sorted(trusted_evidence - evidence_by_id.keys())
    if missing_trusted:
        _fail(f"verified evidence ids are missing from the draft: {', '.join(missing_trusted)}")

    deviations_output: list[dict[str, Any]] = []
    deviations_by_id: dict[str, dict[str, Any]] = {}
    all_targets = set().union(*intended.values())
    for index, raw in enumerate(_list(draft["deviations"], "deviations")):
        item = _require_object(raw, f"deviations[{index}]")
        _strict_keys(item, {"id", "targetId", "summary", "impact"}, set(), f"deviations[{index}]")
        deviation_id = _nonempty(item["id"], f"deviations[{index}].id")
        if deviation_id in deviations_by_id:
            _fail(f"duplicate deviation id {deviation_id}")
        target = _nonempty(item["targetId"], f"deviations[{index}].targetId")
        if target not in all_targets:
            _fail(f"deviations[{index}] targets unknown intent or has unsupported impact")
        _enum(item["impact"], IMPACTS, f"deviations[{index}].impact")
        _safe_text(_nonempty(item["summary"], f"deviations[{index}].summary"), f"deviations[{index}].summary")
        deviations_output.append(dict(item))
        deviations_by_id[deviation_id] = item

    mappings_output: list[dict[str, Any]] = []
    mapping_ids: set[str] = set()
    for index, raw in enumerate(_list(draft["mappings"], "mappings")):
        item = _require_object(raw, f"mappings[{index}]")
        _strict_keys(item, {"id", "kind", "targetId", "status", "fileRefs", "symbolRefs", "commitRefs", "testEvidenceIds", "runtimeEvidenceIds"}, {"deviationIds"}, f"mappings[{index}]")
        mapping_id = _nonempty(item["id"], f"mappings[{index}].id")
        if mapping_id in mapping_ids:
            _fail(f"duplicate mapping id {mapping_id}")
        mapping_ids.add(mapping_id)
        kind = item["kind"]
        target = _nonempty(item["targetId"], f"mappings[{index}].targetId")
        status = item["status"]
        kind = _enum(kind, KINDS, f"mappings[{index}].kind")
        status = _enum(status, STATUSES, f"mappings[{index}].status")
        if target not in intended[kind]:
            _fail(f"mappings[{index}] has unsupported kind/status or unknown target")
        file_refs = [_safe_repo_path(value, f"mappings[{index}].fileRefs") for value in _list(item["fileRefs"], f"mappings[{index}].fileRefs")]
        _unique(file_refs, f"mappings[{index}].fileRefs")
        for file_ref in file_refs:
            _resolve_repo_file(workdir, file_ref, f"mappings[{index}].fileRefs")
        symbols = [_nonempty(value, f"mappings[{index}].symbolRefs") for value in _list(item["symbolRefs"], f"mappings[{index}].symbolRefs")]
        for symbol_index, symbol in enumerate(symbols):
            _safe_text(symbol, f"mappings[{index}].symbolRefs[{symbol_index}]")
        commits = [_nonempty(value, f"mappings[{index}].commitRefs") for value in _list(item["commitRefs"], f"mappings[{index}].commitRefs")]
        tests = [_nonempty(value, f"mappings[{index}].testEvidenceIds") for value in _list(item["testEvidenceIds"], f"mappings[{index}].testEvidenceIds")]
        runtimes = [_nonempty(value, f"mappings[{index}].runtimeEvidenceIds") for value in _list(item["runtimeEvidenceIds"], f"mappings[{index}].runtimeEvidenceIds")]
        deviations = [_nonempty(value, f"mappings[{index}].deviationIds") for value in _list(item.get("deviationIds", []), f"mappings[{index}].deviationIds")]
        for values, label in ((symbols, "symbolRefs"), (commits, "commitRefs"), (tests, "testEvidenceIds"), (runtimes, "runtimeEvidenceIds"), (deviations, "deviationIds")):
            _unique(values, f"mappings[{index}].{label}")
        commits = [_verify_commit(workdir, commit, f"mappings[{index}].commitRefs") for commit in commits]
        referenced: list[dict[str, Any]] = []
        for evidence_id in tests:
            evidence = evidence_by_id.get(evidence_id)
            if not evidence or evidence["kind"] != "test":
                _fail(f"mappings[{index}] references missing or non-test evidence {evidence_id}")
            referenced.append(evidence)
        for evidence_id in runtimes:
            evidence = evidence_by_id.get(evidence_id)
            if not evidence or evidence["kind"] != "runtime":
                _fail(f"mappings[{index}] references missing or non-runtime evidence {evidence_id}")
            referenced.append(evidence)
        for deviation_id in deviations:
            deviation = deviations_by_id.get(deviation_id)
            if not deviation or deviation["targetId"] != target:
                _fail(f"mappings[{index}] references an invalid deviation {deviation_id}")
        if status in {"implemented", "verified"} and (not file_refs or not commits):
            _fail(f"mappings[{index}] {status} requires both repository fileRefs and commitRefs")
        for file_ref in file_refs:
            _verify_file_at_commit(workdir, file_ref, commits, f"mappings[{index}].fileRefs {file_ref}")
        if status == "verified" and not any(evidence["outcome"] == "passed" for evidence in referenced):
            _fail(f"mappings[{index}] verified requires passing test or runtime evidence")
        if status == "implemented" and any(evidence["outcome"] == "passed" for evidence in referenced):
            _fail(f"mappings[{index}] implemented contradicts passing verification evidence")
        if status == "not-started" and (file_refs or symbols or commits or referenced or deviations):
            _fail(f"mappings[{index}] not-started cannot carry implementation evidence or deviations")
        if status == "diverged" and not deviations:
            _fail(f"mappings[{index}] diverged requires a declared deviation")
        output = dict(item)
        output["commitRefs"] = commits
        output["deviationIds"] = deviations
        mappings_output.append(output)

    producer: dict[str, Any] = {"name": "build-loop", "version": producer_version}
    if producer_commit:
        producer["commit"] = producer_commit
    result: dict[str, Any] = {
        "contract": IMPLEMENTATION_MAP_CONTRACT,
        "runId": request["runId"],
        "buildRequestDigest": request["requestDigest"],
        "specDigest": request["specDigest"],
        "taskDigest": request["taskDigest"],
        "producer": producer,
        "mappings": mappings_output,
        "evidence": evidence_output,
        "deviations": deviations_output,
        "implementationMapDigest": "sha256:" + "0" * 64,
        "createdAt": created_at,
    }
    result["implementationMapDigest"] = digest_normalized({key: value for key, value in result.items() if key != "implementationMapDigest"})
    return result


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _resolve_producer_version(value: str) -> str:
    if value != "auto":
        return _nonempty(value, "producer version")
    root = Path(__file__).resolve().parent.parent
    for relative in (Path(".claude-plugin/plugin.json"), Path(".codex-plugin/plugin.json"), Path("package.json")):
        candidate = root / relative
        if candidate.is_file():
            parsed = _require_object(_load_json(candidate), str(relative))
            version = parsed.get("version")
            if isinstance(version, str) and version:
                return version
    _fail("producer version auto-discovery found no versioned plugin manifest")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-request", help="validate a Groundwork BuildRequest v1")
    validate.add_argument("--request", required=True)
    validate.add_argument("--spec", required=True)
    validate.add_argument("--output", help="atomically write the validated request summary")
    emit = sub.add_parser("emit-map", help="verify evidence and emit ImplementationMap v1")
    emit.add_argument("--request", required=True)
    emit.add_argument("--spec", required=True)
    emit.add_argument("--evidence", required=True)
    emit.add_argument("--workdir", default=".")
    emit.add_argument("--output", required=True)
    emit.add_argument("--producer-version", required=True)
    emit.add_argument("--producer-commit")
    emit.add_argument("--created-at", required=True)
    emit.add_argument(
        "--verified-evidence-id",
        action="append",
        default=[],
        help="evidence id whose command Review-B actually executed successfully; repeat per passed receipt",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        request = validate_request(_load_json(Path(args.request)), _load_json(Path(args.spec)))
        if args.command == "validate-request":
            result = {
                "ok": True,
                "contract": request["contract"],
                "runId": request["runId"],
                "requestDigest": request["requestDigest"],
                "taskDigest": request["taskDigest"],
                "taskIds": [task["id"] for task in request["tasks"]],
            }
            if args.output:
                _atomic_write_json(Path(args.output), result)
        else:
            result = build_implementation_map(
                request,
                _load_json(Path(args.evidence)),
                workdir=Path(args.workdir).resolve(),
                producer_version=_resolve_producer_version(args.producer_version),
                producer_commit=args.producer_commit,
                created_at=args.created_at,
                verified_evidence_ids=set(args.verified_evidence_id),
            )
            _atomic_write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except ExchangeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
