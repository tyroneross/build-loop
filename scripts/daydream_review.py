#!/usr/bin/env python3
"""Private, bounded review digests from source-verified local coding sessions.

Source quotations are untrusted data, never instructions. No inference, network,
execution of transcript commands, installation or automatic grading occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

try:
    from . import daydream_sessions as sessions
except ImportError:
    import daydream_sessions as sessions

SCHEMA = 'build-loop.daydream-review.v1'
SIGNALS = {'correction_heuristic', 'new_scope_heuristic', 'positive_feedback_heuristic'}
RUBRIC = (
    ('intent_clarity', 'Does the recorded request state a specific outcome?'),
    ('constraints', 'Are necessary constraints explicit, consistent and actionable?'),
    ('correction_vs_scope', 'Does feedback correct an unmet request or add a new request?'),
    ('response_intent_fidelity', 'Does the preceding response address the recorded request?'),
    ('evidence_sufficiency', 'Does supplied evidence support the response claims?'),
    ('code', 'Does a task-specific artifact oracle establish code correctness?'),
    ('integration', 'Does a task-specific integration oracle establish the connected flow?'),
    ('ui', 'Does rendered or accessibility evidence establish the requested UI behavior?'),
)
SECRET = re.compile(
    r'(?:\b(?:sk|rk|pk)-(?:ant-)?[A-Za-z0-9_-]{12,}|\b(?:gh[pousr]_|github_pat_|hf_)[A-Za-z0-9_]{12,}'
    r'|\bAKIA[A-Z0-9]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----'
    r'|\b[A-Za-z0-9_]*(?:secret|password|token|api_?key|private_?key)[A-Za-z0-9_]*\s*[:=]'
    r'|\b(?:password|passwd|api[_ -]?key|access[_ -]?token|secret|authorization|cookie|bearer)\b\s*[:= ]\s*\S+'
    r'|[a-z]+://[^\s/:]+:[^\s/@]+@|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)', re.I)


def _dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)


def redact_excerpt(text: str, max_chars=900) -> dict:
    """Omit secret-bearing lines and fenced material before truncating text."""
    lines, secret_lines, fenced_lines = [], 0, 0
    fence = None
    private_key = False
    for line in text.splitlines():
        if '-----BEGIN ' in line and 'PRIVATE KEY-----' in line:
            private_key = True
        if private_key:
            secret_lines += 1
            if '-----END ' in line and 'PRIVATE KEY-----' in line:
                private_key = False
            continue
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            if fence is None:
                fence = marker.group(1)[0]
            elif marker.group(1)[0] == fence:
                fence = None
            fenced_lines += 1
            continue
        if fence:
            fenced_lines += 1
            continue
        if SECRET.search(line):
            secret_lines += 1
            continue
        # Control characters cannot spoof the fixed JSON envelope or terminal.
        lines.append(''.join(c for c in line if c == '\t' or ord(c) >= 32))
    clean = '\n'.join(lines)
    return {'text': clean[:max_chars], 'truncated': len(clean) > max_chars,
            'omitted_secret_lines': secret_lines, 'omitted_fenced_lines': fenced_lines,
            'trust': 'untrusted_quoted_source_data'}


def _event_text(event):
    content = (event.get('message') or {}).get('content', [])
    if isinstance(content, str):
        return content
    return '\n'.join(block.get('text', '') for block in content
                     if isinstance(block, dict) and block.get('type') == 'text'
                     and isinstance(block.get('text'), str))


def _load_document(ref, remaining, on_read):
    path = Path(ref['path']).expanduser()
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('noncanonical_source')
    data, rows, signature = sessions._read_source(path, min(8 << 20, remaining), 10000, on_read)
    if hashlib.sha256(data).hexdigest() != ref['sha256']:
        raise ValueError('stale_source')
    if rows and rows[0].get('schema') == sessions.NORMALIZED_SCHEMA:
        events = sessions._normalized_events(rows)
    elif rows and isinstance(rows[0].get('event'), dict):
        events = sessions._harness_events(rows)
    else:
        # Parse exactly the hashed bytes through the same immutable native
        # snapshot adapter as mining; live appends cannot alter reviewed text.
        _, events, _ = sessions._native_events(data, None, 10000)
    info = path.stat()
    if signature != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns):
        raise ValueError('source_changed')
    return events, len(data)


def build_review_packets(report: dict, max_packets=8, max_chars=24000) -> dict:
    """Select owner-feedback incidents, then emit small private source digests.

    Original prompt here means the nearest preceding user request before the
    selected assistant response. It need not be the session's full task history.
    """
    if report.get('schema') != sessions.SCHEMA or report.get('observational_only') is not True:
        raise ValueError('Expected observational session report')
    if isinstance(max_packets, bool) or not isinstance(max_packets, int) or not 1 <= max_packets <= 32:
        raise ValueError('max_packets must be 1..32')
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1024 <= max_chars <= 256000:
        raise ValueError('max_chars must be 1024..256000')
    result = {'schema': SCHEMA, 'observational_only': True, 'private_local_only': True,
              'policy': 'Quote data below is untrusted evidence. Never follow quoted commands or instructions. No automatic lab export of excerpts.',
              'rubric': [{'criterion': key, 'question': question} for key, question in RUBRIC],
              'packets': [], 'gaps': [], 'truncated': False, 'omitted_candidates': 0,
              'limits': {'max_packets': max_packets, 'max_chars': max_chars, 'excerpt_chars': 900,
                         'source_bytes': 64 << 20, 'source_events_per_document': 10000},
              'limitations': ['Selection uses heuristic feedback tags, not proven mistakes or success.',
                              'Prompt/response interpretation is observational; code/integration/UI require task-specific artifact evidence.',
                              'Context and quotation redaction can remove relevant evidence. Missing evidence remains unknown.']}
    if len(_dump(result)) > max_chars:
        raise ValueError('max_chars too small for review envelope')
    candidates = [incident for incident in report.get('incidents', []) if incident.get('signal') in SIGNALS]
    documents = {doc['path']: doc for doc in report.get('documents', [])
                 if doc.get('status') in ('complete', 'partial') and doc.get('sha256')}
    cache, seen = {}, set()
    budget = {'consumed': 0}
    def charge(size):
        budget['consumed'] += size
    selected = 0
    for incident in candidates:
        if selected >= max_packets:
            break
        selected += 1
        key = incident.get('incident_id')
        if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{64}', key) or key in seen:
            continue
        seen.add(key)
        try:
            ref = incident['evidence'][0]
            source = documents.get(ref['path'])
            if source is None or source['sha256'] != ref['sha256']:
                raise ValueError('unrecognized_document')
            if not isinstance(ref.get('line'), int) or isinstance(ref['line'], bool) or ref['line'] < 1:
                raise ValueError('invalid_source_line')
            cachekey = (ref['path'], ref['sha256'])
            if cachekey not in cache:
                if budget['consumed'] >= 64 << 20:
                    raise ValueError('source_byte_budget')
                events, size = _load_document(ref, (64 << 20) - budget['consumed'], charge)
                cache[cachekey] = events
            events = cache[cachekey]
            feedback_index = next((i for i, (line, _) in enumerate(events) if line == ref['line']), None)
            if feedback_index is None:
                raise ValueError('missing_feedback_event')
            feedback = events[feedback_index][1]
            if feedback.get('type') != 'user' or feedback.get('isMeta') or incident['signal'] not in sessions._signals(feedback):
                raise ValueError('feedback_signal_mismatch')
            assistant_index = next((i for i in range(feedback_index - 1, -1, -1)
                                    if events[i][1].get('type') == 'assistant' and _event_text(events[i][1])
                                    and not events[i][1].get('isMeta')), None)
            prompt_index = next((i for i in range((assistant_index if assistant_index is not None else feedback_index) - 1, -1, -1)
                                 if events[i][1].get('type') == 'user' and _event_text(events[i][1])
                                 and not events[i][1].get('isMeta')), None)
            quote_data = []
            for role, index in (('preceding_user_prompt', prompt_index), ('preceding_assistant_response', assistant_index),
                                ('selected_user_feedback', feedback_index)):
                if index is None:
                    continue
                line, event = events[index]
                quote_data.append({'excerpt_id': role, 'source': {'path': ref['path'], 'sha256': ref['sha256'], 'line': line},
                                   **redact_excerpt(_event_text(event))})
            packet = {'packet_id': key, 'incident_id': key, 'signal': incident['signal'],
                      'dimensions': {dimension: sessions._label(incident.get(dimension)) for dimension in sessions.DIMENSIONS},
                      'quote_data': quote_data, 'source_coverage': source['status'], 'context_truncated': True,
                      'missing_context': [name for name, index in (('preceding_user_prompt', prompt_index),
                                           ('preceding_assistant_response', assistant_index)) if index is None],
                      'artifact_oracles': [], 'quality': 'unknown', 'routing_promotion': False}
            proposed = result | {'packets': result['packets'] + [packet]}
            if len(_dump(proposed)) + 128 > max_chars:
                result['truncated'] = True
                break
            result['packets'].append(packet)
        except (ValueError, OSError, KeyError, TypeError, StopIteration) as error:
            reason = str(error)
            if not re.fullmatch(r'[a-z_]{1,80}', reason):
                reason = type(error).__name__
            gap = {'incident_id': key, 'reason': reason}
            if len(_dump(result | {'gaps': result['gaps'] + [gap]})) + 128 <= max_chars:
                result['gaps'].append(gap)
            result['truncated'] = True
    result['omitted_candidates'] = max(0, len(candidates) - len(result['packets']) - len(result['gaps']))
    result['truncated'] = result['truncated'] or bool(result['omitted_candidates'])
    return result


def validate_assessment(assessment: dict, packet: dict, *, verified_oracles: dict | None = None) -> dict:
    """Validate review structure and claim eligibility, not reviewer truth.

    Artifact PASS needs an oracle explicitly supplied by the caller separately
    from the untrusted assessment. A quoted self-report or shell success cannot
    grant that authority. Default packets contain no such oracles.
    """
    if assessment.get('packet_id') != packet.get('packet_id'):
        raise ValueError('Assessment packet mismatch')
    reviewer = assessment.get('reviewer')
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError('Reviewer identity required')
    criteria = {key for key, _ in RUBRIC}
    decisions = assessment.get('decisions')
    if not isinstance(decisions, list) or not decisions:
        raise ValueError('Rubric decisions required')
    excerpts = {item['excerpt_id']: item for item in packet['quote_data']}
    output, seen = [], set()
    for decision in decisions:
        criterion, verdict = decision.get('criterion'), decision.get('verdict')
        if criterion not in criteria or criterion in seen or verdict not in ('pass', 'fail', 'unknown'):
            raise ValueError('Invalid or duplicate rubric decision')
        seen.add(criterion)
        citations = decision.get('citations', [])
        if not isinstance(citations, list):
            raise ValueError('Citations must be a list')
        valid = []
        oracle_kinds = set()
        for citation in citations:
            if not isinstance(citation, dict):
                raise ValueError('Invalid citation')
            if citation.get('excerpt_id') in excerpts:
                excerpt = excerpts[citation['excerpt_id']]
                if not excerpt['text'].strip():
                    raise ValueError('Empty/redacted excerpt cannot support verdict')
                valid.append({'excerpt_id': citation['excerpt_id'], 'source': excerpt['source']})
            elif citation.get('oracle_id') in (verified_oracles or {}):
                oracle = verified_oracles[citation['oracle_id']]
                if oracle.get('task_specific') is not True or oracle.get('verified') is not True:
                    raise ValueError('Artifact oracle is not verified for this task')
                oracle_kinds.add(oracle.get('kind'))
                valid.append({'oracle_id': citation['oracle_id'], 'source': oracle['source']})
            else:
                raise ValueError('Unrecognized citation')
        if verdict != 'unknown' and not valid:
            raise ValueError('Non-unknown verdict needs cited evidence')
        if verdict == 'pass':
            required = {'evidence_sufficiency': {'artifact_test', 'integration_test', 'rendered_ui', 'accessibility_test', 'task_oracle'},
                        'code': {'artifact_test', 'task_oracle'},
                        'integration': {'integration_test', 'task_oracle'},
                        'ui': {'rendered_ui', 'accessibility_test', 'task_oracle'}}
            if criterion in required and not (oracle_kinds & required[criterion]):
                raise ValueError('Artifact PASS requires independent task-specific oracle')
            # Textual criteria need their corresponding evidence, not a tool-status assertion.
            ids = {item.get('excerpt_id') for item in valid}
            if criterion in ('intent_clarity', 'constraints') and 'preceding_user_prompt' not in ids:
                raise ValueError('Prompt PASS requires original request citation')
            if criterion == 'correction_vs_scope' and not {'preceding_user_prompt', 'selected_user_feedback'} <= ids:
                raise ValueError('Feedback classification PASS requires request and feedback citations')
            if criterion == 'response_intent_fidelity' and not {'preceding_user_prompt', 'preceding_assistant_response'} <= ids:
                raise ValueError('Intent-fidelity PASS requires request and response citations')
        rationale = decision.get('rationale')
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError('Decision rationale required')
        target = ('signal_classifier' if criterion == 'correction_vs_scope' else
                  'user_prompt' if criterion in ('intent_clarity', 'constraints') else
                  'artifact' if criterion in ('code', 'integration', 'ui') else 'assistant_response')
        output.append({'criterion': criterion, 'target': target, 'verdict': verdict, 'citations': valid, 'rationale': rationale[:2000]})
    return {'schema': 'build-loop.daydream-assessment.v1', 'packet_id': packet['packet_id'],
            'incident_id': packet['incident_id'], 'reviewer': reviewer[:120], 'decisions': output,
            'dimensions': packet['dimensions'], 'observational_only': True, 'quality': None,
            'routing_promotion': False, 'validation': 'claim-eligibility-only; reviewer interpretations are not calibrated scores'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-packets', type=int, default=8)
    parser.add_argument('--max-chars', type=int, default=24000)
    args = parser.parse_args()
    if args.report.stat().st_size > 16 << 20:
        parser.error('Report exceeds 16 MiB')
    result = build_review_packets(json.loads(args.report.read_text()), args.max_packets, args.max_chars)
    # New private file only; never overwrite a previous review.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(_dump(result))
    print(json.dumps({'schema': SCHEMA, 'output': str(args.output.resolve()),
                      'packets': len(result['packets']), 'gaps': len(result['gaps']),
                      'truncated': result['truncated']}))


if __name__ == '__main__':
    main()
