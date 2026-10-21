#!/usr/bin/env python3
"""Adapter boundaries and real Benchmark Lab contract tests (no live store)."""
import copy
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from daydream_benchmark import evidence_rows, export_report, import_export

LAB = Path(os.environ.get('DAYDREAM_BENCHMARK_LAB', '/Users/tyroneross/dev/git-folder/prompt-model-benchmark-lab'))


def fixture():
    return {'schema': 'build-loop.daydream-sessions.v1', 'observational_only': True,
            'raw_text': 'SECRET_RAW_TRANSCRIPT', 'segments': [{
                'segment_id': 'abc123', 'session_id': 'session1', 'host': 'codex',
                'model': 'observed-model', 'effort': 'unknown', 'project': 'fixture',
                'task': 'unknown', 'signals': {'correction_heuristic': 2},
                'quality': {'code': 1}, 'cost_usd': 0, 'prompt': 'SECRET_RAW_TRANSCRIPT',
                'evidence': [{'path': '/local/transcript.jsonl', 'sha256': 'a' * 64,
                              'line': 4, 'session_id': 'session1', 'text': 'SECRET_RAW_TRANSCRIPT'}]}]}


class ExportTests(unittest.TestCase):
    def test_metrics_remain_unknown_and_metadata_is_whitelisted(self):
        report = fixture()
        report['method'] = {'rules_version': '2026-10-21.2', 'scanner_sha256': 'b'*64}
        row = evidence_rows(report)[0]
        self.assertEqual(json.loads(row['answer'])['producer'], report['method'])
        self.assertIsNone(row['quality'])
        self.assertIsNone(row['cost_usd'])
        self.assertIsNone(row['latency_ms'])
        self.assertEqual(row['provider'], 'unknown')
        self.assertEqual(row['harness'], 'unknown')
        self.assertNotIn('SECRET_RAW_TRANSCRIPT', json.dumps(row))
        metadata = json.loads(row['answer'])
        self.assertEqual(metadata['signals'], {'correction_heuristic': 2})
        self.assertFalse(metadata['routing_promotion'])

    def test_bad_report_identity_or_evidence_fails_closed(self):
        for change in ('schema', 'observational_only', 'segment_id', 'line', 'sha256', 'duplicate'):
            report = fixture()
            if change == 'schema':
                report['schema'] = 'unrelated'
            elif change == 'observational_only':
                report['observational_only'] = False
            elif change == 'segment_id':
                report['segments'][0]['segment_id'] = '../unstable'
            elif change in ('line', 'sha256'):
                report['segments'][0]['evidence'][0][change] = None
            else:
                report['segments'].append(copy.deepcopy(report['segments'][0]))
            with self.subTest(change=change), self.assertRaises(ValueError):
                evidence_rows(report)

    def test_assessment_export_strips_excerpts_and_reasoning(self):
        report = fixture()
        report['segments'][0]['incident_ids'] = ['incident1']
        report['assessments'] = [{'schema': 'build-loop.daydream-assessment.v1',
            'observational_only': True, 'packet_id': 'packet1', 'incident_id': 'incident1',
            'reviewer': 'local-independent-review', 'quote_data': 'PRIVATE_EXCERPT',
            'decisions': [{'criterion': 'response_intent_fidelity', 'verdict': 'fail',
                'rationale': 'PRIVATE_EXCERPT', 'citations': [{'source': {
                    'path': '/local/transcript.jsonl', 'sha256': 'a'*64, 'line': 4},
                    'text': 'PRIVATE_EXCERPT'}]}]}]
        row = evidence_rows(report)[0]
        self.assertNotIn('PRIVATE_EXCERPT', json.dumps(row))
        observed = json.loads(row['answer'])['assessments'][0]
        self.assertEqual(observed['decisions'][0]['verdict'], 'fail')
        self.assertIsNone(row['quality'])

    def test_stable_export_bytes_and_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            first = export_report(fixture(), root)
            second = export_report(fixture(), root)
            self.assertEqual(first, second)
            self.assertEqual(Path(first['evidence_path']).stat().st_mode & 0o777, 0o600)

    def test_import_refuses_changed_connector_before_running_lab(self):
        with tempfile.TemporaryDirectory() as root:
            receipt = export_report(fixture(), root)
            Path(receipt['connector_path']).write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Connector changed'):
                import_export(receipt, '/nonexistent-lab')

    def test_full_manifest_freshness_includes_sources_beyond_capped_locators(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            first, extra = root / 'first.jsonl', root / 'extra.jsonl'
            first.write_text('first')
            extra.write_text('extra')
            report = fixture()
            report['segments'][0]['evidence'][0].update(
                path=str(first), sha256=hashlib.sha256(first.read_bytes()).hexdigest())
            report['documents'] = [{'path': str(extra), 'status': 'complete',
                                    'sha256': hashlib.sha256(extra.read_bytes()).hexdigest()}]
            receipt = export_report(report, root / 'export')
            extra.write_text('changed beyond segment locator cap')
            with self.assertRaisesRegex(ValueError, 'Source changed since mining'):
                import_export(receipt, '/nonexistent-lab')

    @unittest.skipUnless((LAB / 'scripts/evidence_lab.py').is_file(), 'Local Benchmark Lab checkout unavailable')
    def test_real_lab_parser_idempotency_and_revision(self):
        with tempfile.TemporaryDirectory() as root:
            database = Path(root) / 'temporary-lab.sqlite3'
            report = fixture()
            source = Path(root).resolve() / 'session.jsonl'
            source.write_text('source transcript fixture')
            report['segments'][0]['evidence'][0].update(path=str(source), sha256=hashlib.sha256(source.read_bytes()).hexdigest())
            receipt = export_report(report, Path(root) / 'export')
            first = import_export(receipt, LAB, database)
            second = import_export(receipt, LAB, database)
            self.assertEqual(first['lab_result']['sources'][0]['records'], 1)
            self.assertEqual(second['lab_result']['sources'][0]['status'], 'unchanged')
            with closing(sqlite3.connect(database)) as conn:
                self.assertEqual(conn.execute('select count(*) from events').fetchone()[0], 1)
                record = json.loads(conn.execute('select payload from records').fetchone()[0])
                self.assertIsNone(record['quality'])
                self.assertEqual(record['model'], 'observed-model')
                self.assertEqual(record['effort'], 'unknown')
                self.assertEqual(record['tier'], 'field')
                self.assertEqual(record['details']['grading_method'], 'daydream-observation/ungraded-v1')
                self.assertEqual(json.loads(record['answer'])['dimensions']['host'], 'codex')
            revised = copy.deepcopy(report)
            revised['segments'][0]['signals']['correction_heuristic'] = 3
            receipt = export_report(revised, Path(root) / 'export')
            import_export(receipt, LAB, database)
            with closing(sqlite3.connect(database)) as conn:
                self.assertEqual(conn.execute('select count(*) from records').fetchone()[0], 1)
                self.assertEqual(conn.execute('select count(*) from events').fetchone()[0], 2)
                self.assertEqual(conn.execute('select count(*) from feedback').fetchone()[0], 0)
            source.write_text('changed after export')
            with self.assertRaisesRegex(ValueError, 'Source changed since mining'):
                import_export(receipt, LAB, database)


if __name__ == '__main__':
    unittest.main()
