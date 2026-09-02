"""Recording a voice in the console.

The pre-render model clones from a reference clip, so a custom voice is a wav
plus a measured pitch. What has to hold: a clip that cannot be cloned is
refused with a reason, a voice whose clip is gone is not offered, and a
recording can never shadow a voice the deployment ships.
"""
import json
import math
import wave

import pytest

from voicebot.voices import CustomVoices, VoiceError

RATE = 24000


def _voiced(seconds=12.0, f0=150.0, rate=RATE, amp=9000):
    """A pulse train under a syllable envelope — enough structure for the
    pitch tracker, unlike a pure tone, which flatters it."""
    n = int(seconds * rate)
    out = bytearray()
    for i in range(n):
        t = i / rate
        # Sum of a few harmonics: a glottal pulse is not a sine.
        v = sum(math.sin(2 * math.pi * f0 * k * t) / k for k in (1, 2, 3, 4))
        env = 0.55 + 0.45 * math.sin(2 * math.pi * 3.5 * t)     # ~3.5 syllables/s
        s = int(max(-1, min(1, v / 2.1)) * amp * env)
        out += int(s).to_bytes(2, "little", signed=True)
    return bytes(out)


@pytest.fixture
def store(tmp_path):
    return CustomVoices(tmp_path)


def test_a_recording_becomes_a_voice_with_its_own_measured_pitch(store):
    """Not asked for — measured. Every line is normalised to this figure, and
    the only honest source for it is the recording."""
    entry = store.add("Wei Ming", _voiced(f0=150), RATE)
    assert entry["id"] == "wei-ming"
    assert 140 < entry["measured_f0"] < 160, entry["measured_f0"]
    assert entry["target_f0"] == entry["measured_f0"]
    with wave.open(entry["ref_audio"]) as w:
        assert w.getnchannels() == 1 and w.getframerate() == RATE


@pytest.mark.parametrize("label,audio,rate,says", [
    ("Too short",  _voiced(3.0), RATE, "at least"),
    ("Silent",     bytes(RATE * 2 * 12), RATE, "almost silent"),
    ("",           _voiced(12.0), RATE, "name"),
])
def test_a_clip_that_cannot_be_cloned_is_refused_with_the_reason(store, label, audio,
                                                                 rate, says):
    with pytest.raises(VoiceError) as e:
        store.add(label, audio, rate)
    assert says in str(e.value)


def test_noise_is_refused_rather_than_given_a_meaningless_pitch(store):
    """A median taken over a handful of voiced frames does not describe a
    voice, and normalising every line to it is worse than not normalising."""
    import random

    rnd = random.Random(7)
    noise = b"".join(int(rnd.randint(-9000, 9000)).to_bytes(2, "little", signed=True)
                     for _ in range(RATE * 12))
    with pytest.raises(VoiceError) as e:
        store.add("Static", noise, RATE)
    # And say which of the three things went wrong: too little speech in a
    # long clip needs a different fix from a quiet microphone.
    assert "were speech" in str(e.value)


def test_pitch_moves_the_whole_voice_not_one_line(store):
    entry = store.add("Wei Ming", _voiced(f0=150), RATE)
    base = entry["measured_f0"]
    up = store.set_pitch(entry["id"], 2)
    assert up["target_f0"] == pytest.approx(base * 2 ** (2 / 12), rel=1e-3)
    assert up["measured_f0"] == base, "the measurement is not overwritten"
    assert store.set_pitch(entry["id"], 0)["target_f0"] == base


def test_the_pitch_control_is_bounded(store):
    entry = store.add("Wei Ming", _voiced(), RATE)
    assert store.set_pitch(entry["id"], 40)["semitones"] == 6
    assert store.set_pitch(entry["id"], -40)["semitones"] == -6


def test_a_voice_whose_clip_is_gone_is_not_offered(store):
    """Rendering against a missing reference does not fail — it silently
    produces a different speaker, which is the thing to prevent."""
    entry = store.add("Wei Ming", _voiced(), RATE)
    from pathlib import Path
    Path(entry["ref_audio"]).unlink()
    assert store.all() == {}
    assert store.merge_into({})["voices"] == {}


def test_a_recording_cannot_shadow_a_voice_the_profile_ships(store):
    """A shipped voice is part of the deployment. A recording named over it
    would change what every existing cached line was rendered as."""
    store.add("male", _voiced(), RATE)
    cfg = {"voices": {"male": {"label": "Male", "ref_audio": "voices/refs/male.wav",
                               "target_f0": 162}}}
    store.merge_into(cfg)
    assert cfg["voices"]["male"]["ref_audio"] == "voices/refs/male.wav"


def test_merging_gives_the_cache_an_ordinary_voice_entry(store):
    """Nothing downstream should be able to tell a recorded voice from a
    shipped one — same three keys, same code path."""
    entry = store.add("Wei Ming", _voiced(), RATE)
    cfg = store.merge_into({"voices": {}})
    assert set(cfg["voices"]["wei-ming"]) == {"label", "ref_audio", "target_f0"}
    assert cfg["voices"]["wei-ming"]["target_f0"] == entry["target_f0"]


def test_deleting_a_voice_takes_the_recording_with_it(store):
    from pathlib import Path
    entry = store.add("Wei Ming", _voiced(), RATE)
    assert store.remove(entry["id"]) is True
    assert not Path(entry["ref_audio"]).exists()
    assert store.all() == {}
    assert store.remove(entry["id"]) is False


def test_two_voices_with_the_same_name_do_not_collide(store):
    a = store.add("Dave", _voiced(), RATE)
    b = store.add("Dave", _voiced(f0=180), RATE)
    assert a["id"] != b["id"]
    assert len(store.all()) == 2


def test_an_unreadable_store_is_not_fatal(store, caplog):
    store.root.mkdir(parents=True, exist_ok=True)
    store.store.write_text("{ not json")
    assert store.all() == {}


def test_the_pitch_is_in_the_cache_key(tmp_path, store):
    """Changing the pitch changes the audio, so every warmed line for that
    voice becomes a miss. It must not quietly serve the old rendering."""
    from voicebot.runtime.prerender import PrerenderCache

    entry = store.add("Wei Ming", _voiced(), RATE)
    cfg = store.merge_into({"cache_dir": str(tmp_path / "c"), "voices": {}})
    before = PrerenderCache(cfg, 16000).key("hello", "en", entry["id"])
    store.set_pitch(entry["id"], 3)
    cfg2 = store.merge_into({"cache_dir": str(tmp_path / "c"), "voices": {}})
    assert PrerenderCache(cfg2, 16000).key("hello", "en", entry["id"]) != before
