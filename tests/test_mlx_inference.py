import asyncio
import sys
import threading
import types
import wave

import pytest

from voicebot.runtime.mlx_backend import MLXBackend


def config(tmp_path):
    return {'sample_rate':16000, 'asr':{'model':'asr'}, 'llm':{'model':'llm'},
            'tts':{'model':'kokoro', 'prerender':{'model':'clone', 'cache_dir':str(tmp_path),
                                               'voices':{'male':{}}}}}


def fake_models(monkeypatch, seen):
    def record(name):
        seen.append((name, threading.get_ident()))
    class Model:
        _processor = object()
        def generate(self, audio, **kwargs):
            record('asr.generate')
            return types.SimpleNamespace(text='yes')
    def stt_load(repo):
        record(repo + '.load'); return Model()
    def tts_load(repo):
        record(repo + '.load'); return object()
    class Tokenizer:
        def apply_chat_template(self, *args, **kwargs):
            record('template'); return 'prompt'
    def lm_load(repo):
        record(repo + '.load'); return object(), Tokenizer()
    def generate(*args, **kwargs):
        record('llm.generate'); return 'acknowledged'
    for name in ('mlx_audio', 'mlx_audio.stt', 'mlx_audio.tts'):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, 'mlx_audio.stt.utils', types.SimpleNamespace(load=stt_load))
    monkeypatch.setitem(sys.modules, 'mlx_audio.tts.utils', types.SimpleNamespace(load=tts_load))
    monkeypatch.setitem(sys.modules, 'mlx_lm', types.SimpleNamespace(load=lm_load, generate=generate))


def test_startup_and_generation_share_one_thread_and_one_tts(monkeypatch, tmp_path):
    seen = []
    fake_models(monkeypatch, seen)
    be = MLXBackend(config(tmp_path))
    try:
        be.load()
        asyncio.run(be.complete('system', 'hello', 'en'))
        asyncio.run(be.transcribe(b'\0\0'*800, 16000))
        assert [name for name, _ in seen].count('clone.load') == 1
        assert 'kokoro.load' not in [name for name, _ in seen]
        assert len({ident for _, ident in seen}) == 1
        assert seen[0][1] != threading.get_ident()
        assert be.health().tts == 'clone'
    finally:
        be.close()


def test_disabled_or_failed_llm_is_not_loaded_by_a_live_request(monkeypatch, tmp_path):
    be = MLXBackend(config(tmp_path))
    monkeypatch.setattr(be, '_ensure_llm', lambda: pytest.fail('live model load'))
    try:
        assert asyncio.run(be.complete('s', 'u', 'en')).text == ''
    finally:
        be.close()


def test_cache_read_bypasses_a_busy_gpu(monkeypatch, tmp_path):
    be = MLXBackend(config(tmp_path))
    path = be.prerender.path('cached', 'en', 'male')
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b'\1\0'*100)
    entered, release = threading.Event(), threading.Event()
    be._pool.submit(lambda: (entered.set(), release.wait(3)))
    assert entered.wait(1)
    try:
        async def read():
            return await asyncio.wait_for(be.speak('cached','en',True,'male'), .5)
        assert asyncio.run(read()).voice_source == 'cache'
        assert not release.is_set()
    finally:
        release.set(); be.close()


def test_novel_text_uses_resident_cache_voice_once(monkeypatch, tmp_path):
    be = MLXBackend(config(tmp_path))
    be._tts = be.prerender._model = object()
    calls = []
    def render(text, lang, voice, attempts):
        calls.append((text, lang, voice, attempts))
        return b'\1\0'*100
    monkeypatch.setattr(be.prerender, 'render', render)
    try:
        sp = asyncio.run(be.speak('new name', 'en', False, 'male'))
        assert sp.voice_source == 'rendered'
        assert calls == [('new name', 'en', 'male', 1)]
    finally:
        be.close()


def test_failed_voice_never_loads_or_changes_speaker_on_a_miss(monkeypatch, tmp_path):
    be = MLXBackend(config(tmp_path))
    monkeypatch.setattr(be.prerender, 'render', lambda *a: pytest.fail('late clone load'))
    try:
        with pytest.raises(RuntimeError, match='not loaded'):
            asyncio.run(be.speak('new name','en',True,'male'))
    finally:
        be.close()
