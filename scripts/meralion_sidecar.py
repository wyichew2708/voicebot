"""MERaLiON ASR sidecar.

MERaLiON's modelling code needs transformers 4.x; mlx-audio needs >=5.14.
They cannot share an interpreter, so MERaLiON runs in its own venv behind a
tiny HTTP endpoint and the main app calls it. This mirrors MERaLiON's own
recommended deployment (`meralion-3-asr serve`), minus the vLLM backend, which
is CUDA-only and therefore unavailable on a Mac.

Run with the sidecar interpreter, not the app's:
    .venv-meralion/bin/python scripts/meralion_sidecar.py --port 8801
"""
from __future__ import annotations

import argparse
import io
import logging
import time
import wave

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("meralion-sidecar")
app = FastAPI(title="MERaLiON ASR sidecar")
_model = None
_repo = "MERaLiON/MERaLiON-3-3B-ASR"


def _load():
    global _model
    if _model is None:
        from meralion_3_asr import Meralion3ASR
        log.info("loading %s ...", _repo)
        t0 = time.time()
        _model = Meralion3ASR.from_pretrained(_repo, backend="transformers")
        log.info("loaded in %.0fs", time.time() - t0)
    return _model


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"model": _repo, "ready": _model is not None})


@app.post("/transcribe")
async def transcribe(request: Request) -> JSONResponse:
    """Raw 16 kHz mono PCM16 in the body; JSON text out."""
    import numpy as np

    pcm = await request.body()
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    t0 = time.time()
    text = _load().transcribe((audio, 16000))
    took = int((time.time() - t0) * 1000)
    log.info("%d ms  %s", took, text[:60])
    return JSONResponse({"text": text.strip(), "latency_ms": took})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--model", default=_repo)
    args = ap.parse_args()
    globals()["_repo"] = args.model
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _load()                     # fail loudly at boot, not on the first call
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
