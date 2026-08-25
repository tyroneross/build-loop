#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Language-neutral conformance runner for the CLI dispatch consent contract.

Contract: references/cli-dispatch-consent-contract.md. Build Loop's Python
implementation (scripts/cli_dispatch_consent.py) is one of two implementations
of that contract; Rally Point is building the other (Rust, a different repo,
does not exist yet). "Shared contract, separate implementations, graded by one
conformance suite so they cannot drift on behavior" is the contract's own
stated design — this file IS that suite.

Fixture provenance (important — read before trusting a green run): every
entry_sha256 embedded in references/consent-conformance-cases.json was computed
by an independent reimplementation of the contract's stated hashing algorithm
(SHA-256 over UTF-8 canonical JSON of the entry with entry_sha256 removed, keys
sorted, separators (",", ":"), no trailing newline), NOT by calling
cli_dispatch_consent.py. The fixture generator lived at
/private/tmp/.../scratchpad/gen_fixtures.py during authoring (scratch, not
committed) and is reproduced verbatim in this docstring's sibling note in the
PR/commit body. This means a green run here proves the Python implementation
agrees with the CONTRACT, not merely with itself — the one property a
self-generated fixture set could never prove.

Adapters (the seam a `rust` implementation plugs into):

    class Adapter:
        def check(self, product: str, vendor: str, env: dict[str, str],
                   store_path: Path) -> AdapterResult

An adapter's ONLY job is: given a product, a vendor, an environment overlay
(currently just AGENT_DISPATCH_DEPTH), and a store file path already populated
by the runner, invoke the implementation under test and report back its exit
code and its claimed `allowed` boolean. The runner owns fixture setup,
grading, and reporting; the adapter owns nothing but "how do I ask THIS
implementation this one question."

Every case runs against a throwaway temp directory. The runner ALWAYS sets
AGENT_CONSENT_SELFTEST=1 and AGENT_CONSENT_STORE_PATH to that temp path before
invoking any adapter — the real ~/.agent-consent store is never read or
written by this suite (see `_assert_real_store_untouched` and the safety test
in test_consent_conformance.py).

Usage:
    python3 scripts/consent_conformance.py --impl python
    python3 scripts/consent_conformance.py --impl python --cases PATH --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES_PATH = REPO_ROOT / "references" / "consent-conformance-cases.json"
PYTHON_IMPL_PATH = REPO_ROOT / "scripts" / "cli_dispatch_consent.py"
REAL_STORE = Path.home() / ".agent-consent" / "cli-dispatch-consent.json"


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------

REQUIRED_CASE_FIELDS = {"id", "description", "contract_ref", "store", "env", "key", "expect"}


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{path}: expected a non-empty 'cases' array")
    seen_ids: set[str] = set()
    for c in cases:
        missing = REQUIRED_CASE_FIELDS - set(c.keys())
        if missing:
            raise ValueError(f"case {c.get('id', '?')!r} missing fields: {sorted(missing)}")
        if not isinstance(c["expect"], dict) or set(c["expect"].keys()) != {"allowed", "exit"}:
            raise ValueError(f"case {c['id']!r}: expect must be exactly {{'allowed','exit'}}")
        if ":" not in c["key"]:
            raise ValueError(f"case {c['id']!r}: key {c['key']!r} must be 'product:vendor'")
        if c["id"] in seen_ids:
            raise ValueError(f"duplicate case id: {c['id']!r}")
        seen_ids.add(c["id"])
    return cases


# ---------------------------------------------------------------------------
# Store materialization — the ONLY place this suite writes a store file, and
# it always writes under a caller-supplied tmp path, never REAL_STORE.
# ---------------------------------------------------------------------------

def materialize_store(store: Any, store_path: Path) -> None:
    """Write `store` to `store_path` per the case's `store` field type.

    - None            -> ensure the file does not exist (missing-store case)
    - dict            -> json.dumps it (a well-formed wire-format document,
                         possibly with a structurally wrong field like log
                         being the wrong type)
    - str             -> write the string as the literal raw file bytes
                         (used for the empty-file and malformed-JSON cases,
                         where the fixture is deliberately not valid JSON at
                         all and so cannot be expressed as a dict)
    """
    store_path.parent.mkdir(parents=True, exist_ok=True)
    if store is None:
        store_path.unlink(missing_ok=True)
        return
    if isinstance(store, dict):
        store_path.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    if isinstance(store, str):
        store_path.write_text(store, encoding="utf-8")
        return
    raise TypeError(f"case 'store' field must be null, an object, or a string; got {type(store)}")


# ---------------------------------------------------------------------------
# Env building — isolation lives here. Every case gets AGENT_CONSENT_SELFTEST
# and AGENT_CONSENT_STORE_PATH forced to the tmp path; AGENT_DISPATCH_DEPTH is
# set only when the case specifies it, and explicitly removed otherwise so an
# "unset" case can never accidentally inherit an ambient value.
# ---------------------------------------------------------------------------

def build_env(case_env: dict[str, str], store_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["AGENT_CONSENT_SELFTEST"] = "1"
    env["AGENT_CONSENT_STORE_PATH"] = str(store_path)
    env.pop("PYTEST_CURRENT_TEST", None)  # never let an ambient pytest run alter behavior here
    env.pop("AGENT_DISPATCH_DEPTH", None)
    for k, v in case_env.items():
        env[k] = v
    return env


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

@dataclass
class AdapterResult:
    exit_code: int
    allowed: bool | None
    raw_stdout: str
    raw_stderr: str = ""
    error: str | None = None


class Adapter:
    """Base seam. Subclass and implement `check`."""

    name = "base"

    def check(self, product: str, vendor: str, env: dict[str, str], store_path: Path) -> AdapterResult:
        raise NotImplementedError


class PythonCLIAdapter(Adapter):
    """Drives scripts/cli_dispatch_consent.py through its actual CLI surface
    (`--check --json`), reading the process exit code and parsed JSON. This is
    the one adapter this suite ships and runs by default."""

    name = "python"

    def __init__(self, impl_path: Path = PYTHON_IMPL_PATH) -> None:
        self.impl_path = impl_path

    def check(self, product: str, vendor: str, env: dict[str, str], store_path: Path) -> AdapterResult:
        proc = subprocess.run(
            [sys.executable, str(self.impl_path), "--product", product, "--vendor", vendor,
             "--check", "--json"],
            env=env, capture_output=True, text=True, timeout=30,
        )
        allowed: bool | None = None
        error: str | None = None
        try:
            parsed = json.loads(proc.stdout)
            allowed = parsed.get("allowed")
        except json.JSONDecodeError as e:
            error = f"stdout was not valid JSON: {e}"
        return AdapterResult(exit_code=proc.returncode, allowed=allowed,
                              raw_stdout=proc.stdout, raw_stderr=proc.stderr, error=error)


class RustAdapter(Adapter):
    """SEAM — NOT IMPLEMENTED. Rally Point's Rust implementation
    (crates/cockpitd/src/consent.rs, a different repo) does not exist yet.

    When it does, this adapter should shell out to the compiled `cockpitd`
    (or a small dedicated conformance-check binary it exposes) with the same
    contract: given --product/--vendor, AGENT_CONSENT_SELFTEST=1 and
    AGENT_CONSENT_STORE_PATH set in its environment, it must print a JSON
    object containing at least {"allowed": bool} to stdout and exit with the
    contract's exit code (0/1/2/3). Implement `check` exactly like
    PythonCLIAdapter.check above, pointing at the Rust binary instead of
    `sys.executable`. Do not implement this until the Rust binary exists —
    there is nothing to grade yet.
    """

    name = "rust"

    def check(self, product: str, vendor: str, env: dict[str, str], store_path: Path) -> AdapterResult:
        raise NotImplementedError(
            "RustAdapter is an unimplemented seam — Rally Point's Rust consent "
            "implementation (crates/cockpitd/src/consent.rs) does not exist yet. "
            "See the RustAdapter docstring for what a future implementation must satisfy."
        )


ADAPTERS: dict[str, type[Adapter]] = {
    "python": PythonCLIAdapter,
    "rust": RustAdapter,
}


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    case_id: str
    passed: bool
    detail: str
    adapter_result: AdapterResult | None = field(default=None, repr=False)


def grade(case: dict[str, Any], result: AdapterResult) -> CaseResult:
    expect = case["expect"]
    if result.error:
        return CaseResult(case["id"], False, f"adapter error: {result.error}", result)
    problems = []
    if result.exit_code != expect["exit"]:
        problems.append(f"exit={result.exit_code} want={expect['exit']}")
    if result.allowed != expect["allowed"]:
        problems.append(f"allowed={result.allowed!r} want={expect['allowed']!r}")
    if problems:
        return CaseResult(case["id"], False, "; ".join(problems), result)
    return CaseResult(case["id"], True, "ok", result)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_case(adapter: Adapter, case: dict[str, Any], base_tmp: Path) -> CaseResult:
    case_dir = base_tmp / case["id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    store_path = case_dir / "cli-dispatch-consent.json"
    materialize_store(case["store"], store_path)
    env = build_env(case.get("env") or {}, store_path)
    product, vendor = case["key"].split(":", 1)
    result = adapter.check(product, vendor, env, store_path)
    return grade(case, result)


def _assert_real_store_untouched(before: tuple[bool, tuple[int, int] | None]) -> None:
    existed_before, stat_before = before
    existed_after = REAL_STORE.exists()
    if existed_after != existed_before:
        raise RuntimeError(
            "REFUSING TO REPORT RESULTS: the real ~/.agent-consent/cli-dispatch-"
            f"consent.json existence changed during this run (before={existed_before} "
            f"after={existed_after}). This suite must never touch the real store."
        )
    if existed_after:
        st = REAL_STORE.stat()
        stat_after = (st.st_mtime_ns, st.st_size)
        if stat_after != stat_before:
            raise RuntimeError(
                "REFUSING TO REPORT RESULTS: the real ~/.agent-consent/cli-dispatch-"
                f"consent.json was modified during this run (before={stat_before} "
                f"after={stat_after})."
            )


def run_suite(adapter: Adapter, cases: list[dict[str, Any]]) -> list[CaseResult]:
    existed_before = REAL_STORE.exists()
    stat_before = None
    if existed_before:
        st = REAL_STORE.stat()
        stat_before = (st.st_mtime_ns, st.st_size)

    results: list[CaseResult] = []
    with tempfile.TemporaryDirectory(prefix="consent-conformance-") as tmp:
        base_tmp = Path(tmp)
        for case in cases:
            results.append(run_case(adapter, case, base_tmp))

    _assert_real_store_untouched((existed_before, stat_before))
    return results


def print_table(results: list[CaseResult], cases_by_id: dict[str, dict[str, Any]]) -> None:
    width = max((len(r.case_id) for r in results), default=10)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"{status:5s} {r.case_id:<{width}}  {r.detail}")
        if not r.passed:
            desc = cases_by_id[r.case_id]["description"]
            print(f"      {'':<{width}}  case: {desc}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--impl", choices=sorted(ADAPTERS), required=True,
                     help="which implementation to grade")
    ap.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH,
                     help="path to consent-conformance-cases.json")
    ap.add_argument("--impl-path", type=Path, default=None,
                     help="override path to the implementation under test (python adapter only)")
    ap.add_argument("--json", action="store_true", dest="emit_json")
    a = ap.parse_args(argv)

    cases = load_cases(a.cases)
    cases_by_id = {c["id"]: c for c in cases}

    adapter_cls = ADAPTERS[a.impl]
    adapter = adapter_cls(a.impl_path) if a.impl_path and a.impl == "python" else adapter_cls()

    results = run_suite(adapter, cases)
    passed = sum(1 for r in results if r.passed)
    total = len(results)

    if a.emit_json:
        out = {
            "impl": a.impl,
            "cases_path": str(a.cases),
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "results": [
                {"id": r.case_id, "passed": r.passed, "detail": r.detail}
                for r in results
            ],
        }
        print(json.dumps(out, indent=2))
    else:
        print_table(results, cases_by_id)
        print(f"\n{passed}/{total} cases passed ({a.impl})")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
