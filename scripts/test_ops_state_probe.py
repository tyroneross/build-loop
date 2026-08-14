#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/ops_state_probe.py.

Regression core (RCA 2026-07-25, atomize-ai): the EXACT historical shape is a
CLEAN local ``.env.local`` while the PRODUCTION store is corrupted —
  * a production value that captured the whole .env line INCLUDING its inline
    comment (`ENABLE_SEMANTIC_CLUSTERING="true  # Enable semantic clustering
    for trending events` + stored trailing newline), pushed by
    `while IFS='=' read -r key value` + `echo "$value" | vercel env add`
    (atomize-ai scripts/setup-vercel-env.sh:33,61);
  * a plain trailing-newline value (`LANGCHAIN_TRACING_V2="true\\n"` — tracing
    dark ~6 months);
  * duplicate keys within one source (THEME_QUALITY_LLM_ENABLED twice in
    .env.local) — last-write-wins ambiguity.
Old behavior: nothing classified these; the first probe version read
``.env.local`` (clean) and would have reported production as fine. New
behavior: classification comes ONLY from a production pull; local-clean +
production-corrupt classifies CORRUPT; production unreachable means every flag
is UNKNOWN(no_production_source), never silently classified from local files.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ops_state_probe as probe  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures reproducing the real failure shape
# ---------------------------------------------------------------------------

TS_READER = """\
export const clusteringEnabled =
  process.env.ENABLE_SEMANTIC_CLUSTERING === 'true';
export const tracing = process.env.LANGCHAIN_TRACING_V2 === 'true';
export const kg = process.env.ENABLE_KNOWLEDGE_GRAPH === 'true';
export const streaming = process.env.ENABLE_STREAMING_IMPLICATIONS === 'true';
const ui = process.env.ENABLE_KG_UI; // generic flaggy read, no strict compare
"""

# LOCAL dev config: CLEAN values — exactly what misled the first probe version.
LOCAL_ENV_CLEAN = (
    "# Created by Vercel CLI\n"
    "ENABLE_SEMANTIC_CLUSTERING=true\n"
    "LANGCHAIN_TRACING_V2=true\n"
    "ENABLE_KNOWLEDGE_GRAPH=true\n"
)

# PRODUCTION store as `vercel env pull --environment=production` materializes
# the corrupted values:
# - ENABLE_SEMANTIC_CLUSTERING captured its inline comment; quote never closes
#   on the same line because the stored value ends with a real newline
# - LANGCHAIN_TRACING_V2 stored with a trailing space (echo newline residue)
# - ENABLE_KNOWLEDGE_GRAPH clean-ON; THEME_MIN_EVENTS keeps a padded inline
#   comment; ENABLE_STREAMING_IMPLICATIONS absent -> UNKNOWN(not_in_source)
PROD_ENV_CORRUPT = (
    "# Created by Vercel CLI\n"
    'ENABLE_SEMANTIC_CLUSTERING="true  # Enable semantic clustering for trending events\n'
    '"\n'
    "LANGCHAIN_TRACING_V2=true \n"
    "ENABLE_KNOWLEDGE_GRAPH=true\n"
    "THEME_MIN_EVENTS=5              # Minimum event clusters required for theme\n"
    "UNUSED_LEGACY_TOGGLE=false\n"
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "flags.ts").write_text(TS_READER, encoding="utf-8")
    (tmp_path / ".env.local").write_text(LOCAL_ENV_CLEAN, encoding="utf-8")
    return tmp_path


def _prod_fetch(text: str):
    """A fetch stub standing in for `vercel env pull --environment=production`."""
    def fetch(workdir: Path):
        return text, "production-pull"
    return fetch


def _no_prod_fetch(reason: str = "no_vercel_project_link"):
    def fetch(workdir: Path):
        return None, reason
    return fetch


def _by_name(result: dict) -> dict[str, dict]:
    return {f["name"]: f for f in result["flags"]}


# ---------------------------------------------------------------------------
# ACCEPTANCE BAR: local clean + production corrupted MUST classify CORRUPT
# ---------------------------------------------------------------------------

def test_local_clean_production_corrupt_classifies_corrupt(repo: Path) -> None:
    """The exact historical shape. If this passes with only the local file
    driving classification, the probe is dormant on the real input."""
    result = probe.probe_ops_state(repo, fetch=_prod_fetch(PROD_ENV_CORRUPT))
    assert result["env_source_kind"] == "production-pull"
    flags = _by_name(result)

    f = flags["ENABLE_SEMANTIC_CLUSTERING"]
    assert f["status"] == "CORRUPT"
    assert "inline-comment-in-value" in f["defects"]
    assert "unterminated-quote" in f["defects"] or "embedded-newline" in f["defects"]
    # strict === 'true' on the corrupted value yields OFF although intent is ON
    assert f["reads_as"] == "OFF"
    assert f["intended"] == "true"

    t = flags["LANGCHAIN_TRACING_V2"]
    assert t["status"] == "CORRUPT"
    assert "trailing-whitespace" in t["defects"]
    assert t["reads_as"] == "OFF" and t["intended"] == "true"


def test_local_file_never_substitutes_for_production(repo: Path) -> None:
    """Production unreachable -> every flag UNKNOWN(no_production_source),
    even though a clean .env.local sits right there."""
    result = probe.probe_ops_state(repo, fetch=_no_prod_fetch())
    assert result["env_source_kind"] == "none"
    assert any(r.startswith("no_production_source") for r in result["reasons"])
    assert result["flags"], "flag reads must still be discovered"
    for f in result["flags"]:
        assert f["status"] == "UNKNOWN"
        assert f["reason"] == "no_production_source"
    assert result["counts"]["ON"] == 0 and result["counts"]["CORRUPT"] == 0


def test_pull_disabled_is_unknown_not_local(repo: Path) -> None:
    result = probe.probe_ops_state(repo, allow_pull=False)
    assert result["env_source_kind"] == "none"
    assert all(f["status"] == "UNKNOWN" for f in result["flags"])


# ---------------------------------------------------------------------------
# Classification detail (production source)
# ---------------------------------------------------------------------------

def test_clean_true_is_on_and_absent_is_unknown(repo: Path) -> None:
    result = probe.probe_ops_state(repo, fetch=_prod_fetch(PROD_ENV_CORRUPT))
    flags = _by_name(result)
    assert flags["ENABLE_KNOWLEDGE_GRAPH"]["status"] == "ON"
    f = flags["ENABLE_STREAMING_IMPLICATIONS"]
    assert f["status"] == "UNKNOWN"
    assert f["reason"] == "not_in_source"


def test_corrupt_nonflag_and_unreferenced_keys_surface(repo: Path) -> None:
    result = probe.probe_ops_state(repo, fetch=_prod_fetch(PROD_ENV_CORRUPT))
    corrupt_other = {e["key"] for e in result["corrupt_other_keys"]}
    assert "THEME_MIN_EVENTS" in corrupt_other  # padded inline comment
    assert "UNUSED_LEGACY_TOGGLE" in result["unreferenced_keys"]


def test_duplicate_key_is_a_defect() -> None:
    obs = probe.parse_env_text(
        "THEME_QUALITY_LLM_ENABLED=true\n"
        "OTHER=1\n"
        "THEME_QUALITY_LLM_ENABLED=false\n"
    )
    o = obs["THEME_QUALITY_LLM_ENABLED"]
    assert "duplicate-key" in o.defects
    assert o.raw == "false"  # last-write-wins value, defect recorded


def test_duplicate_key_in_production_classifies_corrupt(repo: Path) -> None:
    text = PROD_ENV_CORRUPT + "ENABLE_KNOWLEDGE_GRAPH=false\n"
    flags = _by_name(probe.probe_ops_state(repo, fetch=_prod_fetch(text)))
    f = flags["ENABLE_KNOWLEDGE_GRAPH"]
    assert f["status"] == "CORRUPT"
    assert "duplicate-key" in f["defects"]


# ---------------------------------------------------------------------------
# Local hygiene stays advisory (and duplicate local keys are caught)
# ---------------------------------------------------------------------------

def test_local_hygiene_is_advisory_only(repo: Path) -> None:
    (repo / ".env.local").write_text(
        LOCAL_ENV_CLEAN
        + "THEME_QUALITY_LLM_ENABLED=true\n"
        + "THEME_QUALITY_LLM_ENABLED=true\n",  # real duplicate seen 2026-07-25
        encoding="utf-8",
    )
    result = probe.probe_ops_state(repo, fetch=_no_prod_fetch())
    lh = result["local_hygiene"]
    assert lh is not None and "NOT production" in lh["note"]
    keys = {
        e["key"] for f in lh["files"] for e in f["corrupt_keys"]
    }
    assert "THEME_QUALITY_LLM_ENABLED" in keys
    # advisory only: flags remain UNKNOWN
    assert all(f["status"] == "UNKNOWN" for f in result["flags"])


# ---------------------------------------------------------------------------
# Secret-leak guard + summary + persistence + CLI
# ---------------------------------------------------------------------------

def test_no_secret_values_in_output(repo: Path) -> None:
    text = PROD_ENV_CORRUPT + "OPENAI_API_KEY=sk-fixture-not-a-real-key-123\n"
    result = probe.probe_ops_state(repo, fetch=_prod_fetch(text))
    assert "sk-fixture-not-a-real-key-123" not in json.dumps(result)


def test_summary_line_labels_source(repo: Path) -> None:
    prod = probe.probe_ops_state(repo, fetch=_prod_fetch(PROD_ENV_CORRUPT))
    line = probe.summary_line(prod)
    assert "CORRUPT" in line and "prod, vercel pull" in line
    assert "ENABLE_SEMANTIC_CLUSTERING" in line

    none = probe.probe_ops_state(repo, fetch=_no_prod_fetch())
    line2 = probe.summary_line(none)
    assert "no_vercel_project_link" in line2 or "none" in line2
    assert "ON" in line2  # counts still shown (all zero/unknown)


def test_write_repo_artifact_records_source_kind(repo: Path) -> None:
    result = probe.probe_ops_state(repo, fetch=_prod_fetch(PROD_ENV_CORRUPT))
    out = probe.write_repo_artifact(repo, result)
    assert out is not None and out.is_file()
    loaded = json.loads(out.read_text())
    assert loaded["env_source_kind"] == "production-pull"
    assert loaded["counts"]["CORRUPT"] >= 2


def test_fetch_production_env_unlinked_repo(tmp_path: Path) -> None:
    text, reason = probe.fetch_production_env(tmp_path)
    assert text is None and reason == "no_vercel_project_link"


def test_cli_smoke_no_pull(repo: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "ops_state_probe.py"),
         "--workdir", str(repo), "--no-pull"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    assert "UNKNOWN" in proc.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
