import asyncio
from concurrent.futures import CancelledError
import threading

import pytest

from voicebot.runtime.workers import BoundedExecutor, InferenceBusy
from voicebot.telemetry import measured_worker


def test_cancel_storm_cannot_expand_the_native_queue():
    pool = BoundedExecutor(1, 1)
    entered, release = threading.Event(), threading.Event()
    seen = []
    try:
        first = pool.submit(lambda: (entered.set(), release.wait(3)))
        assert entered.wait(1)
        queued = pool.submit(lambda: seen.append('obsolete'))
        assert queued.cancel()
        for _ in range(100):
            with pytest.raises(InferenceBusy):
                pool.submit(lambda: seen.append('overflow'))
        release.set()
        first.result(2)
    finally:
        release.set()
        pool.shutdown(wait=True)
    assert queued.cancelled()
    assert seen == []


def test_cancelled_async_caller_does_not_free_running_capacity():
    async def run():
        pool = BoundedExecutor(1, 0)
        entered, release = threading.Event(), threading.Event()
        try:
            task = asyncio.create_task(measured_worker(pool, lambda: (entered.set(), release.wait(3))))
            while not entered.is_set():
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(InferenceBusy):
                await measured_worker(pool, lambda: None)
        finally:
            release.set()
            pool.shutdown(wait=True)
    asyncio.run(run())


def test_failures_release_capacity_and_shutdown_cancels_queued_jobs():
    pool = BoundedExecutor(1, 1)
    entered, release = threading.Event(), threading.Event()
    first = pool.submit(lambda: (entered.set(), release.wait(3)))
    assert entered.wait(1)
    queued = pool.submit(lambda: 42)
    pool.shutdown(wait=False, cancel_futures=True)
    release.set()
    first.result(2)
    with pytest.raises(CancelledError):
        queued.result(1)
    with pytest.raises(RuntimeError):
        pool.submit(lambda: None)

    pool = BoundedExecutor(1, 1)
    try:
        with pytest.raises(ZeroDivisionError):
            pool.submit(lambda: 1 / 0).result(1)
        assert pool.submit(lambda: 42).result(1) == 42
    finally:
        pool.shutdown()


@pytest.mark.parametrize('pending', [-1, True, 1.5])
def test_invalid_capacity_is_refused(pending):
    with pytest.raises(ValueError):
        BoundedExecutor(1, pending)
