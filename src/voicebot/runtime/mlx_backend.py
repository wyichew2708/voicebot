"""Apple Silicon backend.

Imports are deliberately lazy: the package must remain importable (and the
mock profile fully usable) on a machine with no MLX installed.

Install the inference extras separately:
    uv pip install -e ".[mlx]"
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

from ..telemetry import measured_worker
from ..lang import detect as detect_lang
from .base import Backend, BackendHealth, Completion, Speech, SpeechChunk, TranscriptResult
from .streaming import stream_worker
from .workers import BoundedExecutor, InferenceBusy

SYSTEM_MAX_TOKENS = 220


class MLXBackend(Backend):
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        # MLX streams are bound to the thread that created them: hopping
        # between pool threads raises "There is no Stream(gpu, N) in current
        # thread". One dedicated worker keeps every array on one thread, and
        # each call returns plain bytes so nothing MLX-owned escapes it.
        pending = cfg.get("inference", {}).get("max_pending", 2)
        self._pool = BoundedExecutor(1, pending, thread_name_prefix="mlx")
        self._io = BoundedExecutor(2, 8, thread_name_prefix="voice-cache")
        self._http = BoundedExecutor(1, pending, thread_name_prefix="asr-http")
        from .prerender import PrerenderCache
        self.prerender = PrerenderCache(cfg.get("tts", {}).get("prerender", {}),
                                        cfg.get("sample_rate", 16000))
        # Candidate models, switchable at runtime. Shares the worker thread
        # and the voices' clips; see tts_models.py.
        from ..tts_models import MLXLab
        self.lab = MLXLab(self.prerender, cfg.get("sample_rate", 16000), self._run)
        self._llm: Any = None
        self._llm_tok: Any = None
        self._asr: Any = None
        self._tts: Any = None
        self._error: str = ""
        self._cached_voice = bool(cfg.get("tts", {}).get("prerender", {}).get("model"))
        # Kokoro's generator yields completed segments incrementally. Cloning
        # and utterance-wide pitch/rate correction retain their buffered path.
        self.streaming_tts = (not self._cached_voice and
                              "kokoro" in cfg.get("tts", {}).get("model", "").lower())

    # ------------------------------------------------------------ loading

    def load(self) -> None:
        """Create and use all MLX models on the same owned thread."""
        self._pool.submit(self._load).result()

    def _load(self) -> None:
        errors: list[str] = []
        try:
            import mlx_audio.stt.utils as stt          # type: ignore
            self._asr = stt.load(self.cfg["asr"]["model"])
            self._attach_processor()
        except Exception as exc:                       # pragma: no cover
            errors.append(f"ASR: {exc}")
        try:
            if self._cached_voice:
                # One resident voice model for both cached and novel lines.
                # Do not also load the former Kokoro fallback.
                self._tts = self.prerender._load_model()
                if self._tts is None:
                    raise RuntimeError("selected cache voice model is unavailable")
            else:
                import mlx_audio.tts.utils as tts          # type: ignore
                self._tts = tts.load(self.cfg["tts"]["model"])
        except Exception as exc:                       # pragma: no cover
            errors.append(f"TTS: {exc}")
        self._error = " · ".join(errors)
        if self.cfg.get("preload_llm", True):
            self._ensure_llm()

    def _attach_processor(self) -> None:
        """Give Whisper its tokenizer if the weights repo does not ship one.

        Several mlx-community Whisper repos contain only config.json and
        weights.safetensors. mlx-audio's post-load hook tries to build a
        WhisperProcessor from that directory, fails, and warns — then the first
        transcription dies with "Processor not found", a long way from the
        cause. Load it from the upstream repo instead; it is a few MB of
        tokenizer and feature-extractor config, no weights.
        """
        if getattr(self._asr, "_processor", None) is not None:
            return
        repo = self.cfg["asr"].get("processor")
        if not repo:
            return
        from transformers import WhisperProcessor      # type: ignore

        self._asr._processor = WhisperProcessor.from_pretrained(repo)

    def _ensure_llm(self) -> None:
        """Startup-only LLM loading, on the owned MLX worker."""
        if self._llm is not None or "LLM:" in self._error:
            return
        try:
            from mlx_lm import load as load_lm         # type: ignore
            self._llm, self._llm_tok = load_lm(self.cfg["llm"]["model"])
        except Exception as exc:                       # pragma: no cover
            self._error = (self._error + " · " if self._error else "") + f"LLM: {exc}"

    # --------------------------------------------------------------- asr

    async def _remote_transcribe(self, pcm: bytes) -> str:
        """Send audio to the MERaLiON sidecar (see scripts/meralion_sidecar.py)."""
        import urllib.request

        url = self.cfg["asr"]["sidecar"].rstrip("/") + "/transcribe"

        def _post() -> str:
            import json as _json
            req = urllib.request.Request(
                url, data=pcm, headers={"Content-Type": "application/octet-stream"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return _json.loads(resp.read()).get("text", "")

        return await measured_worker(self._http, _post)

    async def _run(self, fn, *args):
        """Run an MLX call on the dedicated worker thread."""
        return await measured_worker(self._pool, fn, *args)

    async def transcribe(self, pcm: bytes, sample_rate: int) -> TranscriptResult:
        # A configured sidecar wins: it is the Singlish-capable model, running
        # in its own interpreter because its transformers pin is incompatible
        # with mlx-audio's.
        if self.cfg["asr"].get("sidecar"):
            t0 = time.perf_counter()
            try:
                text = await self._remote_transcribe(pcm)
                return TranscriptResult(
                    text=text, lang=detect_lang(text),
                    latency_ms=int((time.perf_counter() - t0) * 1000))
            except InferenceBusy:
                raise
            except Exception as exc:                    # pragma: no cover
                self._error = f"sidecar unreachable: {exc}"
                # Fall through to the local model rather than dropping the turn.

        if self._asr is None:
            return TranscriptResult(text="", lang="en", latency_ms=0)
        import numpy as np

        t0 = time.perf_counter()

        prompt = self.cfg["asr"].get("initial_prompt")

        def _work() -> str:
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            # Biasing Whisper with Singlish vocabulary measurably rescues the
            # particles: "Wasso expensive mech" becomes "Wah so expensive meh".
            # It leaves Mandarin unchanged, so it is safe to apply always.
            # Not every ASR family takes it, so fall back rather than fail.
            if prompt:
                try:
                    result = self._asr.generate(audio, initial_prompt=prompt)
                    return getattr(result, "text", str(result)).strip()
                except TypeError:
                    pass
            result = self._asr.generate(audio)
            return getattr(result, "text", str(result)).strip()

        text = await self._run(_work)
        return TranscriptResult(text=text, lang=detect_lang(text),
                                latency_ms=int((time.perf_counter() - t0) * 1000))

    # --------------------------------------------------------------- llm

    async def complete(self, system: str, user: str, lang: str,
                       max_tokens: int | None = None) -> Completion:
        if self._llm is None:
            return Completion(text="", latency_ms=0)
        from mlx_lm import generate                     # type: ignore

        t0 = time.perf_counter()

        def _work() -> str:
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": user}]
            # Qwen3 reasons out loud by default. For a one-word routing
            # decision that is pure latency, and the eight-token cap chops the
            # reasoning off before any answer appears — every reply came back
            # as "Here's a thinking process:". Templates that do not know the
            # flag simply ignore it, so this is safe across models.
            try:
                prompt = self._llm_tok.apply_chat_template(
                    messages, add_generation_prompt=True, tokenize=False,
                    enable_thinking=False)
            except TypeError:
                prompt = self._llm_tok.apply_chat_template(
                    messages, add_generation_prompt=True, tokenize=False)
            return str(generate(
                self._llm, self._llm_tok, prompt=prompt,
                max_tokens=max_tokens or self.cfg["llm"].get("max_tokens",
                                                             SYSTEM_MAX_TOKENS),
                verbose=False)).strip()

        text = await self._run(_work)
        return Completion(text=text, latency_ms=int((time.perf_counter() - t0) * 1000))

    # --------------------------------------------------------------- tts

    def cached(self, text: str, lang: str, voice: str | None = None) -> bool:
        """Whether this exact line is already rendered to disk.

        Lets the caller tell a piece that costs nothing from one that has to be
        synthesised, which is the difference between a line that can be
        streamed and one that would drop in the middle.
        """
        if not self._cached_voice:
            return False
        try:
            return self.prerender.path(text, lang, voice).exists()
        except Exception:                   # pragma: no cover - never fatal
            return False

    async def speak(self, text: str, lang: str, prerendered: bool,
                    voice: str | None = None) -> Speech:
        """Read cache independently; synthesize misses with the resident voice."""
        sr = self.cfg.get("sample_rate", 16000)
        t0 = time.perf_counter()

        # A selected trial model speaks every line, cache included: the point
        # is to hear the candidate, not the incumbent's recordings. Ahead of
        # the cache gate, so a candidate is heard on scripted turns too.
        from ..tts_models import speak_with_active
        trial = await speak_with_active(self.lab, text, lang, voice)
        if trial is not None:
            return trial

        if self._cached_voice:
            if voice and self.prerender.voices() and voice not in self.prerender.voices():
                raise ValueError("Unknown selected voice")
            # Cache hit is a disk read: this is the whole point of the design.
            cached = await measured_worker(self._io, self.prerender.get, text, lang, voice)
            if cached is not None:
                return Speech(pcm=cached, sample_rate=sr,
                              latency_ms=int((time.perf_counter() - t0) * 1000),
                              voice_source="cache")
            fresh = await self._run(self._render_resident, text, lang, voice)
            if fresh:
                return Speech(pcm=fresh, sample_rate=sr,
                              latency_ms=int((time.perf_counter() - t0) * 1000),
                              voice_source="rendered")
            raise RuntimeError("Selected voice failed to synthesize; no speaker fallback")

        chunks = [c async for c in self.synthesize(text, lang)]
        return Speech(pcm=b"".join(chunks), sample_rate=sr,
                      latency_ms=int((time.perf_counter() - t0) * 1000),
                      voice_source="live", voice_consistent=True)

    def _render_resident(self, text, lang, voice):
        if self._tts is None or self.prerender._model is not self._tts:
            raise RuntimeError("Selected voice is not loaded; restart after fixing startup errors")
        ref = self.prerender.reference_for(voice, lang)
        if ref:
            from pathlib import Path
            if not Path(ref).is_file():
                raise RuntimeError("Selected voice reference is missing")
        return self.prerender.render(text, lang, voice, 1)

    async def synthesize(self, text: str, lang: str) -> AsyncIterator[bytes]:
        if self._cached_voice:
            speech = await self.speak(text, lang, True)
            frame = int(speech.sample_rate * 0.02) * 2
            for i in range(0, len(speech.pcm), frame):
                yield speech.pcm[i:i + frame]
            return
        if self._tts is None:
            raise RuntimeError("Selected voice is not loaded")
        import numpy as np

        # lang_code is not optional: Kokoro logs "Language mismatch" and falls
        # back to the English phonemiser if it is omitted, silently mangling
        # every Mandarin line.
        spec = self.cfg["tts"].get("voices", {}).get(lang) or {}
        if isinstance(spec, str):                       # legacy plain-voice form
            spec = {"voice": spec, "code": "a"}
        voice = spec.get("voice", "af_heart")
        code = spec.get("code", "a")
        speed = float(self.cfg["tts"].get("speed", 1.0))

        target_sr = self.cfg.get("sample_rate", 16000)

        # A reference clip clones its speaker, which is the only way to get a
        # Singaporean accent out of this stack — no preset voice in Kokoro or
        # anywhere else in mlx-audio is Singaporean. Accent comes from whoever
        # recorded the clip, so supply a Singaporean one.
        ref = self.cfg["tts"].get("reference_audio")
        gen_kwargs = ({"text": text, "ref_audio": ref} if ref
                      else {"text": text, "voice": voice, "lang_code": code,
                            "speed": speed})

        def _work():
            # Convert inside the worker: an mlx array must not cross threads.
            from mlx_audio.resample import resample_audio_array

            for seg in self._tts.generate(**gen_kwargs):
                audio = np.asarray(getattr(seg, "audio", seg), dtype=np.float32)
                # Kokoro emits 24 kHz. Framing that as 16 kHz plays it 1.5x
                # slow and a fifth too low — resample, never assume.
                src_sr = int(getattr(seg, "sample_rate", 24000) or 24000)
                if src_sr != target_sr:
                    audio = np.asarray(
                        resample_audio_array(audio, src_sr, target_sr), dtype=np.float32)
                pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                if len(pcm) > target_sr * 2 * 120:
                    raise ValueError("TTS segment exceeds 120 seconds")
                frame = int(target_sr * 0.02) * 2
                for i in range(0, len(pcm), frame):
                    yield pcm[i:i + frame]

        stream = stream_worker(self._pool, _work)
        try:
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()

    async def stream_speak(self, text, lang, voice=None):
        if not self.streaming_tts:
            speech = await self.speak(text, lang, True, voice)
            yield SpeechChunk(speech.pcm, speech.sample_rate, final=True,
                              voice_source=speech.voice_source)
            return
        sr = self.cfg.get("sample_rate", 16000)
        from ..spoken import speech_chunks
        # Kokoro splits on newlines. Reuse the existing speech-safe chunker
        # instead of splitting numbers, emails or arbitrary character counts.
        stream = self.synthesize("\n".join(speech_chunks(text, lang)), lang)
        try:
            async for chunk in stream:
                yield SpeechChunk(chunk, sr)
        finally:
            await stream.aclose()
        yield SpeechChunk(b"", sr, final=True)

    # ------------------------------------------------------------ health

    def close(self) -> None:
        for pool in (self._pool, self._io, self._http):
            pool.shutdown(wait=False, cancel_futures=True)

    def health(self) -> BackendHealth:
        # Routing can degrade to keyword handlers when the LLM is unavailable.
        return BackendHealth(
            profile="mlx",
            asr=("MERaLiON-3-3B-ASR (sidecar)" if self.cfg["asr"].get("sidecar")
                 else self.cfg["asr"]["model"].split("/")[-1]),
            llm=self.cfg["llm"]["model"].split("/")[-1]
                + ("" if self._llm is not None else " (unavailable or disabled)"),
            tts=(self.prerender.cfg["model"] if self._cached_voice
                 else self.cfg["tts"]["model"]).split("/")[-1],
            ready=self._asr is not None and self._tts is not None,
            detail=self._error,
        )
