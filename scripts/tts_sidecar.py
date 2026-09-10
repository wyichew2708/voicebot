"""TTS sidecar for the CUDA deployment.

Only improvised lines reach this service — the seven scripted turns are served
from voices/cache, which is why a slow, good-sounding model is affordable for
those and a fast one is needed here.

Runs Chatterbox multilingual on CUDA via torch — the *same checkpoint* as the
Mac pre-render path, so a line generated here and a line from the cache sound
like one speaker.

It renders one fragment in one language and nothing more. Splitting a mixed
script line, joining the pieces and putting the result on the voice's own
pitch all happen in `runtime/cuda_backend.py`, which does it by calling the
same code the Mac uses rather than a second implementation of it.

    python scripts/tts_sidecar.py --port 8802
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import io
import logging
import time
import wave
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

log = logging.getLogger("tts-sidecar")
app = FastAPI(title="voicebot TTS sidecar")
_state: dict = {}

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from voicebot.runtime.workers import BoundedExecutor, InferenceBusy

_worker = BoundedExecutor(1, 2, thread_name_prefix="tts-gpu")

REFS = {"male": ROOT / "voices/refs/male.wav",
        "female": ROOT / "voices/refs/female.wav"}


#: The multilingual class has moved between releases of `chatterbox-tts`.
#: Try the known homes and fail at boot naming what was tried — the failure
#: this replaces was silent: the English-only class loaded happily, ignored
#: the language, and read Mandarin through the English phonemiser.
_MULTILINGUAL = (("chatterbox.mtl_tts", "ChatterboxMultilingualTTS"),
                 ("chatterbox.tts", "ChatterboxMultilingualTTS"),
                 ("chatterbox", "ChatterboxMultilingualTTS"))


def _multilingual_class():
    import importlib
    for module, name in _MULTILINGUAL:
        try:
            return getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError):
            continue
    raise RuntimeError(
        "no multilingual Chatterbox class found; tried "
        + ", ".join(f"{m}.{n}" for m, n in _MULTILINGUAL)
        + ". The English-only ChatterboxTTS is NOT a substitute: it ignores "
          "the language and reads Mandarin through the English phonemiser.")


def _reference(named: str | None, voice: str) -> Path | None:
    """The clip to clone: named by the caller, else looked up by voice id.

    Named wins, and a name that does not resolve is refused rather than
    quietly replaced. The console resolves the clip per voice *and per
    language* from its profile — a Mandarin line clones a Mandarin speaker —
    and the two-entry table below knows nothing of that. Rendering against
    the model's default speaker is not an error anything downstream can see;
    it is a stranger's voice in the middle of the call.
    """
    if named:
        path = Path(named)
        if not path.is_absolute():
            path = ROOT / path
        return path if path.exists() else None
    ref = REFS.get(voice)
    return ref if ref and ref.exists() else None


def _model():
    if "m" not in _state:
        import torch
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        log.info("loading chatterbox multilingual on %s", dev)
        _state["m"] = _multilingual_class().from_pretrained(device=dev)
        _state["dev"] = dev
    return _state["m"]


@app.on_event("startup")
async def startup():
    await asyncio.get_running_loop().run_in_executor(_worker, _model)


@app.on_event("shutdown")
async def shutdown():
    _worker.shutdown(wait=False, cancel_futures=True)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ready": "m" in _state, "device": _state.get("dev", "?"),
                         "model": "chatterbox-multilingual-v3"},
                        status_code=200 if "m" in _state else 503)


@app.post("/tts")
async def tts(request: Request) -> Response:
    body = await request.json()
    text = body.get("text", "")
    voice = body.get("voice", "male")
    # The model's own code for the language, resolved by the caller. Not
    # optional and not defaulted: the checkpoint falls back to English and
    # phonemises whatever it is given accordingly, which is audible as
    # English-sounding nonsense rather than as an error.
    lang = str(body.get("lang") or "").lower()
    target_sr = int(body.get("sample_rate", 16000))
    if not text.strip():
        return Response(status_code=400, content=b"empty text")
    if not lang:
        return Response(status_code=400, content=b"missing lang")

    ref = _reference(body.get("ref_audio"), voice)
    if ref is None:
        return Response(status_code=400,
                        content=b"selected voice reference clip not found")
    if "m" not in _state:
        return Response(status_code=503, content=b"TTS is not ready")
    if len(text) > 4000 or target_sr not in (16000, 24000, 48000):
        return Response(status_code=400, content=b"unsupported text length or sample rate")
    try:
        audio = await asyncio.get_running_loop().run_in_executor(
            _worker, _render, text, lang, ref, target_sr)
    except InferenceBusy:
        return Response(status_code=503, content=b"TTS capacity is full",
                        headers={"Retry-After": "1"})
    return Response(content=audio, media_type="audio/wav")


def _render(text, lang, ref, target_sr):
    import numpy as np

    t0 = time.time()
    model = _state.get("m")
    if model is None:
        raise RuntimeError("TTS model is not loaded")
    wav = model.generate(text, language_id=lang,
                         audio_prompt_path=str(ref) if ref else None,
                         temperature=0.5)
    audio = np.asarray(wav.squeeze().detach().cpu().numpy(), dtype=np.float32)
    src_sr = int(getattr(model, "sr", 24000))

    if src_sr != target_sr:
        # Linear resample: adequate for speech, and avoids another dependency.
        n = int(len(audio) * target_sr / src_sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n),
                          np.arange(len(audio)), audio).astype(np.float32)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(target_sr)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    log.info("%d ms  [%s] %r", int((time.time() - t0) * 1000), lang, text[:48])
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8802)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
