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
