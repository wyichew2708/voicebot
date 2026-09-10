"""Bound native work, including jobs whose asyncio caller has gone away."""
from concurrent.futures import Future, ThreadPoolExecutor
from threading import BoundedSemaphore


class InferenceBusy(RuntimeError):
    """The backend has no capacity for another job; retry a later turn."""


class BoundedExecutor(ThreadPoolExecutor):
    def __init__(self, max_workers=1, max_pending=2, *, thread_name_prefix="voice"):
        if isinstance(max_pending, bool) or not isinstance(max_pending, int) or max_pending < 0:
            raise ValueError("max_pending must be a nonnegative integer")
        super().__init__(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
        self._slots = BoundedSemaphore(max_workers + max_pending)

    def submit(self, fn, /, *args, **kwargs):
        if not self._slots.acquire(blocking=False):
            raise InferenceBusy("Voice inference capacity is full; please try again shortly")
        result = Future()

        def run():
            # Keep cancelled queue entries admitted until the worker drains
            # them, preventing cancellation storms from growing the queue.
            if not result.set_running_or_notify_cancel():
                return
            try:
                value = fn(*args, **kwargs)
            except BaseException as exc:
                result.set_exception(exc)
            else:
                result.set_result(value)

        try:
            future = super().submit(run)
        except BaseException:
            self._slots.release()
            raise
        def finished(native):
            if native.cancelled():
                result.cancel()  # shutdown cancelled a job before dequeue
            self._slots.release()

        future.add_done_callback(finished)
        return result
