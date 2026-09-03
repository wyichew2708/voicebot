"""A Mandarin call is cloned from a Mandarin speaker.

Every scripted line, Mandarin included, used to be Chatterbox cloning an
English reference clip. The Chinese front-end got the characters right and
the *speaker* it was imitating had never spoken Mandarin, so the tones came
out flat: transcribed back, every English-reference rendering lost all its
punctuation, and every Mandarin-reference one kept it. Each voice now names a
clip per language, and the language of the line picks it.

The property this file guards hardest is the one that is invisible when it
breaks: the English cache keys must not move. 4,379 rendered files are keyed
on the reference path and the target pitch, and a per-language setting that
resolved differently for English would re-render every one of them into a
slightly different voice.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from voicebot import config
from voicebot.runtime.prerender import PrerenderCache, for_language

ROOT = Path(__file__).resolve().parents[1]

_SCALAR = {"voices": {"male": {"ref_audio": "voices/refs/male.wav", "target_f0": 162}}}
_PER_LANG = {"voices": {"male": {
    "ref_audio": {"en": "voices/refs/male.wav", "zh": "voices/refs/zm_yunjian.wav"},
    "target_f0": {"en": 162, "zh": 135}}}}


def _cache(cfg, tmp_path):
    cfg = dict(cfg, cache_dir=str(tmp_path / "cache"))
    return PrerenderCache(cfg, 16000)


# --- resolution ------------------------------------------------------------

def test_a_scalar_setting_applies_to_every_language():
    assert for_language("voices/refs/male.wav", "zh") == "voices/refs/male.wav"
    assert for_language(162, None) == 162


def test_a_mapping_is_read_by_the_language_of_the_line(tmp_path):
    c = _cache(_PER_LANG, tmp_path)
    assert c.reference_for("male", "zh") == "voices/refs/zm_yunjian.wav"
    assert c.reference_for("male", "en") == "voices/refs/male.wav"
    assert c.target_f0("male", "zh") == 135
    assert c.target_f0("male", "en") == 162


def test_a_language_without_its_own_clip_falls_back_to_english(tmp_path):
    """A recorded voice has one clip. It still has to render a Mandarin line
    rather than raise — in the English speaker's voice, as it always did."""
    c = _cache({"voices": {"solo": {"ref_audio": {"en": "voices/refs/male.wav"},
                                    "target_f0": {"en": 162}}}}, tmp_path)
    assert c.reference_for("solo", "zh") == "voices/refs/male.wav"
    assert c.target_f0("solo", "zh") == 162


def test_the_pace_correction_is_per_language_too(tmp_path):
    """The female English clip reads slowly and is sped up 10%. The Mandarin
    clip does not, and inheriting the English figure would rush it."""
    c = _cache({"voices": {"female": {"ref_audio": {"en": "a.wav", "zh": "b.wav"},
                                      "rate": {"en": 1.1, "zh": 1.0}}}}, tmp_path)
    assert c.rate_for("female", "en") == pytest.approx(1.1)
    assert c.rate_for("female", "zh") == pytest.approx(1.0)


# --- the cache key ---------------------------------------------------------

def test_english_keys_do_not_move_when_a_mandarin_clip_is_added(tmp_path):
    """The property that matters most. Every English line on disk was keyed
    under the scalar form; the per-language form has to resolve to the very
    same key or the whole cache silently misses."""
    before = _cache(_SCALAR, tmp_path)
    after = _cache(_PER_LANG, tmp_path)
    line = "Please look through the email and renew by the due date."
    assert before.key(line, "en", "male") == after.key(line, "en", "male")


def test_mandarin_keys_do_move(tmp_path):
    """Same line, different speaker, different file — never the old audio."""
    before = _cache(_SCALAR, tmp_path)
    after = _cache(_PER_LANG, tmp_path)
    line = "请您在到期日之前完成续保。"
    assert before.key(line, "zh", "male") != after.key(line, "zh", "male")


def test_a_paced_voice_keeps_its_english_key(tmp_path):
    """`rate` is appended to the key only when it is not 1.0, and only for the
    language it applies to."""
    scalar = _cache({"voices": {"f": {"ref_audio": "a.wav", "target_f0": 233,
                                      "rate": 1.1}}}, tmp_path)
    per = _cache({"voices": {"f": {"ref_audio": {"en": "a.wav", "zh": "b.wav"},
                                   "target_f0": {"en": 233, "zh": 226},
                                   "rate": {"en": 1.1, "zh": 1.0}}}}, tmp_path)
    line = "No problem."
    assert scalar.key(line, "en", "f") == per.key(line, "en", "f")


# --- rendering -------------------------------------------------------------

def test_every_piece_of_a_mixed_line_is_cloned_from_the_mandarin_clip(tmp_path):
    """A Mandarin sentence with an English address in it is two fragments and
    one speaker: the Mandarin one reads the address, as a Mandarin-speaking
    agent would, rather than a second voice cutting in at the seam."""
    pytest.importorskip("mlx_audio")
    import numpy as np

    seen: list[dict] = []

    class _Seg:
        audio = np.zeros(1600, dtype=np.float32)
        sample_rate = 16000

    class _Model:
        def generate(self, **kw):
            seen.append(kw)
            yield _Seg()

    c = _cache({"voices": {"male": {
        "ref_audio": {"en": "voices/refs/male.wav", "zh": "voices/refs/zm_yunjian.wav"}}}},
        tmp_path)
    c._model = _Model()
    c.render("我是来跟您确认您在Jurong West Street 4, #08-212的居家保险续保事项。", "zh", "male")
    assert len(seen) >= 2
    assert {kw["ref_audio"] for kw in seen} == {"voices/refs/zm_yunjian.wav"}
    assert {kw["lang_code"] for kw in seen} == {"zh", "en"}


# --- the shipped profiles --------------------------------------------------

@pytest.mark.parametrize("profile", ["mac-polyglot", "rhel"])
def test_every_shipped_voice_has_a_mandarin_speaker_and_the_clip_exists(profile):
    """Every male voice clones zm_yunjian for Mandarin, every female voice
    zf_xiaobei — the two the operator chose by ear. A config that names a
    clip the repository does not carry is the RHEL failure mode: it renders
    the default speaker and nothing notices."""
    pr = config.load(profile)["backend"]["tts"]["prerender"]
    c = PrerenderCache(pr, 16000)
    for vid in pr["voices"]:
        ref = c.reference_for(vid, "zh")
        assert ref and ref.endswith(("zf_xiaobei.wav", "zm_yunjian.wav")), (vid, ref)
        assert (ROOT / ref).exists(), ref
        assert c.reference_for(vid, "en") != ref, vid
        assert c.target_f0(vid, "zh") in (135, 226), vid


def test_the_live_fallback_on_the_deployed_profile_has_a_mandarin_speaker():
    """Only reached when the pre-render model is down — but reached without
    this, Kokoro read Mandarin through the English phonemiser as af_heart."""
    tts = config.load("mac-polyglot")["backend"]["tts"]
    assert tts["voices"]["zh"]["code"] == "z"
    assert tts["voices"]["zh"]["voice"].startswith("z")


# --- the console -----------------------------------------------------------

def test_the_picker_reads_the_english_figures_and_flags_mandarin(tmp_path, monkeypatch):
    from voicebot import server
    from voicebot.voices import CustomVoices

    monkeypatch.setattr(server, "VOICES", CustomVoices(tmp_path))
    cfg = config.load("mock")
    cfg.setdefault("backend", {}).setdefault("tts", {})["prerender"] = {
        "cache_dir": str(tmp_path / "cache"),
        "voices": {"male": {"label": "Male",
                            "ref_audio": {"en": "voices/refs/male.wav",
                                          "zh": "voices/refs/zm_yunjian.wav"},
                            "target_f0": {"en": 162, "zh": 135}},
                   "solo": {"label": "Solo", "ref_audio": "voices/refs/male.wav",
                            "target_f0": 200}}}
    server._state.clear()
    server._state["cfg"] = cfg
    try:
        rows = {r["id"]: r for r in server._voice_rows()}
    finally:
        server._state.clear()
    assert rows["male"]["target_f0"] == 162 and rows["male"]["mandarin"] is True
    assert rows["solo"]["target_f0"] == 200 and rows["solo"]["mandarin"] is False
    assert rows["male"]["sample"] is True          # the English clip is on disk


# --- the GPU sidecar -------------------------------------------------------

def _sidecar():
    spec = importlib.util.spec_from_file_location(
        "tts_sidecar", ROOT / "scripts/tts_sidecar.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _client(mod, monkeypatch, recorder):
    from fastapi.testclient import TestClient

    class _Tensor:
        def __init__(self, a): self._a = a
        def squeeze(self): return self
        def detach(self): return self
        def cpu(self): return self
        def numpy(self): return self._a

    class _Model:
        sr = 16000

        def generate(self, text, **kw):
            import numpy as np
            recorder.append((text, kw))
            return _Tensor(np.zeros(1600, dtype=np.float32))

    monkeypatch.setitem(mod._state, "m", _Model())
    monkeypatch.setitem(mod._state, "dev", "cpu")
    return TestClient(mod.app)


def test_a_named_clip_is_the_one_cloned(monkeypatch, tmp_path):
    clip = tmp_path / "zm.wav"
    clip.write_bytes(b"RIFF")
    mod = _sidecar()
    seen: list = []
    r = _client(mod, monkeypatch, seen).post(
        "/tts", json={"text": "就是续保的事。", "lang": "zh", "voice": "male",
                      "ref_audio": str(clip)})
    assert r.status_code == 200
    assert seen[0][1]["audio_prompt_path"] == str(clip)


def test_a_relative_clip_path_resolves_against_the_deployment(monkeypatch):
    """The console sends paths as they appear in the profile —
    `voices/refs/...` — and the sidecar's working directory is the same
    mount, so those resolve. This is what makes the cache and the live path
    clone the same file."""
    mod = _sidecar()
    seen: list = []
    r = _client(mod, monkeypatch, seen).post(
        "/tts", json={"text": "就是续保的事。", "lang": "zh", "voice": "male",
                      "ref_audio": "voices/refs/zm_yunjian.wav"})
    assert r.status_code == 200
    assert seen[0][1]["audio_prompt_path"] == str(ROOT / "voices/refs/zm_yunjian.wav")


def test_a_named_clip_that_is_missing_is_refused(monkeypatch):
    """Not defaulted. Rendering the model's own speaker instead would produce
    a file of the right length in the wrong voice, which nothing downstream
    can tell from success."""
    mod = _sidecar()
    seen: list = []
    r = _client(mod, monkeypatch, seen).post(
        "/tts", json={"text": "就是续保的事。", "lang": "zh", "voice": "male",
                      "ref_audio": "voices/refs/nobody.wav"})
    assert r.status_code == 400
    assert not seen


def test_without_a_named_clip_the_old_lookup_still_works(monkeypatch):
    mod = _sidecar()
    seen: list = []
    r = _client(mod, monkeypatch, seen).post(
        "/tts", json={"text": "No problem.", "lang": "en", "voice": "male"})
    assert r.status_code == 200
    assert seen[0][1]["audio_prompt_path"] == str(ROOT / "voices/refs/male.wav")
