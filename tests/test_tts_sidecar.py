"""The GPU TTS sidecar.

No CUDA and no models here, so the models themselves are stubbed. What is
worth testing is exactly what went wrong before, and what the engine registry
must not let happen again: a model loaded for the wrong job, a language it
cannot speak read as though it could, a reference clip silently ignored.
"""
import importlib.util
import io
import sys
import types
import wave
from pathlib import Path

import numpy as np
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


class _FakeTensor:
    """Chatterbox returns a torch tensor; the sidecar unwraps it."""
    def __init__(self, a): self._a = a
    def squeeze(self): return self
    def detach(self): return self
    def cpu(self): return self
    def numpy(self): return self._a


class _FakeModel:
    sr = 16000
    conds = None

    def __init__(self, recorder):
        self.recorder = recorder

    def generate(self, text, **kw):
        self.recorder.append((text, kw))
        return _FakeTensor(np.zeros(1600, dtype=np.float32))


def _client(mod, monkeypatch, recorder, engine=None):
    from fastapi.testclient import TestClient

    monkeypatch.setitem(mod._state, "m", _FakeModel(recorder))
    monkeypatch.setitem(mod._state, "dev", "cpu")
    if engine:
        monkeypatch.setitem(mod._state, "engine_name", engine)
    return TestClient(mod.app)


def test_the_language_is_passed_to_the_model(monkeypatch):
    mod = _sidecar()
    seen: list = []
    r = _client(mod, monkeypatch, seen).post(
        "/tts", json={"text": "就是续保的事。", "lang": "zh", "voice": "male"})
    assert r.status_code == 200
    assert seen[0][1]["language_id"] == "zh"
    assert r.headers["X-Engine"] == "chatterbox"


def test_a_request_without_a_language_is_refused(monkeypatch):
    """Not defaulted. A silently-English Mandarin line is the bug this file
    exists to prevent, and a 400 is how the caller finds out."""
    mod = _sidecar()
    seen: list = []
    r = _client(mod, monkeypatch, seen).post(
        "/tts", json={"text": "就是续保的事。", "voice": "male"})
    assert r.status_code == 400
    assert not seen


# ------------------------------------------------------------------ engines

def test_the_default_engine_is_the_one_the_cache_was_rendered_with(monkeypatch):
    """The console cannot tell a live line from a cached one except by ear.
    Whatever else is available, the process that is not told otherwise
    speaks with the checkpoint every cached line was rendered by."""
    mod = _sidecar()
    monkeypatch.delenv("TTS_ENGINE", raising=False)
    monkeypatch.setitem(mod._state, "m", object())
    assert mod._engine().name == "chatterbox"


def test_every_candidate_in_the_shortlist_has_an_engine():
    """All seven proposed, plus the incumbent: CosyVoice 3, Fish Speech S2,
    Chatterbox Turbo, F5-TTS, IndexTTS 2, Kokoro, VibeVoice Realtime."""
    mod = _sidecar()
    assert {"chatterbox", "chatterbox-turbo", "cosyvoice3", "f5", "indextts2",
            "kokoro", "fish", "fish-server", "vibevoice"} <= set(mod.ENGINES)


def test_fish_in_process_takes_the_transcript_and_the_final_chunk(monkeypatch, tmp_path):
    """Fish's engine yields header, segments and a final; only the final is
    the whole line. And it will not clone without the transcript — a 400,
    not a random voice."""
    mod = _sidecar()
    seen: list = []

    class _Result:
        def __init__(self, code, audio=None, error=None):
            self.code, self.audio, self.error = code, audio, error

    class _FishEngine:
        def inference(self, req):
            seen.append(req)
            yield _Result("header", (44100, np.zeros(44, dtype=np.float32)))
            yield _Result("segment", (44100, np.zeros(441, dtype=np.float32)))
            yield _Result("final", (44100, np.zeros(4410, dtype=np.float32)))

    class _Ref:
        def __init__(self, audio, text): self.audio, self.text = audio, text

    class _Req:
        def __init__(self, text, references, format, streaming):
            self.text, self.references = text, references

    monkeypatch.setitem(sys.modules, "fish_speech", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "fish_speech.utils", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "fish_speech.utils.schema",
                        types.SimpleNamespace(ServeReferenceAudio=_Ref, ServeTTSRequest=_Req))
    from fastapi.testclient import TestClient
    monkeypatch.setitem(mod._state, "m", _FishEngine())
    monkeypatch.setitem(mod._state, "dev", "cpu")
    monkeypatch.setitem(mod._state, "engine_name", "fish")
    c = TestClient(mod.app)
    clip = tmp_path / "ref.wav"
    clip.write_bytes(b"RIFF....")

    r = c.post("/tts", json={"text": "Selamat petang.", "lang": "ms", "voice": "male",
                             "ref_audio": str(clip)})
    assert r.status_code == 400 and "transcript" in r.text
    assert not seen

    r = c.post("/tts", json={"text": "Selamat petang.", "lang": "ms", "voice": "male",
                             "ref_audio": str(clip), "ref_text": "Selamat pagi."})
    assert r.status_code == 200, r.text
    assert seen[0].references[0].text == "Selamat pagi."
    assert seen[0].references[0].audio == b"RIFF...."
    with wave.open(io.BytesIO(r.content)) as w:      # the final chunk, resampled
        assert w.getframerate() == 16000 and w.getnframes() == 1600


def test_vibevoice_is_a_preset_english_voice(monkeypatch, tmp_path):
    """No cloning and no Mandarin: the clip is ignored, Mandarin is a 400,
    and the text goes through the processor with the cached voice prompt
    exactly as the authors' own inference script does. The request's gender
    picks the preset: Carter for a man, Emma for a woman."""
    mod = _sidecar()
    calls: list = []

    class _Tensor(_FakeTensor):
        def float(self): return self

    class _Processor:
        tokenizer = object()

        def process_input_with_cached_prompt(self, text, cached_prompt, **kw):
            calls.append(("process", text, cached_prompt))
            return {"tts_text_ids": _FakeTensor(np.zeros(3))}

    class _Model:
        def generate(self, **kw):
            calls.append(("generate", kw))
            return types.SimpleNamespace(speech_outputs=[_Tensor(np.zeros(4800, dtype=np.float32))])

    monkeypatch.setitem(sys.modules, "torch",
                        types.SimpleNamespace(is_tensor=lambda v: False))
    from fastapi.testclient import TestClient
    presets = {"en-Carter_man": "/x/en-Carter_man.pt", "en-Emma_woman": "/x/en-Emma_woman.pt"}
    prompts = {"en-Carter_man": {"voice": "Carter"}, "en-Emma_woman": {"voice": "Emma"}}
    monkeypatch.setitem(mod._state, "m", {"model": _Model(), "processor": _Processor(),
                                          "presets": presets, "prompts": prompts,
                                          "device": "cpu"})
    monkeypatch.setitem(mod._state, "dev", "cpu")
    monkeypatch.setitem(mod._state, "engine_name", "vibevoice")
    monkeypatch.delenv("VIBEVOICE_VOICE", raising=False)
    c = TestClient(mod.app)

    r = c.post("/tts", json={"text": "就是续保的事。", "lang": "zh", "voice": "male"})
    assert r.status_code == 400 and "vibevoice" in r.text
    r = c.post("/tts", json={"text": "No problem [chuckle].", "lang": "en", "voice": "bella",
                             "gender": "female", "ref_audio": "voices/refs/male.wav"})
    assert r.status_code == 200, r.text
    assert calls[0] == ("process", "No problem [chuckle].", {"voice": "Emma"})
    kw = calls[1][1]
    assert kw["cfg_scale"] == mod.VibeVoice.CFG_SCALE and kw["all_prefilled_outputs"] == {"voice": "Emma"}
    assert kw["all_prefilled_outputs"] is not prompts["en-Emma_woman"], "a copy per line"
    with wave.open(io.BytesIO(r.content)) as w:      # 24 kHz in, 16 kHz out
        assert w.getframerate() == 16000 and w.getnframes() == 3200
    calls.clear()
    c.post("/tts", json={"text": "Hi.", "lang": "en", "voice": "male"})
    assert calls[0][2] == {"voice": "Carter"}, "no gender sent: the voice id decides"
    h = c.get("/health").json()
    assert h["clones"] is False and h["languages"] == ["en"]


def test_vibevoice_matches_a_preset_by_name_the_way_the_authors_do():
    mod = _sidecar()
    presets = {"en-Carter_man": "a", "en-Emma_woman": "b", "de-Spk0_man": "c"}
    assert mod.VibeVoice.match(presets, "Carter") == "en-Carter_man"
    assert mod.VibeVoice.match(presets, "emma") == "en-Emma_woman"
    assert mod.VibeVoice.match(presets, "en-Emma_woman") == "en-Emma_woman"
    assert mod.VibeVoice.match(presets, "man") is None, "ambiguous is not a match"
    assert mod.VibeVoice.match(presets, "Zoe") is None


def test_vibevoice_finds_its_presets_in_the_clone(tmp_path):
    mod = _sidecar()
    d = tmp_path / "demo" / "voices" / "streaming_model" / "en"
    d.mkdir(parents=True)
    (d / "Carter.pt").write_bytes(b"x")
    (d / "Emma.pt").write_bytes(b"x")
    assert list(mod.VibeVoice.presets(str(tmp_path))) == ["Carter", "Emma"]
    assert mod.VibeVoice.presets(str(tmp_path / "nowhere")) == {}


def test_an_unknown_engine_is_refused_by_name(monkeypatch):
    mod = _sidecar()
    monkeypatch.setenv("TTS_ENGINE", "elevenlabs")
    with pytest.raises(RuntimeError) as e:
        mod._engine()
    assert "elevenlabs" in str(e.value) and "cosyvoice3" in str(e.value)


def test_a_missing_dependency_names_the_install(monkeypatch):
    """`ModuleNotFoundError: f5_tts` three imports deep is not an
    instruction. The error says which engine and what to install."""
    mod = _sidecar()
    monkeypatch.setenv("TTS_ENGINE", "f5")
    monkeypatch.setitem(sys.modules, "f5_tts", None)      # import raises
    monkeypatch.setitem(sys.modules, "f5_tts.api", None)
    with pytest.raises(RuntimeError) as e:
        mod._engine()
    assert "f5" in str(e.value) and "pip install f5-tts" in str(e.value)


def test_an_english_only_engine_refuses_a_mandarin_line(monkeypatch):
    """Chatterbox Turbo has no language argument at all. Handed 就是续保的事
    it would read it through the English front-end and return audio of a
    plausible length; the 400 is the only place this can be caught."""
    mod = _sidecar()
    seen: list = []
    c = _client(mod, monkeypatch, seen, engine="chatterbox-turbo")
    r = c.post("/tts", json={"text": "就是续保的事。", "lang": "zh", "voice": "male",
                             "ref_audio": "voices/refs/male.wav"})
    assert r.status_code == 400
    assert "zh" in r.text and "chatterbox-turbo" in r.text
    assert not seen, "the model must not have been asked"

    r = c.post("/tts", json={"text": "No problem.", "lang": "en", "voice": "male",
                             "ref_audio": "voices/refs/male.wav"})
    assert r.status_code == 200
    assert "language_id" not in seen[0][1], "Turbo takes no language argument"
    assert seen[0][1]["audio_prompt_path"].endswith("voices/refs/male.wav")


def test_a_cloning_engine_without_a_clip_says_so(monkeypatch):
    """Not the model's default speaker: a stranger mid-call is the failure
    every reference-clip rule in this repo exists to prevent."""
    mod = _sidecar()
    seen: list = []
    c = _client(mod, monkeypatch, seen, engine="chatterbox-turbo")
    r = c.post("/tts", json={"text": "No problem.", "lang": "en", "voice": "nobody"})
    assert r.status_code == 400
    assert "reference clip" in r.text


def test_health_names_the_engine_and_what_it_speaks(monkeypatch):
    mod = _sidecar()
    c = _client(mod, monkeypatch, [], engine="chatterbox-turbo")
    h = c.get("/health").json()
    assert h["ready"] is True
    assert h["engine"] == "chatterbox-turbo"
    assert h["languages"] == ["en"]
    assert h["clones"] is True


def test_the_reference_transcript_is_read_from_beside_the_clip(tmp_path):
    """CosyVoice 3 and F5 clone better told what the clip says, and Fish will
    not clone without it. A .txt next to the wav is how a clip carries it;
    a transcript sent with the request wins over the file."""
    mod = _sidecar()
    clip = tmp_path / "someone.wav"
    clip.write_bytes(b"RIFF")
    assert mod._reference_text(clip, None) is None
    (tmp_path / "someone.txt").write_text("Good afternoon, this is a test.\n")
    assert mod._reference_text(clip, None) == "Good afternoon, this is a test."
    assert mod._reference_text(clip, "Said on the request") == "Said on the request"
    assert mod._reference_text(None, None) is None


def test_the_transcript_travels_to_an_engine_that_wants_it(monkeypatch, tmp_path):
    """CosyVoice 3 takes the transcript as its zero-shot prompt, behind the
    system prefix its own examples use; without one it takes the
    transcript-free path. Both are exercised against a fake model."""
    mod = _sidecar()
    calls: list = []

    class _Cosy:
        sample_rate = 24000

        def inference_zero_shot(self, text, prompt_text, prompt_wav, stream=False):
            calls.append(("zero_shot", text, prompt_text, prompt_wav))
            yield {"tts_speech": _FakeTensor(np.zeros(2400, dtype=np.float32))}

        def inference_cross_lingual(self, text, prompt_wav, stream=False):
            calls.append(("cross_lingual", text, None, prompt_wav))
            yield {"tts_speech": _FakeTensor(np.zeros(2400, dtype=np.float32))}

    _Cosy.__name__ = "CosyVoice3"
    from fastapi.testclient import TestClient
    monkeypatch.setitem(mod._state, "m", _Cosy())
    monkeypatch.setitem(mod._state, "dev", "cpu")
    monkeypatch.setitem(mod._state, "engine_name", "cosyvoice3")
    c = TestClient(mod.app)
    clip = tmp_path / "ref.wav"
    clip.write_bytes(b"RIFF")

    r = c.post("/tts", json={"text": "No problem.", "lang": "en", "voice": "male",
                             "ref_audio": str(clip), "ref_text": "Hello there."})
    assert r.status_code == 200
    kind, text, prompt, wav = calls[-1]
    assert kind == "zero_shot" and text == "No problem."
    assert prompt == mod.CosyVoice3.SYSTEM + "Hello there."

    r = c.post("/tts", json={"text": "没问题。", "lang": "zh", "voice": "male",
                             "ref_audio": str(clip)})
    assert r.status_code == 200
    kind, text, _, _ = calls[-1]
    assert kind == "cross_lingual" and text.endswith("没问题。")
    # Resampled to the 16 kHz the caller asked for, whatever the model emits.
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 16000 and w.getnframes() == 1600


def test_a_preset_voice_engine_picks_the_speaker_by_language(monkeypatch):
    """Kokoro does not clone. The clip is ignored — said once in the log —
    and the preset chosen is the same Mandarin speaker the cloning voices
    use for Mandarin, so a fallback line is at least not a stranger."""
    mod = _sidecar()
    asked: list = []

    class _Pipe:
        def __init__(self, code): self.code = code

        def __call__(self, text, voice):
            asked.append((self.code, voice, text))
            yield "gs", "ps", np.zeros(2400, dtype=np.float32)

    from fastapi.testclient import TestClient
    monkeypatch.setitem(mod._state, "m", {"a": _Pipe("a"), "z": _Pipe("z")})
    monkeypatch.setitem(mod._state, "dev", "cpu")
    monkeypatch.setitem(mod._state, "engine_name", "kokoro")
    monkeypatch.delenv("KOKORO_VOICE_ZH", raising=False)
    c = TestClient(mod.app)
    r = c.post("/tts", json={"text": "就是续保的事。", "lang": "zh", "voice": "female",
                             "ref_audio": "voices/refs/zf_xiaobei.wav"})
    assert r.status_code == 200
    assert asked[-1][:2] == ("z", "zf_xiaobei")
    r = c.post("/tts", json={"text": "Selamat petang.", "lang": "ms", "voice": "male"})
    assert r.status_code == 400, "Malay is not a language this engine has a preset for"


def test_listing_engines_needs_no_model(capsys):
    """`--list-engines` is documentation; it must work on a laptop with
    nothing installed, which is where someone decides what to try."""
    mod = _sidecar()
    import sys as _sys
    argv = _sys.argv
    _sys.argv = ["tts_sidecar.py", "--list-engines"]
    try:
        mod.main()
    finally:
        _sys.argv = argv
    out = capsys.readouterr().out
    assert "cosyvoice3" in out and "chatterbox-turbo" in out and "langs: en" in out
