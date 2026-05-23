# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Schemas mirroring NavGator's component/connection JSON shape.

Ground truth: ``/Users/tyroneross/dev/git-folder/NavGator/.navgator/architecture/``.
We use ``dataclasses`` (per project memory: minimal deps, no pydantic) and
explicitly preserve any unknown keys via ``extra`` so future NavGator schema
extensions round-trip without code changes. The constructors accept ``**kwargs``
and stash anything not declared into ``extra``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Open-vocabulary type validation (D7 — warn, never drop).
#
# The Component/Connection constructors already accept an arbitrary string
# ``type`` and stash unknown keys in ``extra``/``raw``; these validators add
# the *gate* that enrich/digest/diagram call. An unknown type returns
# ``ok=True`` (RETAINED) plus a warning string so the unknown threads the full
# chain (schema → enrich → digest → diagram → checkpoint) without being
# dropped. Adding a type is additive and never bumps SCHEMA_VERSION.
# ---------------------------------------------------------------------------

def _validate_type(kind: str, t: str) -> tuple[bool, str, Optional[str]]:
    # Lazy import — keeps schemas.py free of an import cycle with _taxonomy.
    from . import _taxonomy as _tx

    known = (
        _tx.known_node_types() if kind == "node" else _tx.known_edge_types()
    )
    if t in known:
        return True, t, None
    # D7: unknown is retained, flagged with a warning — never rejected/dropped.
    return (
        True,
        t,
        f"unknown {kind} type {t!r} — retained (open vocab, warn-not-drop, D7)",
    )


def validate_node_type(t: str) -> tuple[bool, str, Optional[str]]:
    """(ok, normalized, warning|None). Unknown → ok=True + warning (retained)."""
    return _validate_type("node", t)


def validate_edge_type(t: str) -> tuple[bool, str, Optional[str]]:
    """(ok, normalized, warning|None). Unknown → ok=True + warning (retained)."""
    return _validate_type("edge", t)


def _split_known(declared: set[str], data: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    known = {k: v for k, v in data.items() if k in declared}
    extra = {k: v for k, v in data.items() if k not in declared}
    return known, extra


# ---------------------------------------------------------------------------
# Component
# ---------------------------------------------------------------------------

@dataclass
class Role:
    purpose: str = ""
    layer: str = "unknown"
    critical: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Role":
        if not data:
            return cls()
        declared = {"purpose", "layer", "critical"}
        known, extra = _split_known(declared, data)
        return cls(extra=extra, **known)

    def to_dict(self) -> Dict[str, Any]:
        out = {"purpose": self.purpose, "layer": self.layer, "critical": self.critical}
        out.update(self.extra)
        return out


@dataclass
class Source:
    detection_method: str = "auto"
    config_files: List[str] = field(default_factory=list)
    confidence: float = 0.95
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Source":
        if not data:
            return cls()
        declared = {"detection_method", "config_files", "confidence"}
        known, extra = _split_known(declared, data)
        return cls(extra=extra, **known)

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "detection_method": self.detection_method,
            "config_files": list(self.config_files),
            "confidence": self.confidence,
        }
        out.update(self.extra)
        return out


@dataclass
class Component:
    component_id: str
    name: str
    type: str = "component"
    role: Role = field(default_factory=Role)
    source: Source = field(default_factory=Source)
    connects_to: List[str] = field(default_factory=list)
    connected_from: List[str] = field(default_factory=list)
    status: str = "active"
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: int = 0
    last_updated: int = 0
    stable_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        # Accept full NavGator-shape dicts via Component(**json_data).
        declared = {
            "component_id", "name", "type", "role", "source",
            "connects_to", "connected_from", "status", "tags", "metadata",
            "timestamp", "last_updated", "stable_id",
        }
        known, extra = _split_known(declared, data)
        # Required-ish fields; default if absent.
        self.component_id = known.get("component_id", "")
        self.name = known.get("name", "")
        self.type = known.get("type", "component")
        self.role = Role.from_dict(known.get("role")) if isinstance(known.get("role"), dict) else known.get("role") or Role()
        self.source = Source.from_dict(known.get("source")) if isinstance(known.get("source"), dict) else known.get("source") or Source()
        self.connects_to = list(known.get("connects_to") or [])
        self.connected_from = list(known.get("connected_from") or [])
        self.status = known.get("status", "active")
        self.tags = list(known.get("tags") or [])
        self.metadata = dict(known.get("metadata") or {})
        self.timestamp = int(known.get("timestamp") or 0)
        self.last_updated = int(known.get("last_updated") or 0)
        self.stable_id = known.get("stable_id", "")
        self.extra = extra

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "component_id": self.component_id,
            "name": self.name,
            "type": self.type,
            "role": self.role.to_dict() if isinstance(self.role, Role) else self.role,
            "source": self.source.to_dict() if isinstance(self.source, Source) else self.source,
            "connects_to": list(self.connects_to),
            "connected_from": list(self.connected_from),
            "status": self.status,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "timestamp": self.timestamp,
            "last_updated": self.last_updated,
            "stable_id": self.stable_id,
        }
        out.update(self.extra)
        return out


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

@dataclass
class Connection:
    """Connection record.

    NavGator's on-disk shape uses nested ``from``/``to`` objects with
    ``component_id`` + ``location`` keys, plus ``connection_type``,
    ``code_reference``, and a ``semantic`` block. We preserve every field
    verbatim and additionally expose flat aliases (``from_id``, ``to_id``,
    ``classification``, etc.) for ergonomic access in analysis code.
    """

    connection_id: str = ""
    from_id: str = ""
    to_id: str = ""
    from_stable: str = ""
    to_stable: str = ""
    type: str = "imports"  # connection_type
    file: str = ""
    line: int = 0
    symbol: str = ""
    symbol_type: str = "import"
    confidence: float = 1.0
    classification: str = "production"
    detected_from: str = "build-loop-native-scanner"
    description: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        # Flat constructor (used by scanner.py).
        flat_keys = {
            "connection_id", "from_id", "to_id", "from_stable", "to_stable",
            "type", "file", "line", "symbol", "symbol_type", "confidence",
            "classification", "detected_from", "description",
        }
        if any(k in data for k in flat_keys) and "from" not in data:
            self.connection_id = data.get("connection_id", "")
            self.from_id = data.get("from_id", "")
            self.to_id = data.get("to_id", "")
            self.from_stable = data.get("from_stable", "")
            self.to_stable = data.get("to_stable", "")
            self.type = data.get("type", "imports")
            self.file = data.get("file", "")
            self.line = int(data.get("line") or 0)
            self.symbol = data.get("symbol", "")
            self.symbol_type = data.get("symbol_type", "import")
            self.confidence = float(data.get("confidence") if data.get("confidence") is not None else 1.0)
            self.classification = data.get("classification", "production")
            self.detected_from = data.get("detected_from", "build-loop-native-scanner")
            self.description = data.get("description", "")
            self.raw = data.get("raw", {})
            return

        # NavGator-shape constructor.
        self.connection_id = data.get("connection_id", "")
        frm = data.get("from") or {}
        to = data.get("to") or {}
        self.from_id = frm.get("component_id", "")
        self.to_id = to.get("component_id", "")
        self.from_stable = frm.get("stable_id", "")
        self.to_stable = to.get("stable_id", "")
        self.type = data.get("connection_type", "imports")
        loc = frm.get("location") or {}
        self.file = loc.get("file", "")
        self.line = int(loc.get("line") or 0)
        code_ref = data.get("code_reference") or {}
        self.symbol = code_ref.get("symbol", "")
        self.symbol_type = code_ref.get("symbol_type", "import")
        self.confidence = float(data.get("confidence") if data.get("confidence") is not None else 1.0)
        sem = data.get("semantic") or {}
        self.classification = sem.get("classification", "production")
        self.detected_from = data.get("detected_from", "build-loop-native-scanner")
        self.description = data.get("description", "")
        self.raw = data

    def to_dict(self) -> Dict[str, Any]:
        """Emit NavGator-shape on-disk JSON."""
        if self.raw:
            # Round-trip preserve unknown fields.
            out = dict(self.raw)
            out["connection_id"] = self.connection_id
            return out
        out = {
            "connection_id": self.connection_id,
            "from": {
                "component_id": self.from_id,
                "stable_id": self.from_stable,
                "location": {"file": self.file, "line": self.line},
            },
            "to": {
                "component_id": self.to_id,
                "stable_id": self.to_stable,
                "location": {"file": self.file, "line": self.line},
            },
            "connection_type": self.type,
            "code_reference": {
                "file": self.file,
                "symbol": self.symbol,
                "symbol_type": self.symbol_type,
                "line_start": self.line,
            },
            "detected_from": self.detected_from,
            "confidence": self.confidence,
            "timestamp": 0,
            "last_verified": 0,
            "semantic": {"classification": self.classification, "confidence": 0.4},
        }
        if self.description:
            out["description"] = self.description
        return out


# ---------------------------------------------------------------------------
# Index / Manifest envelopes
# ---------------------------------------------------------------------------

@dataclass
class Index:
    schema_version: str = SCHEMA_VERSION
    components: List[Dict[str, Any]] = field(default_factory=list)
    connections: List[Dict[str, Any]] = field(default_factory=list)
    component_count: int = 0
    connection_count: int = 0
    generated_at: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Manifest:
    schema_version: str = SCHEMA_VERSION
    generator: str = "build-loop-native"
    generator_version: str = "0.1.0"
    repo_root: str = ""
    component_count: int = 0
    connection_count: int = 0
    files_scanned: int = 0
    generated_at: int = 0
    last_full_scan_at: int = 0
    last_incremental_at: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Lesson (Chunk 1 stub mirroring NavGator's lesson shape)
# ---------------------------------------------------------------------------

@dataclass
class Lesson:
    id: str
    category: str = "general"
    pattern: str = ""
    signature: str = ""
    severity: str = "info"  # info | warning | error
    context: Dict[str, Any] = field(default_factory=dict)
    example: str = ""
    validation: str = ""
    promoted: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Lesson":
        declared = {
            "id", "category", "pattern", "signature", "severity",
            "context", "example", "validation", "promoted",
        }
        known, extra = _split_known(declared, data)
        return cls(extra=extra, **known)

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "id": self.id,
            "category": self.category,
            "pattern": self.pattern,
            "signature": self.signature,
            "severity": self.severity,
            "context": dict(self.context),
            "example": self.example,
            "validation": self.validation,
            "promoted": self.promoted,
        }
        out.update(self.extra)
        return out
