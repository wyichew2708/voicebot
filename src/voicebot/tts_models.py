"""Switching TTS models at runtime, for the experiments stage.

`config/tts-models.yaml` lists the candidates. A **lab** sits on the backend
and does three things with that list: says which models this machine can run,
renders one line with any of them, and — when one is *selected* — takes over
every agent line so a whole call can be heard in it. The console's "TTS model"
switch, `make tts-say` and the benchmark's `--model` are all the same lab.

The shipped path is untouched when nothing is selected: cached scripted turns,
live model for the rest. Selecting a model bypasses the cache on purpose —
the point is to hear the candidate, not the incumbent's recordings — so a
selected call is as slow as that model is. The console shows the latency.

Three labs, one per platform:

- `MLXLab`  — loads mlx-audio models in-process on the Mac (and F5 through
  its own `f5-tts-mlx` port), keeping the last two loaded.
- `SidecarLab` — the GPU box: one sidecar per engine, each on its own port,
  addressed from `backend.tts.sidecars` or `VOICEBOT_TTS_SIDECARS`.
- `MockLab` — lists the models and renders silence, so the console works
  without any of them installed.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from . import pcm as P
from .config import CONFIG_DIR
from .runtime.base import Speech
from .spoken import segment_by_script

log = logging.getLogger("voicebot.tts_models")

REGISTRY = CONFIG_DIR / "tts-models.yaml"
SYSTEM_MODEL = ""          # the empty id: the shipped path, nothing selected


class Unsupported(ValueError):
    """The model cannot do what was asked — the wrong language, no clip."""


class Unavailable(RuntimeError):
    """The model is not runnable on this machine, and the message says why."""


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    note: str = ""
    languages: tuple[str, ...] = ()          # empty: anything
    clone: bool = True
    speaker: dict[str, str] = field(default_factory=dict)
    lang_codes: dict[str, str] = field(default_factory=dict)
    mlx: dict[str, str] = field(default_factory=dict)
    gpu: dict[str, str] = field(default_factory=dict)

    def speaks(self, lang: str) -> bool:
        return not self.languages or lang in self.languages

    def lang_code(self, lang: str) -> str:
        return self.lang_codes.get(lang, lang)

    def speaker_for(self, lang: str) -> str | None:
        if not self.speaker:
            return None
        return self.speaker.get(lang) or self.speaker.get("en") or next(iter(self.speaker.values()))

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "note": self.note,
                "languages": list(self.languages), "clone": self.clone,
                "mlx": dict(self.mlx), "gpu": dict(self.gpu)}


def load_registry(path: Path | str | None = None) -> dict[str, ModelSpec]:
    """The candidates, in file order. A missing file is an empty registry,
    not an error: the shipped path needs none of this."""
    p = Path(path) if path else REGISTRY
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: dict[str, ModelSpec] = {}
    for mid, entry in (raw.get("models") or {}).items():
        entry = entry or {}
        out[str(mid)] = ModelSpec(
            id=str(mid), label=str(entry.get("label", mid)), note=str(entry.get("note", "")),
            languages=tuple(str(l) for l in (entry.get("languages") or [])),
            clone=bool(entry.get("clone", True)),
            speaker={str(k): str(v) for k, v in (entry.get("speaker") or {}).items()},
            lang_codes={str(k): str(v) for k, v in (entry.get("lang_codes") or {}).items()},
            mlx={str(k): str(v) for k, v in (entry.get("mlx") or {}).items()},
            gpu={str(k): str(v) for k, v in (entry.get("gpu") or {}).items()})
    return out


def generate_kwargs(spec: ModelSpec, text: str, lang: str,
                    ref: str | None, ref_text: str | None) -> dict[str, Any]:
    """What an mlx-audio `generate` is handed for this model and this piece.

    A cloning model takes the clip (and its transcript when there is one); a
    preset model takes `voice=` — a clip handed to it is at best ignored — and
    every model is told the language in its own code, because the ones that
    default to English do so silently.
    """
    kw: dict[str, Any] = {"text": text, "lang_code": spec.lang_code(lang)}
    if spec.clone:
        if not ref:
            raise Unsupported(f"{spec.id} clones from a reference clip and the voice has none")
        kw["ref_audio"] = ref
        if ref_text:
            kw["ref_text"] = ref_text
    else:
        speaker = spec.speaker_for(lang)
        if speaker:
            kw["voice"] = speaker
    return kw


def f5_mlx_piece(text: str, ref: str | None, ref_text: str | None,
                 sample_rate: int, model_name: str = "lucasnewman/f5-tts-mlx") -> bytes:
    """One piece through the `f5-tts-mlx` package, as 16-bit PCM at
    `sample_rate`. Shared with the benchmark so both say the same thing."""
    import numpy as np
    from f5_tts_mlx.generate import generate

    audio = generate(generation_text=text, model_name=model_name,
                     ref_audio_path=ref, ref_audio_text=ref_text)
    audio = np.asarray(audio, dtype=np.float32).squeeze()
    src = 24000
    if src != sample_rate and len(audio) > 1:
        n = int(len(audio) * sample_rate / src)
        audio = np.interp(np.linspace(0, len(audio) - 1, n),
                          np.arange(len(audio)), audio).astype(np.float32)
    return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()


def _join(parts: list[bytes], sample_rate: int) -> bytes:
    """Pieces of one line, joined the way the live path joins them."""
    if len(parts) == 1:
        return parts[0]
    out = bytearray()
    for i, part in enumerate(parts):
        part = P.trim(part, head=(i > 0), tail=(i < len(parts) - 1),
                      keep_ms=10, sample_rate=sample_rate)
        if i:
            out += P.silence(40, sample_rate)
        out += part
    return bytes(out)


# ------------------------------------------------------------------- labs

class Lab:
    """Base: the registry, the selection, and the shape of `render`."""
    platform = "?"

    def __init__(self, specs: dict[str, ModelSpec] | None = None) -> None:
        self.specs = load_registry() if specs is None else dict(specs)
        self.active: str | None = None

    def select(self, model_id: str | None) -> str | None:
        """Choose the model every agent line goes through, or None for the
        shipped path. An unknown id raises KeyError."""
        if not model_id:
            self.active = None
            return None
        if model_id not in self.specs:
            raise KeyError(model_id)
        self.active = model_id
        log.info("TTS model selected for every line: %s", model_id)
        return model_id

    def availability(self, spec: ModelSpec) -> tuple[bool, str]:     # pragma: no cover
        return False, "not runnable on this platform"

    def models(self) -> list[dict[str, Any]]:
        out = []
        for spec in self.specs.values():
            ok, why = self.availability(spec)
            out.append(spec.describe() | {"available": ok, "reason": why,
                                          "platform": self.platform,
                                          "active": spec.id == self.active})
        return out

    async def render(self, text: str, lang: str, voice: str | None,
                     model_id: str) -> Speech:                       # pragma: no cover
        raise NotImplementedError

    def spec(self, model_id: str) -> ModelSpec:
        try:
            return self.specs[model_id]
        except KeyError:
            raise KeyError(model_id) from None


class MockLab(Lab):
    platform = "mock"

    def __init__(self, backend: Any, specs: dict[str, ModelSpec] | None = None) -> None:
        super().__init__(specs)
        self._backend = backend

    def availability(self, spec: ModelSpec) -> tuple[bool, str]:
        return False, "mock profile — no models are loaded; the switch is exercised, not heard"

    async def render(self, text, lang, voice, model_id) -> Speech:
        spec = self.spec(model_id)
        if not spec.speaks(lang):
            raise Unsupported(f"{spec.id} does not speak {lang!r}")
        sp = await self._backend.speak(text, lang, prerendered=False, voice=voice)
        return Speech(pcm=sp.pcm, sample_rate=sp.sample_rate, latency_ms=sp.latency_ms,
                      voice_source=f"trial:{model_id}")


class MLXLab(Lab):
    """In-process mlx-audio models on the Mac."""
    platform = "mlx"
    KEEP = 2                     # loaded models kept resident

    def __init__(self, prerender: Any, sample_rate: int,
                 run: Callable[..., Awaitable[Any]],
                 specs: dict[str, ModelSpec] | None = None) -> None:
        super().__init__(specs)
        self._prerender = prerender          # for the voices' clips and transcripts
        self.sample_rate = sample_rate
        self._run = run                      # the backend's single MLX worker
        self._loaded: dict[str, Any] = {}

    def availability(self, spec: ModelSpec) -> tuple[bool, str]:
        pkg = spec.mlx.get("package")
        if not spec.mlx:
            return False, "no MLX build — run the GPU sidecar (" + \
                spec.gpu.get("engine", "?") + ") and point VOICEBOT_TTS_SIDECARS at it"
        if pkg == "f5-tts-mlx":
            ok = importlib.util.find_spec("f5_tts_mlx") is not None
            return ok, "" if ok else "pip install f5-tts-mlx"
        if pkg:
            try:
                importlib.metadata.distribution(pkg)
                return True, ""
            except importlib.metadata.PackageNotFoundError:
                return False, f"pip install {pkg} — in its own venv, it installs as mlx_audio"
        ok = importlib.util.find_spec("mlx_audio") is not None
        return ok, "" if ok else "make mlx"

    def _load(self, repo: str) -> Any:
        if repo in self._loaded:
            return self._loaded[repo]
        # The pre-render model is often the incumbent: reuse it rather than
        # holding a second copy of the same weights.
        pre = getattr(self._prerender, "_model", None)
        if pre is not None and getattr(self._prerender, "cfg", {}).get("model") == repo:
            self._loaded[repo] = pre
            return pre
        import mlx_audio.tts.utils as tts                 # type: ignore
        log.info("loading TTS model %s", repo)
        model = tts.load(repo)
        while len(self._loaded) >= self.KEEP:
            gone = next(iter(self._loaded))
            log.info("unloading TTS model %s", gone)
            del self._loaded[gone]
        self._loaded[repo] = model
        return model

    def _render_sync(self, spec: ModelSpec, text: str, lang: str, voice: str | None) -> bytes:
        import numpy as np

        ref = self._prerender.reference_for(voice, lang) if spec.clone else None
        ref_text = self._prerender.reference_text_for(voice, lang) if spec.clone else None
        pieces = segment_by_script(text, lang)
        parts: list[bytes] = []
        if spec.mlx.get("package") == "f5-tts-mlx":
            for piece, piece_lang in pieces:
                piece_ref = self._prerender.reference_for(voice, piece_lang) or ref
                parts.append(f5_mlx_piece(piece, piece_ref, ref_text, self.sample_rate))
            return _join(parts, self.sample_rate)

        model = self._load(spec.mlx["repo"])
        for piece, piece_lang in pieces:
            kw = generate_kwargs(spec, piece, piece_lang, ref, ref_text)
            buf = bytearray()
            for seg in model.generate(**kw):
                audio = np.asarray(getattr(seg, "audio", seg), dtype=np.float32).squeeze()
                src = int(getattr(seg, "sample_rate", 24000) or 24000)
                if src != self.sample_rate and audio.size > 1:
                    # Linear, like the sidecar: adequate for speech and it
                    # keeps this file importable without mlx.
                    n = int(audio.size * self.sample_rate / src)
                    audio = np.interp(np.linspace(0, audio.size - 1, n),
                                      np.arange(audio.size), audio).astype(np.float32)
                buf += (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
            parts.append(bytes(buf))
        return _join(parts, self.sample_rate)

    async def render(self, text, lang, voice, model_id) -> Speech:
        spec = self.spec(model_id)
        if not spec.speaks(lang):
            raise Unsupported(f"{spec.label} does not speak {lang!r} "
                              f"(speaks: {', '.join(spec.languages)})")
        ok, why = self.availability(spec)
        if not ok:
            raise Unavailable(f"{spec.label} is not runnable here: {why}")
        t0 = time.perf_counter()
        pcm = await self._run(self._render_sync, spec, text, lang, voice)
        return Speech(pcm=pcm, sample_rate=self.sample_rate,
                      latency_ms=int((time.perf_counter() - t0) * 1000),
                      voice_source=f"trial:{model_id}")


class SidecarLab(Lab):
    """The GPU box: one sidecar per engine, each on its own port.

    Addresses come from `backend.tts.sidecars` ({engine: url}) and
    `VOICEBOT_TTS_SIDECARS` ("cosyvoice3=http://…:8803,vibevoice=http://…:8804").
    The profile's own `base_url` counts for whatever engine it reports.
    """
    platform = "cuda"
    PROBE_TTL = 10.0

    def __init__(self, tts_cfg: dict[str, Any], sample_rate: int,
                 specs: dict[str, ModelSpec] | None = None) -> None:
        super().__init__(specs)
        self.sample_rate = sample_rate
        self._tts_cfg = tts_cfg
        self.sidecars: dict[str, str] = {str(k): str(v).rstrip("/")
                                         for k, v in (tts_cfg.get("sidecars") or {}).items()}
        for pair in filter(None, os.environ.get("VOICEBOT_TTS_SIDECARS", "").split(",")):
            engine, _, url = pair.partition("=")
            if engine.strip() and url.strip():
                self.sidecars[engine.strip()] = url.strip().rstrip("/")
        self._probed: dict[str, tuple[float, dict | None]] = {}
        self._backends: dict[str, Any] = {}

    def _health(self, url: str) -> dict | None:
        now = time.monotonic()
        hit = self._probed.get(url)
        if hit and now - hit[0] < self.PROBE_TTL:
            return hit[1]
        info = self._backend_for(url)._sidecar_health()
        self._probed[url] = (now, info)
        return info

    def _url_for(self, spec: ModelSpec) -> str | None:
        engine = spec.gpu.get("engine")
        if not engine:
            return None
        if engine in self.sidecars:
            return self.sidecars[engine]
        base = str(self._tts_cfg.get("base_url", "")).rstrip("/")
        if base and (self._health(base) or {}).get("engine") == engine:
            return base
        return None

    def availability(self, spec: ModelSpec) -> tuple[bool, str]:
        engine = spec.gpu.get("engine")
        if not engine:
            return False, "no GPU engine for this model"
        url = self._url_for(spec)
        if not url:
            return False, (f"no sidecar for engine {engine!r} — start one "
                           f"(make tts-sidecar TTS_ENGINE={engine} TTS_PORT=…) and add "
                           f"{engine}=http://host:port to VOICEBOT_TTS_SIDECARS")
        info = self._health(url)
        if info is None:
            return False, f"sidecar at {url} is not answering"
        got = info.get("engine")
        if got and got != engine:
            return False, f"sidecar at {url} is running {got!r}, not {engine!r}"
        return True, ""

    def _backend_for(self, url: str) -> Any:
        if url not in self._backends:
            from .runtime.cuda_backend import CUDABackend
            cfg = {"sample_rate": self.sample_rate,
                   "tts": {"base_url": url, "lab": False,     # no lab inside a lab
                           "prerender": dict(self._tts_cfg.get("prerender") or {})}}
            self._backends[url] = CUDABackend(cfg)
        return self._backends[url]

    async def render(self, text, lang, voice, model_id) -> Speech:
        spec = self.spec(model_id)
        if not spec.speaks(lang):
            raise Unsupported(f"{spec.label} does not speak {lang!r} "
                              f"(speaks: {', '.join(spec.languages)})")
        ok, why = self.availability(spec)
        if not ok:
            raise Unavailable(f"{spec.label} is not runnable here: {why}")
        url = self._url_for(spec)
        t0 = time.perf_counter()
        be = self._backend_for(url)
        pcm = b"".join([c async for c in be.synthesize(text, lang, voice)])
        if not pcm:
            raise Unavailable(f"{spec.label}: the sidecar at {url} returned no audio")
        return Speech(pcm=pcm, sample_rate=self.sample_rate,
                      latency_ms=int((time.perf_counter() - t0) * 1000),
                      voice_source=f"trial:{model_id}")


async def speak_with_active(lab: Lab | None, text: str, lang: str,
                            voice: str | None) -> Speech | None:
    """The override hook every backend's `speak` calls first: the selected
    model's rendering, or None to carry on down the shipped path. A failure
    falls back and is logged once per line rather than dropping the turn."""
    if lab is None or not lab.active:
        return None
    try:
        return await lab.render(text, lang, voice, lab.active)
    except (Unsupported, Unavailable) as exc:
        log.warning("trial model %s could not say this line (%s) — shipped path used",
                    lab.active, exc)
        return None
    except asyncio.CancelledError:
        raise
    except Exception as exc:                          # pragma: no cover - model
        log.warning("trial model %s failed (%s) — shipped path used", lab.active, exc)
        return None
