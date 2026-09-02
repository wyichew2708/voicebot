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


def _model():
    if "m" not in _state:
        import torch
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        log.info("loading chatterbox multilingual on %s", dev)
        _state["m"] = _multilingual_class().from_pretrained(device=dev)
        _state["dev"] = dev
    return _state["m"]


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ready": "m" in _state, "device": _state.get("dev", "?")})


@app.post("/tts")
async def tts(request: Request) -> Response:
    import numpy as np

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

    ref = REFS.get(voice)
    t0 = time.time()
    model = _model()
    wav = model.generate(text, language_id=lang,
                         audio_prompt_path=str(ref) if ref and ref.exists() else None,
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
    return Response(content=buf.getvalue(), media_type="audio/wav")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8802)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _model()                    # fail at boot, not on the first call
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
