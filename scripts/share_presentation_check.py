#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Check Build Loop's scoped share-presentation record; never claim render proof."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

FIELDS = ('surface', 'recipient', 'title', 'description', 'image', 'identity',
          'payload', 'privacy', 'fallback', 'validation')


def nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check(record: object) -> dict:
    errors, unverified, failed = [], [], []
    if not isinstance(record, dict):
        return {'structural_pass': False, 'errors': ['record must be an object'],
                'unverified': [], 'failed': [], 'checks_pass': False, 'recipient_rendering_proven': False}
    applicability = record.get('applicability')
    if applicability not in ('applicable', 'not_applicable'):
        errors.append('applicability must be applicable or not_applicable')
    if not nonempty(record.get('reason')):
        errors.append('reason is required')
    surfaces = record.get('surfaces')
    if not isinstance(surfaces, list):
        errors.append('surfaces must be an array')
        surfaces = []
    if applicability == 'applicable' and not surfaces:
        errors.append('applicable record needs at least one surface')
    if applicability == 'not_applicable' and surfaces:
        errors.append('not_applicable record must have empty surfaces')
    for index, surface in enumerate(surfaces):
        prefix = f'surfaces[{index}]'
        if not isinstance(surface, dict):
            errors.append(f'{prefix} must be an object')
            continue
        for field in FIELDS:
            if not nonempty(surface.get(field)):
                errors.append(f'{prefix}.{field} is required')
        evidence = surface.get('evidence')
        if not isinstance(evidence, list):
            errors.append(f'{prefix}.evidence must be an array')
            continue
        if not evidence:
            unverified.append(f'{prefix}: no execution evidence recorded')
        for item in evidence:
            if not isinstance(item, dict):
                errors.append(f'{prefix}.evidence entries must be objects')
                continue
            if (not nonempty(item.get('check')) or not nonempty(item.get('detail'))
                    or item.get('status') not in ('verified', 'unverified', 'not_applicable', 'failed')):
                errors.append(f'{prefix}.evidence requires check, status and detail')
            if item.get('status') == 'failed':
                failed.append(f"{prefix}: {item.get('check')}: {item.get('detail')}")
            if item.get('status') == 'unverified':
                unverified.append(f"{prefix}: {item.get('check')}: {item.get('detail')}")
    return {'structural_pass': not errors, 'errors': errors, 'unverified': unverified,
            'failed': failed, 'checks_pass': not errors and not failed,
            'recipient_rendering_proven': False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('record', type=Path)
    args = parser.parse_args()
    try:
        result = check(json.loads(args.record.read_text()))
    except (OSError, ValueError) as exc:
        result = {'structural_pass': False, 'errors': [str(exc)],
                  'unverified': [], 'failed': [], 'checks_pass': False, 'recipient_rendering_proven': False}
    print(json.dumps(result, indent=2))
    return 0 if result['checks_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
