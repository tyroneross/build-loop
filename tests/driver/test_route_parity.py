# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""`bl route` must select the model the Python resolvers select.

Compared, for every tier token (ladder rungs and legacy tokens), segment and
host the taxonomy defines, over several `.build-loop/` states (outages,
overrides, tier cache, host allowlist):
  - `scripts/model_index.py resolve`  vs  `bl route resolve`
  - `scripts/resolve_agent_model.py`  vs  `bl route agent`, for every agent.
The Python side runs each script's own `main()` in-process (the code path of
the command line); a sample runs both as real commands, under several
environments, to hold host detection and the command lines equal.

`python3 tests/driver/test_route_parity.py --report` prints the counts and
the timing of both resolvers.
"""
from __future__ import annotations

import contextlib
import io
import itertools
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
BL = Path(os.environ.get("BL_BIN") or REPO / "target" / "release" / "bl")
sys.path.insert(0, str(SCRIPTS))

import model_index  # noqa: E402
import resolve_agent_model  # noqa: E402

TAXONOMY = json.loads((REPO / "references" / "model-taxonomy.json").read_text())
LADDER = list(TAXONOMY["tiers"]["order"])
LEGACY = [k for k in TAXONOMY["tiers"]["legacy_aliases"] if not k.startswith("_")]
TIERS = LADDER + LEGACY + ["T9"]
SEGMENTS = [None] + list(TAXONOMY["segments"]) + ["no_such_segment"]
HOST_ROWS = {k: v for k, v in TAXONOMY["hosts"].items() if not k.startswith("_")}
PROVIDERS = sorted({p for row in HOST_ROWS.values() for p in row["providers"]})
ALL_HOSTS = (
    [None, "any", "zzz", "codex,local", "CLAUDE", " , "]
    + list(HOST_ROWS)
    + [a for row in HOST_ROWS.values() for a in row.get("aliases", [])]
    + PROVIDERS
)
CORE_HOSTS = [None, "any", "claude_code", "codex", "cursor", "local"]
AGENTS = sorted(p.stem for p in (REPO / "agents").glob("*.md"))

needs_bl = pytest.mark.skipif(not BL.exists(), reason="bl not built (cargo build --release)")


def _states(now: float) -> dict[str, dict[str, object]]:
    """`.build-loop/` file sets, by scenario name."""
    def down(*ids: str, ttl: int = 3600) -> dict:
        return {"unavailable": [{"id": i, "recorded_at": now, "ttl": ttl} for i in ids]}

    defaults = sorted({TAXONOMY["preferred"]["generative_reasoning"][r][0] for r in ("T1", "T2", "T3", "T4")})
    opus_canonical = (TAXONOMY["models"]["opus"].get("aliases") or ["opus"])[0]
    return {
        "clean": {},
        "opus-down": {"model-availability.json": down("opus")},
        "opus-down-by-alias": {"model-availability.json": down(opus_canonical)},
        "opus-fable-down": {"model-availability.json": down("opus", "fable")},
        "defaults-down": {"model-availability.json": down(*defaults)},
        "expired-and-legacy": {"model-availability.json": {"unavailable": ["opus", {"id": "sonnet", "recorded_at": now - 7200, "ttl": 60}, {"id": "haiku"}]}},
        "host-allowlist": {"model-availability.json": {"hostProviders": ["openai", "Local"]}},
        "config-override": {"config.json": {"modelOverrides": {"frontier": "gpt-6.1-sol", "thinking": "claude-fable-5", "code": "haiku", "pattern": "brand-new-model", "agents": {"implementer": "opus", "fact-checker": {"model": "haiku"}}}}},
        "state-override-and-outage": {"state.json": {"config": {"modelOverrides": {"frontier": "haiku", "code": "opus"}}}, "model-availability.json": down("opus")},
        "tier-cache": {
            "model-availability.json": down("haiku", "gpt-6-luna", "gpt-5-nano", "llama3.2-3b", "sonnet"),
            "model-tier-cache.json": {
                "zeta-pattern": {"tier": "pattern", "provenance": "verified", "provider": "anthropic"},
                "guess": {"tier": "pattern", "provenance": "unverified"},
                "alpha-pattern": {"tier": "pattern", "provenance": "verified"},
                "cached-code": {"tier": "code", "provenance": "verified", "provider": "openai"},
            },
        },
    }


def _write_state(root: Path, files: dict[str, object]) -> Path:
    (root / ".build-loop").mkdir(parents=True)
    for name, body in files.items():
        (root / ".build-loop" / name).write_text(json.dumps(body))
    return root


def _py_index(tier: str, segment: str | None, host: str | None, workdir: Path) -> dict:
    argv = ["resolve", "--json", "--workdir", str(workdir), "--tier", tier]
    if segment is not None:
        argv += ["--segment", segment]
    if host is not None:
        argv += ["--host", host]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = model_index.main(argv)
    if not out.getvalue().strip():
        return {"error": True, "code": code}
    data = json.loads(out.getvalue())
    return {"model": data["model"], "resolved": data["resolved"], "source": data["resolver_source"]}


def _py_agent(agent: str, host: str | None, workdir: Path, agents_dir: Path | None = None) -> dict:
    argv = [agent, "--json", "--workdir", str(workdir)]
    if host is not None:
        argv += ["--host-providers", host]
    if agents_dir is not None:
        argv += ["--agents-dir", str(agents_dir)]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        resolve_agent_model.main(argv)
    if not out.getvalue().strip():
        return {"error": True}
    data = json.loads(out.getvalue())
    return {"model": data["model"], "resolved": data["resolved"], "source": data["source"]}


def _bl_batch(requests: list[dict], env: dict[str, str] | None = None) -> list[dict]:
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    cp = subprocess.run([str(BL), "route", "batch"], input=payload, capture_output=True, text=True, env=env, check=True)
    replies = [json.loads(line) for line in cp.stdout.splitlines()]
    assert len(replies) == len(requests)
    return replies


def _same(py: dict, bl: dict) -> bool:
    if py.get("error") or bl.get("error"):
        return bool(py.get("error")) and bool(bl.get("error"))
    return (py["model"], py["resolved"], py["source"]) == (bl["model"], bl["resolved"], bl["source"])


def _index_cases(tmp: Path) -> tuple[list[dict], list[dict]]:
    """Every (state, tier, segment, host) request and Python's answer."""
    requests, expected = [], []
    for name, files in _states(time.time()).items():
        # Python prunes expired outage records on read; give each side its own copy.
        py_dir = _write_state(tmp / name / "py", files)
        bl_dir = _write_state(tmp / name / "bl", files)
        hosts = ALL_HOSTS if name == "clean" else CORE_HOSTS
        for tier, segment, host in itertools.product(TIERS, SEGMENTS, hosts):
            requests.append({"op": "resolve", "tier": tier, "segment": segment, "host": host, "workdir": str(bl_dir), "state": name})
            expected.append(_py_index(tier, segment, host, py_dir))
    return requests, expected


SYNTHETIC_AGENTS = {
    "no-role": "---\nname: no-role\nmodel: sonnet\n---\n",
    "bad-tier": "---\nname: bad-tier\nsegment: agentic_execution\ntier: T9\nmodel: claude-opus-5\n---\n",
    "bad-segment-legacy-tier": "---\nname: x\nsegment: nope\ntier: code\n---\n",
    "inherits": "---\nname: inherits\nmodel: inherit\n---\n",
    "nothing": "---\nname: nothing\n---\n",
    "quoted": "---\nname: quoted\ndescription: |\n  tier: frontier\nsegment: \"governance_evaluation\"\ntier: 'thinking'\n---\n",
    "specialist": "---\nsegment: governance_evaluation\ntier: T-S\nmodel: haiku\n---\n",
}


def _agent_cases(tmp: Path) -> tuple[list[dict], list[dict]]:
    requests, expected = [], []
    synthetic = tmp / "synthetic-agents"
    synthetic.mkdir()
    for name, body in SYNTHETIC_AGENTS.items():
        (synthetic / f"{name}.md").write_text(body)
    states = _states(time.time())
    for name in ("clean", "opus-down", "defaults-down", "config-override", "host-allowlist"):
        py_dir = _write_state(tmp / "agents" / name / "py", states[name])
        bl_dir = _write_state(tmp / "agents" / name / "bl", states[name])
        for host in [None, "any", "anthropic", "openai", "cursor", "local", " , "]:
            for agent in AGENTS:
                requests.append({"op": "agent", "agent": agent, "host": host, "workdir": str(bl_dir), "state": name})
                expected.append(_py_agent(agent, host, py_dir))
            for agent in list(SYNTHETIC_AGENTS) + ["missing-agent"]:
                requests.append({"op": "agent", "agent": agent, "host": host, "workdir": str(bl_dir), "agents_dir": str(synthetic), "state": name})
                expected.append(_py_agent(agent, host, py_dir, synthetic))
    return requests, expected


def _mismatches(requests: list[dict], expected: list[dict]) -> list[str]:
    got = _bl_batch(requests)
    return [f"{r} python={e} bl={g}" for r, e, g in zip(requests, expected, got) if not _same(e, g)]


@needs_bl
def test_index_resolve_parity(tmp_path: Path) -> None:
    requests, expected = _index_cases(tmp_path)
    assert len(requests) > 5000
    # The matrix must exercise real selection, not only errors and empty cells.
    assert len({e.get("model") for e in expected if e.get("model")}) >= 8
    bad = _mismatches(requests, expected)
    assert not bad, f"{len(bad)} of {len(requests)} differ:\n" + "\n".join(bad[:20])


@needs_bl
def test_agent_resolve_parity(tmp_path: Path) -> None:
    requests, expected = _agent_cases(tmp_path)
    assert len(AGENTS) >= 20
    bad = _mismatches(requests, expected)
    assert not bad, f"{len(bad)} of {len(requests)} differ:\n" + "\n".join(bad[:20])


HOST_ENV_KEYS = ("CLAUDE", "CODEX", "CURSOR", "GEMINI", "ANTHROPIC", "OPENAI", "GOOGLE", "BUILD_LOOP_HOST")
ENVIRONMENTS = {
    "no-host": {},
    "claude": {"CLAUDECODE": "1"},
    "plugin-root-only": {"CLAUDE_PLUGIN_ROOT": ""},
    "codex": {"CODEX_HOME": "/nonexistent"},
    "cursor": {"CURSOR_TRACE_ID": "x"},
    "gemini": {"GEMINI_CLI": "1"},
    "override-cursor": {"CLAUDECODE": "1", "BUILD_LOOP_HOST_PROVIDERS": "cursor"},
    "small-ram": {"BUILD_LOOP_HOST_PROVIDERS": "local", "BUILD_LOOP_HOST_RAM_GB": "8"},
}


@needs_bl
@pytest.mark.parametrize("env_name", list(ENVIRONMENTS))
def test_command_lines_agree_under_each_host_environment(env_name: str, tmp_path: Path) -> None:
    """Both real commands, with host detection driven by the environment."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(HOST_ENV_KEYS)}
    env.update(ENVIRONMENTS[env_name])
    workdir = _write_state(tmp_path / "w", {})
    cases = [(t, None) for t in LEGACY + ["T1", "T3", "T5"]] + [("T3", "agentic_execution")]
    for tier, segment in cases:
        extra = ["--segment", segment] if segment else []
        py = subprocess.run([sys.executable, str(SCRIPTS / "model_index.py"), "resolve", "--json", "--workdir", str(workdir), "--tier", tier, *extra], capture_output=True, text=True, env=env)
        bl = subprocess.run([str(BL), "route", "resolve", "--json", "--workdir", str(workdir), "--tier", tier, *extra], capture_output=True, text=True, env=env)
        py_data, bl_data = json.loads(py.stdout), json.loads(bl.stdout)
        assert (py_data["model"], py_data["resolved"], py.returncode) == (bl_data["model"], bl_data["resolved"], bl.returncode), (env_name, tier, segment)
    # `model` is the taxonomy id on every host; the fused Cursor Task slug
    # (`dispatch_model`) is not ported to bl.
    py = subprocess.run([sys.executable, str(SCRIPTS / "resolve_agent_model.py"), "implementer", "--json", "--workdir", str(workdir)], capture_output=True, text=True, env=env)
    bl = subprocess.run([str(BL), "route", "agent", "implementer", "--json", "--workdir", str(workdir)], capture_output=True, text=True, env=env)
    assert json.loads(py.stdout)["model"] == json.loads(bl.stdout)["model"], (env_name, py.stdout, bl.stdout)


def _report() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="bl-route-parity-"))
    try:
        t0 = time.perf_counter()
        requests, expected = _index_cases(tmp)
        py_index_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        bad = _mismatches(requests, expected)
        bl_index_s = time.perf_counter() - t0
        a_requests, a_expected = _agent_cases(tmp)
        a_bad = _mismatches(a_requests, a_expected)

        workdir = _write_state(tmp / "timing", {})
        def wall(argv: list[str], runs: int = 15) -> float:
            samples = []
            for _ in range(runs):
                t = time.perf_counter()
                subprocess.run(argv, capture_output=True, check=False)
                samples.append((time.perf_counter() - t) * 1000)
            return statistics.median(samples)

        print(json.dumps({
            "index_compared": len(requests), "index_mismatches": len(bad),
            "agent_compared": len(a_requests), "agent_mismatches": len(a_bad), "agents": len(AGENTS),
            "distinct_models_selected": sorted({e["model"] for e in expected + a_expected if e.get("model")}),
            "python_in_process_ms_per_resolve": round(py_index_s * 1000 / len(requests), 3),
            "bl_batch_ms_per_resolve": round(bl_index_s * 1000 / len(requests), 4),
            "python_command_median_ms": round(wall([sys.executable, str(SCRIPTS / "model_index.py"), "resolve", "--tier", "frontier", "--workdir", str(workdir)]), 1),
            "bl_command_median_ms": round(wall([str(BL), "route", "resolve", "--tier", "frontier", "--workdir", str(workdir)]), 1),
            "load_average": os.getloadavg(),
            "first_mismatches": (bad + a_bad)[:10],
        }, indent=2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _report()
