"""CUDA / RHEL backend.

Unlike the Mac path this loads no models in-process. Each capability is an HTTP
service — which is how vLLM is meant to be run, is how the reserved Qwen3.6
already runs, and is what MERaLiON's own deployment guide recommends. The
process here stays small and restartable while the GPU work lives behind
stable endpoints.

    ASR  ->  vLLM,  OpenAI-compatible  /v1/audio/transcriptions
    LLM  ->  vLLM,  OpenAI-compatible  /v1/chat/completions
    TTS  ->  a small sidecar,          POST /tts -> audio/wav

Scripted turns do not reach the TTS service at all: they are served from the
pre-rendered cache, whose keys are platform-independent, so the wavs rendered
on a Mac are reused here byte-for-byte.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import time
import urllib.error
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncIterator

from ..lang import detect as detect_lang
from .base import Backend, BackendHealth, Completion, Speech, TranscriptResult

log = logging.getLogger("voicebot.cuda")

TIMEOUT = 30


class CUDABackend(Backend):
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.sample_rate = cfg.get("sample_rate", 16000)
        self._errors: list[str] = []
        # Requests are blocking urllib calls; a small pool keeps them off the
        # event loop without pulling in another dependency.
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cuda-http")
        from .prerender import PrerenderCache
        self.prerender = PrerenderCache(cfg.get("tts", {}).get("prerender", {}),
                                        self.sample_rate)

    # ------------------------------------------------------------- helpers

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    @staticmethod
    def _post(url: str, data: bytes, content_type: str,
              headers: dict[str, str] | None = None) -> bytes:
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": content_type,
                                              **(headers or {})})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()

    def _wav(self, pcm: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(pcm)
        return buf.getvalue()

    # ----------------------------------------------------------------- asr

    async def transcribe(self, pcm: bytes, sample_rate: int) -> TranscriptResult:
        asr = self.cfg.get("asr", {})
        url = asr.get("base_url", "").rstrip("/") + "/v1/audio/transcriptions"
        model = asr.get("model", "")
        t0 = time.perf_counter()

        def _work() -> str:
            # multipart/form-data by hand: one small dependency avoided.
            boundary = "----voicebot-asr-boundary"
            parts = [
                f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n'
                f'{model}\r\n'.encode(),
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
                self._wav(pcm),
                f'\r\n--{boundary}--\r\n'.encode(),
            ]
            body = b"".join(parts)
            raw = self._post(url, body, f"multipart/form-data; boundary={boundary}")
            return json.loads(raw).get("text", "").strip()

        try:
            text = await self._run(_work)
        except Exception as exc:                        # pragma: no cover
            log.warning("ASR request failed: %s", exc)
            self._errors.append(f"asr: {exc}")
            return TranscriptResult(text="", lang="en", latency_ms=0)
        return TranscriptResult(text=text, lang=detect_lang(text),
                                latency_ms=int((time.perf_counter() - t0) * 1000))

    # ----------------------------------------------------------------- llm

    async def complete(self, system: str, user: str, lang: str,
                       max_tokens: int | None = None) -> Completion:
        llm = self.cfg.get("llm", {})
        url = llm.get("base_url", "").rstrip("/") + "/v1/chat/completions"
        t0 = time.perf_counter()
        payload = json.dumps({
            "model": llm.get("model", ""),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens or llm.get("max_tokens", 220),
            "temperature": llm.get("temperature", 0.3),
        }).encode()

        def _work() -> str:
            raw = self._post(url, payload, "application/json")
            data = json.loads(raw)
            return data["choices"][0]["message"]["content"].strip()

        try:
            text = await self._run(_work)
        except Exception as exc:                        # pragma: no cover
            log.warning("LLM request failed: %s", exc)
            return Completion(text="", latency_ms=0)
        return Completion(text=text, latency_ms=int((time.perf_counter() - t0) * 1000))

    # ----------------------------------------------------------------- tts

    def cached(self, text: str, lang: str, voice: str | None = None) -> bool:
        """Whether this exact line is already rendered to disk.

        Same cache and same keys as the Mac — the two profiles are checked
        against each other precisely so a line rendered on one is a hit on the
        other. It matters more here: a miss on this box does not render, it
        drops to the live voice, which is a different speaker mid-call.
        """
        try:
            return self.prerender.path(text, lang, voice).exists()
        except Exception:                   # pragma: no cover - never fatal
            return False

    async def speak(self, text: str, lang: str, prerendered: bool,
                    voice: str | None = None) -> Speech:
        t0 = time.perf_counter()
        if prerendered:
            cached = await self._run(self.prerender.get, text, lang, voice)
            if cached is not None:
                return Speech(voice_source="cache", pcm=cached, sample_rate=self.sample_rate,
                              latency_ms=int((time.perf_counter() - t0) * 1000))
            # No render-on-miss here. On a Mac a miss costs a slow line; on a
            # call server it would stall the turn behind a model load. Warm the
            # cache with `make prerender` and ship it — misses fall through to
            # the live voice instead.
            log.warning("pre-render miss on the server for %r — using live TTS",
                        text[:48])

        chunks = [c async for c in self.synthesize(text, lang, voice)]
        return Speech(voice_source="live", pcm=b"".join(chunks), sample_rate=self.sample_rate,
                      latency_ms=int((time.perf_counter() - t0) * 1000))

    def _speak_one(self, piece: str, lang_code: str, voice: str,
                   ref: str | None = None) -> bytes:
        """One fragment, one language, from the sidecar.

        The reference clip travels with the request. The sidecar used to keep
        a two-entry table of its own, so every other voice — and every
        Mandarin line, which clones a different clip from the English one —
        fell back to the model's default speaker without a word of complaint.
        """
        url = self.cfg.get("tts", {}).get("base_url", "").rstrip("/") + "/tts"
        body: dict[str, Any] = {"text": piece, "lang": lang_code, "voice": voice,
                                "sample_rate": self.sample_rate}
        if ref:
            body["ref_audio"] = ref
        raw = self._post(url, json.dumps(body).encode(), "application/json")
        with wave.open(io.BytesIO(raw)) as w:
            if w.getframerate() != self.sample_rate:
                log.warning("TTS returned %d Hz, expected %d",
                            w.getframerate(), self.sample_rate)
            return w.readframes(w.getnframes())

    async def synthesize(self, text: str, lang: str,
                         voice: str | None = None) -> AsyncIterator[bytes]:
        """The same treatment a cached line gets, on the improvised path.

        Everything here mirrors `PrerenderCache.render`, and deliberately by
        calling the same code rather than repeating it: a live line that is
        segmented differently, or left off the voice's own pitch, is heard as
        the speaker changing halfway through the call.
        """
        from .. import pcm as P
        from ..spoken import segment_by_script

        voice = voice or self.prerender.default_voice()
        pieces = segment_by_script(text, lang)
        # The line's language picks the speaker, not the piece's: an address
        # inside a Mandarin sentence is read by the Mandarin speaker, as it is
        # in the cache, rather than by a second voice at the seam.
        ref = self.prerender.reference_for(voice, lang)

        def _work() -> bytes:
            out = bytearray()
            for i, (piece, piece_lang) in enumerate(pieces):
                part = self._speak_one(piece, self.prerender.lang_code(piece_lang),
                                       voice, ref)
                if len(pieces) > 1:
                    part = P.trim(part, head=(i > 0), tail=(i < len(pieces) - 1),
                                  keep_ms=10, sample_rate=self.sample_rate)
                    if i:
                        out += P.silence(40, self.sample_rate)
                out += part
            return self.prerender.normalise_pitch(bytes(out), voice, lang)

        try:
            pcm = await self._run(_work)
        except Exception as exc:                        # pragma: no cover
            log.warning("TTS request failed: %s", exc)
            return
        frame = int(self.sample_rate * 0.02) * 2        # 20 ms of int16
        for i in range(0, len(pcm), frame):
            yield pcm[i:i + frame]

    # -------------------------------------------------------------- health

    def health(self) -> BackendHealth:
        """Probe each service, and check it is the *right* service.

        Liveness alone is not enough: an unrelated process on the port answers
        with a 404, which would otherwise read as ready and let calls start
        against nothing. vLLM exposes the loaded model at /v1/models, so
        confirm the configured model is actually there.
        """
        def probe(url: str, expect_model: str | None = None) -> bool:
            if not url:
                return False
            base = url.rstrip("/")
            if expect_model:
                try:
                    with urllib.request.urlopen(base + "/v1/models", timeout=3) as r:
                        served = {m.get("id", "") for m in json.loads(r.read()).get("data", [])}
                except Exception:
                    return False
                if not served:
                    return False
                # vLLM may report a path or a repo id; match on the tail.
                tail = expect_model.split("/")[-1]
                return any(tail in s for s in served)
            try:
                with urllib.request.urlopen(base + "/health", timeout=3) as r:
                    return 200 <= r.status < 300
            except Exception:
                return False

        asr_cfg = self.cfg.get("asr", {})
        llm_cfg = self.cfg.get("llm", {})
        asr_up = probe(asr_cfg.get("base_url", ""), asr_cfg.get("model"))
        llm_up = probe(llm_cfg.get("base_url", ""), llm_cfg.get("model"))
        tts_up = probe(self.cfg.get("tts", {}).get("base_url", ""))
        detail = ", ".join(n for n, ok in
                           (("asr", asr_up), ("llm", llm_up), ("tts", tts_up)) if not ok)
        return BackendHealth(
            profile="cuda",
            asr=self.cfg.get("asr", {}).get("model", "?").split("/")[-1],
            llm=self.cfg.get("llm", {}).get("model", "?").split("/")[-1],
            tts=(self.cfg.get("tts", {}).get("model", "?").split("/")[-1]
                 + " + prerender cache"),
            # The cache covers the scripted turns, so ASR is the only hard
            # dependency for a call to start.
            ready=asr_up,
            detail=("unreachable: " + detail) if detail else "",
        )

    def close(self) -> None:
        self._pool.shutdown(wait=False)
