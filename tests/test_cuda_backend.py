"""The CUDA backend against a stub of the GPU services.

There is no NVIDIA hardware here, but the backend talks HTTP rather than
loading models, so everything except the GPU itself is testable: request
shapes, the pre-render cache short-circuit, and — most importantly — that
health does not report ready when a dependency is missing or wrong.
"""
import asyncio
import io
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from voicebot.runtime.cuda_backend import CUDABackend


def _wav(seconds=0.4, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(rate * seconds))
    return buf.getvalue()


class _Stub(BaseHTTPRequestHandler):
    served_model = "MERaLiON/MERaLiON-3-3B-ASR"
    seen: list = []
    tts_bodies: list = []

    def log_message(self, *a):            # keep pytest output clean
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            self._json({"data": [{"id": self.served_model}]})
        elif self.path == "/health":
            self._json({"ok": True})
        else:
            self._json({"error": "nope"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        type(self).seen.append((self.path, self.headers.get("Content-Type", ""), len(raw)))
        if self.path == "/tts":
            type(self).tts_bodies.append(json.loads(raw))
        if self.path == "/v1/audio/transcriptions":
            self._json({"text": "Yes speaking"})
        elif self.path == "/v1/chat/completions":
            self._json({"choices": [{"message": {"content": "acknowledged"}}]})
        elif self.path == "/tts":
            body = _wav()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json({"error": "nope"}, 404)


@pytest.fixture
def stub():
    _Stub.seen = []
    _Stub.tts_bodies = []
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield url, _Stub
    srv.shutdown()


def _cfg(url, **over):
    cfg = {
        "sample_rate": 16000,
        "asr": {"base_url": url, "model": "MERaLiON/MERaLiON-3-3B-ASR"},
        "llm": {"base_url": url, "model": "Qwen/Qwen3.6-35B-A3B"},
        "tts": {"base_url": url, "model": "chatterbox", "prerender": {}},
    }
    cfg.update(over)
    return cfg


def test_transcribe_posts_multipart_and_returns_text(stub):
    url, S = stub
    be = CUDABackend(_cfg(url))
    r = asyncio.run(be.transcribe(b"\x00\x00" * 1600, 16000))
    assert r.text == "Yes speaking"
    path, ctype, size = S.seen[-1]
    assert path == "/v1/audio/transcriptions"
    assert ctype.startswith("multipart/form-data"), "vLLM expects a file upload"
    assert size > 3200, "the wav header and audio should both be sent"


def test_speak_prefers_the_cache_and_never_calls_tts(tmp_path, stub):
    """Scripted turns must not reach the TTS service — that is the whole point
    of shipping the pre-rendered cache to the server."""
    url, S = stub
    cfg = _cfg(url)
    cfg["tts"]["prerender"] = {"cache_dir": str(tmp_path), "voices": {"male": {}}}
    be = CUDABackend(cfg)
    text = "Good afternoon Mr Tan."
    # Seed the cache exactly as `make prerender` would.
    with wave.open(str(be.prerender.path(text, "en", "male")), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x02\x00" * 8000)
    sp = asyncio.run(be.speak(text, "en", prerendered=True, voice="male"))
    assert len(sp.pcm) == 16000
    assert not [s for s in S.seen if s[0] == "/tts"], "cache hit still hit the network"


def test_a_cache_miss_falls_back_to_live_tts(tmp_path, stub):
    url, S = stub
    cfg = _cfg(url)
    cfg["tts"]["prerender"] = {"cache_dir": str(tmp_path), "voices": {"male": {}}}
    be = CUDABackend(cfg)
    sp = asyncio.run(be.speak("not cached", "en", prerendered=True, voice="male"))
    assert sp.pcm, "a miss should still produce audio"
    assert [s for s in S.seen if s[0] == "/tts"], "expected a live TTS request"


def test_health_is_not_ready_when_a_service_is_down():
    be = CUDABackend(_cfg("http://127.0.0.1:9"))     # discard port: nothing there
    h = be.health()
    assert not h.ready
    assert "asr" in h.detail


def test_health_rejects_a_service_serving_the_wrong_model(stub):
    """A stale process on the port answers 404s and used to read as ready,
    which would let calls start against nothing."""
    url, S = stub
    S.served_model = "some/other-model"
    try:
        be = CUDABackend(_cfg(url))
        assert not be.health().ready, "wrong model must not count as ready"
    finally:
        S.served_model = "MERaLiON/MERaLiON-3-3B-ASR"


def test_health_is_ready_when_asr_serves_the_configured_model(stub):
    url, _ = stub
    assert CUDABackend(_cfg(url)).health().ready


# ------------------------------------------------------- container contract

def test_env_overrides_reach_the_config():
    """One image, every environment: service addresses are env vars because
    they differ between a laptop, compose service names and a bare host."""
    import os
    from voicebot import config

    keys = {"VOICEBOT_ASR_URL": "http://asr:8801",
            "VOICEBOT_LLM_URL": "http://gpu:8000",
            "VOICEBOT_TTS_URL": "http://tts:8802"}
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ.update(keys)
        cfg = config.load("rhel")
        assert cfg["backend"]["asr"]["base_url"] == "http://asr:8801"
        assert cfg["backend"]["llm"]["base_url"] == "http://gpu:8000"
        assert cfg["backend"]["tts"]["base_url"] == "http://tts:8802"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_compose_does_not_start_the_llm():
    """Voice must point at a dedicated replica or a priority queue. Starting a
    second Qwen3.6 in compose would both duplicate 20 GB and hide that."""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1]
    spec = yaml.safe_load((root / "docker-compose.yml").read_text())
    assert set(spec["services"]) == {"asr", "tts", "console"}, list(spec["services"])
    env = spec["services"]["console"]["environment"]
    assert "VOICEBOT_LLM_URL" in env, "the console must be told where the LLM is"


def test_voices_are_mounted_not_baked_into_the_image():
    """28 MB of audio that changes with the script, not the code — baking it in
    would mean rebuilding the image to change a voice."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert "voices/cache/" in (root / ".dockerignore").read_text()
    assert "./voices:/app/voices" in (root / "docker-compose.yml").read_text()


# --- parity with the Mac path ---------------------------------------------
# The scripted turns come from a shared cache, so they are identical by
# construction. These are about the improvised path, which is the only place
# the two deployments can drift.

_ZH_MIXED = "我是来跟您确认您在Jurong West Street 4, #08-212的居家保险续保事项。"


def test_a_mixed_line_reaches_the_sidecar_as_fragments_in_their_own_language(stub):
    """One front-end cannot read two scripts. Handed the line whole, the
    sidecar renders the address through the Mandarin phonemiser."""
    url, S = stub
    be = CUDABackend(_cfg(url, tts={"base_url": url, "model": "chatterbox",
                                    "prerender": {"voices": {"male": {}}}}))
    asyncio.run(_drain(be.synthesize(_ZH_MIXED, "zh")))

    langs = [b["lang"] for b in S.tts_bodies]
    assert langs == ["zh", "en", "zh"], langs
    assert "Jurong West Street 4" in S.tts_bodies[1]["text"]


def test_the_live_path_never_leaves_the_language_to_a_default(stub):
    """The checkpoint defaults to English and phonemises accordingly, so a
    missing language is not an error anywhere — it is English-sounding
    nonsense of about the right duration."""
    url, S = stub
    be = CUDABackend(_cfg(url, tts={"base_url": url, "model": "chatterbox",
                                    "prerender": {"voices": {"male": {}}}}))
    asyncio.run(_drain(be.synthesize("就是续保的事，没别的。", "zh")))
    assert S.tts_bodies and all(b.get("lang") for b in S.tts_bodies)


def test_the_live_voice_is_put_on_the_same_pitch_as_the_cache(stub):
    """Cloning re-derives the speaker per line. Left uncorrected, an
    improvised line sits at a different pitch from the cached one either side
    of it, and the caller hears the speaker change mid-call."""
    url, S = stub
    be = CUDABackend(_cfg(url, tts={"base_url": url, "model": "chatterbox",
                                    "prerender": {"voices": {"male": {"target_f0": 162}}}}))
    seen = []
    real = be.prerender.normalise_pitch
    be.prerender.normalise_pitch = lambda pcm, voice: seen.append(voice) or real(pcm, voice)
    asyncio.run(_drain(be.synthesize("No problem.", "en")))
    assert seen == ["male"]


async def _drain(gen):
    return b"".join([c async for c in gen])
