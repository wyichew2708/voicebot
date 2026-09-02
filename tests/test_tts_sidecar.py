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
