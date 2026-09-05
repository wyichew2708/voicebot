"""The runtime TTS model switch.

No model here either. What has to hold: the registry describes every
candidate, a preset model is never handed a clip and a cloning model never
goes without one, a selected model takes over every line of a call and
nothing else changes when none is selected, and the console's endpoints say
what happened rather than guessing.
"""
import asyncio
import io
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voicebot import tts_models as T

ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------- registry

def test_the_registry_lists_every_candidate_with_what_it_needs():
    specs = T.load_registry()
    assert {"chatterbox", "cosyvoice3", "chatterbox-turbo", "indextts2", "vibevoice",
            "kokoro", "f5", "fish"} <= set(specs)
    for spec in specs.values():
        assert spec.label
        assert spec.mlx or spec.gpu, f"{spec.id} has no way to run anywhere"
        if not spec.clone:
            assert spec.speaker, f"{spec.id} neither clones nor names a preset"
        if spec.gpu:
            assert spec.gpu.get("engine"), spec.id


def test_the_gpu_engines_named_exist_in_the_sidecar():
    import importlib.util
    spec = importlib.util.spec_from_file_location("tts_sidecar", ROOT / "scripts/tts_sidecar.py")
    sidecar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sidecar)
    for m in T.load_registry().values():
        if m.gpu:
            assert m.gpu["engine"] in sidecar.ENGINES, f"{m.id} names an engine that is not there"


def test_a_missing_registry_is_empty_not_fatal(tmp_path):
    assert T.load_registry(tmp_path / "nope.yaml") == {}


def test_languages_and_presets_resolve():
    specs = T.load_registry()
    assert specs["chatterbox-turbo"].speaks("en") and not specs["chatterbox-turbo"].speaks("zh")
    assert specs["fish"].speaks("ta"), "an empty list means no fixed list"
    assert specs["kokoro"].speaker_for("zh") == "zm_yunjian"
    assert specs["kokoro"].lang_code("zh") == "z" and specs["cosyvoice3"].lang_code("zh") == "zh"


# ------------------------------------------------------------ generate kwargs

def test_a_cloning_model_gets_the_clip_and_a_preset_model_gets_a_name():
    specs = T.load_registry()
    kw = T.generate_kwargs(specs["cosyvoice3"], "No problem.", "en",
                           "voices/refs/male.wav", "Hello there.")
    assert kw == {"text": "No problem.", "lang_code": "en",
                  "ref_audio": "voices/refs/male.wav", "ref_text": "Hello there."}
    kw = T.generate_kwargs(specs["kokoro"], "没问题。", "zh", "voices/refs/zm_yunjian.wav", None)
    assert kw == {"text": "没问题。", "lang_code": "z", "voice": "zm_yunjian"}
    assert "ref_audio" not in kw, "a preset model is never handed a clip"
    with pytest.raises(T.Unsupported):
        T.generate_kwargs(specs["chatterbox-turbo"], "Hi.", "en", None, None)


# ------------------------------------------------------------------ MLX lab

class _Seg:
    def __init__(self, n, sr):
        import numpy as np
        self.audio = np.zeros(n, dtype="float32")
        self.sample_rate = sr


class _FakeMLX:
    def __init__(self):
        self.calls = []

    def generate(self, **kw):
        self.calls.append(kw)
        yield _Seg(16000, 16000)


def _mlx_lab(monkeypatch, tmp_path, fake, specs=None):
    from voicebot.runtime.prerender import PrerenderCache

    pre = PrerenderCache({"cache_dir": str(tmp_path), "default_voice": "male",
                          "voices": {"male": {"ref_audio": {"en": "voices/refs/male.wav",
                                                            "zh": "voices/refs/zm_yunjian.wav"},
                                              "ref_text": {"en": "Hello."}}}}, 16000)

    async def run(fn, *a):
        return fn(*a)

    lab = T.MLXLab(pre, 16000, run, specs)
    monkeypatch.setattr(lab, "_load", lambda repo: fake)
    monkeypatch.setattr(lab, "availability", lambda spec: (True, ""))
    return lab


def test_the_mlx_lab_renders_a_mixed_line_piece_by_piece(monkeypatch, tmp_path):
    fake = _FakeMLX()
    lab = _mlx_lab(monkeypatch, tmp_path, fake)
    sp = asyncio.run(lab.render("我是来跟您确认您在Jurong West Street 4, #08-212的续保事项。",
                                "zh", "male", "chatterbox"))
    assert sp.voice_source == "trial:chatterbox" and sp.pcm
    codes = [c["lang_code"] for c in fake.calls]
    assert codes == ["zh", "en", "zh"], codes
    # The line's language picks the clip for every piece, as on a live call.
    assert {c["ref_audio"] for c in fake.calls} == {"voices/refs/zm_yunjian.wav"}
    # The voice has an English transcript only. The Mandarin clip must not be
    # described by it — a transcript that does not match the clip is worse
    # than none — so no piece carries one.
    assert all("ref_text" not in c for c in fake.calls), fake.calls
    fake.calls.clear()
    asyncio.run(lab.render("No problem.", "en", "male", "chatterbox"))
    assert fake.calls[0]["ref_text"] == "Hello.", "the English clip's transcript travels"


def test_the_mlx_lab_refuses_the_wrong_language_before_loading_anything(monkeypatch, tmp_path):
    fake = _FakeMLX()
    lab = _mlx_lab(monkeypatch, tmp_path, fake)
    with pytest.raises(T.Unsupported):
        asyncio.run(lab.render("就是续保的事。", "zh", "male", "chatterbox-turbo"))
    assert not fake.calls


def test_a_preset_model_is_asked_by_name_in_its_own_language_code(monkeypatch, tmp_path):
    fake = _FakeMLX()
    lab = _mlx_lab(monkeypatch, tmp_path, fake)
    asyncio.run(lab.render("就是续保的事。", "zh", "male", "kokoro"))
    assert fake.calls == [{"text": "就是续保的事。", "lang_code": "z", "voice": "zm_yunjian"}]


def test_selecting_a_model_takes_over_every_line_and_clearing_it_does_not(monkeypatch, tmp_path):
    fake = _FakeMLX()
    lab = _mlx_lab(monkeypatch, tmp_path, fake)
    assert asyncio.run(T.speak_with_active(lab, "Hi.", "en", "male")) is None
    lab.select("kokoro")
    sp = asyncio.run(T.speak_with_active(lab, "Hi.", "en", "male"))
    assert sp is not None and sp.voice_source == "trial:kokoro"
    # A line the model cannot say falls back to the shipped path, loudly.
    lab.select("chatterbox-turbo")
    assert asyncio.run(T.speak_with_active(lab, "就是续保的事。", "zh", "male")) is None
    with pytest.raises(KeyError):
        lab.select("elevenlabs")
    lab.select(None)
    assert lab.active is None


def test_the_mlx_lab_keeps_only_a_couple_of_models_resident(monkeypatch, tmp_path):
    from voicebot.runtime.prerender import PrerenderCache

    async def run(fn, *a):
        return fn(*a)

    lab = T.MLXLab(PrerenderCache({"cache_dir": str(tmp_path)}, 16000), 16000, run)
    import sys
    import types
    loaded = []
    utils = types.SimpleNamespace(load=lambda repo: loaded.append(repo) or object())
    tts = types.SimpleNamespace(utils=utils)
    monkeypatch.setitem(sys.modules, "mlx_audio", types.SimpleNamespace(tts=tts))
    monkeypatch.setitem(sys.modules, "mlx_audio.tts", tts)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", utils)
    for repo in ("a", "b", "c"):
        lab._load(repo)
    assert loaded == ["a", "b", "c"]
    assert list(lab._loaded) == ["b", "c"], "the oldest is dropped, not the newest"
    lab._load("c")
    assert loaded == ["a", "b", "c"], "a resident model is not reloaded"


# -------------------------------------------------------------- sidecar lab

class _Stub(BaseHTTPRequestHandler):
    engine = "cosyvoice3"
    bodies: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"ready": True, "engine": self.engine, "languages": ["en", "zh"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        type(self).bodies.append(json.loads(self.rfile.read(n)))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"\x01\x00" * 8000)
        out = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def sidecar():
    _Stub.bodies = []
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _Stub
    srv.shutdown()


def test_the_sidecar_lab_routes_a_model_to_its_engines_sidecar(sidecar, tmp_path, monkeypatch):
    url, S = sidecar
    monkeypatch.setenv("VOICEBOT_TTS_SIDECARS", f"cosyvoice3={url}")
    lab = T.SidecarLab({"base_url": "http://127.0.0.1:9", "prerender": {
        "cache_dir": str(tmp_path), "voices": {"male": {"ref_audio": "voices/refs/male.wav"}}}},
        16000)
    rows = {m["id"]: m for m in lab.models()}
    assert rows["cosyvoice3"]["available"] is True
    assert rows["kokoro"]["available"] is False and "sidecar" in rows["kokoro"]["reason"]
    sp = asyncio.run(lab.render("No problem.", "en", "male", "cosyvoice3"))
    assert sp.pcm and sp.voice_source == "trial:cosyvoice3"
    assert S.bodies[0]["lang"] == "en" and S.bodies[0]["ref_audio"] == "voices/refs/male.wav"


def test_the_sidecar_lab_notices_the_wrong_engine_on_a_port(sidecar, tmp_path, monkeypatch):
    url, S = sidecar
    monkeypatch.setenv("VOICEBOT_TTS_SIDECARS", f"kokoro={url}")      # but it serves cosyvoice3
    lab = T.SidecarLab({"prerender": {"cache_dir": str(tmp_path)}}, 16000)
    ok, why = lab.availability(lab.spec("kokoro"))
    assert not ok and "cosyvoice3" in why
    with pytest.raises(T.Unavailable):
        asyncio.run(lab.render("Hi.", "en", None, "kokoro"))


def test_a_selected_model_bypasses_the_cache_on_the_gpu_box(sidecar, tmp_path, monkeypatch):
    """The point of selecting a model is to hear it — including on the lines
    the cache would otherwise serve in the incumbent's voice."""
    from voicebot.runtime.cuda_backend import CUDABackend

    url, S = sidecar
    monkeypatch.setenv("VOICEBOT_TTS_SIDECARS", f"cosyvoice3={url}")
    be = CUDABackend({"sample_rate": 16000,
                      "tts": {"base_url": "http://127.0.0.1:9",
                              "prerender": {"cache_dir": str(tmp_path), "voices": {"male": {}}}}})
    text = "Good afternoon Mr Tan."
    with wave.open(str(be.prerender.path(text, "en", "male")), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x02\x00" * 4000)
    sp = asyncio.run(be.speak(text, "en", prerendered=True, voice="male"))
    assert sp.voice_source == "cache" and not S.bodies
    be.lab.select("cosyvoice3")
    sp = asyncio.run(be.speak(text, "en", prerendered=True, voice="male"))
    assert sp.voice_source == "trial:cosyvoice3" and S.bodies


# ------------------------------------------------------------- the console

@pytest.fixture
def client(tmp_path):
    from voicebot import config, server

    cfg = config.load("mock")
    cfg.setdefault("backend", {}).setdefault("tts", {}).setdefault(
        "prerender", {"cache_dir": str(tmp_path / "cache"),
                      "voices": {"male": {"label": "Male", "target_f0": 162}}})
    server._state.clear()
    server._state["cfg"] = cfg
    yield TestClient(server.app)
    server._state.clear()


def test_the_console_lists_models_and_says_why_each_is_not_runnable(client):
    d = client.get("/api/tts/models").json()
    assert d["active"] is None
    ids = {m["id"] for m in d["models"]}
    assert {"cosyvoice3", "kokoro", "vibevoice"} <= ids
    assert all(m["available"] is False and "mock" in m["reason"] for m in d["models"])


def test_selecting_a_model_is_reported_and_fixed_during_a_call(client):
    from voicebot import server

    r = client.post("/api/tts/model", json={"id": "kokoro"})
    assert r.status_code == 200 and r.json()["active"] == "kokoro"
    assert client.get("/api/health").json()["tts_model"] == "kokoro"
    assert client.post("/api/tts/model", json={"id": "nope"}).status_code == 404
    server._state["live_calls"] = 1
    r = client.post("/api/tts/model", json={"id": None})
    assert r.status_code == 409 and r.json()["active"] == "kokoro"
    server._state["live_calls"] = 0
    assert client.post("/api/tts/model", json={"id": None}).json()["active"] is None


def test_say_returns_audio_with_the_model_and_latency_in_headers(client):
    r = client.post("/api/tts/say", json={"text": "Good afternoon Mr Tan.", "model": "kokoro"})
    assert r.status_code == 200, r.text
    assert r.headers["X-Model"] == "kokoro" and r.headers["X-Lang"] == "en"
    assert int(r.headers["X-Latency-Ms"]) >= 0
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 16000 and w.getnframes() > 0
    # Mandarin is guessed from the text, and an English-only model says no.
    r = client.post("/api/tts/say", json={"text": "就是续保的事。", "model": "vibevoice"})
    assert r.status_code == 400 and "zh" in r.json()["error"]
    # No model: the path a call would take.
    r = client.post("/api/tts/say", json={"text": "Hello."})
    assert r.status_code == 200 and r.headers["X-Model"] == "shipped"
    assert client.post("/api/tts/say", json={"text": "  "}).status_code == 400
    assert client.post("/api/tts/say", json={"text": "Hi", "model": "nope"}).status_code == 404
