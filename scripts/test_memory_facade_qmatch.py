#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_facade._q_match — the recall token matcher.

Regression: a full-phrase substring match silently dropped every canonical
decision/lesson for realistic multi-word goal queries (audit 2026-05-31,
canonical_memory.merged=0 on every real run). Token-OR matching fixes it.
"""
from __future__ import annotations

import builtins
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from memory_facade.common import _q_match  # noqa: E402


def test_empty_query_matches_everything():
    assert _q_match("anything at all", "") is True
    assert _q_match("anything", "   ") is True


def test_single_token_substring_still_matches():
    assert _q_match("decision: rally coordination", "rally") is True


def test_regression_multiword_goal_query_now_matches_on_any_token():
    # The exact failure class from the audit: a natural-language goal query whose
    # full phrase never appears verbatim, but individual tokens do.
    text = "decision-project-build-loop: background snapshot handoff; tags: context, rally"
    query = "background snapshot polished B handoff context"
    # Old behavior (full phrase) => False (dropped). New behavior (token-OR) => True.
    assert _q_match(text, query) is True


def test_no_token_matches_returns_false():
    assert _q_match("decision about telemetry and otel", "kubernetes helm istio") is False


def test_case_insensitive():
    assert _q_match("Rally Coordination Decision", "rally") is True
    assert _q_match("rally coordination", "RALLY") is True


def test_none_text_is_safe():
    assert _q_match(None, "rally") is False
    assert _q_match(None, "") is True


def test_default_mode_is_byte_identical_to_legacy_token_or(monkeypatch):
    monkeypatch.delenv("BUILD_LOOP_MEMORY_MATCH", raising=False)
    text = "main domain explain chain"
    query = "ai unrelated"
    expected = any(t in text.lower() for t in query.lower().split() if t)

    assert _q_match(text, query) is expected


def test_boundary_mode_rejects_short_substring_accidents():
    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": "boundary"}):
        assert _q_match("main domain explain chain", "ai") is False


def test_legacy_mode_keeps_short_substring_accidents():
    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": "legacy"}):
        assert _q_match("main domain explain chain", "ai") is True


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("release migrations safely", "migration"),
        ("deployment checklist", "deploy"),
    ],
)
def test_boundary_mode_allows_prefix_morphology(text, query):
    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": "boundary"}):
        assert _q_match(text, query) is True


def test_boundary_stopword_only_query_matches_everything():
    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": "boundary"}):
        assert _q_match("unrelated memory", "the of a") is True


@pytest.mark.parametrize("mode", ["legacy", "boundary"])
def test_empty_query_matches_everything_in_both_modes(mode):
    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": mode}):
        assert _q_match("anything at all", "") is True


def test_unknown_mode_falls_back_to_legacy():
    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": "experimental"}):
        assert _q_match("main domain explain chain", "ai") is True


def test_boundary_import_failure_falls_back_to_legacy():
    real_import = builtins.__import__

    def fail_memory_rank_import(name, *args, **kwargs):
        if name == "memory_rank":
            raise ImportError("simulated memory_rank import failure")
        return real_import(name, *args, **kwargs)

    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": "boundary"}):
        with patch("builtins.__import__", side_effect=fail_memory_rank_import):
            assert _q_match("main domain explain chain", "ai") is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_recall_uses_canonical_evidence_before_ranking(tmp_path,monkeypatch):
    import memory_facade as facade
    import memory_locator
    import _paths
    root=tmp_path/'memory';root.mkdir()
    current={'path':str(root/'lessons/current.md'),'id':'current','_kind':'lessons'}
    same_source={'path':current['path'],'id':'content-current','_kind':'content'}
    stale={'path':str(root/'lessons/stale.md'),'id':'stale','_kind':'lessons'}
    remote={'id':'remote','_kind':'semantic'}
    buckets={k:[] for k in facade.KINDS};buckets['lessons']=[current,stale];buckets['semantic']=[remote];buckets['content']=[same_source]
    monkeypatch.setattr(facade,'_fan_out',lambda *a,**kw:(buckets,[]))
    monkeypatch.setattr(_paths,'memory_store_root',lambda:root)
    monkeypatch.setattr(facade,'_order',lambda rows,query:rows)
    monkeypatch.setattr(facade,'_emit_telemetry',lambda *a,**kw:None)
    def verify(selected_root,rows):
        assert selected_root==root
        assert [r['path'] for r in rows]==['lessons/current.md','lessons/stale.md','lessons/current.md']
        return [dict(current,path='lessons/current.md',evidence={'review_state':'unknown'})],['evidence_invalidated']
    monkeypatch.setattr(memory_locator,'verify_evidence',verify)
    result=facade.recall('lookup',workdir=tmp_path)
    assert {r['id'] for r in result['merged']}=={'current','remote','content-current'}
    assert result['results_by_kind']['content'][0]['id']=='content-current'
    assert result['results_by_kind']['lessons'][0]['evidence']['review_state']=='unknown'
    assert 'evidence_invalidated' in result['reasons']


def test_recall_checks_relative_decision_path(tmp_path, monkeypatch):
    import memory_facade as facade
    import memory_locator
    import _paths
    row={'path':'projects/demo/decisions/policy.md','id':'policy','_kind':'decisions'}
    buckets={k:[] for k in facade.KINDS};buckets['decisions']=[row]
    monkeypatch.setattr(facade,'_fan_out',lambda *a,**kw:(buckets,[]))
    monkeypatch.setattr(_paths,'memory_store_root',lambda:tmp_path)
    monkeypatch.setattr(facade,'_emit_telemetry',lambda *a,**kw:None)
    def verify(root, rows):
        assert rows[0]['path']==row['path']
        return [],['evidence_invalidated']
    monkeypatch.setattr(memory_locator,'verify_evidence',verify)
    result=facade.recall('policy',workdir=tmp_path)
    assert not result['merged'] and 'evidence_invalidated' in result['reasons']


def test_recall_regenerates_stale_index_text_from_verified_bytes(tmp_path, monkeypatch):
    import hashlib
    import memory_facade as facade
    import memory_locator
    import _paths
    path=tmp_path/'lessons/policy.md';path.parent.mkdir()
    path.write_text('---\nvalidity_state: current\n---\n# Corrected policy\nUse safe advice.\n')
    evidence={'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'available':True}
    rows=[{'path':str(path),'id':'policy','_kind':'content','title':'Old dangerous title','snippet':'Dangerous advice','summary':'Dangerous advice'}]
    buckets={k:[] for k in facade.KINDS};buckets['content']=rows
    monkeypatch.setattr(facade,'_fan_out',lambda *a,**kw:(buckets,[]))
    monkeypatch.setattr(_paths,'memory_store_root',lambda:tmp_path)
    monkeypatch.setattr(facade,'_emit_telemetry',lambda *a,**kw:None)
    monkeypatch.setattr(memory_locator,'verify_evidence',lambda root,rs:([dict(rs[0],evidence=evidence)],[]))
    result=facade.recall('advice',workdir=tmp_path)['merged'][0]
    assert result['title']=='Corrected policy'
    assert 'safe advice' in result['snippet'] and result['summary']=='Use safe advice.'
    path.write_text('# A concurrent edit')
    assert not facade.recall('advice',workdir=tmp_path)['merged']
