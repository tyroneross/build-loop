"""Preserve full fixture evidence before pytest abbreviates a differential failure."""
import hashlib
import json
from dataclasses import asdict, replace
import re
from pathlib import Path


def save_failure(root: Path, case: str, binary: Path, oracle, native) -> Path:
    # A sibling survives the next build_world reset and is not part of the compared tree.
    path = root.parent / "parity-failure.json"
    def encode(value):
        if isinstance(value, bytes):
            return {"hex": value.hex()}
        raise TypeError(type(value).__name__)
    try:
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    except OSError:
        digest = None
    packet = {"case": case, "binary": str(binary),
              "binary_sha256": digest,
              "oracle": asdict(oracle), "native": asdict(native)}
    path.write_text(json.dumps(packet, default=encode, ensure_ascii=True, indent=2) + "\n")
    return path


_DIAGNOSTIC = re.compile(rb"bl: diagnostic: hook=(?:pre-edit|trace) reason=[a-z_]+ elapsed_ms=[0-9]+\n")


def without_diagnostics(snapshot):
    """Keep all ordinary stderr; raw snapshots remain available for failure evidence."""
    return replace(snapshot, stderr=b"".join(
        line for line in snapshot.stderr.splitlines(keepends=True)
        if not _DIAGNOSTIC.fullmatch(line)))
