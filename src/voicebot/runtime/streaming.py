"""Bound a synchronous model iterator without moving its state across threads."""
import asyncio
import queue
import threading

from ..telemetry import measured_worker


async def stream_worker(pool, produce, *, capacity=8):
    """Yield bytes as produced; cancellation closes the iterator on its owner.

    The producer must bound each chunk. Native inference already in progress
    cannot be preempted, but a producer blocked by backpressure can stop.
    """
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
        raise ValueError("stream capacity must be a positive integer")
    chunks = queue.Queue(maxsize=capacity)
    stopped = threading.Event()
    ready = asyncio.Event()
    loop = asyncio.get_running_loop()

    def wake():
        if not loop.is_closed():
            try:
                loop.call_soon_threadsafe(ready.set)
            except RuntimeError:
                pass  # disconnect closed the event loop

    def run():
        iterator = iter(produce())
        try:
            while not stopped.is_set():
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                while not stopped.is_set():
                    try:
                        chunks.put_nowait(chunk)
                        wake()
                        break
                    except queue.Full:
                        stopped.wait(.02)
        finally:
            close = getattr(iterator, 'close', None)
            if close:
                close()

    task = asyncio.create_task(measured_worker(pool, run))
    task.add_done_callback(lambda _: ready.set())
    try:
        while True:
            ready.clear()
            try:
                yield chunks.get_nowait()
            except queue.Empty:
                if task.done():
                    await task  # propagate failure; never emit false EOF
                    break
                await ready.wait()
    finally:
        stopped.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
