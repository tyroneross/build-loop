#!/usr/bin/env python3
"""Export observational Daydream segments to Benchmark Lab's local model-log API.

No raw transcript text, inferred quality, remote calls, or routing promotion.
The Lab's model-log adapter retains only grading_method in details, so the
reviewable answer contains an explicit whitelist of observational provenance.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

SCHEMA = 'build-loop.daydream-benchmark.v1'
REPORT_SCHEMA = 'build-loop.daydream-sessions.v1'
SIGNALS = {'correction_heuristic', 'new_scope_heuristic', 'positive_feedback_heuristic',
           'tool_error_observed', 'test_pass_observed', 'test_fail_observed'}
DIMENSIONS = ('host', 'provider', 'model', 'effort', 'harness', 'project', 'task', 'session_id')


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))


def _string(value):
    return value if isinstance(value, str) and value.strip() else 'unknown'


def _assessment_metadata(report: dict, segment: dict) -> list[dict]:
    """Keep validated review decisions and locators, never quotes or rationale."""
    result = []
    for assessment in report.get('assessments', []):
        if assessment.get('schema') != 'build-loop.daydream-assessment.v1' or assessment.get('observational_only') is not True:
            raise ValueError('Expected observational validated review assessment')
        if assessment.get('incident_id') not in segment.get('incident_ids', []):
            continue
        decisions = []
        for decision in assessment.get('decisions', []):
            if decision.get('criterion') not in {'intent_clarity', 'constraints', 'correction_vs_scope',
                    'response_intent_fidelity', 'evidence_sufficiency', 'code', 'integration', 'ui'}:
                raise ValueError('Unknown assessment criterion')
            if decision.get('verdict') not in ('pass', 'fail', 'unknown'):
                raise ValueError('Unknown assessment verdict')
            citations = []
            for citation in decision.get('citations', []):
                ref = citation.get('source', {})
                if not isinstance(ref.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', ref['sha256']):
                    raise ValueError('Assessment source hash required')
                if type(ref.get('line')) is not int or ref['line'] < 1:
                    raise ValueError('Assessment source line required')
                citations.append({'path': _string(ref.get('path')), 'sha256': ref['sha256'], 'line': ref['line']})
            criterion = decision['criterion']
            target = ('signal_classifier' if criterion == 'correction_vs_scope' else
                      'user_prompt' if criterion in ('intent_clarity', 'constraints') else
                      'artifact' if criterion in ('code', 'integration', 'ui') else 'assistant_response')
            decisions.append({'criterion': criterion, 'target': target, 'verdict': decision['verdict'], 'citations': citations})
        result.append({'packet_id': assessment['packet_id'], 'incident_id': assessment['incident_id'],
                       'reviewer': _string(assessment.get('reviewer'))[:120], 'decisions': decisions,
                       'observational_only': True, 'quality': None, 'routing_promotion': False})
    return result


def evidence_rows(report: dict) -> list[dict]:
    """One unscored Lab record per stable segment; reject ambiguous identities."""
    if report.get('schema') != REPORT_SCHEMA or report.get('observational_only') is not True:
        raise ValueError('Expected observational build-loop.daydream-sessions.v1 report')
    rows, seen = [], set()
    for segment in report.get('segments', []):
        key = segment.get('segment_id')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', key):
            raise ValueError('Stable segment_id required')
        if key in seen:
            raise ValueError('Duplicate segment_id')
        seen.add(key)
        dimensions = {k: _string(segment.get(k)) for k in DIMENSIONS}
        refs = []
        for ref in segment.get('evidence', []):
            line = ref.get('line')
            if not isinstance(line, int) or isinstance(line, bool) or line < 1:
                raise ValueError('Evidence line must be a positive integer')
            sha = ref.get('sha256')
            if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha):
                raise ValueError('Evidence sha256 required')
            locator = {'path': _string(ref.get('path')), 'sha256': sha, 'line': line,
                       'session_id': _string(ref.get('session_id'))}
            timestamp = ref.get('timestamp')
            if isinstance(timestamp, str):
                try:
                    datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    locator['timestamp'] = timestamp
                except ValueError:
                    pass
            refs.append(locator)
        if not refs:
            raise ValueError('Segment evidence required')
        signals = {k: v for k, v in segment.get('signals', {}).items()
                   if k in SIGNALS and isinstance(v, int)
                   and not isinstance(v, bool) and v >= 0}
        # Never copy arbitrary report/segment fields, prompts, excerpts or answers.
        coverage = report.get('coverage', {})
        scope = {'complete': coverage.get('complete') is True,
                 'truncated': coverage.get('truncated') is True,
                 'reported_gaps': len(coverage.get('gaps', []))}
        method = report.get('method', {})
        scanner_hash = method.get('scanner_sha256')
        producer = {'rules_version': _string(method.get('rules_version')),
                    'scanner_sha256': scanner_hash if isinstance(scanner_hash, str) and re.fullmatch(r'[a-f0-9]{64}', scanner_hash) else None}
        metadata = {'schema': SCHEMA, 'observational_only': True, 'segment_id': key,
                    'dimensions': dimensions, 'evidence': refs, 'signals': signals, 'coverage': scope, 'producer': producer,
                    'quality_status': 'ungraded', 'routing_promotion': False,
                    'assessments': _assessment_metadata(report, segment)}
        rows.append({'schema': 'pmbl.evidence/v1', 'id': 'daydream-' + key,
                     **{k: dimensions[k] for k in ('provider', 'model', 'effort', 'harness', 'task')},
                     'complexity': 'unknown', 'cohort': 'daydream-observational/v1',
                     'case_id': dimensions['session_id'],
                     'occurred_at': next((r['timestamp'] for r in refs if 'timestamp' in r), None),
                     'quality': None, 'cost_usd': None,
                     'latency_ms': None, 'grading_method': 'daydream-observation/ungraded-v1',
                     'answer': _json(metadata)})
    return sorted(rows, key=lambda r: r['id'])


def _atomic(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_report(report: dict, output_dir: str | Path, source_id='daydream-sessions') -> dict:
    """Write a stable snapshot plus separate connector; never edit Lab config."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', source_id):
        raise ValueError('Invalid connector source_id')
    root = Path(output_dir).expanduser().resolve()
    rows = evidence_rows(report)
    content = ''.join(_json(row) + '\n' for row in rows)
    path, config_path = root / 'evidence.jsonl', root / 'connector.json'
    _atomic(path, content)
    config = {'sources': [{'id': source_id, 'adapter': 'model-log', 'path': str(path)}]}
    _atomic(config_path, _json(config) + '\n')
    source_refs = [{'path': doc['path'], 'sha256': doc['sha256']}
                   for doc in report.get('documents', [])
                   if doc.get('status') in ('complete', 'partial') and doc.get('sha256')]
    # Segment locators are capped by the miner; retain the full document manifest
    # for source freshness, including documents beyond those first few locators.
    source_manifest = _json(source_refs) + '\n'
    manifest_path = root / 'source-manifest.json'
    _atomic(manifest_path, source_manifest)
    receipt = {'schema': SCHEMA, 'status': 'exported', 'observational_only': True,
               'records': len(rows), 'source_id': source_id, 'evidence_path': str(path),
               'connector_path': str(config_path), 'source_manifest_path': str(manifest_path),
               'source_manifest_sha256': hashlib.sha256(source_manifest.encode()).hexdigest(), 'sha256': hashlib.sha256(content.encode()).hexdigest(),
               'unavailable_metrics': ['quality', 'cost_usd', 'latency_ms']}
    _atomic(root / 'export-receipt.json', _json(receipt) + '\n')
    return receipt


def validate_sources(evidence: Path, manifest: list[dict] | None = None) -> dict:
    """Rehash regular local source files without retaining or exposing contents."""
    expected = {}
    for line in evidence.read_text().splitlines():
        row = json.loads(line)
        metadata = json.loads(row['answer'])
        refs = list(metadata['evidence'])
        refs.extend(ref for assessment in metadata.get('assessments', [])
                    for decision in assessment['decisions'] for ref in decision['citations'])
        for ref in refs:
            path = Path(ref['path']).expanduser()
            if not path.is_absolute():
                raise ValueError('Source locator must be absolute')
            if path in expected and expected[path] != ref['sha256']:
                raise ValueError('Conflicting source revisions in export')
            expected[path] = ref['sha256']
    for ref in manifest or []:
        path = Path(ref['path']).expanduser()
        if not path.is_absolute() or not re.fullmatch(r'[a-f0-9]{64}', ref['sha256']):
            raise ValueError('Invalid source manifest locator')
        if path in expected and expected[path] != ref['sha256']:
            raise ValueError('Conflicting source revisions in manifest')
        expected[path] = ref['sha256']
    for path, expected_hash in expected.items():
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError('Source freshness check rejects symlinks')
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 128 * 1024 * 1024:
                raise ValueError('Source freshness check requires bounded regular files')
            digest = hashlib.sha256()
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('Source changed during freshness check')
            if digest.hexdigest() != expected_hash:
                raise ValueError('Source changed since mining; remine before import')
    return {'status': 'hashes_match_before_import', 'files': len(expected),
            'limitation': 'Point-in-time check; source files are not locked after validation'}


def import_export(receipt: dict, lab_root: str | Path, db: str | Path | None = None) -> dict:
    """Run the existing local Lab CLI, saving its idempotent import result."""
    lab = Path(lab_root).expanduser().resolve()
    script = lab / 'scripts/evidence_lab.py'
    config = Path(receipt['connector_path']).resolve()
    evidence = Path(receipt['evidence_path']).resolve()
    if hashlib.sha256(evidence.read_bytes()).hexdigest() != receipt['sha256']:
        raise ValueError('Export changed since receipt')
    actual_config = json.loads(config.read_text())
    expected = {'sources': [{'id': receipt['source_id'], 'adapter': 'model-log', 'path': str(evidence)}]}
    if actual_config != expected:
        raise ValueError('Connector changed since export')
    manifest_path = Path(receipt['source_manifest_path'])
    manifest_data = manifest_path.read_bytes()
    if hashlib.sha256(manifest_data).hexdigest() != receipt['source_manifest_sha256']:
        raise ValueError('Source manifest changed since export')
    freshness = validate_sources(evidence, json.loads(manifest_data))
    # evidence_lab.py DEFAULT_DB is this path; --db makes the destination explicit.
    database = Path(db).expanduser().resolve() if db else lab / 'data/local/evidence-lab/evidence.sqlite3'
    result = subprocess.run([sys.executable, str(script), '--db', str(database), 'pull', '--config', str(config)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Benchmark Lab import failed: ' + (result.stdout + result.stderr)[-4000:])
    response = json.loads(result.stdout)
    output = {**receipt, 'status': 'imported', 'database': str(database), 'source_freshness': freshness, 'lab_result': response}
    _atomic(config.parent / 'import-receipt.json', _json(output) + '\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--source-id', default='daydream-sessions')
    parser.add_argument('--import-lab', type=Path)
    parser.add_argument('--db', type=Path)
    args = parser.parse_args()
    if args.db and not args.import_lab:
        parser.error('--db requires --import-lab')
    receipt = export_report(json.loads(args.report.read_text()), args.output_dir, args.source_id)
    if args.import_lab:
        receipt = import_export(receipt, args.import_lab, args.db)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
