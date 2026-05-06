"""NavGator adapter implementation.

Mode resolution:
    * ``auto`` (default) — native engine for ported capabilities; escalate to
      NavGator only for ``llm_map``/``schema``/``diagram``. If NavGator is
      absent, escalation-only methods return ``{"available": False, ...}``.
    * ``native`` — force native engine. Escalation-only methods raise
      ``CapabilityNotAvailable``.
    * ``navgator`` — force NavGator subprocess. If NavGator is absent any call
      raises ``NavGatorNotAvailable``.

NavGator subprocess invocation goes through ``_run_navgator`` which enforces a
30-second timeout and converts non-zero exits / missing-binary errors into
``AdapterError`` subclasses. JSON output from NavGator is passed through
verbatim (no reshaping) — the adapter is a transport layer.

Tests must NOT depend on NavGator being installed. Both
``is_navgator_available`` (which probes ``shutil.which`` and ``.mcp.json``) and
``_run_navgator`` (which calls ``subprocess.run``) are monkeypatch-friendly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence

from .. import analysis as A
from ..scanner import scan_repo
from ..schemas import Component, Connection, SCHEMA_VERSION
from ..storage import (
    arch_dir,
    read_index,
    read_manifest,
    write_file_map,
    write_graph,
    write_hashes,
    write_index,
    write_manifest,
    write_reverse_deps,
    write_timeline,
)


AdapterMode = Literal["auto", "native", "navgator"]

# Capabilities not yet ported into the native engine. Reaching these in
# ``native`` mode raises ``CapabilityNotAvailable``.
ESCALATION_ONLY: frozenset[str] = frozenset({"llm_map", "schema", "diagram"})

# How long NavGator subprocess calls may run before we kill them.
NAVGATOR_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


class AdapterError(Exception):
    """Base error for adapter failures."""


class NavGatorNotAvailable(AdapterError):
    """NavGator was requested (mode='navgator') but is not installed."""


class CapabilityNotAvailable(AdapterError):
    """A NavGator-only capability was requested in mode='native'."""


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _mcp_has_navgator(workdir: Path) -> bool:
    """Return True if a project ``.mcp.json`` registers a NavGator MCP server.

    Checks for any of: ``plugin_navgator``, ``navgator``, or a key matching
    ``*navgator*`` (case-insensitive). Robust to malformed JSON — we never
    let detection raise.
    """
    for candidate in (workdir / ".mcp.json", workdir / ".claude" / ".mcp.json"):
        try:
            if not candidate.exists():
                continue
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        servers = (data or {}).get("mcpServers") or {}
        if not isinstance(servers, dict):
            continue
        for key in servers.keys():
            if "navgator" in key.lower():
                return True
    return False


def is_navgator_available(workdir: Optional[Path | str] = None) -> bool:
    """Return True if NavGator can be invoked from this environment.

    True if EITHER:
        * ``shutil.which('navgator')`` returns a path, OR
        * ``.mcp.json`` (project or ``.claude/.mcp.json``) registers a
          NavGator MCP server.

    A NavGator MCP server is sufficient because callers may route through MCP
    tools rather than the CLI binary. The subprocess code path still requires
    the CLI; if only the MCP path is present, ``_run_navgator`` will raise
    ``NavGatorNotAvailable`` and the auto-mode escalation will return
    ``{"available": False}``.
    """
    if shutil.which("navgator"):
        return True
    wd = Path(workdir) if workdir else Path(os.getcwd())
    return _mcp_has_navgator(wd)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class Adapter:
    """Capability dispatcher across native engine and NavGator subprocess.

    Construction is cheap and side-effect-free. Each method resolves the
    backend at call time using ``self.mode`` and current detection.

    Args:
        mode: ``auto`` | ``native`` | ``navgator``. Defaults to ``auto``.
    """

    def __init__(self, mode: AdapterMode = "auto") -> None:
        if mode not in ("auto", "native", "navgator"):
            raise ValueError(
                f"invalid mode: {mode!r} (expected 'auto', 'native', or 'navgator')"
            )
        self.mode: AdapterMode = mode

    # -- Internal routing ---------------------------------------------------

    def _require_navgator(self, workdir: Path) -> None:
        if not is_navgator_available(workdir):
            raise NavGatorNotAvailable(
                "NavGator is not installed (no `navgator` CLI on PATH and no "
                "navgator MCP server in .mcp.json). Install via "
                "`npm i -g @tyroneross/navgator` or use mode='auto'/'native'."
            )

    def _use_navgator(self, capability: str, workdir: Path) -> bool:
        """Decide whether the active mode + capability should hit NavGator."""
        if self.mode == "navgator":
            return True
        if self.mode == "native":
            return False
        # auto: native is canonical for ported capabilities; escalate only for
        # the three capabilities NavGator owns exclusively.
        if capability in ESCALATION_ONLY:
            return is_navgator_available(workdir)
        return False

    def _capability_unavailable(self, capability: str) -> Dict[str, Any]:
        return {
            "available": False,
            "reason": (
                f"NavGator not installed; {capability.replace('_', '-')} "
                "unavailable until ported into the native engine."
            ),
            "capability": capability,
        }

    # -- Subprocess transport ----------------------------------------------

    def _run_navgator(
        self,
        args: Sequence[str],
        workdir: Path,
        timeout: int = NAVGATOR_TIMEOUT_S,
    ) -> Dict[str, Any]:
        """Invoke ``navgator`` and return parsed JSON.

        Always passes ``--json --agent`` (callers pass only the subcommand and
        capability-specific flags; transport flags are appended here).

        Raises:
            NavGatorNotAvailable: binary missing.
            AdapterError: non-zero exit, timeout, or unparseable JSON.
        """
        binary = shutil.which("navgator")
        if not binary:
            raise NavGatorNotAvailable(
                "`navgator` CLI not found on PATH. Install with "
                "`npm i -g @tyroneross/navgator`."
            )
        cmd: List[str] = [binary, *args, "--json", "--agent"]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise AdapterError(
                f"navgator timed out after {timeout}s: {' '.join(cmd)}"
            ) from e
        except FileNotFoundError as e:  # race: PATH changed mid-call
            raise NavGatorNotAvailable(str(e)) from e

        if proc.returncode != 0:
            raise AdapterError(
                f"navgator exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        out = proc.stdout.strip()
        if not out:
            return {}
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            raise AdapterError(
                f"navgator returned non-JSON output (first 200 chars): {out[:200]!r}"
            ) from e

    # -- Native helpers ----------------------------------------------------

    @staticmethod
    def _load_graph(workdir: Path) -> tuple[List[Component], List[Connection]]:
        idx = read_index(workdir)
        if not idx:
            return [], []
        comps = [Component(**c) for c in idx.get("components", [])]
        conns = [Connection(**c) for c in idx.get("connections", [])]
        return comps, conns

    @staticmethod
    def _resolve_target(
        target: str,
        components: Sequence[Component],
    ) -> Optional[Component]:
        if not components:
            return None
        for c in components:
            if c.component_id == target:
                return c
        target_norm = target.replace(os.sep, "/").lstrip("./")
        for c in components:
            if c.metadata.get("file") == target_norm:
                return c
        for c in components:
            f = c.metadata.get("file", "")
            if f.endswith("/" + target_norm) or f == target_norm:
                return c
        for c in components:
            if c.name.endswith(target_norm) or c.name == target_norm:
                return c
        return None

    # -- Capability methods -------------------------------------------------

    def scan(self, workdir: Path | str, incremental: bool = False) -> Dict[str, Any]:
        """Scan repo. Native is canonical; NavGator is a subprocess passthrough."""
        wd = Path(workdir).resolve()
        if self._use_navgator("scan", wd):
            self._require_navgator(wd)
            args = ["scan"]
            if incremental:
                args.append("--incremental")
            return self._run_navgator(args, wd)

        # Native path — mirrors cli.cmd_scan minus argparse glue.
        import time

        t0 = time.time()
        result = scan_repo(wd)
        elapsed_ms = int((time.time() - t0) * 1000)

        write_index(wd, result.to_index())
        graph = {
            "nodes": [
                {
                    "id": c.component_id,
                    "name": c.name,
                    "layer": (
                        c.role.layer
                        if hasattr(c.role, "layer")
                        else (c.role or {}).get("layer", "unknown")
                    ),
                }
                for c in result.components
            ],
            "edges": [
                {"from": conn.from_id, "to": conn.to_id, "type": conn.type}
                for conn in result.connections
            ],
        }
        write_graph(wd, graph)
        write_file_map(wd, {"files": result.file_map})
        write_hashes(wd, {"files": result.hashes})

        rev: Dict[str, List[str]] = {}
        for conn in result.connections:
            rev.setdefault(conn.to_id, []).append(conn.from_id)
        write_reverse_deps(wd, {"reverse_deps": rev})

        now_ms = int(time.time() * 1000)
        prior = read_manifest(wd) or {}
        timeline = {
            "events": (prior.get("timeline") or [])
            + [
                {
                    "event": "scan",
                    "mode": "incremental" if incremental else "full",
                    "ts": now_ms,
                    "elapsed_ms": elapsed_ms,
                    "components": len(result.components),
                    "connections": len(result.connections),
                }
            ][-50:]
        }
        write_timeline(wd, timeline)

        write_manifest(
            wd,
            {
                "schema_version": SCHEMA_VERSION,
                "generator": "build-loop-native",
                "generator_version": "0.1.0",
                "repo_root": str(wd),
                "component_count": len(result.components),
                "connection_count": len(result.connections),
                "files_scanned": result.files_scanned,
                "generated_at": now_ms,
                "last_full_scan_at": now_ms
                if not incremental
                else (prior.get("last_full_scan_at") or 0),
                "last_incremental_at": now_ms
                if incremental
                else (prior.get("last_incremental_at") or 0),
                "elapsed_ms": elapsed_ms,
            },
        )

        return {
            "ok": True,
            "components": len(result.components),
            "connections": len(result.connections),
            "files_scanned": result.files_scanned,
            "elapsed_ms": elapsed_ms,
            "arch_dir": str(arch_dir(wd)),
        }

    def impact(self, component: str, workdir: Path | str) -> Dict[str, Any]:
        wd = Path(workdir).resolve()
        if self._use_navgator("impact", wd):
            self._require_navgator(wd)
            return self._run_navgator(["impact", component], wd)

        comps, conns = self._load_graph(wd)
        if not comps:
            return {"ok": False, "error": "No index found. Run `scan` first."}
        target = self._resolve_target(component, comps)
        if not target:
            return {"ok": False, "error": f"target not found: {component}"}
        report = A.compute_impact(target.component_id, comps, conns)
        return {
            "ok": True,
            "target": {
                "component_id": target.component_id,
                "name": target.name,
                "file": target.metadata.get("file"),
            },
            **report.to_dict(),
        }

    def trace(
        self,
        component: str,
        workdir: Path | str,
        depth: int = 3,
        direction: str = "both",
    ) -> Dict[str, Any]:
        wd = Path(workdir).resolve()
        if self._use_navgator("trace", wd):
            self._require_navgator(wd)
            return self._run_navgator(
                ["trace", component, "--depth", str(depth), "--direction", direction],
                wd,
            )

        comps, conns = self._load_graph(wd)
        if not comps:
            return {"ok": False, "error": "No index found. Run `scan` first."}
        target = self._resolve_target(component, comps)
        if not target:
            return {"ok": False, "error": f"target not found: {component}"}
        paths = A.trace_dataflow(
            target.component_id, comps, conns, depth=depth, direction=direction
        )
        return {
            "ok": True,
            "target": target.component_id,
            "direction": direction,
            "depth": depth,
            "paths": paths,
            "path_count": len(paths),
        }

    def connections(self, component: str, workdir: Path | str) -> Dict[str, Any]:
        wd = Path(workdir).resolve()
        if self._use_navgator("connections", wd):
            self._require_navgator(wd)
            return self._run_navgator(["connections", component], wd)

        comps, conns = self._load_graph(wd)
        if not comps:
            return {"ok": False, "error": "No index found. Run `scan` first."}
        target = self._resolve_target(component, comps)
        if not target:
            return {"ok": False, "error": f"target not found: {component}"}
        out_edges = [c.to_dict() for c in conns if c.from_id == target.component_id]
        in_edges = [c.to_dict() for c in conns if c.to_id == target.component_id]
        return {
            "ok": True,
            "component_id": target.component_id,
            "name": target.name,
            "outgoing": out_edges,
            "incoming": in_edges,
            "outgoing_count": len(out_edges),
            "incoming_count": len(in_edges),
        }

    def rules(self, workdir: Path | str) -> Dict[str, Any]:
        wd = Path(workdir).resolve()
        if self._use_navgator("rules", wd):
            self._require_navgator(wd)
            return self._run_navgator(["rules"], wd)

        comps, conns = self._load_graph(wd)
        if not comps:
            return {"ok": False, "error": "No index found. Run `scan` first."}
        violations = A.check_rules(comps, conns)
        return {
            "ok": True,
            "violation_count": len(violations),
            "violations": [v.to_dict() for v in violations],
        }

    def dead(self, workdir: Path | str) -> Dict[str, Any]:
        wd = Path(workdir).resolve()
        if self._use_navgator("dead", wd):
            self._require_navgator(wd)
            return self._run_navgator(["dead"], wd)

        comps, conns = self._load_graph(wd)
        if not comps:
            return {"ok": False, "error": "No index found. Run `scan` first."}
        report = A.find_dead(comps, conns, repo_root=wd)
        return {"ok": True, **report.to_dict()}

    # -- Escalation-only capabilities --------------------------------------

    def llm_map(self, workdir: Path | str) -> Dict[str, Any]:
        wd = Path(workdir).resolve()
        if self.mode == "native":
            raise CapabilityNotAvailable(
                "llm-map is NavGator-only. Use mode='auto' or 'navgator', or "
                "wait for the capability to be ported into the native engine."
            )
        if self.mode == "navgator" or self._use_navgator("llm_map", wd):
            if self.mode == "navgator":
                self._require_navgator(wd)
            elif not is_navgator_available(wd):
                return self._capability_unavailable("llm_map")
            return self._run_navgator(["llm-map"], wd)
        return self._capability_unavailable("llm_map")

    def schema(
        self, workdir: Path | str, model: Optional[str] = None
    ) -> Dict[str, Any]:
        wd = Path(workdir).resolve()
        if self.mode == "native":
            raise CapabilityNotAvailable(
                "schema is NavGator-only. Use mode='auto' or 'navgator'."
            )
        if self.mode == "navgator" or self._use_navgator("schema", wd):
            if self.mode == "navgator":
                self._require_navgator(wd)
            elif not is_navgator_available(wd):
                return self._capability_unavailable("schema")
            args: List[str] = ["schema"]
            if model:
                args.append(model)
            return self._run_navgator(args, wd)
        return self._capability_unavailable("schema")

    def diagram(
        self,
        workdir: Path | str,
        mode: str = "summary",
        focus: Optional[str] = None,
    ) -> Dict[str, Any]:
        wd = Path(workdir).resolve()
        if self.mode == "native":
            raise CapabilityNotAvailable(
                "diagram is NavGator-only. Use mode='auto' or 'navgator'."
            )
        if self.mode == "navgator" or self._use_navgator("diagram", wd):
            if self.mode == "navgator":
                self._require_navgator(wd)
            elif not is_navgator_available(wd):
                return self._capability_unavailable("diagram")
            args: List[str] = ["diagram", "--mode", mode]
            if focus:
                args.extend(["--focus", focus])
            return self._run_navgator(args, wd)
        return self._capability_unavailable("diagram")
