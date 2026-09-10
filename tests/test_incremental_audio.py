import asyncio
import threading

import pytest

from voicebot.playout import PlayoutWindow
from voicebot.runtime.streaming import stream_worker
from voicebot.runtime.workers import BoundedExecutor
from voicebot.runtime.base import SpeechChunk
from voicebot.telemetry import MeasuredBackend, ResponseTrace


def test_first_audio_arrives_before_later_generation_finishes():
    async def run():
        pool = BoundedExecutor(1, 1)
        release = threading.Event()
        done = threading.Event()
        def produce():
            try:
                yield b'first'
                assert release.wait(3)
                yield b'second'
            finally:
                done.set()
        stream = stream_worker(pool, produce)
        try:
            assert await asyncio.wait_for(anext(stream), 1) == b'first'
            assert not done.is_set()
            release.set()
            assert await anext(stream) == b'second'
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
        finally:
            release.set(); await stream.aclose(); pool.shutdown()
    asyncio.run(run())


def test_slow_consumer_bounds_production_and_cancel_closes_on_owner():
    async def run():
        pool = BoundedExecutor(1, 1)
        produced = []
        owner = []
        def produce():
            owner.append(threading.get_ident())
            try:
                for i in range(10000):
                    produced.append(i)
                    yield b'\0\0'*320
            finally:
                owner.append(threading.get_ident())
        stream = stream_worker(pool, produce, capacity=2)
        try:
            await anext(stream)
            await asyncio.sleep(.06)  # allow the producer to encounter backpressure
            assert len(produced) <= 4  # consumed one, queued two, one held by producer
            await stream.aclose()
        finally:
            pool.shutdown()
        assert len(owner) == 2 and owner[0] == owner[1] != threading.get_ident()
    asyncio.run(run())


def test_producer_failure_propagates_after_earlier_audio():
    async def run():
        pool = BoundedExecutor(1, 1)
        def produce():
            yield b'first'
            raise RuntimeError('synthesis failed')
        stream = stream_worker(pool, produce)
        try:
            assert await anext(stream) == b'first'
            with pytest.raises(RuntimeError, match='synthesis failed'):
                await anext(stream)
        finally:
            await stream.aclose(); pool.shutdown()
    asyncio.run(run())


def test_playout_requires_valid_consumption_before_sending_more():
    async def run():
        w = PlayoutWindow(16000)
        await w.reserve(32000)
        pending = asyncio.create_task(w.reserve(320))
        await asyncio.sleep(0)
        assert not pending.done()
        for n in (None, True, -1, 0, 32001, 1.5):
            assert not w.acknowledge(n)
        assert w.acknowledge(320)
        await asyncio.wait_for(pending, 1)
        assert w.sent == 32320
        assert not w.acknowledge(320)
    asyncio.run(run())


def test_stream_metrics_measure_first_chunk_and_do_not_claim_service_rtf():
    class Backend:
        async def stream_speak(self):
            yield SpeechChunk(b'\0\0'*320,16000)
            yield SpeechChunk(b'',16000,final=True)
    async def run():
        rows=[]
        trace=ResponseTrace({},rows.append)
        measured=MeasuredBackend(Backend(),trace)
        chunks=[c async for c in measured.stream_speak()]
        trace.finish('completed')
        assert chunks[-1].final
        op=rows[0]['operations'][0]
        assert op['first_chunk_ms'] is not None and op['audio_seconds'] == .02
        assert op['tts_service_rtf'] is None
    asyncio.run(run())
