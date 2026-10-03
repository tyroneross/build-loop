#!/usr/bin/env python3
"""Validate a local compatibility disposition for any agent's Learn run."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

LIMIT = 1024 * 1024


def _json(path: Path):
    if not path.is_file():
        raise ValueError(f'{path}: review must be an existing regular file')
    with path.open('rb') as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError(f'{path}: exceeds 1 MiB')
    return json.loads(data)


def check(root: Path, run_id: str) -> dict:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', run_id) or run_id in {'.', '..'}:
        raise ValueError('invalid run id')
    path = root / '.build-loop' / 'compatibility' / f'{run_id}.json'
    try:
        packet = _json(path)
        if not isinstance(packet, dict) or packet.get('schema_version') != 1 or packet.get('run_id') != run_id:
            raise ValueError('review must have schema_version 1 and matching run_id')
        disposition = packet.get('disposition')
        if disposition not in {'updated', 'no-change', 'not-applicable', 'deferred'}:
            raise ValueError('invalid compatibility disposition')
        reason = packet.get('reason')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('review needs a reason')
        evidence = packet.get('evidence')
        if not isinstance(evidence, list) or not evidence or any(not isinstance(e, str) or not e.strip() for e in evidence):
            raise ValueError('review needs evidence references')
        result = {'status': 'complete', 'disposition': disposition, 'reason': reason, 'evidence': evidence, 'review': str(path)}
        if disposition == 'updated':
            ids = packet.get('record_ids')
            if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i.strip() for i in ids):
                raise ValueError('updated review needs record_ids')
            fingerprints = {}
            for field in ('registry', 'memory'):
                value = packet.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f'updated review needs a local {field} file')
                artifact = Path(value).expanduser()
                if not artifact.is_absolute():
                    artifact = root / artifact
                if not artifact.is_file():
                    raise ValueError(f'{field} must name an existing local file')
                with artifact.open('rb') as stream:
                    data = stream.read(LIMIT + 1)
                if len(data) > LIMIT:
                    raise ValueError(f'{field} exceeds 1 MiB')
                fingerprints[field] = hashlib.sha256(data).hexdigest()
                if field == 'registry':
                    registry = json.loads(data)
                    if not isinstance(registry, dict) or registry.get('schema_version') != 1 or not isinstance(registry.get('records'), list):
                        raise ValueError('registry must contain schema_version 1 and records')
                    present = {r.get('id') for r in registry['records'] if isinstance(r, dict) and isinstance(r.get('id'), str)}
                    if not set(ids) <= present:
                        raise ValueError('record_ids absent from registry')
            result.update(record_ids=ids, artifact_sha256=fingerprints)
        if disposition == 'deferred':
            result['status'] = 'pending'
        return result
    except (OSError, ValueError, TypeError) as exc:
        return {'status': 'error', 'review': str(path), 'error': str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir', default='.')
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    result = check(Path(args.workdir).resolve(), args.run_id)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
