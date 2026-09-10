import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from voicebot.telemetry import MeasuredBackend, ResponseTrace, Operation, Worker, milliseconds
from voicebot.latency_report import summarize, distribution, markdown
from voicebot.runtime.base import Speech
from voicebot.telemetry import measured_worker


@pytest.mark.parametrize('value', [None, True, '50', float('nan'), float('inf'), -1, 600001])
def test_invalid_client_durations_are_missing(value):
    assert milliseconds(value) is None


def test_zero_is_a_valid_duration():
    assert milliseconds(0) == 0
    assert distribution([None, 0, 10]) == {'n':2, 'p50':0, 'p95':10}


def test_response_queue_and_browser_durations_use_separate_clocks():
    now = [1000.0]
    rows = []
    trace = ResponseTrace({'profile':'mock'}, rows.append, clock=lambda: now[0])
    now[0] += .2
    trace.start()
    now[0] += .4
    trace.audio_ready(1, 'acknowledgement', 'en')
    assert trace.playback_started(1, 1200, 'media_playing_event')
    # Duplicate and invalid audio IDs cannot lower measured latency.
    assert trace.playback_started(1, 1, 'media_playing_event') is None
    assert trace.playback_started(2, 1, 'media_playing_event') is None
    trace.audio_ready(2, 'answer', 'en')
    trace.playback_started(2, 1900, 'webaudio_schedule_estimate')
    trace.finish('completed')
    trace.finish('interrupted')
    assert len(rows) == 1
    row = rows[0]
    assert row['response_queue_ms'] == 200
    assert row['audio'][0]['server_audio_ready_ms'] == 600
    assert row['audio'][0]['client_first_audio_ms'] == 1200
    assert row['audio'][1]['client_first_audio_ms'] == 1900
    assert trace.playback_started(2, 0, 'media_playing_event') is None


def test_cache_hits_do_not_teach_tts_speed():
    cached = Operation('tts', 0, finish=.001, status='completed',
                       voice_source='cache', audio_seconds=2)
    live = Operation('tts', 0, finish=1, status='completed',
                     voice_source='live', audio_seconds=2)
    assert cached.snapshot(1)['tts_service_rtf'] is None
    assert live.snapshot(1)['tts_service_rtf'] == .5


def test_executor_queue_and_service_time_are_measured_without_using_mock_latency():
    async def run():
        rows = []
        trace = ResponseTrace({}, rows.append)
        pool = ThreadPoolExecutor(max_workers=1)
        entered, release = threading.Event(), threading.Event()
        blocker = pool.submit(lambda: (entered.set(), release.wait(2)))
        assert entered.wait(1)
        class Backend:
            async def speak(self, *args):
                return await measured_worker(pool, lambda: Speech(b'\0\0'*16000,16000,999999,'live'))
        measured = MeasuredBackend(Backend(), trace)
        task = asyncio.create_task(measured.speak('x'))
        # Allow the coroutine to enqueue work behind the controlled blocker.
        while not trace.operations or not trace.operations[0].workers:
            await asyncio.sleep(0)
        worker = trace.operations[0].workers[0]
        assert worker.started is None
        release.set()
        await task
        trace.finish('completed')
        pool.shutdown()
        op = rows[0]['operations'][0]
        assert op['worker_queue_ms'] is not None
        assert op['worker_run_ms'] is not None
        assert op['wall_ms'] < 999999
        assert op['tts_service_rtf'] is not None
    asyncio.run(run())


def test_late_native_worker_cannot_rewrite_published_snapshot():
    rows = []
    trace = ResponseTrace({}, rows.append)
    op = Operation('tts', trace.requested, workers=[Worker(trace.requested)])
    trace.operations.append(op)
    trace.finish('interrupted')
    op.workers[0].started = trace.requested + 1
    op.workers[0].finished = trace.requested + 2
    op.status = 'completed'
    assert rows[0]['operations'][0]['worker_queue_ms'] is None
    assert rows[0]['operations'][0]['workers_unfinished'] == 1
    assert rows[0]['status'] == 'interrupted'


def measured_row(**overrides):
    return {'kind':'response_metrics', 'schema_version':1, 'profile':'cuda',
            'input_kind':'microphone', 'declared_model_state':'warm', 'cache_state':'miss',
            'status':'completed', 'language':'en', 'voice':'male',
            'audio':[{'role':'acknowledgement','client_first_audio_ms':200},
                     {'role':'answer','client_first_audio_ms':1500}], **overrides}


def test_report_keeps_filler_missing_interrupted_and_mock_out_of_answer_percentile():
    rows = [measured_row(), measured_row(status='interrupted', audio=[
                {'role':'answer','client_first_audio_ms':1}]),
            measured_row(audio=[]), measured_row(profile='mock'),
            measured_row(input_kind='typed'), measured_row(declared_model_state='cold')]
    report = summarize([{'events':rows}, {'events':[{'kind':'transcript','latency_ms':5}]}])
    assert report['legacy_calls_without_measurements'] == 1
    assert len(report['groups']) == 4
    group = next(g for g in report['groups'] if g['profile']=='cuda'
                 and g['input_kind']=='microphone' and g['declared_model_state']=='warm')
    assert group['answer_first_audio_ms'] == {'n':1,'p50':1500,'p95':1500}
    assert group['acknowledgement_first_audio_ms']['p50'] == 200
    assert group['missing_answer_playback'] == 1
    assert group['statuses'] == {'completed':2,'interrupted':1}


def test_legacy_transcripts_cannot_be_reported_as_audio_benchmarks():
    report = summarize([{'events':[{'kind':'transcript','latency_ms':9}]}])
    assert report['groups'] == []
    assert 'No response measurements found' in markdown(report)


def test_endpoint_policies_and_detectors_are_not_mixed_in_report():
    rows = [measured_row(speech_detector=detector, endpoint_policy=policy,
                        endpoint_target_ms=450, endpoint_reason='silence')
            for detector, policy in [('energy','balanced'), ('silero-v5','balanced'),
                                     ('silero-v5','patient')]]
    report = summarize([{'events':rows + [measured_row()]}])
    assert len(report['groups']) == 4
    neural = next(g for g in report['groups'] if g['speech_detector']=='silero-v5'
                  and g['endpoint_policy']=='balanced')
    assert neural['endpoint_target_ms'] == {'n':1,'p50':450,'p95':450}
    assert neural['endpoint_reasons'] == {'silence':1}
    assert 'silero-v5' in markdown(report)
