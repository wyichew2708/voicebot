"""Model-free backend.

Runs the entire product — state machine, gates, console, event stream — with
no models installed. This is the development default and it is also what the
console's replay mode uses on demo day if the real stack misbehaves.
"""
from __future__ import annotations

import asyncio
import random
from typing import AsyncIterator

from .base import Backend, BackendHealth, Completion, Speech, TranscriptResult


class MockBackend(Backend):
    def __init__(self, latency_ms: tuple[int, int] = (520, 760)) -> None:
        self._lo, self._hi = latency_ms
        self._rng = random.Random(7)   # deterministic: demos should repeat
        # The model switch works here too — listed, selectable, silent — so
        # the console can be exercised without a single model installed.
        from ..tts_models import MockLab
        self.lab = MockLab(self)

    def _lat(self) -> int:
        return self._rng.randint(self._lo, self._hi)

    async def transcribe(self, pcm: bytes, sample_rate: int) -> TranscriptResult:
        await asyncio.sleep(0.05)
        return TranscriptResult(text="", lang="en", latency_ms=self._lat())

    async def complete(self, system: str, user: str, lang: str,
                       max_tokens: int | None = None) -> Completion:
        lat = self._lat()
        await asyncio.sleep(lat / 1000)
        if lang == "zh":
            text = "好的，我明白了。我会把详情发到您的邮箱。"
        else:
            text = ("Let me check that for you. I'll send the details across by email "
                    "so you can confirm against your own record.")
        return Completion(text=text, latency_ms=lat)

    async def speak(self, text: str, lang: str, prerendered: bool,
                    voice: str | None = None) -> Speech:
        # A scripted line is a disk read and a playout buffer; a generated line
        # pays for synthesis too. Simulating both at the same cost would flatter
        # the pre-rendering optimisation the design depends on.
        lo, hi = (90, 170) if prerendered else (self._lo, self._hi)
        lat = self._rng.randint(lo, hi)
        await asyncio.sleep(lat / 1000)
        # Silence roughly the length the line would take to say, so the client's
        # playback and barge-in paths get exercised without any models.
        seconds = max(0.6, len(text) / 15)
        return Speech(pcm=b"\x00\x00" * int(16000 * seconds),
                      sample_rate=16000, latency_ms=lat)

    async def synthesize(self, text: str, lang: str) -> AsyncIterator[bytes]:
        # Silence at 16 kHz, chunked like a real streaming synthesiser would be.
        for _ in range(max(1, len(text) // 40)):
            await asyncio.sleep(0.02)
            yield b"\x00" * 640

    def health(self) -> BackendHealth:
        return BackendHealth(profile="mock", asr="mock", llm="mock", tts="mock",
                             ready=True, detail="No models loaded")
