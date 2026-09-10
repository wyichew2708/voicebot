"""Interruptible response ownership and bounded, generation-tagged output.

Only the writer touches the socket. Only one response may mutate dialogue
state, even if a backend takes time to acknowledge cancellation. Cancelling
an executor await does not stop its native worker: late results are discarded,
and backend worker capacity is still owned by that backend.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class Job:
    generation: int
    run: Callable[[int], Awaitable[None]]
    interrupted: Callable[[], None]
    cancelled: bool = False


class RealtimeTransport:
    def __init__(self, send: Callable[[dict | bytes], Awaitable[None]], *,
                 queue_size: int = 32, send_timeout: float = 5.0):
        self.generation = 0
        self._send = send
        self._send_timeout = send_timeout
        self._out: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self._wake = asyncio.Event()
        self._pending: Job | None = None
        self._job: Job | None = None
        self._active: asyncio.Task | None = None
        self._closed = False
        self._runner = asyncio.create_task(self._run(), name="voice-response-owner")
        self._writer = asyncio.create_task(self._write(), name="voice-socket-writer")

    async def send(self, payload: dict | bytes, generation: int) -> None:
        if not self._closed and generation == self.generation:
            await self._out.put((generation, payload))

    def notify(self, payload: dict) -> None:
        """Best-effort UI status; never block mic reception behind audio."""
        if not self._closed and not self._out.full():
            self._out.put_nowait((self.generation, payload))

    def invalidate(self, reason: str, client_turn: int = 0) -> int:
        """No inference await here: the reader must continue taking mic frames."""
        self.generation += 1
        self._pending = None
        if self._job is not None:
            self._job.cancelled = True
        if self._active is not None and not self._active.done():
            # Cancel once. Repeated cancellation can interrupt cleanup itself.
            if not self._active.cancelling():
                self._active.cancel()
        while not self._out.empty():
            self._out.get_nowait()
        self._out.put_nowait((self.generation, {
            "kind": "audio_cancel", "generation": self.generation,
            "reason": reason,
            "client_turn": client_turn,
        }))
        return self.generation

    def submit(self, run: Callable[[int], Awaitable[None]],
               interrupted: Callable[[], None], *, reason: str = "caller",
               client_turn: int = 0) -> int:
        generation = self.invalidate(reason, client_turn)
        # Latest input wins; do not accumulate utterances behind a slow model.
        self._pending = Job(generation, run, interrupted)
        self._wake.set()
        return generation

    async def _run(self) -> None:
        while not self._closed:
            await self._wake.wait()
            self._wake.clear()
            if self._closed:
                return
            job, self._pending = self._pending, None
            if job is None or job.generation != self.generation:
                continue
            self._job = job
            self._active = asyncio.create_task(job.run(job.generation))
            try:
                await self._active
            except asyncio.CancelledError:
                if self._closed:
                    raise
            except Exception:
                # Fail the connection, never leave a caller on a broken session.
                raise
            finally:
                if job.cancelled:
                    job.interrupted()
                self._active = None
                self._job = None

    async def _write(self) -> None:
        while True:
            generation, payload = await self._out.get()
            if generation == self.generation:
                # A stalled client must not retain the call indefinitely.
                await asyncio.wait_for(self._send(payload), self._send_timeout)

    async def receive(self, receive: Callable[[], Awaitable[dict]]) -> dict:
        """Also observe response/writer failure while waiting for input."""
        incoming = asyncio.create_task(receive())
        try:
            done, _ = await asyncio.wait(
                (incoming, self._runner, self._writer),
                return_when=asyncio.FIRST_COMPLETED)
            for task in (self._runner, self._writer):
                if task in done:
                    await task
                    raise RuntimeError("voice transport stopped")
            return await incoming
        finally:
            if not incoming.done():
                incoming.cancel()
            with suppress(asyncio.CancelledError):
                await incoming

    async def close(self) -> None:
        self._closed = True
        self._pending = None
        if self._active is not None and not self._active.done():
            if not self._active.cancelling():
                self._active.cancel()
        self._wake.set()
        self._writer.cancel()
        await asyncio.gather(self._runner, self._writer, return_exceptions=True)
