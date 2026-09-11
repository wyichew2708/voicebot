"""Strict, model-independent scoring for routing experiments (not voice quality)."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from .call import router


def load_cases(path):
    raw = Path(path).read_bytes()
    cases = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    seen = set()
    for c in cases:
        if (not isinstance(c.get('id'), str) or c['id'] in seen
                or c.get('expected') not in router.LABELS
                or c.get('language') not in ('en', 'zh', 'singlish')
                or not isinstance(c.get('text'), str) or not c['text'].strip()
                or type(c.get('turn')) is not int or not 0 <= c['turn'] <= 7
                or not isinstance(c.get('pending'), (str, type(None)))
                or type(c.get("identity_verified", False)) is not bool):
            raise ValueError('Invalid or duplicate routing case')
        seen.add(c['id'])
    if not cases:
        raise ValueError('Empty routing dataset')
    return cases, hashlib.sha256(raw).hexdigest()


def prompt(c):
    return router.user_prompt(c['text'], c['turn'], c['language'], pending=c.get('pending'), identity_verified=c.get('identity_verified', False))


def score(cases, predictions):
    indexed = {}
    valid_ids = {c['id'] for c in cases}
    for p in predictions:
        if p.get('id') in indexed or p.get('id') not in valid_ids:
            raise ValueError('Duplicate or unknown prediction ID')
        if p.get('status', 'ok') not in ('ok', 'timeout', 'unavailable', 'invalid_output'):
            raise ValueError('Unknown prediction status')
        indexed[p['id']] = p
    rows = []
    for c in cases:
        p = indexed.get(c['id'])
        status = p.get('status', 'ok') if p else 'missing'
        label = router.parse(p.get('reply')) if p and status == 'ok' else None
        if status == 'ok' and label is None:
            status = 'invalid_output'
        latency = p.get('wall_ms') if p else None
        if latency is not None and (type(latency) not in (int, float)
                                    or not math.isfinite(latency) or latency < 0):
            raise ValueError('Invalid measured latency')
        rows.append({'id':c['id'], 'language':c['language'], 'expected':c['expected'],
                     'predicted':label, 'status':status,
                     'correct':status == 'ok' and label == c['expected'], 'wall_ms':latency})
    def summary(items):
        measured = sorted(r['wall_ms'] for r in items if r['wall_ms'] is not None)
        return {'n':len(items), 'correct':sum(r['correct'] for r in items),
                'accuracy':sum(r['correct'] for r in items)/len(items),
                'statuses':dict(Counter(r['status'] for r in items)),
                'wall_ms':{'n':len(measured),
                           'p50':measured[math.ceil(len(measured)*.5)-1] if measured else None,
                           'p95':measured[math.ceil(len(measured)*.95)-1] if measured else None}}
    return {'overall':summary(rows),
            'by_language':{k:summary([r for r in rows if r['language']==k]) for k in sorted({r['language'] for r in rows})},
            'by_intent':{k:summary([r for r in rows if r['expected']==k]) for k in sorted({r['expected'] for r in rows})},
            'confusion':dict(Counter(f"{r['expected']} -> {r['predicted'] or r['status']}" for r in rows)),
            'failures':[r for r in rows if not r['correct']], 'rows':rows}


CRITICAL_INTENTS = frozenset({'dnc', 'human', 'advice', 'email_change'})


def compare(cases, dataset_sha256, baseline, candidate, *, min_accuracy=.95,
            max_failure_rate=.02):
    """Re-score raw evidence, then evaluate routing-only gates; never deploy."""
    for value in (min_accuracy, max_failure_rate):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('Gate thresholds must be finite fractions from 0 to 1')
    expected = {'dataset_sha256':dataset_sha256, 'prompt_version':router.PROMPT_VERSION,
                'system_prompt_sha256':hashlib.sha256(router.system_prompt().encode()).hexdigest(),
                'max_tokens':router.MAX_TOKENS}
    reports = []
    blockers = []
    for name, source in (('baseline',baseline), ('candidate',candidate)):
        for key, value in expected.items():
            if source.get(key) != value:
                raise ValueError(f'{name} provenance mismatch: {key}')
        if source.get('mode') not in ('live', 'score'):
            raise ValueError(f'{name} is not an inference report')
        timeout = source.get('timeout_ms')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(f'{name} has an invalid timeout')
        for key in ('model','revision','runtime','quantization'):
            if not isinstance(source.get(key), str) or source[key].strip().lower() in ('','unknown'):
                blockers.append(f'{name}: missing {key}')
        reports.append(score(cases, source['predictions']))
    if baseline['timeout_ms'] != candidate['timeout_ms']:
        raise ValueError('Incomparable timeout limits')
    state = baseline.get('declared_state')
    if state not in ('cold','warm') or candidate.get('declared_state') != state:
        blockers.append('Matching declared cold/warm conditions are required')
    b, c = reports
    bo, co = b['overall'], c['overall']
    failures = sum(n for status, n in co['statuses'].items() if status != 'ok')
    critical = [r['id'] for r in c['rows'] if r['expected'] in CRITICAL_INTENTS and not r['correct']]
    missing_critical = sorted(CRITICAL_INTENTS - {r['expected'] for r in c['rows']})
    regressions = [new['id'] for old,new in zip(b['rows'],c['rows']) if old['correct'] and not new['correct']]
    fixes = [new['id'] for old,new in zip(b['rows'],c['rows']) if not old['correct'] and new['correct']]
    language_regressions = [lang for lang, stats in c['by_language'].items()
                            if stats['accuracy'] < b['by_language'][lang]['accuracy']]
    gates = {
        'accuracy_floor':co['accuracy'] >= min_accuracy,
        'overall_non_regression':co['accuracy'] >= bo['accuracy'],
        'language_non_regression':not language_regressions,
        'critical_cases_present':not missing_critical,
        'critical_errors_zero':not critical,
        'failure_rate':failures/co['n'] <= max_failure_rate,
        'timeout_non_regression':co['statuses'].get('timeout',0) <= bo['statuses'].get('timeout',0),
    }
    complete_timing = all(r['wall_ms']['n'] == r['n'] for r in (bo,co))
    latency = None
    if complete_timing and state in ('cold','warm') and candidate.get('declared_state') == state:
        latency = {p:co['wall_ms'][p]-bo['wall_ms'][p] for p in ('p50','p95')}
        gates['latency_non_regression'] = all(delta <= 0 for delta in latency.values())
        gates['latency_improves'] = any(delta < 0 for delta in latency.values())
    else:
        blockers.append('Complete measured timings under matching conditions are required')
    return {
        'schema_version':1, **expected,
        'thresholds':{'min_accuracy':min_accuracy,'max_failure_rate':max_failure_rate},
        'baseline_model':baseline.get('model'), 'candidate_model':candidate.get('model'),
        'baseline':b, 'candidate':c, 'gates':gates, 'evidence_blockers':blockers,
        'routing_checks_passed':all(gates.values()) and not blockers,
        'accuracy_delta':co['accuracy']-bo['accuracy'], 'latency_delta_ms':latency,
        'regression_ids':regressions, 'fixed_ids':fixes, 'critical_failure_ids':critical,
        'missing_critical_intents':missing_critical, 'language_regressions':language_regressions,
        'case_splits':dict(Counter(case.get('split','unknown') for case in cases)),
        'promotion_eligible':False,
        'remaining_release_gates':[
            'Independent reviewed held-out data and deterministic action/consent regressions.',
            'Verify actual served weights, revision, reasoning settings and hardware conditions.',
            'Measure total RAM/GPU memory, full voice latency and sustained audio behavior on target hardware.',
        ],
        'notes':[
            'Summaries are recomputed from raw predictions; supplied summary metrics are ignored.',
            'Thresholds are provisional experiment defaults, not approved production acceptance criteria.',
            'Metadata and external timings are declarations, not independent hardware attestations.',
            'Paired regressions and fixes are listed even when aggregate accuracy is unchanged.',
            'No configuration or model is changed by this comparison.',
        ],
    }
