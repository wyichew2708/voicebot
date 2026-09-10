"""Group measured response events without inventing latency for old call logs."""
from __future__ import annotations

from collections import Counter, defaultdict
import math

from .telemetry import milliseconds


def distribution(values):
    data = sorted(v for value in values if (v := milliseconds(value)) is not None)
    def percentile(q):
        return data[max(0, math.ceil(q * len(data)) - 1)] if data else None
    return {'n': len(data), 'p50': percentile(.5), 'p95': percentile(.95)}


def first_audio(row, role):
    values = [milliseconds(a.get('client_first_audio_ms')) for a in row.get('audio', [])
              if a.get('role') == role]
    return min((v for v in values if v is not None), default=None)


def summarize(calls):
    groups = defaultdict(list)
    legacy_calls = 0
    for call in calls:
        rows = [e for e in call.get('events', []) if e.get('kind') == 'response_metrics'
                and e.get('schema_version') == 1]
        if not rows:
            legacy_calls += 1
        for row in rows:
            models = tuple(sorted(row.get('models', {}).items()))
            key = (row.get('profile', 'unknown'), row.get('declared_model_state', 'unknown'),
                   row.get('input_kind', 'unknown'), row.get('cache_state', 'unknown'),
                   row.get('language', 'unknown'), row.get('voice', 'unknown'), models)
            groups[key].append(row)
    report = []
    for (profile, state, input_kind, cache, language, voice, models), rows in sorted(groups.items()):
        completed = [r for r in rows if r.get('status') == 'completed']
        operations = [op for r in completed for op in r.get('operations', [])
                      if op.get('status') == 'completed']
        report.append({
            'profile': profile, 'declared_model_state': state, 'input_kind': input_kind,
            'cache_state': cache, 'language': language, 'voice': voice,
            'models': dict(models), 'responses': len(rows),
            'statuses': dict(Counter(r.get('status', 'unknown') for r in rows)),
            'answer_first_audio_ms': distribution(first_audio(r, 'answer') for r in completed),
            'acknowledgement_first_audio_ms': distribution(first_audio(r, 'acknowledgement') for r in completed),
            'clarification_first_audio_ms': distribution(first_audio(r, 'clarification') for r in completed),
            'missing_answer_playback': sum(first_audio(r, 'answer') is None for r in completed),
            'response_queue_ms': distribution(r.get('response_queue_ms') for r in completed),
            'endpoint_ms': distribution(r.get('endpoint_ms') for r in completed),
            'backend_operations_ms': {
                stage: distribution(op.get('wall_ms') for op in operations if op.get('stage') == stage)
                for stage in ('asr', 'llm', 'tts')},
            'worker_queue_ms': distribution(op.get('worker_queue_ms') for op in operations),
            'tts_service_rtf': distribution(op.get('tts_service_rtf') for op in operations),
        })
    return {
        'schema_version': 1, 'legacy_calls_without_measurements': legacy_calls, 'groups': report,
        'notes': [
            'Client playback values are estimates or browser playing events, not acoustic loopback measurements.',
            'Cold/warm is operator-declared; unknown remains unknown. Model processes may be external.',
            'Mock, typed and opening traffic are separated from microphone response samples.',
            'Percentiles use completed responses/operations only. Missing playback is never zero latency.',
            'Operation spans overlap and include queueing; do not add their percentiles into end-to-end latency.',
            'Cache hits do not contribute a TTS real-time-factor sample.',
        ],
    }


def markdown(report):
    def show(stats):
        return '—' if not stats['n'] else f"{stats['p50']:.1f} / {stats['p95']:.1f} ({stats['n']})"
    lines = ['# Voice response latency', '',
             'Values: p50 / p95 in milliseconds (sample count). Playback values are browser estimates.', '',
             '| Group | Profile | Declared state | Input | Language / voice | Cache | Completed / total | Answer audio | Acknowledgement audio | Missing answer |',
             '|---|---|---|---|---|---|---:|---:|---:|---:|']
    for index, g in enumerate(report['groups'], 1):
        lines.append(f"| {index} | {g['profile']} | {g['declared_model_state']} | {g['input_kind']} | {g['language']} / {g['voice']} | {g['cache_state']} | "
                     f"{g['statuses'].get('completed', 0)} / {g['responses']} | "
                     f"{show(g['answer_first_audio_ms'])} | {show(g['acknowledgement_first_audio_ms'])} | "
                     f"{g['missing_answer_playback']} |")
    if not report['groups']:
        lines += ['', 'No response measurements found. Recorded transcripts cannot establish audible latency.']
    lines.append('')
    for index, g in enumerate(report['groups'], 1):
        models = ', '.join(f'{k}: {v}' for k, v in g['models'].items()) or 'unknown'
        statuses = ', '.join(f'{k}: {v}' for k, v in sorted(g['statuses'].items()))
        lines.append(f'- Group {index} — models: {models}; responses: {statuses}.')
    lines += ['', f"Calls without measurements: {report['legacy_calls_without_measurements']}", '']
    for note in report['notes']:
        lines.append('- ' + note)
    lines += ['', 'The JSON report includes model IDs, status counts, stage timings, queueing and TTS real-time factors.']
    return '\n'.join(lines) + '\n'
