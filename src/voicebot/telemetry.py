"""Per-response measurements. Client and server monotonic clocks never mix."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
import math
import time
from typing import Callable


def milliseconds(value):
    """Accept bounded finite client durations; missing is not zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 3) if math.isfinite(value) and 0 <= value <= 600_000 else None


@dataclass
class Worker:
    queued: float
    started: float | None = None
    finished: float | None = None


@dataclass
class Operation:
    stage: str
    start: float
    finish: float | None = None
    status: str = 'running'
    workers: list[Worker] = field(default_factory=list)
    voice_source: str | None = None
    audio_seconds: float | None = None
    first_chunk_ms: float | None = None

    def snapshot(self, now):
        elapsed = ((self.finish if self.finish is not None else now) - self.start) * 1000
        queues = [(w.started - w.queued) * 1000 for w in self.workers if w.started is not None]
        runs = [(w.finished - w.started) * 1000 for w in self.workers
                if w.started is not None and w.finished is not None]
        return {
            'stage': self.stage, 'status': self.status,
            'wall_ms': round(max(0, elapsed), 3),
            'worker_queue_ms': round(sum(queues), 3) if queues else None,
            # CUDA workers include HTTP/service wait; this is not GPU kernel time.
            'worker_run_ms': round(sum(runs), 3) if runs else None,
            'workers_unfinished': sum(w.finished is None for w in self.workers),
            'voice_source': self.voice_source, 'audio_seconds': self.audio_seconds,
            'first_chunk_ms': self.first_chunk_ms,
            'tts_service_rtf': (round(elapsed / 1000 / self.audio_seconds, 4)
                if self.stage == 'tts' and self.status == 'completed'
                and self.voice_source in ('rendered', 'live') and self.audio_seconds else None),
        }


_operation: ContextVar[Operation | None] = ContextVar('voice_operation', default=None)


async def measured_worker(pool, fn, *args):
    """Measure the actual executor wait, without changing worker ownership."""
    op = _operation.get()
    worker = Worker(time.perf_counter()) if op is not None else None
    if op is not None:
        op.workers.append(worker)

    def run():
        if worker is not None:
            worker.started = time.perf_counter()
        try:
            return fn(*args)
        finally:
            if worker is not None:
                worker.finished = time.perf_counter()

    return await asyncio.get_running_loop().run_in_executor(pool, run)


class ResponseTrace:
    def __init__(self, metadata: dict, publish: Callable[[dict], None], *,
                 clock=time.perf_counter):
        self.metadata = metadata
        self.publish = publish
        self.clock = clock
        self.requested = clock()
        self.started = None
        self.operations: list[Operation] = []
        self.audio: dict[int, dict] = {}
        self.closed = False

    def start(self):
        self.started = self.clock()

    def audio_ready(self, audio_id, role, language):
        if not self.closed:
            previous = {a["language"] for a in self.audio.values()}
            self.metadata["language"] = "mixed" if previous - {language} else language
            self.audio[audio_id] = {
                'audio_id': audio_id, 'role': role, 'language': language,
                'server_audio_ready_ms': round((self.clock() - self.requested) * 1000, 3),
                'client_first_audio_ms': None, 'playback_method': None,
            }

    def playback_started(self, audio_id, duration_ms, method):
        row = self.audio.get(audio_id)
        duration = milliseconds(duration_ms)
        if (self.closed or row is None or duration is None
                or row['client_first_audio_ms'] is not None or method not in (
                    'webaudio_output_timestamp_estimate', 'webaudio_schedule_estimate',
                    'media_playing_event')):
            return None
        row.update(client_first_audio_ms=duration, playback_method=method)
        return dict(row)

    def finish(self, status):
        if self.closed:
            return
        self.closed = True
        now = self.clock()
        operations = [op.snapshot(now) for op in self.operations]
        sources = {op['voice_source'] for op in operations if op['stage'] == 'tts'
                   and op['voice_source'] is not None}
        cache = ('none' if not sources else 'hit' if sources == {'cache'} else
                 'mixed' if 'cache' in sources else 'miss')
        # Lists/dicts are copied: an abandoned native worker finishing later
        # cannot rewrite an already-published measurement.
        self.publish({
            'kind': 'response_metrics', 'schema_version': 1, **self.metadata,
            'status': status, 'cache_state': cache,
            'response_queue_ms': (round((self.started - self.requested) * 1000, 3)
                                  if self.started is not None else None),
            'server_response_ms': round((now - self.requested) * 1000, 3),
            'operations': operations, 'audio': [dict(a) for a in self.audio.values()],
        })


class MeasuredBackend:
    """A session-scoped decorator; no model, prompt or audio is duplicated."""
    def __init__(self, backend, trace: ResponseTrace):
        self.backend, self.trace = backend, trace

    def __getattr__(self, name):
        return getattr(self.backend, name)

    async def _call(self, stage, method, *args, **kwargs):
        op = Operation(stage, self.trace.clock())
        self.trace.operations.append(op)
        token = _operation.set(op)
        try:
            result = await method(*args, **kwargs)
            op.status = 'completed'
            if stage == 'tts':
                op.voice_source = result.voice_source
                op.audio_seconds = len(result.pcm) / 2 / result.sample_rate
            return result
        except asyncio.CancelledError:
            op.status = 'cancelled'
            raise
        except Exception:
            op.status = 'error'
            raise
        finally:
            op.finish = self.trace.clock()
            _operation.reset(token)

    async def transcribe(self, *args, **kwargs):
        return await self._call('asr', self.backend.transcribe, *args, **kwargs)

    async def complete(self, *args, **kwargs):
        return await self._call('llm', self.backend.complete, *args, **kwargs)

    async def speak(self, *args, **kwargs):
        return await self._call('tts', self.backend.speak, *args, **kwargs)

    async def stream_speak(self, *args, **kwargs):
        op = Operation('tts', self.trace.clock(), voice_source='stream', audio_seconds=0)
        self.trace.operations.append(op)
        token = _operation.set(op)
        stream = self.backend.stream_speak(*args, **kwargs)
        try:
            async for chunk in stream:
                if chunk.pcm and op.first_chunk_ms is None:
                    op.first_chunk_ms = round((self.trace.clock() - op.start) * 1000, 3)
                op.audio_seconds += len(chunk.pcm) / 2 / chunk.sample_rate
                yield chunk
            op.status = 'completed'
        except (asyncio.CancelledError, GeneratorExit):
            op.status = 'cancelled'
            raise
        except Exception:
            op.status = 'error'
            raise
        finally:
            await stream.aclose()
            op.finish = self.trace.clock()
            _operation.reset(token)
