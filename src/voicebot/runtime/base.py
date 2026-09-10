"""The only platform-aware seam in the product.

Everything above this — the call state machine, the gates, the script, the
console — is identical on Apple Silicon and on the RHEL GPU box. Only model
loading and inference differ.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable


@dataclass
class TranscriptResult:
    text: str
    lang: str
    latency_ms: int


@dataclass
class Completion:
    text: str
    latency_ms: int


@dataclass
class Speech:
    """One agent turn's audio, plus how long it took to produce."""
    pcm: bytes                 # 16-bit little-endian mono
    sample_rate: int
    latency_ms: int
    #: Delivery path: cache read, local render, or live synthesis. Live speech
    #: can use the same voice; voice_consistent declares that backend contract.
    voice_source: str = "cache"
    voice_consistent: bool = False  # live synthesis explicitly uses the cache voice


@dataclass
class SpeechChunk:
    pcm: bytes
    sample_rate: int
    final: bool = False
    voice_source: str = "live"


@dataclass
class BackendHealth:
    profile: str
    asr: str
    llm: str
    tts: str
    ready: bool
    detail: str = ""


@runtime_checkable
class Backend(Protocol):
    """Response methods must propagate asyncio cancellation after cleanup.

    A cancelled coroutine must not write call state or emit events later.
    Executor/native inference may finish internally; the transport fences its
    result and the backend retains ownership of its worker until it returns.
    Cancelling an await is not a guarantee of native GPU/HTTP request abort.
    """
    streaming_tts: bool = False

    async def stream_speak(self, text: str, lang: str, voice: str | None = None):
        """Buffered fallback. Only streaming_tts=True promises early segments."""
        speech = await self.speak(text, lang, prerendered=True, voice=voice)
        yield SpeechChunk(speech.pcm, speech.sample_rate, final=True,
                          voice_source=speech.voice_source)

    async def transcribe(self, pcm: bytes, sample_rate: int) -> TranscriptResult: ...

    async def complete(self, system: str, user: str, lang: str,
                       max_tokens: int | None = None) -> Completion:
        """Generation.

        Used for exactly one thing: choosing which handler an unrecognised
        reply belongs to. Nothing it returns is spoken — see `call.router`.
        `max_tokens` is small for that job, and a caller is waiting on it.
        """
        ...

    async def speak(self, text: str, lang: str, prerendered: bool,
                    voice: str | None = None) -> Speech:
        """Produce the audio for one agent turn.

        `prerendered` marks a scripted line whose audio was rendered at build
        time. Those must not touch the LLM or the synthesiser at call time —
        that shortcut is the whole reason most of this call is fast.
        """
        ...

    def synthesize(self, text: str, lang: str) -> AsyncIterator[bytes]: ...
    def health(self) -> BackendHealth: ...
