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
    #: Which model actually spoke. "cache" and "rendered" are the same voice;
    #: "live" is a different speaker entirely and must never happen twice in
    #: one call without the operator being told.
    voice_source: str = "cache"


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
