"""The GPU TTS sidecar.

No CUDA and no Chatterbox here, so the model itself is stubbed. What is worth
testing is exactly what went wrong: the sidecar loaded the *English-only*
class, ignored the language it was sent, and read Mandarin through the English
phonemiser — a failure with no error and an output file of plausible length.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest


def test_health_remains_responsive_and_overload_is_bounded(monkeypatch):
    import asyncio
    import threading
    import httpx
    from voicebot.runtime.workers import BoundedExecutor

    mod = _sidecar()
    mod._worker.shutdown()
    mod._worker = BoundedExecutor(1, 0)
    mod._state['m'] = object()
    entered, release = threading.Event(), threading.Event()
    def render(*args):
        entered.set()
        assert release.wait(3)
        return b'fake-wav'
    monkeypatch.setattr(mod, '_render', render)
    monkeypatch.setattr(mod, '_reference', lambda *a: Path('reference.wav'))

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app),
                                     base_url='http://test') as client:
            body = {'text':'hello','lang':'en','voice':'male'}
            first = asyncio.create_task(client.post('/tts', json=body))
            while not entered.is_set():
                await asyncio.sleep(0)
            try:
                health = await asyncio.wait_for(client.get('/health'), .5)
                assert health.status_code == 200
                busy = await asyncio.wait_for(client.post('/tts', json=body), .5)
                assert busy.status_code == 503
                assert busy.headers['retry-after'] == '1'
            finally:
                release.set()
                assert (await first).status_code == 200
    try:
        asyncio.run(run())
    finally:
        release.set()
        mod._worker.shutdown()


def test_unloaded_sidecar_refuses_requests_without_loading(monkeypatch):
    from fastapi.testclient import TestClient
    mod = _sidecar()
    monkeypatch.setattr(mod, '_model', lambda: pytest.fail('request-time model load'))
    monkeypatch.setattr(mod, '_reference', lambda *a: Path('reference.wav'))
    client = TestClient(mod.app)
    assert client.get('/health').status_code == 503
    assert client.post('/tts', json={'text':'hello','lang':'en'}).status_code == 503

ROOT = Path(__file__).resolve().parents[1]


def _sidecar():
    spec = importlib.util.spec_from_file_location(
        "tts_sidecar", ROOT / "scripts/tts_sidecar.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_it_refuses_to_run_on_the_english_only_class(monkeypatch):
    """ChatterboxTTS loads happily and is not a substitute. Failing at boot
    with a named reason beats a call where every Mandarin line is nonsense."""
    mod = _sidecar()
    monkeypatch.setitem(sys.modules, "chatterbox",
                        types.SimpleNamespace(ChatterboxTTS=object))
    monkeypatch.setitem(sys.modules, "chatterbox.tts",
                        types.SimpleNamespace(ChatterboxTTS=object))
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", types.SimpleNamespace())
    with pytest.raises(RuntimeError) as e:
        mod._multilingual_class()
    assert "ChatterboxTTS" in str(e.value)


def test_it_finds_the_multilingual_class_wherever_the_release_put_it(monkeypatch):
    sentinel = object()
    mod = _sidecar()
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts",
                        types.SimpleNamespace(ChatterboxMultilingualTTS=sentinel))
    assert mod._multilingual_class() is sentinel


def _client(mod, monkeypatch, recorder):
    from fastapi.testclient import TestClient

    class _FakeTensor:
        """Chatterbox returns a torch tensor; the sidecar unwraps it."""
        def __init__(self, a): self._a = a
        def squeeze(self): return self
        def detach(self): return self
        def cpu(self): return self
        def numpy(self): return self._a

    class _FakeModel:
        sr = 16000

        def generate(self, text, **kw):
            import numpy as np
            recorder.append((text, kw))
            return _FakeTensor(np.zeros(1600, dtype=np.float32))

    monkeypatch.setitem(mod._state, "m", _FakeModel())
    monkeypatch.setitem(mod._state, "dev", "cpu")
    return TestClient(mod.app)


def test_the_language_is_passed_to_the_model(monkeypatch):
    mod = _sidecar()
    seen: list = []
    r = _client(mod, monkeypatch, seen).post(
        "/tts", json={"text": "就是续保的事。", "lang": "zh", "voice": "male"})
    assert r.status_code == 200
    assert seen[0][1]["language_id"] == "zh"


def test_a_request_without_a_language_is_refused(monkeypatch):
    """Not defaulted. A silently-English Mandarin line is the bug this file
    exists to prevent, and a 400 is how the caller finds out."""
    mod = _sidecar()
    seen: list = []
    r = _client(mod, monkeypatch, seen).post(
        "/tts", json={"text": "就是续保的事。", "voice": "male"})
    assert r.status_code == 400
    assert not seen
