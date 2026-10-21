"""Offline regressions for the complete producer-to-retrieval identity boundary."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import embed_backend as eb
from semantic_index import upsert_fact, query_facts, connect
from semantic_index.backfill import backfill_embeddings


@pytest.mark.parametrize('backend_type', ['OllamaBackend', 'DaemonBackend'])
@pytest.mark.parametrize('defect', ['model', 'count', 'dim', 'nan', 'bool', 'version', 'http'])
def test_transport_rejects_bad_response_without_retry(backend_type, defect, monkeypatch):
    monkeypatch.setattr(eb, 'EMBED_DIM', 2)
    backend = getattr(eb, backend_type)(model='bge-m3')
    data = {'ok': True, 'model': 'bge-m3:latest', 'protocol_version': 1, 'embeddings': [[1., 0.]]}
    if defect == 'model': data['model'] = 'mxbai'
    if defect == 'count': data['embeddings'] = []
    if defect == 'dim': data['embeddings'] = [[1.]]
    if defect == 'nan': data['embeddings'] = [[float('nan'), 0.]]
    if defect == 'bool': data['embeddings'] = [[True, 0.]]
    if defect == 'version':
        data['protocol_version'] = True
        if backend_type == 'OllamaBackend':
            data.pop('model')  # native Ollama has no daemon version contract
    conn = Mock()
    conn.getresponse.return_value = SimpleNamespace(status=400 if defect == 'http' else 200,
                                                   read=lambda: json.dumps(data).encode())
    backend._conn = conn
    with pytest.raises(RuntimeError): backend.embed('real input')
    assert conn.request.call_count == 1


def test_daemon_network_failure_retries_once(monkeypatch):
    monkeypatch.setattr(eb, 'EMBED_DIM', 2)
    bad, good = Mock(), Mock()
    bad.request.side_effect = ConnectionResetError('lost')
    good.getresponse.return_value = SimpleNamespace(status=200, read=lambda: json.dumps(
        {'ok': True, 'model': 'bge-m3', 'protocol_version': 1, 'embeddings': [[1., 0.]]}).encode())
    backend = eb.DaemonBackend(model='bge-m3')
    backend._ensure_conn = Mock(side_effect=[bad, good])
    assert backend.embed('text') == [1., 0.]
    assert backend._ensure_conn.call_count == 2


@pytest.mark.parametrize('identity', [None, 'unknown', 'mxbai', 'bge-m3'])
def test_unverified_or_mismatched_rows_never_get_dense_only_score(tmp_path, identity):
    db = tmp_path / 'facts.sqlite'
    upsert_fact(subject='car', predicate='is', object_text='vehicle', project='p',
                embedding=[1., 0.], embedding_model=identity, db_path=db)
    if identity == 'bge-m3':
        with connect(db) as conn:
            conn.execute("UPDATE semantic_facts SET metadata_json = ?", (json.dumps(
                {'embedding_model_version': identity}),))
    called = Mock(return_value=[1., 0.])
    assert query_facts(query='automobile', db_path=db, embed_fn=called, embedding_model='bge-m3') == []
    called.assert_not_called()
    assert query_facts(query='car', db_path=db, embed_fn=called, embedding_model='bge-m3')[0]['subject'] == 'car'


def test_backfill_refreshes_untrusted_and_wrong_space_once(tmp_path):
    db = tmp_path / 'facts.sqlite'
    for index, model in enumerate([None, 'mxbai', 'bge-m3']):
        upsert_fact(subject=f'car{index}', predicate='is', object_text='vehicle', project='p',
                    embedding=[0., 1.], embedding_model=model, db_path=db)
    fn = Mock(return_value=[1., 0.])
    result = backfill_embeddings(db_path=db, embed_fn=fn, embedding_model='bge-m3', batch_size=1)
    assert result.embedded == 2 and result.failed == 0
    assert backfill_embeddings(db_path=db, embed_fn=fn, embedding_model='bge-m3').scanned == 0
    assert fn.call_count == 2
    assert len(query_facts(query='automobile', db_path=db, embed_fn=lambda _: [1., 0.],
                           embedding_model='bge-m3')) == 3


def test_injected_embedder_cannot_inherit_stale_metadata(tmp_path):
    db = tmp_path / 'facts.sqlite'
    upsert_fact(subject='car', predicate='is', object_text='vehicle', project='p',
                metadata={'embedding_model_version':'bge-m3', 'embedding_identity_verified':True},
                embed_fn=lambda _: [1., 0.], db_path=db)
    assert query_facts(query='automobile', db_path=db, embed_fn=lambda _: [1., 0.],
                       embedding_model='bge-m3') == []


def test_postgres_readers_bind_actual_space_before_ranking(monkeypatch):
    import recall
    captured=[]
    monkeypatch.setattr(recall, 'query', lambda sql, params: captured.append((sql, params)) or [])
    for fn,args in [(recall.hybrid_search_facts, ('q',[1.]*1024,'personal_memory',5,0.)),
                    (recall.hybrid_search_episodes, ('q',[1.]*1024,'personal_memory',5))]:
        assert fn(*args) == []
        assert captured == []
        fn(*args, embedding_model='bge-m3')
        sql, params = captured.pop()
        assert sql.count('%s') == len(params)
        assert eb.PG_EMBEDDING_SPACE_FILTER in sql
        assert ['bge-m3','bge-m3:latest'] in params


def test_consolidation_all_writes_keep_actual_provenance(monkeypatch):
    import consolidate_memory as cm
    import db
    statements=[]
    def execute(sql, params):
        assert sql.count('%s') == len(params)
        statements.append((sql,params))
        return {'id':'new'}
    monkeypatch.setattr(db,'execute',execute)
    monkeypatch.setattr(db,'query_one',execute)
    candidate={'subject':'s','predicate':'p','object':'o', **{'embedding_model_version':'bge-m3',
                'embedding_identity_verified':True}}
    cm._do_insert(candidate,[1.]*1024,'personal_memory')
    cm._do_update('old',candidate,[1.]*1024,'personal_memory')
    cm._record_conflict('old',candidate,[1.]*1024,'personal_memory')
    for sql,params in statements[:3]:
        assert 'embedding_model_version' in sql
        assert 'bge-m3' in params
        metadata = next(json.loads(v) for v in params if isinstance(v,str) and v.startswith('{'))
        assert metadata['embedding_identity_verified'] is True


def test_migrator_checks_entire_actual_batch_before_first_write(monkeypatch):
    import migrate_reembed_to_bgem3 as migration
    monkeypatch.setattr(eb,'active_model', lambda:'bge-m3')
    calls=[]
    monkeypatch.setattr(migration,'execute',lambda *args: calls.append(args))
    rows=[{'id':'one','subject':'s'}, {'id':'two','subject':'t'}]
    monkeypatch.setattr(migration,'embed',lambda _: [[1.]*1024, [float('nan')]*1024])
    with pytest.raises(RuntimeError): migration.reembed_batch('personal_memory',rows,'bge-m3')
    assert calls == []
    monkeypatch.setattr(migration,'embed',lambda _: [[1.]*1024]*2)
    with pytest.raises(RuntimeError): migration.reembed_batch('personal_memory',rows,'mxbai')
    assert calls == []
    assert migration.reembed_batch('personal_memory',rows,'bge-m3') == 2
    for sql,params in calls:
        assert sql.count('%s') == len(params)
        assert params[1] == 'bge-m3'
        assert json.loads(params[2])['embedding_identity_verified'] is True


def test_navgator_passes_computed_model_to_metadata_and_column(monkeypatch):
    import sync_navgator_lessons as nav
    monkeypatch.setattr(eb,'embed',lambda _: [1.]*1024)
    monkeypatch.setattr(eb,'active_model',lambda:'bge-m3')
    vector, provenance = nav._embed_safely('pattern',[], 'lesson1')
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    nav._upsert_lesson(conn,schema='personal_memory',lesson={'id':'lesson1'},project='p',
                       embedding=vector,embedding_metadata=provenance)
    sql,params=cur.execute.call_args.args
    assert sql.count('%s') == len(params)
    assert params[-1] == 'bge-m3'
    metadata=next(json.loads(v) for v in params if isinstance(v,str) and v.startswith('{'))
    assert metadata['embedding_identity_verified'] is True


def test_context_backfill_records_producer_after_embedding(monkeypatch):
    import contextual_prepend as contextual
    import migrate_add_chunk_context_column as migration
    monkeypatch.setattr(contextual,'available_router_target',lambda:'fixture-router')
    monkeypatch.setattr(contextual,'generate_chunk_context',lambda *a,**kw:'fixture context')
    monkeypatch.setattr(migration,'query',lambda *a:[{'id':'one','subject':'s','object':'o'}])
    produced=[]
    def embed(text):
        produced.append(text)
        return [1.]*1024
    monkeypatch.setattr(eb,'embed',embed)
    monkeypatch.setattr(eb,'active_model',lambda:'bge-m3' if produced else 'mxbai')
    writes=[]
    monkeypatch.setattr(migration,'execute',lambda *args:writes.append(args))
    assert migration.backfill('personal_memory',dry_run=False,force=False,batch_size=10)['filled'] == 1
    sql,params=writes[0]
    assert sql.count('%s') == len(params)
    assert params[2] == 'bge-m3'
    assert json.loads(params[3])['embedding_identity_verified'] is True


def test_lessons_unknown_query_space_never_reads_or_scores_vectors():
    from lessons_index.query import _cosine_rerank
    conn=Mock()
    candidates=[{'id':1,'score':3.}]
    assert _cosine_rerank(conn,candidates,[1.,0.],None) == candidates
    conn.execute.assert_not_called()


def test_default_facade_preserves_index_owned_producer_resolution(monkeypatch,tmp_path):
    import memory_facade.semantic as facade
    db=tmp_path/'facts.sqlite'
    monkeypatch.setattr(eb,'embed',lambda _:[1.,0.])
    monkeypatch.setattr(eb,'active_model',lambda:'bge-m3')
    upsert_fact(subject='car',predicate='is',object_text='vehicle',project='p',db_path=db)
    import semantic_index
    monkeypatch.setattr(semantic_index,'_db_path',lambda _:db)
    out,reasons=facade.read_semantic(tmp_path,'automobile',5,'p',skip_postgres=True)
    assert out[0]['subject'] == 'car'
    assert reasons == ['skipped_postgres']


@pytest.mark.parametrize('metadata', [[], 'text', None, {'embedding_model_version':'bge-m3',
                                                        'embedding_identity_verified':1}])
def test_backfill_repairs_malformed_trust_metadata_idempotently(tmp_path, metadata):
    db=tmp_path/'facts.sqlite'
    upsert_fact(subject='car',predicate='is',object_text='vehicle',project='p',
                db_path=db,embedding=[1.,0.])
    with connect(db) as conn:
        conn.execute('UPDATE semantic_facts SET metadata_json=?',(json.dumps(metadata),))
    fn=Mock(return_value=[1.,0.])
    first=backfill_embeddings(db_path=db,embed_fn=fn,embedding_model='bge-m3')
    assert first.embedded == 1 and first.failed == 0
    assert backfill_embeddings(db_path=db,embed_fn=fn,embedding_model='bge-m3').scanned == 0
    assert query_facts(query='automobile',db_path=db,embed_fn=fn,embedding_model='bge-m3')


def test_dense_only_dimension_mismatch_falls_back_to_keyword(tmp_path):
    db=tmp_path/'facts.sqlite'
    upsert_fact(subject='car',predicate='is',object_text='vehicle',project='p',db_path=db,
                embedding=[1.,0.],embedding_model='bge-m3')
    fn=lambda _:[1.,0.,0.]
    assert query_facts(query='automobile',db_path=db,embed_fn=fn,embedding_model='bge-m3') == []
    assert query_facts(query='car',db_path=db,embed_fn=fn,embedding_model='bge-m3')[0]['subject'] == 'car'


@pytest.mark.parametrize('vector', [[True, False], ['1', '0'], [10**1000, 0], [float('nan'),0]])
def test_write_rejects_raw_invalid_vectors_before_coercion(tmp_path, vector):
    db=tmp_path/'facts.sqlite'
    upsert_fact(subject='car',predicate='is',object_text='vehicle',project='p',db_path=db,
                embed_fn=lambda _:vector,embedding_model='fixture')
    with connect(db) as conn:
        row=conn.execute('SELECT embedding_json, metadata_json FROM semantic_facts').fetchone()
    assert row[0] is None
    assert json.loads(row[1])['embedding_identity_verified'] is False
    assert query_facts(query='automobile',db_path=db,embed_fn=lambda _:[1.,0.],
                       embedding_model='fixture') == []
    assert query_facts(query='car',db_path=db,mode='keyword')
